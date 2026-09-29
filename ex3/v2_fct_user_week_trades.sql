-- v2 intermediate model: fct_user_week_trades
-- Grain: ONE ROW PER (user_id, week_start). Tested unique in test_pipeline.py.
-- * No devices join at all: a trade is a trade regardless of how many devices the user
--   has (removes fan-out) or whether a device is active *today* (removes the
--   non-point-in-time filter and stops web-only users from disappearing).
-- * Status meaning comes from dim_trade_status, not magic numbers. LEFT JOIN so an unmapped
--   status is visible (n_unmapped_status) instead of silently dropped; a DQ test fails on it.
-- * Weeks are UTC, Monday-start. created_at is assumed stored as UTC.
--   Snowflake: DATE_TRUNC('week', CONVERT_TIMEZONE('UTC', t.created_at))  (WEEK_START = 0/1 => Monday)
--   SQLite (used by the tests): DATE(t.created_at, '-6 days', 'weekday 1')
SELECT
    t.user_id,
    DATE(t.created_at, '-6 days', 'weekday 1')                         AS week_start,
    COUNT(*)                                                           AS n_trades_any_status,
    SUM(CASE WHEN s.is_completed = 1 THEN 1 ELSE 0 END)                AS n_completed_trades,
    SUM(CASE WHEN s.status_id IS NULL THEN 1 ELSE 0 END)               AS n_unmapped_status,
    CASE WHEN SUM(CASE WHEN s.is_completed = 1 THEN 1 ELSE 0 END) > 0
         THEN 1 ELSE 0 END                                             AS is_active_trader
FROM trades t
LEFT JOIN dim_trade_status s
       ON s.status_id = t.status
GROUP BY t.user_id, DATE(t.created_at, '-6 days', 'weekday 1')
