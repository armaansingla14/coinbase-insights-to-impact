-- v2 intermediate model: fct_user_week_trades
-- Grain: ONE ROW PER (user_id, week_start). Tested unique in test_pipeline.py.
-- * No devices join at all: a trade is a trade regardless of how many devices the user
--   has (removes fan-out) or whether a device is active *today* (removes the
--   non-point-in-time filter and stops web-only users from disappearing).
-- * Status meaning comes from dim_trade_status, not magic numbers. LEFT JOIN so an unmapped
--   status is visible (n_unmapped_status) instead of silently dropped; see v2_dq_checks.sql.
-- * Weeks are UTC, Monday-start. created_at is assumed TIMESTAMP_TZ (stored as UTC); if NTZ, use 3-arg CONVERT_TIMEZONE.
--   Snowflake: DATE_TRUNC('week', CONVERT_TIMEZONE('UTC', t.created_at))  (WEEK_START = 0/1 => Monday)
--   SQLite (used by the tests): DATE(t.created_at, '-6 days', 'weekday 1')
--
-- Country (ASSUMPTION - metric owner to confirm): the country valid AS OF the user's FIRST
--   COMPLETED trade of the week, read from the users history (one row per country change,
--   valid from valid_from until the next row). Exactly one country per user-week, so a user who
--   moves mid-week is counted once and their trades are never multiplied.
--   Alternatives the owner may prefer: signup country (earliest row), or current country
--   (latest row; restates history whenever a user moves).
--   Deterministic rules:
--   * valid_from IS NULL = the earliest row (treated as valid since before any dated row).
--   * Ties on valid_from are broken by country, NULL country last.
--   * No row valid at that time (trade before the first dated row), user missing from users,
--     or NULL country => 'UNKNOWN'. Never dropped.
--   * country is NULL on rows that are not active traders (no completed trade => no anchor).
--   Snowflake: DATE valid_from vs TIMESTAMP compares as midnight (= the SQLite text compare).
--   NULLS LAST is written out because Snowflake defaults to NULLS FIRST for DESC (SQLite: LAST).
--   QUALIFY rn = 1 could replace the rn filter in the final join.
WITH user_week AS (
    SELECT
        t.user_id,
        DATE(t.created_at, '-6 days', 'weekday 1')                         AS week_start,
        COUNT(*)                                                           AS n_trades_any_status,
        SUM(CASE WHEN s.is_completed = 1 THEN 1 ELSE 0 END)                AS n_completed_trades,
        SUM(CASE WHEN s.status_id IS NULL THEN 1 ELSE 0 END)               AS n_unmapped_status,
        MIN(CASE WHEN s.is_completed = 1 THEN t.created_at END)            AS first_completed_at,
        CASE WHEN SUM(CASE WHEN s.is_completed = 1 THEN 1 ELSE 0 END) > 0
             THEN 1 ELSE 0 END                                             AS is_active_trader
    FROM trades t
    LEFT JOIN dim_trade_status s
           ON s.status_id = t.status
    GROUP BY t.user_id, DATE(t.created_at, '-6 days', 'weekday 1')
), country_as_of_first_completed AS (
    SELECT w.user_id, w.week_start, u.country,
           ROW_NUMBER() OVER (PARTITION BY w.user_id, w.week_start
                              ORDER BY u.valid_from DESC NULLS LAST, u.country NULLS LAST) AS rn
    FROM user_week w
    JOIN users u
      ON u.user_id = w.user_id
     AND (u.valid_from IS NULL OR u.valid_from <= w.first_completed_at)
    WHERE w.is_active_trader = 1
)
SELECT
    w.user_id,
    w.week_start,
    w.n_trades_any_status,
    w.n_completed_trades,
    w.n_unmapped_status,
    w.first_completed_at,
    w.is_active_trader,
    CASE WHEN w.is_active_trader = 1 THEN COALESCE(c.country, 'UNKNOWN') END AS country
FROM user_week w
LEFT JOIN country_as_of_first_completed c
       ON c.user_id = w.user_id AND c.week_start = w.week_start AND c.rn = 1
