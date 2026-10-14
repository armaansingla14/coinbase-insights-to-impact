-- SQLite port of ex1/fct_onboarding_funnel.sql (same layers, same logic, same column names).
-- Business-rule ASSUMPTIONS are documented once, in the header of the Snowflake file.
-- Snowflake -> SQLite translations:
--   QUALIFY ROW_NUMBER() ... = 1        -> subquery on rn = 1
--   IFF / DECODE                        -> CASE
--   BOOLOR_AGG                          -> MAX over 0/1
--   LEAST(a, b)                         -> scalar MIN(a, b)       (both return NULL if any argument is NULL)
--   DATEADD('hour', n, ts)              -> datetime(ts, 'n hours')
--   REGEXP_LIKE (full-string match), TRY_TO_TIMESTAMP_TZ/NTZ, TO_TIMESTAMP_NTZ(epoch), TRY_TO_NUMBER,
--   CONVERT_TIMEZONE, DATEDIFF('minute') -> Python UDFs with Snowflake semantics, registered in build.py
-- Timestamps are TEXT 'YYYY-MM-DD HH:MM:SS' in UTC (sorts correctly as text). Booleans are 0/1.
-- Raw tables use '_' instead of '.' for schemas: raw_app_events, raw_kyc_events, raw_ledger_events,
-- core_accounts, core_anon_links.

DROP VIEW IF EXISTS stg_onboarding_events;
DROP VIEW IF EXISTS int_identity_map;
DROP VIEW IF EXISTS int_onboarding_quarantine;
DROP VIEW IF EXISTS fct_onboarding_events;
DROP TABLE IF EXISTS fct_onboarding_funnel;

