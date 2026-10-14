-- ADDITIVE country cut: legacy FROM/WHERE/metric logic UNCHANGED, country exposed.
-- The only change is presentation: NULL country is labelled 'UNKNOWN' (SQL GROUP BY already
-- keeps NULL as its own group; the label stops BI tools / joins to a country dim dropping it).
-- Label on the dashboard: "Legacy definition; known issues in DATA-123."
-- The cut MUST be shown with the reconcile row below, so it never pretends to add up.
--
-- Reconciliation (name: reconcile, asserted in test_pipeline.py):
--   * completed_trades: SUM over countries == global legacy total ALWAYS (SUM is additive,
--     same joined row set, every row lands in exactly one group).
--   * active_traders:   SUM over countries == global COUNT(DISTINCT user_id) ONLY IF every trader
--     appears in exactly one country. legacy joins ALL users rows (country history), so a user
--     with rows in N countries is counted once in each of them: overcount = N - 1 per such user.
--     NULL-country users do NOT break it (they form one 'UNKNOWN' group).

-- name: country_cut
SELECT COALESCE(u.country, 'UNKNOWN') AS country,
       COUNT(DISTINCT t.user_id) AS active_traders,
       SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
FROM users u, trades t, devices d
WHERE u.user_id = t.user_id AND t.user_id = d.user_id
  AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1
GROUP BY u.country;

-- name: reconcile
WITH legacy_rows AS (          -- the legacy joined row set, before GROUP BY
    SELECT COALESCE(u.country, 'UNKNOWN') AS country, t.user_id,
           CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END AS is_completed
    FROM users u, trades t, devices d
    WHERE u.user_id = t.user_id AND t.user_id = d.user_id
      AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1
), by_country AS (
    SELECT country,
           COUNT(DISTINCT user_id) AS active_traders,
           SUM(is_completed)       AS completed_trades
    FROM legacy_rows
    GROUP BY country
), multi_country_users AS (
    SELECT user_id FROM legacy_rows GROUP BY user_id HAVING COUNT(DISTINCT country) > 1
)
SELECT (SELECT SUM(active_traders)          FROM by_country)  AS sum_country_active_traders,
       (SELECT COUNT(DISTINCT user_id)      FROM legacy_rows) AS global_active_traders,
       (SELECT SUM(active_traders)          FROM by_country)
     - (SELECT COUNT(DISTINCT user_id)      FROM legacy_rows) AS active_traders_overcount,
       (SELECT COUNT(*)            FROM multi_country_users)  AS users_in_multiple_countries,
       (SELECT SUM(completed_trades)        FROM by_country)  AS sum_country_completed_trades,
       (SELECT SUM(is_completed)            FROM legacy_rows) AS global_completed_trades;
