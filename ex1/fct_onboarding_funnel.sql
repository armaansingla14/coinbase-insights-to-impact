-- Onboarding funnel model (Snowflake-flavoured SQL, dbt-style layering: one statement per model).
-- Executable twin for local testing: ex1/sqlite/onboarding_funnel_sqlite.sql (+ ex1/tests/test_funnel.py).
--
-- Assumed sources (fictional):
--   raw.app_events    : event_id, user_ref, event_name ('sign_up','email_confirmed',...), ts_raw
--                       user_ref = user UUID or pre-signup anonymous_id, mixed case / padded
--                       ts_raw   = ISO-8601 string; usually with offset (Z, +05:30, +0530), sometimes without
--   raw.kyc_events    : event_id, user_ref (UUID, upper case, sometimes padded), status ('approved','rejected'),
--                       ts_epoch_s (NUMBER, epoch seconds, UTC by definition)
--   raw.ledger_events : txn_id, account_id (INT), txn_type ('deposit','trade',...), status, ts_local (naive
--                       local wall-clock string), tz_name (IANA name, sometimes NULL)
--   core.accounts     : account_id, user_uuid, created_at (TIMESTAMP_NTZ in UTC) -- identity system of record
--   core.anon_links   : anonymous_id -> user_uuid (written at login/sign-up)
-- Every event_ts_utc below is TIMESTAMP_NTZ holding UTC, independent of the session TIMEZONE parameter.

------------------------------------------------------------------------
-- 1. STAGING: one CTE per source, same output contract
--    (user_ref, step, event_ts_utc, ts_was_assumed_utc, source, source_event_id)
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.stg_onboarding_events AS
WITH stg_app AS (
    SELECT
        LOWER(TRIM(user_ref))                                   AS user_ref,
        CASE event_name WHEN 'sign_up'         THEN 'signup'
                        WHEN 'email_confirmed' THEN 'email_verified' END AS step,
        IFF(REGEXP_LIKE(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})'),
            CONVERT_TIMEZONE('UTC', TRY_TO_TIMESTAMP_TZ(TRIM(ts_raw)))::TIMESTAMP_NTZ,  -- honour the offset
            TRY_TO_TIMESTAMP_NTZ(TRIM(ts_raw)))                 AS event_ts_utc,        -- no offset: assume UTC, explicitly
        NOT REGEXP_LIKE(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})') AS ts_was_assumed_utc,
        'app' AS source, event_id::VARCHAR AS source_event_id
    FROM raw.app_events
    WHERE event_name IN ('sign_up', 'email_confirmed')
),
stg_kyc AS (
    SELECT
        LOWER(TRIM(user_ref))                                   AS user_ref,
        'identity_verified'                                     AS step,
        TO_TIMESTAMP_NTZ(ts_epoch_s::NUMBER)                    AS event_ts_utc,        -- numeric epoch = seconds, UTC
        FALSE                                                   AS ts_was_assumed_utc,
        'kyc' AS source, event_id::VARCHAR AS source_event_id
    FROM raw.kyc_events
    WHERE status = 'approved'                                   -- rejected attempts kept in a separate model
),
stg_ledger AS (
    SELECT
        -- lower-cased to match int_identity_map; unknown accounts keep a traceable ref so they reach quarantine
        COALESCE(LOWER(TRIM(a.user_uuid::VARCHAR)), 'ledger_account:' || l.account_id::VARCHAR) AS user_ref,
        CASE l.txn_type WHEN 'deposit' THEN 'first_deposit'
                        WHEN 'trade'   THEN 'first_trade' END   AS step,
        -- CONVERT_TIMEZONE(source_tz, target_tz, ntz): wall-clock in tz_name -> UTC (DST-aware)
        CONVERT_TIMEZONE(COALESCE(l.tz_name, 'UTC'), 'UTC', TRY_TO_TIMESTAMP_NTZ(l.ts_local)) AS event_ts_utc,
        l.tz_name IS NULL                                       AS ts_was_assumed_utc,
        'ledger' AS source, l.txn_id::VARCHAR AS source_event_id
    FROM raw.ledger_events l
    LEFT JOIN core.accounts a ON a.account_id = l.account_id
    WHERE l.txn_type IN ('deposit', 'trade')
      AND l.status = 'completed'                                -- a failed deposit is not a deposit
)
SELECT * FROM stg_app UNION ALL SELECT * FROM stg_kyc UNION ALL SELECT * FROM stg_ledger;

------------------------------------------------------------------------
-- 2. IDENTITY RESOLUTION: every source ref -> one canonical user_uuid
--    Deterministic links only (system-of-record tables); no fuzzy matching in v1.
--    Test: user_ref unique (an anonymous_id linked to two users would fan out every event).
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.int_identity_map AS
SELECT LOWER(TRIM(user_uuid::VARCHAR)) AS user_ref, LOWER(TRIM(user_uuid::VARCHAR)) AS user_uuid FROM core.accounts
UNION
SELECT LOWER(TRIM(anonymous_id)), LOWER(TRIM(user_uuid::VARCHAR)) FROM core.anon_links;

------------------------------------------------------------------------
-- 3. QUARANTINE: every staged event that cannot enter the fact, with a reason. Nothing vanishes silently.
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.int_onboarding_quarantine AS
SELECT e.*, m.user_uuid,
       CASE WHEN m.user_uuid IS NULL THEN 'unmatched_ref' ELSE 'unparseable_ts' END AS reason
FROM analytics.stg_onboarding_events e
LEFT JOIN analytics.int_identity_map m ON m.user_ref = e.user_ref
WHERE m.user_uuid IS NULL OR e.event_ts_utc IS NULL;
-- (users that resolve but have neither a sign-up event nor an account row are listed by the
--  "no_signup_anchor" test at the bottom of this file)

