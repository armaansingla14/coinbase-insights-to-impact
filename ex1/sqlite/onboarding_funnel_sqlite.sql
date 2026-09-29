-- SQLite port of ex1/fct_onboarding_funnel.sql (same layers, same logic, same column names).
-- Snowflake -> SQLite translations:
--   QUALIFY ROW_NUMBER() ... = 1        -> subquery on rn = 1
--   IFF / DECODE                        -> CASE
--   BOOLOR_AGG                          -> MAX over 0/1
--   REGEXP_LIKE (full-string match), TRY_TO_TIMESTAMP_TZ/NTZ, TO_TIMESTAMP_NTZ(epoch), CONVERT_TIMEZONE,
--   DATEDIFF('minute')                  -> Python UDFs with Snowflake semantics, registered in build.py
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
        LOWER(TRIM(user_ref))                                   AS user_ref,
        CASE event_name WHEN 'sign_up'         THEN 'signup'
                        WHEN 'email_confirmed' THEN 'email_verified' END AS step,
        CASE WHEN regexp_like(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})')
             THEN convert_timezone('UTC', try_to_timestamp_tz(TRIM(ts_raw)))
             ELSE try_to_timestamp_ntz(TRIM(ts_raw)) END        AS event_ts_utc,
        NOT regexp_like(TRIM(ts_raw), '.*(Z|[+-][0-9]{2}:?[0-9]{2})') AS ts_was_assumed_utc,
        'app' AS source, CAST(event_id AS TEXT) AS source_event_id
    FROM raw_app_events
    WHERE event_name IN ('sign_up', 'email_confirmed')
),
stg_kyc AS (
    SELECT
        LOWER(TRIM(user_ref))                                   AS user_ref,
        'identity_verified'                                     AS step,
        to_timestamp_ntz(CAST(ts_epoch_s AS NUMERIC))           AS event_ts_utc,
        0                                                       AS ts_was_assumed_utc,
        'kyc' AS source, CAST(event_id AS TEXT) AS source_event_id
    FROM raw_kyc_events
    WHERE status = 'approved'
),
stg_ledger AS (
    SELECT
        COALESCE(LOWER(TRIM(CAST(a.user_uuid AS TEXT))), 'ledger_account:' || CAST(l.account_id AS TEXT)) AS user_ref,
        CASE l.txn_type WHEN 'deposit' THEN 'first_deposit'
                        WHEN 'trade'   THEN 'first_trade' END   AS step,
        convert_timezone(COALESCE(l.tz_name, 'UTC'), 'UTC', try_to_timestamp_ntz(l.ts_local)) AS event_ts_utc,
        l.tz_name IS NULL                                       AS ts_was_assumed_utc,
        'ledger' AS source, CAST(l.txn_id AS TEXT) AS source_event_id
    FROM raw_ledger_events l
    LEFT JOIN core_accounts a ON a.account_id = l.account_id
    WHERE l.txn_type IN ('deposit', 'trade')
      AND l.status = 'completed'
)
SELECT * FROM stg_app UNION ALL SELECT * FROM stg_kyc UNION ALL SELECT * FROM stg_ledger;

-- 2. IDENTITY RESOLUTION ---------------------------------------------------------------------
CREATE VIEW int_identity_map AS
SELECT LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_ref, LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_uuid FROM core_accounts
UNION
SELECT LOWER(TRIM(anonymous_id)), LOWER(TRIM(CAST(user_uuid AS TEXT))) FROM core_anon_links;

-- 3. QUARANTINE ------------------------------------------------------------------------------
CREATE VIEW int_onboarding_quarantine AS
SELECT e.*, m.user_uuid,
       CASE WHEN m.user_uuid IS NULL THEN 'unmatched_ref' ELSE 'unparseable_ts' END AS reason
FROM stg_onboarding_events e
LEFT JOIN int_identity_map m ON m.user_ref = e.user_ref
WHERE m.user_uuid IS NULL OR e.event_ts_utc IS NULL;

-- 4. LONG FACT -------------------------------------------------------------------------------
CREATE VIEW fct_onboarding_events AS
WITH ranked AS (
    SELECT
        m.user_uuid, e.step,
        CASE e.step WHEN 'signup' THEN 1 WHEN 'email_verified' THEN 2 WHEN 'identity_verified' THEN 3
                    WHEN 'first_deposit' THEN 4 WHEN 'first_trade' THEN 5 END AS step_order,
        e.event_ts_utc, e.source, e.source_event_id, e.ts_was_assumed_utc,
        COUNT(*) OVER (PARTITION BY m.user_uuid, e.step)         AS n_source_events,
        ROW_NUMBER() OVER (PARTITION BY m.user_uuid, e.step
                           ORDER BY e.event_ts_utc, e.source, e.source_event_id) AS rn
    FROM stg_onboarding_events e
    JOIN int_identity_map m ON m.user_ref = e.user_ref
    WHERE e.step IS NOT NULL
      AND e.event_ts_utc IS NOT NULL
),
firsts AS (
    SELECT user_uuid, step, step_order, event_ts_utc, source, source_event_id, ts_was_assumed_utc, n_source_events
    FROM ranked WHERE rn = 1                                     -- = QUALIFY ... = 1
)
SELECT *,
       COALESCE(event_ts_utc < MAX(event_ts_utc) OVER (PARTITION BY user_uuid ORDER BY step_order
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS is_before_prior_step
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
        MAX(is_before_prior_step)                                       AS is_out_of_order
    FROM fct_onboarding_events
    GROUP BY 1
),
accounts AS (
    SELECT LOWER(TRIM(CAST(user_uuid AS TEXT))) AS user_uuid, MIN(created_at) AS created_at
    FROM core_accounts GROUP BY 1
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
    COALESCE(signup_at, created_at)                              AS signup_at,
    signup_at IS NULL                                            AS signup_event_missing,
    DATE(COALESCE(signup_at, created_at))                        AS signup_cohort_date,
    email_verified_at, identity_verified_at, first_deposit_at, first_trade_at,
    (email_verified_at IS NOT NULL OR identity_verified_at IS NOT NULL
        OR first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)         AS reached_email_verified,
    (identity_verified_at IS NOT NULL OR first_deposit_at IS NOT NULL
        OR first_trade_at IS NOT NULL)                                         AS reached_identity_verified,
    (first_deposit_at IS NOT NULL OR first_trade_at IS NOT NULL)               AS reached_first_deposit,
    first_trade_at IS NOT NULL                                                 AS reached_first_trade,
    (email_verified_at IS NULL AND identity_verified_at IS NOT NULL)
      OR (identity_verified_at IS NULL AND first_deposit_at IS NOT NULL)
      OR (first_deposit_at IS NULL AND first_trade_at IS NOT NULL)             AS has_missing_step,
    COALESCE(is_out_of_order, 0)                                               AS is_out_of_order,
    COALESCE(has_assumed_tz, 0)                                                AS has_assumed_tz,
    datediff_minute(signup_at, first_trade_at)                                 AS minutes_signup_to_first_trade,
    CURRENT_TIMESTAMP                                                          AS _loaded_at
FROM spine
WHERE COALESCE(signup_at, created_at) IS NOT NULL;
