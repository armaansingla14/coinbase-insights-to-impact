-- SQLite twin of the PM query shown in submission.md (Exercise 1):
-- 7-day conversion by weekly (Monday-start) sign-up cohort, mature cohorts only.
-- :now is the pinned "current time" so the result is deterministic in tests.
SELECT date(signup_at, '-6 days', 'weekday 1')                                              AS cohort_week,
       COUNT(*)                                                                             AS signups,
       AVG(CASE WHEN COALESCE(first_deposit_at, first_trade_at)          -- a trade implies a deposit
                     < datetime(signup_at, '+7 days') THEN 1.0 ELSE 0 END)                  AS deposit_7d,
       AVG(CASE WHEN first_trade_at   < datetime(signup_at, '+7 days') THEN 1.0 ELSE 0 END) AS trade_7d
FROM fct_onboarding_funnel
WHERE signup_at < datetime(:now, '-7 days')            -- immature cohorts excluded
GROUP BY 1
ORDER BY 1;
