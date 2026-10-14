-- Onboarding funnel model (Snowflake-flavoured SQL, dbt-style layering: one statement per model).
-- Executable twin for local testing: ex1/sqlite/onboarding_funnel_sqlite.sql (+ ex1/tests/test_funnel.py).
-- This file is not executed anywhere; Snowflake behaviour the twin cannot prove is marked UNVERIFIED.
--
-- Assumed sources (fictional):
--   raw.app_events    : event_id, user_ref, event_name ('sign_up','email_confirmed',...), ts_raw
--                       user_ref = user UUID or pre-signup anonymous/device id, padded, UUIDs in mixed case
--                       ts_raw   = ISO-8601 string; usually with offset (Z, +05:30, +0530), sometimes without
--   raw.kyc_events    : event_id, user_ref (UUID, upper case, sometimes padded), status ('approved','rejected'),
--                       ts_epoch_s (VARCHAR landing column: epoch seconds, UTC by definition; sometimes garbage)
--   raw.ledger_events : txn_id, account_id (INT), txn_type ('deposit','trade',...), status, ts_local (naive
--                       local wall-clock string), tz_name (IANA name, sometimes NULL)
--   core.accounts     : account_id, user_uuid, created_at (TIMESTAMP_NTZ in UTC) -- identity system of record
--   core.anon_links   : anonymous_id -> user_uuid (written at login/sign-up; one user may have many ids)
-- Every event_ts_utc below is TIMESTAMP_NTZ holding UTC, independent of the session TIMEZONE parameter.
--
-- BUSINESS-RULE ASSUMPTIONS (to confirm with the product owner; each one is a test in ex1/tests):
--   A1 Email verification and KYC are mandatory before funding or trading, and email comes before KYC.
--      So a later observed step lets us INFER an earlier one whose event is missing.
--   A2 A trade does NOT imply a deposit: card buys and crypto received from outside are alternate routes.
--      Deposit and trade are parallel "funding" routes; neither is a prerequisite of the other.
--   A3 *_at columns hold OBSERVED timestamps only. Nothing is invented; inference lives in *_state:
--        observed       = a usable source event exists (its earliest timestamp is in *_at)
--        inferred       = no usable event, but a later step was observed (A1); never used for deposit/trade
--        rejected_event = no usable event, but a source event for this user+step sits in quarantine
--                         (e.g. unparseable timestamp). This is OUR rejection, not a failed KYC/deposit.
--        not_reached    = none of the above
--      Precedence observed > inferred > rejected_event > not_reached (a later observed step is stronger
--      evidence than a broken event; the broken event stays visible in the quarantine view).
--      reached_* = state IN ('observed','inferred'). first_funding_at = earliest observed deposit or trade.
--   A4 Identity: user UUIDs are hex and case-insensitive (RFC 4122) -> LOWER(TRIM). Anonymous/device ids are
--      opaque and case-SENSITIVE -> TRIM only, exact match ('anon-Ab' and 'anon-ab' are different devices).
--      A ref is treated as a UUID iff it has the 8-4-4-4-12 hex shape. A ref linked to more than one user
--      is ambiguous and quarantined (never fanned out); a link to a UUID without an account is an orphan.
--   A5 Ledger wall-clock times: a time that does not exist in tz_name (spring-forward gap) is quarantined
--      as 'nonexistent_local_time'; a time that exists twice (fall-back overlap) resolves to the EARLIER
--      instant and is flagged ts_dst_ambiguous. Both are detected by round-tripping through
--      CONVERT_TIMEZONE, so the result does not depend on which instant Snowflake picks (UNVERIFIED);
--      the round-trip assumes 1-hour DST shifts (true for the zones in scope).

