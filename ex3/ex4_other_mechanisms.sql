-- Ex4: two OTHER mechanisms that can move "% of WAU who traded" from 34% to ~41%, besides the
-- device-grain numerator in ex4_device_vs_user_rate.sql. These DEMONSTRATE POSSIBLE mechanisms
-- on synthetic data; they are hypotheses to check against the real legacy SQL,
-- NOT the real cause.
-- Tables: wau_ex4(user_id, is_kyc_verified, is_test_account), trades_ex4(user_id, status) with
-- status 'COMPLETED' or 'FAILED'.

-- name: baseline
-- Reference definition: real (non-test) WAU; numerator = WAU with >= 1 COMPLETED trade.
SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN t.status = 'COMPLETED' THEN w.user_id END)
             / COUNT(DISTINCT w.user_id), 1) AS pct_wau_traded
FROM wau_ex4 w
LEFT JOIN trades_ex4 t ON t.user_id = w.user_id
WHERE w.is_test_account = 0;

-- name: attempted_numerator
-- (a) Numerator counts ATTEMPTED trades (any status): users whose trades all failed count too.
SELECT ROUND(100.0 * COUNT(DISTINCT t.user_id) / COUNT(DISTINCT w.user_id), 1) AS pct_wau_traded
FROM wau_ex4 w
LEFT JOIN trades_ex4 t ON t.user_id = w.user_id
WHERE w.is_test_account = 0;

-- name: eligible_only_denominator
-- (b1) Denominator restricted to KYC-verified WAU: unverified users (who cannot trade) drop out
-- of the denominator only, so the rate rises with no change in trading.
SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN t.status = 'COMPLETED' THEN w.user_id END)
             / COUNT(DISTINCT w.user_id), 1) AS pct_wau_traded
FROM wau_ex4 w
LEFT JOIN trades_ex4 t ON t.user_id = w.user_id
WHERE w.is_test_account = 0 AND w.is_kyc_verified = 1;

-- name: includes_test_accounts
-- (b2) Test / internal accounts not excluded: they trade every week, inflating both sides.
SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN t.status = 'COMPLETED' THEN w.user_id END)
             / COUNT(DISTINCT w.user_id), 1) AS pct_wau_traded
FROM wau_ex4 w
LEFT JOIN trades_ex4 t ON t.user_id = w.user_id;
