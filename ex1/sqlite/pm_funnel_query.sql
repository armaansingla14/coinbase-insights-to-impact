-- SQLite twin of ex1/pm_funnel_query.sql (definitions, grain and caveats are documented there).
-- 7-day conversion by weekly (Monday, UTC) sign-up cohort, mature weeks only, as counts.
-- :now is the pinned "current time" (= SYSDATE() in Snowflake) so the result is deterministic in tests.
-- Monday: strftime('%w') is 0=Sunday..6=Saturday, so (%w + 6) % 7 = DAYOFWEEKISO - 1 = days since Monday.
WITH users AS (
    SELECT
        date(signup_at, '-' || ((CAST(strftime('%w', signup_at) AS INTEGER) + 6) % 7) || ' days')
                                                                                    AS cohort_week,
        COALESCE(first_deposit_after_signup_at                  -- >= signup_at by construction
                 < datetime(signup_at, '+7 days'), 0)                  AS deposit_in_7d,
        COALESCE(first_trade_after_signup_at
                 < datetime(signup_at, '+7 days'), 0)                  AS trade_in_7d
    FROM fct_onboarding_funnel
)
SELECT cohort_week,
       COUNT(*)                                AS signups,
       SUM(deposit_in_7d)                      AS observed_deposit_7d,
       SUM(deposit_in_7d OR trade_in_7d)       AS funding_7d,
       SUM(trade_in_7d)                        AS trade_7d
FROM users
WHERE datetime(cohort_week, '+14 days') <= :now                                     -- mature weeks only
GROUP BY cohort_week
ORDER BY cohort_week;