------------------------------------------------------------------------
-- 1. STAGING: one CTE per source, same output contract
--    (user_ref, step, event_ts_utc, ts_was_assumed_utc, ts_dst_ambiguous, ts_null_reason, source, source_event_id)
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.stg_onboarding_events AS
WITH stg_app AS (
    SELECT
        IFF(REGEXP_LIKE(TRIM(user_ref), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'),
            LOWER(TRIM(user_ref)), TRIM(user_ref))              AS user_ref,  -- A4: UUID vs anon id
        CASE event_name WHEN 'sign_up'         THEN 'signup'
                        WHEN 'email_confirmed' THEN 'email_verified' END AS step,
        IFF(REGEXP_LIKE(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})'),
            CONVERT_TIMEZONE('UTC', TRY_TO_TIMESTAMP_TZ(TRIM(ts_raw)))::TIMESTAMP_NTZ,  -- honour the offset
            TRY_TO_TIMESTAMP_NTZ(TRIM(ts_raw)))                 AS event_ts_utc,        -- no offset: assume UTC, explicitly
        NOT REGEXP_LIKE(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})') AS ts_was_assumed_utc,
        FALSE                                                   AS ts_dst_ambiguous,
        NULL::VARCHAR                                           AS ts_null_reason,
        'app' AS source, event_id::VARCHAR AS source_event_id
    FROM raw.app_events
    WHERE event_name IN ('sign_up', 'email_confirmed')
),
stg_kyc AS (
    SELECT
        IFF(REGEXP_LIKE(TRIM(user_ref), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'),
            LOWER(TRIM(user_ref)), TRIM(user_ref))              AS user_ref,
        'identity_verified'                                     AS step,
        -- numeric epoch = seconds, UTC. TRY_TO_NUMBER turns garbage into NULL -> quarantine, not a failed build
        -- (TO_TIMESTAMP_NTZ(NUMBER, scale 0) is always seconds; a digit STRING would be magnitude-guessed)
        TO_TIMESTAMP_NTZ(TRY_TO_NUMBER(ts_epoch_s::VARCHAR))    AS event_ts_utc,
        FALSE                                                   AS ts_was_assumed_utc,
        FALSE                                                   AS ts_dst_ambiguous,
        NULL::VARCHAR                                           AS ts_null_reason,
        'kyc' AS source, event_id::VARCHAR AS source_event_id
    FROM raw.kyc_events
    WHERE status = 'approved'                                   -- rejected attempts kept in a separate model
),
ledger_local AS (
    SELECT txn_id, account_id, txn_type, tz_name, COALESCE(tz_name, 'UTC') AS tz,
           TRY_TO_TIMESTAMP_NTZ(ts_local) AS local_ntz
    FROM raw.ledger_events
    WHERE txn_type IN ('deposit', 'trade')
      AND status = 'completed'                                  -- a failed deposit is not a deposit
),
ledger_guess AS (
    -- CONVERT_TIMEZONE(source_tz, target_tz, ntz): wall clock in tz -> UTC. Gap/overlap choice UNVERIFIED.
    SELECT *, CONVERT_TIMEZONE(tz, 'UTC', local_ntz) AS utc_guess FROM ledger_local
),
ledger_resolved AS (
    -- A5: keep a UTC instant only if it maps back to the same wall clock; prefer the earlier one.
    SELECT *,
           CASE WHEN CONVERT_TIMEZONE('UTC', tz, DATEADD('hour', -1, utc_guess)) = local_ntz
                THEN DATEADD('hour', -1, utc_guess)    -- overlap, engine picked the later instant
                WHEN CONVERT_TIMEZONE('UTC', tz, utc_guess) = local_ntz
                THEN utc_guess END                     -- else NULL: gap time (or unparseable)
                                                       AS event_ts_utc,
           COALESCE(CONVERT_TIMEZONE('UTC', tz, DATEADD('hour', -1, utc_guess)) = local_ntz
                 OR CONVERT_TIMEZONE('UTC', tz, DATEADD('hour', 1, utc_guess)) = local_ntz, FALSE)
                                                       AS ts_dst_ambiguous
    FROM ledger_guess
),
stg_ledger AS (
    SELECT
        -- resolved through int_identity_map like every other ref, so a bad accounts row cannot fan out
        'ledger_account:' || account_id::VARCHAR               AS user_ref,
        CASE txn_type WHEN 'deposit' THEN 'first_deposit'
                      WHEN 'trade'   THEN 'first_trade' END     AS step,
        event_ts_utc,
        tz_name IS NULL                                         AS ts_was_assumed_utc,
        ts_dst_ambiguous,
        IFF(local_ntz IS NOT NULL AND event_ts_utc IS NULL, 'nonexistent_local_time', NULL) AS ts_null_reason,
        'ledger' AS source, txn_id::VARCHAR AS source_event_id
    FROM ledger_resolved
)
SELECT * FROM stg_app UNION ALL SELECT * FROM stg_kyc UNION ALL SELECT * FROM stg_ledger;

------------------------------------------------------------------------
-- 2. IDENTITY RESOLUTION: every source ref -> at most one canonical user_uuid
--    Deterministic links only (system-of-record tables); no fuzzy matching in v1.
--    One row per user_ref (unique by construction); only link_status = 'resolved' rows carry a user_uuid.
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.int_identity_map AS
WITH links AS (
    SELECT LOWER(TRIM(user_uuid::VARCHAR)) AS user_ref, LOWER(TRIM(user_uuid::VARCHAR)) AS user_uuid
    FROM core.accounts
    UNION
    SELECT 'ledger_account:' || account_id::VARCHAR, LOWER(TRIM(user_uuid::VARCHAR))
    FROM core.accounts
    UNION
    SELECT IFF(REGEXP_LIKE(TRIM(anonymous_id), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'),
               LOWER(TRIM(anonymous_id)), TRIM(anonymous_id)),  -- A4: anon ids are case-sensitive
           LOWER(TRIM(user_uuid::VARCHAR))
    FROM core.anon_links
),
known_users AS (
    SELECT DISTINCT LOWER(TRIM(user_uuid::VARCHAR)) AS user_uuid FROM core.accounts
)
SELECT
    l.user_ref,
    CASE WHEN COUNT(DISTINCT l.user_uuid) > 1 THEN 'ambiguous_link'  -- would fan out events
         WHEN COUNT(k.user_uuid) = 0           THEN 'orphan_link'     -- user has no account
         ELSE 'resolved' END                                   AS link_status,
    IFF(COUNT(DISTINCT l.user_uuid) = 1 AND COUNT(k.user_uuid) > 0,
        MIN(l.user_uuid), NULL)                                AS user_uuid,
    COUNT(DISTINCT l.user_uuid)                                AS n_linked_uuids
FROM links l
LEFT JOIN known_users k ON k.user_uuid = l.user_uuid
GROUP BY l.user_ref;

------------------------------------------------------------------------
-- 3. QUARANTINE: every staged event that cannot enter the fact, with a reason. Nothing vanishes silently.
--    reason: unmatched_ref | ambiguous_link | orphan_link | unparseable_ts | nonexistent_local_time
--    user_uuid is set only when identity resolved (i.e. the problem is the timestamp).
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.int_onboarding_quarantine AS
SELECT e.*, m.user_uuid,
       CASE WHEN m.user_ref IS NULL          THEN 'unmatched_ref'
            WHEN m.link_status <> 'resolved' THEN m.link_status
            ELSE COALESCE(e.ts_null_reason, 'unparseable_ts') END AS reason
FROM analytics.stg_onboarding_events e
LEFT JOIN analytics.int_identity_map m ON m.user_ref = e.user_ref
WHERE m.user_ref IS NULL OR m.link_status <> 'resolved' OR e.event_ts_utc IS NULL;

------------------------------------------------------------------------
-- 4. LONG FACT: one row per (user_uuid, step) = first occurrence
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.fct_onboarding_events AS
WITH firsts AS (
    SELECT
        m.user_uuid, e.step,
        DECODE(e.step, 'signup', 1, 'email_verified', 2, 'identity_verified', 3,
                       'first_deposit', 4, 'first_trade', 5)      AS step_order,
        DECODE(e.step, 'signup', 1, 'email_verified', 2, 'identity_verified', 3,
                       'first_deposit', 4, 'first_trade', 4)      AS funnel_stage,      -- A2: parallel routes
        e.event_ts_utc, e.source, e.source_event_id, e.ts_was_assumed_utc, e.ts_dst_ambiguous,
        COUNT(*) OVER (PARTITION BY m.user_uuid, e.step)         AS n_source_events   -- retries / replays visible, not hidden
    FROM analytics.stg_onboarding_events e
    JOIN analytics.int_identity_map m ON m.user_ref = e.user_ref
                                     AND m.link_status = 'resolved' -- the rest is in int_onboarding_quarantine
    WHERE e.step IS NOT NULL
      AND e.event_ts_utc IS NOT NULL                             -- parse failures are in int_onboarding_quarantine
    QUALIFY ROW_NUMBER() OVER (PARTITION BY m.user_uuid, e.step
                               ORDER BY e.event_ts_utc, e.source, e.source_event_id) = 1
)
SELECT *,
       -- TRUE if a step happened before ANY step of an earlier stage (not just the adjacent one, so a gap such as
       -- a missing KYC event does not hide the problem). Deposit and trade share a stage, so a card buy before
       -- the first deposit is not out of order (A2). RANGE with a value offset needs a numeric ORDER BY key.
       COALESCE(event_ts_utc < MAX(event_ts_utc) OVER (PARTITION BY user_uuid ORDER BY funnel_stage
                                   RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), FALSE) AS is_before_prior_step
FROM firsts;

------------------------------------------------------------------------
-- 5. WIDE FUNNEL: one row per user who signed up (grain = user_uuid)
--    Spine = users with events FULL OUTER JOIN accounts, so an account with zero events still counts.
------------------------------------------------------------------------
CREATE OR REPLACE TABLE analytics.fct_onboarding_funnel AS
WITH pivoted AS (
    SELECT
        user_uuid,
        MIN(IFF(step = 'signup',            event_ts_utc, NULL)) AS signup_at,
        MIN(IFF(step = 'email_verified',    event_ts_utc, NULL)) AS email_verified_at,
        MIN(IFF(step = 'identity_verified', event_ts_utc, NULL)) AS identity_verified_at,
        MIN(IFF(step = 'first_deposit',     event_ts_utc, NULL)) AS first_deposit_at,
        MIN(IFF(step = 'first_trade',       event_ts_utc, NULL)) AS first_trade_at,
        BOOLOR_AGG(ts_was_assumed_utc)                           AS has_assumed_tz,
        BOOLOR_AGG(ts_dst_ambiguous)                             AS has_dst_ambiguous_ts,
        BOOLOR_AGG(is_before_prior_step)                         AS is_out_of_order
    FROM analytics.fct_onboarding_events
    GROUP BY 1
),
rejected AS (   -- user+step pairs whose only evidence is a quarantined event (identity resolved, timestamp bad)
    SELECT user_uuid,
           BOOLOR_AGG(step = 'email_verified')                   AS email_rejected,
           BOOLOR_AGG(step = 'identity_verified')                AS identity_rejected,
           BOOLOR_AGG(step = 'first_deposit')                    AS deposit_rejected,
           BOOLOR_AGG(step = 'first_trade')                      AS trade_rejected
    FROM analytics.int_onboarding_quarantine
    WHERE user_uuid IS NOT NULL
    GROUP BY 1
),
accounts AS (
    SELECT LOWER(TRIM(user_uuid::VARCHAR)) AS user_uuid, MIN(created_at) AS created_at
    FROM core.accounts GROUP BY 1
),
spine AS (
    SELECT COALESCE(p.user_uuid, a.user_uuid) AS user_uuid, a.created_at,
           p.signup_at, p.email_verified_at, p.identity_verified_at, p.first_deposit_at, p.first_trade_at,
           p.has_assumed_tz, p.has_dst_ambiguous_ts, p.is_out_of_order,
           r.email_rejected, r.identity_rejected, r.deposit_rejected, r.trade_rejected
    FROM pivoted p
    FULL OUTER JOIN accounts a ON a.user_uuid = p.user_uuid
    LEFT JOIN rejected r ON r.user_uuid = COALESCE(p.user_uuid, a.user_uuid)
),
states AS (     -- A1-A3: precedence observed > inferred > rejected_event > not_reached
    SELECT *,
        CASE WHEN email_verified_at IS NOT NULL      THEN 'observed'
             WHEN COALESCE(identity_verified_at, first_deposit_at, first_trade_at) IS NOT NULL
                                                     THEN 'inferred'
             WHEN email_rejected                     THEN 'rejected_event'
             ELSE 'not_reached' END                  AS email_verified_state,
        CASE WHEN identity_verified_at IS NOT NULL   THEN 'observed'
             WHEN COALESCE(first_deposit_at, first_trade_at) IS NOT NULL
                                                     THEN 'inferred'
             WHEN identity_rejected                  THEN 'rejected_event'
             ELSE 'not_reached' END                  AS identity_verified_state,
        CASE WHEN first_deposit_at IS NOT NULL       THEN 'observed'    -- A2: never inferred
             WHEN deposit_rejected                   THEN 'rejected_event'
             ELSE 'not_reached' END                  AS first_deposit_state,
        CASE WHEN first_trade_at IS NOT NULL         THEN 'observed'
             WHEN trade_rejected                     THEN 'rejected_event'
             ELSE 'not_reached' END                  AS first_trade_state
    FROM spine
)
SELECT
    user_uuid,
    COALESCE(signup_at, created_at)                              AS signup_at,        -- fallback: account record
    signup_at IS NULL                                            AS signup_event_missing,
    DATE(COALESCE(signup_at, created_at))                        AS signup_cohort_date, -- UTC day
    email_verified_at, identity_verified_at, first_deposit_at, first_trade_at,  -- OBSERVED only (A3)
    LEAST(COALESCE(first_deposit_at, first_trade_at),   -- LEAST is NULL if any arg is NULL,
          COALESCE(first_trade_at, first_deposit_at))   -- so compare only non-null values
                                                                 AS first_funding_at,
    email_verified_state, identity_verified_state, first_deposit_state, first_trade_state,

    email_verified_state IN ('observed', 'inferred')              AS reached_email_verified,
    identity_verified_state IN ('observed', 'inferred')           AS reached_identity_verified,
    first_deposit_state = 'observed'                              AS reached_first_deposit,
    first_trade_state = 'observed'                                AS reached_first_trade,
    (first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)  AS reached_funding,

    -- data quality flags, kept on the row so analysts can filter instead of guess.
    -- has_missing_step: a mandatory step (A1) has no usable event but a later step was observed.
    (email_verified_state = 'inferred' OR identity_verified_state = 'inferred')
                                                                  AS has_missing_step,
    COALESCE(is_out_of_order, FALSE)                                           AS is_out_of_order,
    COALESCE(has_assumed_tz, FALSE)                                            AS has_assumed_tz,
    COALESCE(has_dst_ambiguous_ts, FALSE)                                      AS has_dst_ambiguous_ts,

    DATEDIFF('minute', signup_at, first_trade_at)                -- event-based only: NULL if signup event missing
                                                                 AS minutes_signup_to_first_trade,
    CURRENT_TIMESTAMP()                                          AS _loaded_at
FROM states
WHERE COALESCE(signup_at, created_at) IS NOT NULL;               -- no anchor -> test "no_signup_anchor" below
-- Incremental in production: reprocess users with any event OR new identity link loaded in the last 7 days
-- (late data; a late anon->user link re-keys old events), MERGE on user_uuid so reruns are idempotent;
-- first-occurrence is chosen by event time, so a late event with an earlier timestamp correctly replaces the old one.

/* ---------------------------------------------------------------------
   DATA QUALITY TESTS (dbt schema.yml equivalents; all implemented as executable checks in ex1/sqlite/dq_checks.py)
   - fct_onboarding_funnel.user_uuid: unique, not_null                                       (error)
   - fct_onboarding_events: unique (user_uuid, step); step in accepted_values;
     event_ts_utc not_null                                                                   (error)
   - int_identity_map.user_ref: unique (no fan-out)                                          (error)
   - core.accounts: each account_id maps to exactly one user_uuid                            (error)
   - ambiguous identity links (ref -> >1 user; events quarantined, not fanned out)           (warn)
   - identity match rate: unmatched + ambiguous + orphan refs / all refs  (warn >= 0.5%, error >= 1%)
   - no_signup_anchor: users in fct_onboarding_events with no signup event and no account = 0 (warn)
   - reconciliation: signups in funnel vs core.accounts created, per UTC day, |diff| < 0.5%  (error)
   - is_out_of_order rate < 2% (beyond that it's a pipeline bug, not user behaviour)       (warn)
   - has_assumed_tz rate trending: alert on jumps vs trailing baseline (new client version dropping offsets)
   - freshness: max(event_ts_utc) per source within 6h of now                               (warn)
   - no future timestamps; no event before 2012-01-01 (Coinbase founding) -> parse failures  (error)
   - event conservation: staged = usable + quarantined                                      (error)
--------------------------------------------------------------------- */
