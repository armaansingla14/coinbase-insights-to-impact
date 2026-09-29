-- Legacy snippet, VERBATIM from the inherited 400-line pipeline (do not edit).
-- Pinned by the characterization test in test_pipeline.py (the "baseline contract").
SELECT u.country, COUNT(DISTINCT t.user_id) AS active_traders, SUM(CASE WHEN t.status = 1 OR t.status = 3 THEN 1 ELSE 0 END) AS completed_trades
FROM users u, trades t, devices d WHERE u.user_id = t.user_id AND t.user_id = d.user_id AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1 GROUP BY u.country
