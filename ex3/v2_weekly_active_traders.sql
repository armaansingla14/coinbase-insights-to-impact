-- v2 weekly active traders by country.
-- Reads fct_user_week_trades (v2_fct_user_week_trades.sql: one row per user per week, with
--   exactly one country per active user-week = country as of their first completed trade;
--   an ASSUMPTION for the metric owner to confirm, see that file).
-- Definition: active trader = user with >= 1 COMPLETED trade in the UTC week (per dim_trade_status).
--   NOTE: this is a definition change vs legacy (legacy counted a user with only failed trades);
--   it ships side-by-side and only replaces legacy after metric-owner sign-off.
-- Additive by construction: each active user-week sits in exactly one country, so the sum over
--   countries equals the global COUNT(DISTINCT user_id) for the week (tested).
-- Traders missing from users, with NULL country, or with no country row valid at trade time
--   are already 'UNKNOWN' in the fact, so they never silently disappear.
SELECT
    w.week_start,
    w.country,
    COUNT(*)                  AS active_traders,   -- 1 row per user-week => distinct users
    SUM(w.n_completed_trades) AS completed_trades
FROM fct_user_week_trades w
WHERE w.is_active_trader = 1
GROUP BY w.week_start, w.country
ORDER BY w.week_start, w.country
