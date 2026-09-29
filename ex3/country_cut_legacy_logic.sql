-- ADDITIVE country cut: legacy FROM/WHERE/metric logic UNCHANGED, country exposed.
-- The only change is presentation: NULL country is labelled 'UNKNOWN' (SQL GROUP BY already
-- keeps NULL as its own group; the label stops BI tools / joins to a country dim dropping it).
-- Label on the dashboard: "Legacy definition; known issues in DATA-123."
--
-- Reconciliation (see name: reconcile below, asserted in test_pipeline.py):
--   * completed_trades: SUM over countries == global legacy total ALWAYS (SUM is additive,
--     same joined row set, every row lands in exactly one group).
--   * active_traders:   SUM over countries == global COUNT(DISTINCT user_id) ONLY IF every trader
--     has exactly one row in users. A user with 2 users rows (country history) is counted once
--     in each country. NULL-country users do NOT break it (they form one 'UNKNOWN' group).

-- name: country_cut
SELECT COALESCE(u.country, 'UNKNOWN') AS country,
       COUNT(DISTINCT t.user_id) AS active_traders,
       SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
FROM users u, trades t, devices d
WHERE u.user_id = t.user_id AND t.user_id = d.user_id
  AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1
GROUP BY u.country;

-- name: reconcile
WITH by_country AS (
    SELECT u.country,
           COUNT(DISTINCT t.user_id) AS active_traders,
           SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
    FROM users u, trades t, devices d
    WHERE u.user_id = t.user_id AND t.user_id = d.user_id
      AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1
    GROUP BY u.country
), global_legacy AS (          -- same logic, no country grain
    SELECT COUNT(DISTINCT t.user_id) AS active_traders,
           SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
    FROM users u, trades t, devices d
    WHERE u.user_id = t.user_id AND t.user_id = d.user_id
      AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1
)
SELECT (SELECT SUM(active_traders)   FROM by_country) AS sum_country_active_traders,
       (SELECT active_traders        FROM global_legacy) AS global_active_traders,
       (SELECT SUM(completed_trades) FROM by_country) AS sum_country_completed_trades,
       (SELECT completed_trades      FROM global_legacy) AS global_completed_trades,
       (SELECT COUNT(*) FROM (SELECT user_id FROM users GROUP BY user_id HAVING COUNT(*) > 1))
                                                    AS users_with_multiple_rows;
