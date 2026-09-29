-- Ex4 mechanism demo: "% of weekly active users who traded".
-- Denominator in both: distinct weekly active users (WAU).
-- name: legacy_device_level
-- Legacy: numerator counts (user, device) pairs that traded => a user trading on 2 devices counts twice.
SELECT ROUND(100.0 * (SELECT COUNT(*) FROM (SELECT DISTINCT user_id, device_id FROM trades_wk))
             / (SELECT COUNT(DISTINCT user_id) FROM wau), 1) AS pct_wau_traded;

-- name: user_level
-- Rebuilt: numerator counts distinct users that traded and were WAU.
SELECT ROUND(100.0 * (SELECT COUNT(DISTINCT t.user_id) FROM trades_wk t
                      WHERE t.user_id IN (SELECT user_id FROM wau))
             / (SELECT COUNT(DISTINCT user_id) FROM wau), 1) AS pct_wau_traded;
