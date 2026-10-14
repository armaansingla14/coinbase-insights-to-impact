-- v2 data-quality checks on fct_user_week_trades. In production each query must return 0 rows
-- (or 0) or the run fails; the tests assert the exact counts on the synthetic fixture.

-- name: unmapped_status_trades
-- Trades whose status is not in dim_trade_status. They are excluded from "completed", so a new
-- status code would otherwise lower the metric silently.
SELECT COALESCE(SUM(n_unmapped_status), 0) AS n_unmapped_status_trades
FROM fct_user_week_trades;

-- name: users_with_only_unmapped_trades
-- User-weeks where EVERY trade has an unmapped status: these users are absent from the active
-- trader count only because we cannot classify their trades, not because they did not trade.
SELECT user_id, week_start, n_trades_any_status
FROM fct_user_week_trades
WHERE n_unmapped_status = n_trades_any_status
ORDER BY user_id, week_start;

-- name: grain_duplicates
-- One row per (user_id, week_start); a duplicate would double count in every downstream metric.
SELECT user_id, week_start, COUNT(*) AS n_rows
FROM fct_user_week_trades
GROUP BY user_id, week_start
HAVING COUNT(*) > 1;

-- name: trade_status_lookup_duplicates
-- dim_trade_status must have one row per status_id. A duplicate would multiply every trade with
-- that status in the LEFT JOIN, inflating n_trades_any_status and n_completed_trades.
SELECT status_id, COUNT(*) AS n_rows
FROM dim_trade_status
GROUP BY status_id
HAVING COUNT(*) > 1;