-- 1. STAGING -------------------------------------------------------------------------------
CREATE VIEW stg_onboarding_events AS
WITH stg_app AS (
    SELECT
        CASE WHEN regexp_like(TRIM(user_ref), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
             THEN LOWER(TRIM(user_ref)) ELSE TRIM(user_ref) END AS user_ref,
        CASE event_name WHEN 'sign_up'         THEN 'signup'
                        WHEN 'email_confirmed' THEN 'email_verified' END AS step,
        CASE WHEN regexp_like(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})')
             THEN convert_timezone('UTC', try_to_timestamp_tz(TRIM(ts_raw)))
             ELSE try_to_timestamp_ntz(TRIM(ts_raw)) END        AS event_ts_utc,
        NOT regexp_like(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})') AS ts_was_assumed_utc,
        0                                                       AS ts_dst_ambiguous,
        NULL                                                    AS ts_null_reason,
        'app' AS source, CAST(event_id AS TEXT) AS source_event_id
    FROM raw_app_events
    WHERE event_name IN ('sign_up', 'email_confirmed')
),
stg_kyc AS (
    SELECT
        CASE WHEN regexp_like(TRIM(user_ref), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
             THEN LOWER(TRIM(user_ref)) ELSE TRIM(user_ref) END AS user_ref,
        'identity_verified'                                     AS step,
        to_timestamp_ntz(try_to_number(ts_epoch_s))             AS event_ts_utc,  -- garbage -> NULL -> quarantine
        0                                                       AS ts_was_assumed_utc,
        0                                                       AS ts_dst_ambiguous,
        NULL                                                    AS ts_null_reason,
        'kyc' AS source, CAST(event_id AS TEXT) AS source_event_id
    FROM raw_kyc_events
    WHERE status = 'approved'
),
ledger_local AS (
    SELECT txn_id, account_id, txn_type, tz_name, COALESCE(tz_name, 'UTC') AS tz,
           try_to_timestamp_ntz(ts_local) AS local_ntz
    FROM raw_ledger_events
    WHERE txn_type IN ('deposit', 'trade')
      AND status = 'completed'
),
ledger_guess AS (
    SELECT *, convert_timezone(tz, 'UTC', local_ntz) AS utc_guess FROM ledger_local
),
ledger_resolved AS (
    SELECT *,
           CASE WHEN convert_timezone('UTC', tz, datetime(utc_guess, '-1 hours')) = local_ntz
                THEN datetime(utc_guess, '-1 hours')
                WHEN convert_timezone('UTC', tz, utc_guess) = local_ntz
                THEN utc_guess END                                  AS event_ts_utc,
           COALESCE(convert_timezone('UTC', tz, datetime(utc_guess, '-1 hours')) = local_ntz
                 OR convert_timezone('UTC', tz, datetime(utc_guess, '+1 hours')) = local_ntz, 0)
                                                                    AS ts_dst_ambiguous
    FROM ledger_guess
),
stg_ledger AS (
    SELECT
        'ledger_account:' || CAST(account_id AS TEXT)           AS user_ref,
        CASE txn_type WHEN 'deposit' THEN 'first_deposit'
                      WHEN 'trade'   THEN 'first_trade' END     AS step,
        event_ts_utc,
        tz_name IS NULL                                         AS ts_was_assumed_utc,
        ts_dst_ambiguous,
        CASE WHEN local_ntz IS NOT NULL AND event_ts_utc IS NULL
             THEN 'nonexistent_local_time' END                  AS ts_null_reason,
        'ledger' AS source, CAST(txn_id AS TEXT) AS source_event_id
    FROM ledger_resolved
)
SELECT * FROM stg_app UNION ALL SELECT * FROM stg_kyc UNION ALL SELECT * FROM stg_ledger;

-- 2. IDENTITY RESOLUTION ---------------------------------------------------------------------
CREATE VIEW int_identity_map AS
WITH links AS (
    SELECT LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_ref, LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_uuid
    FROM core_accounts
    UNION
    SELECT 'ledger_account:' || CAST(account_id AS TEXT), LOWER(TRIM(CAST(user_uuid AS TEXT)))
    FROM core_accounts
    UNION
    SELECT CASE WHEN regexp_like(TRIM(anonymous_id), '[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}')
                THEN LOWER(TRIM(anonymous_id)) ELSE TRIM(anonymous_id) END,
           LOWER(TRIM(CAST(user_uuid AS TEXT)))
    FROM core_anon_links
),
known_users AS (SELECT DISTINCT LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_uuid FROM core_accounts)
SELECT
    l.user_ref,
    CASE WHEN COUNT(DISTINCT l.user_uuid) > 1 THEN 'ambiguous_link'
         WHEN COUNT(k.user_uuid) = 0           THEN 'orphan_link'
         ELSE 'resolved' END                                   AS link_status,
    CASE WHEN COUNT(DISTINCT l.user_uuid) = 1 AND COUNT(k.user_uuid) > 0
         THEN MIN(l.user_uuid) END                             AS user_uuid,
    COUNT(DISTINCT l.user_uuid)                                AS n_linked_uuids
FROM links l
LEFT JOIN known_users k ON k.user_uuid = l.user_uuid
GROUP BY l.user_ref;

-- 3. QUARANTINE ------------------------------------------------------------------------------
CREATE VIEW int_onboarding_quarantine AS
SELECT e.*, m.user_uuid,
       CASE WHEN m.user_ref IS NULL          THEN 'unmatched_ref'
            WHEN m.link_status <> 'resolved' THEN m.link_status
            ELSE COALESCE(e.ts_null_reason, 'unparseable_ts') END AS reason
FROM stg_onboarding_events e
LEFT JOIN int_identity_map m ON m.user_ref = e.user_ref
WHERE m.user_ref IS NULL OR m.link_status <> 'resolved' OR e.event_ts_utc IS NULL;

-- 4. LONG FACT -------------------------------------------------------------------------------
CREATE VIEW fct_onboarding_events AS
WITH ranked AS (
    SELECT
        m.user_uuid, e.step,
        CASE e.step WHEN 'signup' THEN 1 WHEN 'email_verified' THEN 2 WHEN 'identity_verified' THEN 3
                    WHEN 'first_deposit' THEN 4 WHEN 'first_trade' THEN 5 END AS step_order,
        CASE e.step WHEN 'signup' THEN 1 WHEN 'email_verified' THEN 2 WHEN 'identity_verified' THEN 3
                    WHEN 'first_deposit' THEN 4 WHEN 'first_trade' THEN 4 END AS funnel_stage,
        e.event_ts_utc, e.source, e.source_event_id, e.ts_was_assumed_utc, e.ts_dst_ambiguous,
        COUNT(*) OVER (PARTITION BY m.user_uuid, e.step)         AS n_source_events,
        ROW_NUMBER() OVER (PARTITION BY m.user_uuid, e.step
                           ORDER BY e.event_ts_utc, e.source, e.source_event_id) AS rn
    FROM stg_onboarding_events e
    JOIN int_identity_map m ON m.user_ref = e.user_ref AND m.link_status = 'resolved'
    WHERE e.step IS NOT NULL
      AND e.event_ts_utc IS NOT NULL
),
firsts AS (
    SELECT user_uuid, step, step_order, funnel_stage, event_ts_utc, source, source_event_id,
           ts_was_assumed_utc, ts_dst_ambiguous, n_source_events
    FROM ranked WHERE rn = 1                                     -- = QUALIFY ... = 1
)
SELECT *,
       COALESCE(event_ts_utc < MAX(event_ts_utc) OVER (PARTITION BY user_uuid ORDER BY funnel_stage
                                   RANGE BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS is_before_prior_step
FROM firsts;

-- 5. WIDE FUNNEL -----------------------------------------------------------------------------
CREATE TABLE fct_onboarding_funnel AS
WITH pivoted AS (
    SELECT
        user_uuid,
        MIN(CASE WHEN step = 'signup'            THEN event_ts_utc END) AS signup_at,
        MIN(CASE WHEN step = 'email_verified'    THEN event_ts_utc END) AS email_verified_at,
        MIN(CASE WHEN step = 'identity_verified' THEN event_ts_utc END) AS identity_verified_at,
        MIN(CASE WHEN step = 'first_deposit'     THEN event_ts_utc END) AS first_deposit_at,
        MIN(CASE WHEN step = 'first_trade'       THEN event_ts_utc END) AS first_trade_at,
        MAX(ts_was_assumed_utc)                                         AS has_assumed_tz,
        MAX(ts_dst_ambiguous)                                           AS has_dst_ambiguous_ts,
        MAX(is_before_prior_step)                                       AS is_out_of_order
    FROM fct_onboarding_events
    GROUP BY 1
),
rejected AS (
    SELECT user_uuid,
           MAX(step = 'email_verified')    AS email_rejected,
           MAX(step = 'identity_verified') AS identity_rejected,
           MAX(step = 'first_deposit')     AS deposit_rejected,
           MAX(step = 'first_trade')       AS trade_rejected
    FROM int_onboarding_quarantine
    WHERE user_uuid IS NOT NULL
    GROUP BY 1
),
accounts AS (
    SELECT LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_uuid, MIN(created_at) AS created_at
    FROM core_accounts GROUP BY 1
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
states AS (
    SELECT *,
        CASE WHEN email_verified_at IS NOT NULL      THEN 'observed'
             WHEN COALESCE(identity_verified_at, first_deposit_at, first_trade_at) IS NOT NULL
                                                     THEN 'inferred'
             WHEN email_rejected = 1                 THEN 'rejected_event'
             ELSE 'not_reached' END                  AS email_verified_state,
        CASE WHEN identity_verified_at IS NOT NULL   THEN 'observed'
             WHEN COALESCE(first_deposit_at, first_trade_at) IS NOT NULL
                                                     THEN 'inferred'
             WHEN identity_rejected = 1              THEN 'rejected_event'
             ELSE 'not_reached' END                  AS identity_verified_state,
        CASE WHEN first_deposit_at IS NOT NULL       THEN 'observed'
             WHEN deposit_rejected = 1               THEN 'rejected_event'
             ELSE 'not_reached' END                  AS first_deposit_state,
        CASE WHEN first_trade_at IS NOT NULL         THEN 'observed'
             WHEN trade_rejected = 1                 THEN 'rejected_event'
             ELSE 'not_reached' END                  AS first_trade_state
    FROM spine
)
SELECT
    user_uuid,
    COALESCE(signup_at, created_at)                              AS signup_at,
    signup_at IS NULL                                            AS signup_event_missing,
    DATE(COALESCE(signup_at, created_at))                        AS signup_cohort_date,
    email_verified_at, identity_verified_at, first_deposit_at, first_trade_at,
    MIN(COALESCE(first_deposit_at, first_trade_at),
        COALESCE(first_trade_at, first_deposit_at))                            AS first_funding_at,
    email_verified_state, identity_verified_state, first_deposit_state, first_trade_state,
    email_verified_state IN ('observed', 'inferred')              AS reached_email_verified,
    identity_verified_state IN ('observed', 'inferred')           AS reached_identity_verified,
    first_deposit_state = 'observed'                              AS reached_first_deposit,
    first_trade_state = 'observed'                                AS reached_first_trade,
    (first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)  AS reached_funding,
    (email_verified_state = 'inferred' OR identity_verified_state = 'inferred')
                                                                  AS has_missing_step,
    COALESCE(is_out_of_order, 0)                                               AS is_out_of_order,
    COALESCE(has_assumed_tz, 0)                                                AS has_assumed_tz,
    COALESCE(has_dst_ambiguous_ts, 0)                                          AS has_dst_ambiguous_ts,
    datediff_minute(signup_at, first_trade_at)                                 AS minutes_signup_to_first_trade,
    CURRENT_TIMESTAMP                                                          AS _loaded_at
FROM states
WHERE COALESCE(signup_at, created_at) IS NOT NULL;