------------------------------------------------------------------------
-- 4. LONG FACT: one row per (user_uuid, step) = first occurrence
------------------------------------------------------------------------
CREATE OR REPLACE VIEW analytics.fct_onboarding_events AS
WITH firsts AS (
    SELECT
        m.user_uuid, e.step,
        DECODE(e.step, 'signup', 1, 'email_verified', 2, 'identity_verified', 3,
                       'first_deposit', 4, 'first_trade', 5)      AS step_order,
        e.event_ts_utc, e.source, e.source_event_id, e.ts_was_assumed_utc,
        COUNT(*) OVER (PARTITION BY m.user_uuid, e.step)         AS n_source_events   -- retries / replays visible, not hidden
    FROM analytics.stg_onboarding_events e
    JOIN analytics.int_identity_map m ON m.user_ref = e.user_ref -- misses are in int_onboarding_quarantine
    WHERE e.step IS NOT NULL
      AND e.event_ts_utc IS NOT NULL                             -- parse failures are in int_onboarding_quarantine
    QUALIFY ROW_NUMBER() OVER (PARTITION BY m.user_uuid, e.step
                               ORDER BY e.event_ts_utc, e.source, e.source_event_id) = 1
)
SELECT *,
       -- TRUE if an EARLIER funnel step happened LATER in time (compares against every earlier step, not just
       -- the adjacent one, so a gap such as a missing KYC event does not hide the problem)
       COALESCE(event_ts_utc < MAX(event_ts_utc) OVER (PARTITION BY user_uuid ORDER BY step_order
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), FALSE) AS is_before_prior_step
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
        BOOLOR_AGG(is_before_prior_step)                         AS is_out_of_order
    FROM analytics.fct_onboarding_events
    GROUP BY 1
),
accounts AS (
    SELECT LOWER(TRIM(user_uuid::VARCHAR)) AS user_uuid, MIN(created_at) AS created_at
    FROM core.accounts GROUP BY 1
),
spine AS (
    SELECT COALESCE(p.user_uuid, a.user_uuid) AS user_uuid, a.created_at,
           p.signup_at, p.email_verified_at, p.identity_verified_at, p.first_deposit_at, p.first_trade_at,
           p.has_assumed_tz, p.is_out_of_order
    FROM pivoted p
    FULL OUTER JOIN accounts a ON a.user_uuid = p.user_uuid
)
SELECT
    user_uuid,
    COALESCE(signup_at, created_at)                              AS signup_at,        -- fallback: account record
    signup_at IS NULL                                            AS signup_event_missing,
    DATE(COALESCE(signup_at, created_at))                        AS signup_cohort_date, -- UTC day
    email_verified_at, identity_verified_at, first_deposit_at, first_trade_at,

    -- "reached" flags: a later step implies earlier ones happened, even if the event was lost
    (email_verified_at IS NOT NULL OR identity_verified_at IS NOT NULL
        OR first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)         AS reached_email_verified,
    (identity_verified_at IS NOT NULL OR first_deposit_at IS NOT NULL
        OR first_trade_at IS NOT NULL)                                         AS reached_identity_verified,
    (first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)               AS reached_first_deposit,
    first_trade_at IS NOT NULL                                                 AS reached_first_trade,

    -- data quality flags, kept on the row so analysts can filter instead of guess.
    -- has_missing_step: some post-signup step has no event although a later step does. Checking each adjacent
    -- pair is sufficient: any gap is followed (eventually) by a NULL step whose next step is present.
    (email_verified_at IS NULL AND identity_verified_at IS NOT NULL)
      OR (identity_verified_at IS NULL AND first_deposit_at IS NOT NULL)
      OR (first_deposit_at IS NULL AND first_trade_at IS NOT NULL)             AS has_missing_step,
    COALESCE(is_out_of_order, FALSE)                                           AS is_out_of_order,
    COALESCE(has_assumed_tz, FALSE)                                            AS has_assumed_tz,

    DATEDIFF('minute', signup_at, first_trade_at)                -- event-based only: NULL if signup event missing
                                                                 AS minutes_signup_to_first_trade,
    CURRENT_TIMESTAMP()                                          AS _loaded_at
FROM spine
WHERE COALESCE(signup_at, created_at) IS NOT NULL;               -- no anchor -> test "no_signup_anchor" below
-- Incremental in production: reprocess users with any event OR new identity link loaded in the last 7 days
-- (late data; a late anon->user link re-keys old events), MERGE on user_uuid so reruns are idempotent;
-- first-occurrence is chosen by event time, so a late event with an earlier timestamp correctly replaces the old one.

/* ---------------------------------------------------------------------
   DATA QUALITY TESTS (dbt schema.yml equivalents; all implemented as executable checks in ex1/tests/test_funnel.py)
   - fct_onboarding_funnel.user_uuid: unique, not_null                                       (error)
   - fct_onboarding_events: unique (user_uuid, step); step in accepted_values;
     event_ts_utc not_null                                                                   (error)
   - int_identity_map.user_ref: unique (no fan-out)                                          (error)
   - identity match rate: unmatched refs / all refs  (warn >= 0.5%, error >= 1%)
   - no_signup_anchor: users in fct_onboarding_events with no signup event and no account = 0 (warn)
   - reconciliation: signups in funnel vs core.accounts created, per UTC day, |diff| < 0.5%  (error)
   - is_out_of_order rate < 2% (beyond that it's a pipeline bug, not user behaviour)       (warn)
   - has_assumed_tz rate trending: alert on jumps vs trailing baseline (new client version dropping offsets)
   - freshness: max(event_ts_utc) per source within 6h of now                               (warn)
   - no future timestamps; no event before 2012-01-01 (Coinbase founding) -> parse failures  (error)
--------------------------------------------------------------------- */
