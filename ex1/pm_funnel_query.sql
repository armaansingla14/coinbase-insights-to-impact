-- PM query: 7-day onboarding conversion by weekly sign-up cohort (Snowflake).
-- SQLite twin, executed by the tests: ex1/sqlite/pm_funnel_query.sql
--
-- Grain        : one row per cohort_week = the Monday (UTC) of the week containing signup_at.
--                Computed from DAYOFWEEKISO, so the session WEEK_START parameter is irrelevant.
-- Denominator  : signups = users in fct_onboarding_funnel whose signup_at falls in that week
--                (signup_at = sign-up event, or account creation if the event is missing).
-- Numerators   : users with an OBSERVED event in [signup_at, signup_at + 7 days): lower bound
--                inclusive, upper strict (exactly +7d is day 8; an event before signup_at
--                does not count either).
--   observed_deposit_7d : first deposit in the window
--   trade_7d            : first trade in the window
--   funding_7d          : deposit in the window OR trade in the window, tested per event.
--                         A trade does not imply a deposit (card buy / crypto receive), and
--                         COALESCE(deposit, trade) would miss a trade-funded user whose
--                         deposit came after day 7.
-- Maturity     : a week is reported only once EVERY user in it has had 7 full days, i.e.
--                cohort_week + 14 days <= now (last possible signup + 7 days). SYSDATE() is UTC NTZ,
--                like signup_at; CURRENT_TIMESTAMP() would follow the session TIMEZONE.
-- Output is counts; divide by signups for rates.
-- Caveat: uses first occurrences only. A user whose FIRST trade predates signup_at (flagged
-- is_out_of_order) is not counted even if a later trade falls inside the window.
WITH users AS (
    SELECT
        DATEADD('day', 1 - DAYOFWEEKISO(signup_at), DATE(signup_at))    AS cohort_week,
        COALESCE(first_deposit_at >= signup_at
             AND first_deposit_at < DATEADD('day', 7, signup_at), FALSE) AS deposit_in_7d,
        COALESCE(first_trade_at >= signup_at
             AND first_trade_at < DATEADD('day', 7, signup_at), FALSE)   AS trade_in_7d
    FROM analytics.fct_onboarding_funnel
)
SELECT cohort_week,
       COUNT(*)                                AS signups,
       COUNT_IF(deposit_in_7d)                 AS observed_deposit_7d,
       COUNT_IF(deposit_in_7d OR trade_in_7d)  AS funding_7d,
       COUNT_IF(trade_in_7d)                   AS trade_7d
FROM users
WHERE DATEADD('day', 14, cohort_week::TIMESTAMP_NTZ) <= SYSDATE()      -- mature weeks only
GROUP BY cohort_week
ORDER BY cohort_week;
