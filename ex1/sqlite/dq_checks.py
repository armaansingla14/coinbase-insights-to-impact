"""Executable versions of the data-quality tests listed in the footer of ex1/fct_onboarding_funnel.sql.

Each check returns a Result(name, status, metric, detail) with status in {'pass', 'warn', 'error'}.
Thresholds mirror the SQL footer.
"""
from collections import namedtuple
from datetime import datetime, timedelta

Result = namedtuple("Result", "name status metric detail")
ACCEPTED_STEPS = ("signup", "email_verified", "identity_verified", "first_deposit", "first_trade")


def _one(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def funnel_grain(conn):
    dups = _one(conn, "SELECT COUNT(*) FROM (SELECT user_uuid FROM fct_onboarding_funnel "
                      "GROUP BY user_uuid HAVING COUNT(*) > 1)")
    nulls = _one(conn, "SELECT COUNT(*) FROM fct_onboarding_funnel WHERE user_uuid IS NULL")
    return Result("funnel.user_uuid unique+not_null", "error" if dups or nulls else "pass",
                  dups + nulls, f"duplicate uuids={dups}, null uuids={nulls}")


def events_grain(conn):
    dups = _one(conn, "SELECT COUNT(*) FROM (SELECT 1 FROM fct_onboarding_events "
                      "GROUP BY user_uuid, step HAVING COUNT(*) > 1)")
    nulls = _one(conn, "SELECT COUNT(*) FROM fct_onboarding_events "
                       "WHERE user_uuid IS NULL OR step IS NULL OR event_ts_utc IS NULL")
    return Result("events (user_uuid, step) unique+not_null", "error" if dups or nulls else "pass",
                  dups + nulls, f"duplicate keys={dups}, null key/ts={nulls}")


def events_accepted_steps(conn):
    ph = ",".join("?" * len(ACCEPTED_STEPS))
    bad = _one(conn, f"SELECT COUNT(*) FROM fct_onboarding_events WHERE step NOT IN ({ph})", *ACCEPTED_STEPS)
    return Result("events.step accepted_values", "error" if bad else "pass", bad, f"rows outside list={bad}")


def identity_map_unique(conn):
    fan = _one(conn, "SELECT COUNT(*) FROM (SELECT user_ref FROM int_identity_map "
                     "GROUP BY user_ref HAVING COUNT(DISTINCT user_uuid) > 1)")
    return Result("identity_map.user_ref unique (no fan-out)", "error" if fan else "pass", fan,
                  f"refs mapping to >1 user={fan}")


def identity_match_rate(conn, warn=0.005, error=0.01):
    total = _one(conn, "SELECT COUNT(DISTINCT user_ref) FROM stg_onboarding_events WHERE step IS NOT NULL")
    unmatched = _one(conn, "SELECT COUNT(DISTINCT user_ref) FROM int_onboarding_quarantine "
                           "WHERE reason = 'unmatched_ref'")
    rate = unmatched / total if total else 0.0
    status = "error" if rate >= error else "warn" if rate >= warn else "pass"
    return Result("identity match rate", status, rate, f"unmatched refs {unmatched}/{total} = {rate:.2%}")


def no_signup_anchor(conn):
    n = _one(conn, "SELECT COUNT(DISTINCT user_uuid) FROM fct_onboarding_events "
                   "WHERE user_uuid NOT IN (SELECT user_uuid FROM fct_onboarding_funnel)")
    return Result("no_signup_anchor", "warn" if n else "pass", n,
                  f"resolved users with no sign-up event and no account={n}")


def reconciliation(conn, tol=0.005):
    rows = conn.execute("""
        WITH f AS (SELECT signup_cohort_date AS d, COUNT(*) AS n FROM fct_onboarding_funnel GROUP BY 1),
             a AS (SELECT DATE(created_at) AS d, COUNT(DISTINCT LOWER(TRIM(user_uuid))) AS n
                   FROM core_accounts GROUP BY 1)
        SELECT COALESCE(f.d, a.d) AS d, COALESCE(f.n, 0) AS nf, COALESCE(a.n, 0) AS na
        FROM f FULL OUTER JOIN a ON f.d = a.d ORDER BY 1""").fetchall()
    bad = [(d, nf, na) for d, nf, na in rows if abs(nf - na) > tol * max(na, 1)]
    worst = max((abs(nf - na) / max(na, 1) for _, nf, na in rows), default=0.0)
    return Result("reconciliation funnel vs accounts per day", "error" if bad else "pass", worst,
                  f"days outside {tol:.1%}: {bad}")


def out_of_order_rate(conn, limit=0.02):
    n, k = conn.execute("SELECT COUNT(*), SUM(is_out_of_order) FROM fct_onboarding_funnel").fetchone()
    rate = (k or 0) / n if n else 0.0
    return Result("is_out_of_order rate", "warn" if rate >= limit else "pass", rate,
                  f"{k}/{n} = {rate:.2%} (limit {limit:.0%})")


def assumed_tz_jump(conn, jump_pp=0.10):
    """Alert when a cohort day's has_assumed_tz rate exceeds the trailing mean of earlier days by > jump_pp."""
    rows = conn.execute("SELECT signup_cohort_date, AVG(has_assumed_tz) FROM fct_onboarding_funnel "
                        "GROUP BY 1 ORDER BY 1").fetchall()
    alerts = []
    for i in range(1, len(rows)):
        baseline = sum(r[1] for r in rows[:i]) / i
        if rows[i][1] - baseline > jump_pp:
            alerts.append((rows[i][0], round(rows[i][1], 3), round(baseline, 3)))
    return Result("has_assumed_tz rate jump", "warn" if alerts else "pass", len(alerts), f"alerts={alerts}")


def freshness(conn, now, max_lag_hours=6):
    rows = conn.execute("SELECT source, MAX(event_ts_utc) FROM stg_onboarding_events GROUP BY 1").fetchall()
    now_dt = datetime.fromisoformat(now)
    stale = [(s, m) for s, m in rows if m is None or now_dt - datetime.fromisoformat(m) > timedelta(hours=max_lag_hours)]
    missing = {"app", "kyc", "ledger"} - {s for s, _ in rows}
    return Result("freshness per source", "warn" if stale or missing else "pass", len(stale) + len(missing),
                  f"stale={stale}, missing sources={sorted(missing)}")


def timestamp_bounds(conn, now, floor="2012-01-01 00:00:00"):
    fut = _one(conn, "SELECT COUNT(*) FROM stg_onboarding_events WHERE event_ts_utc > ?", now)
    old = _one(conn, "SELECT COUNT(*) FROM stg_onboarding_events WHERE event_ts_utc < ?", floor)
    fut_acc = _one(conn, "SELECT COUNT(*) FROM core_accounts WHERE created_at > ? OR created_at < ?", now, floor)
    bad = fut + old + fut_acc
    return Result("no future / pre-2012 timestamps", "error" if bad else "pass", bad,
                  f"future events={fut}, pre-2012 events={old}, accounts out of range={fut_acc}")


def event_conservation(conn):
    """Nothing vanishes: every staged funnel event is either usable by the fact or sits in quarantine."""
    staged = _one(conn, "SELECT COUNT(*) FROM stg_onboarding_events WHERE step IS NOT NULL")
    usable = _one(conn, "SELECT COUNT(*) FROM stg_onboarding_events e JOIN int_identity_map m "
                        "ON m.user_ref = e.user_ref WHERE e.step IS NOT NULL AND e.event_ts_utc IS NOT NULL")
    quarantined = _one(conn, "SELECT COUNT(*) FROM int_onboarding_quarantine WHERE step IS NOT NULL")
    ok = staged == usable + quarantined
    return Result("event conservation (staged = usable + quarantined)", "pass" if ok else "error",
                  staged - usable - quarantined, f"staged={staged}, usable={usable}, quarantined={quarantined}")


def run_all(conn, now):
    return [funnel_grain(conn), events_grain(conn), events_accepted_steps(conn), identity_map_unique(conn),
            identity_match_rate(conn), no_signup_anchor(conn), reconciliation(conn), out_of_order_rate(conn),
            assumed_tz_jump(conn), freshness(conn, now), timestamp_bounds(conn, now), event_conservation(conn)]
