-- v2 weekly active traders by country.
-- Reads fct_user_week_trades (v2_fct_user_week_trades.sql: one row per user per week).
-- Definition: active trader = user with >= 1 COMPLETED trade in the UTC week (per dim_trade_status).
--   NOTE: this is a definition change vs legacy (legacy counted a user with only failed trades);
--   it ships side-by-side and only replaces legacy after metric-owner sign-off.
-- Country: signup country = earliest row in users per user_id, so dim_user is ONE ROW PER USER
--   (tested). Point-in-time alternative: pick the row valid at week_start; same shape.
-- LEFT JOIN + COALESCE: traders missing from users, or with NULL country, land in 'UNKNOWN'
--   instead of silently disappearing.
WITH dim_user AS (
    SELECT user_id, country AS signup_country
    FROM (
        SELECT user_id, country,
               ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY valid_from, country) AS rn
        FROM users
    )
    WHERE rn = 1
)
SELECT
    w.week_start,
    COALESCE(u.signup_country, 'UNKNOWN') AS country,
    COUNT(*)                              AS active_traders,   -- 1 row per user-week => COUNT(*) is distinct users
    SUM(w.n_completed_trades)             AS completed_trades
FROM fct_user_week_trades w
LEFT JOIN dim_user u
       ON u.user_id = w.user_id
WHERE w.is_active_trader = 1
GROUP BY w.week_start, COALESCE(u.signup_country, 'UNKNOWN')
ORDER BY w.week_start, country
