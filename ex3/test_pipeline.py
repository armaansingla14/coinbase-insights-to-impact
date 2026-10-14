"""Exercise 3 / 4 evidence: run the legacy snippet and the v2 model on a tiny fixture.

stdlib only:  python3 -m unittest -v ex3/test_pipeline.py   (or: python3 ex3/test_pipeline.py)
All data is SYNTHETIC.

Fixture week: 2024-01-01 (Mon) .. 2024-01-07 (Sun), timestamps stored as UTC text.
Status codes (fictional, to be confirmed with the owning team):
  1 FILLED (completed), 2 FAILED, 3 PARTIALLY_FILLED (completed), 4 CANCELLED; 99 is UNMAPPED.

  user  story                                  devices (is_active)        trades in week
  u1    single device                          d1(1)                      2 completed, 1 failed, 1 cancelled
  u2    multi-device -> fan-out                d2a(1) d2b(1) d2c(1)       2 completed, 1 status 99
  u3    web-only, no device -> legacy drops    -                          1 completed
  u4    only failed / cancelled trades         d4(1)                      2 failed, 1 cancelled
  u5    device deactivated AFTER the week      d5(0, deactivated 02-01)   1 completed
  u6    NULL country                           d6(1)                      1 completed
  u7    3 users rows: CA (valid_from NULL),    d7(1)                      1 completed 01-02 (in CA),
        US from 01-03, MX from 06-01                                      1 completed 01-05 (in US)
  u8    device activated AFTER the week        d8(1, activated 02-01)     1 completed
  u9    missing from users                     d9(1)                      1 completed
  u10   only UNMAPPED statuses                 d10(1)                     2 x status 99
  (plus u1 trades at 2023-12-31 23:59:59 and 2024-01-08 00:00:00: outside the week)
"""
import re
import sqlite3
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sql(name):
    return (HERE / name).read_text()


def named(name, query_name):
    """Return the statement following '-- name: <query_name>' in a multi-query file."""
    parts = re.split(r"^-- name: (\w+)\s*$", sql(name), flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))[query_name]


FIXTURE = """
CREATE TABLE users (user_id TEXT, country TEXT, valid_from TEXT);
CREATE TABLE devices (device_id TEXT, user_id TEXT, is_active INTEGER, activated_at TEXT, deactivated_at TEXT);
CREATE TABLE trades (trade_id INTEGER PRIMARY KEY, user_id TEXT, status INTEGER, created_at TEXT);
CREATE TABLE dim_trade_status (status_id INTEGER PRIMARY KEY, status_name TEXT, is_completed INTEGER);

INSERT INTO dim_trade_status VALUES (1,'FILLED',1),(2,'FAILED',0),(3,'PARTIALLY_FILLED',1),(4,'CANCELLED',0);

INSERT INTO users VALUES
 ('u1','US','2023-01-01'), ('u2','GB','2023-01-01'), ('u3','US','2023-01-01'),
 ('u4','DE','2023-01-01'), ('u5','FR','2023-01-01'), ('u6',NULL,'2023-01-01'),
 ('u7','CA',NULL), ('u7','US','2024-01-03'), ('u7','MX','2024-06-01'),
 ('u8','JP','2023-01-01'), ('u10','BR','2023-01-01');

INSERT INTO devices VALUES
 ('d1','u1',1,'2023-06-01 00:00:00',NULL),
 ('d2a','u2',1,'2023-06-01 00:00:00',NULL), ('d2b','u2',1,'2023-06-01 00:00:00',NULL),
 ('d2c','u2',1,'2023-06-01 00:00:00',NULL),
 ('d4','u4',1,'2023-06-01 00:00:00',NULL),
 ('d5','u5',0,'2023-06-01 00:00:00','2024-02-01 00:00:00'),
 ('d6','u6',1,'2023-06-01 00:00:00',NULL),
 ('d7','u7',1,'2023-06-01 00:00:00',NULL),
 ('d8','u8',1,'2024-02-01 00:00:00',NULL),
 ('d9','u9',1,'2023-06-01 00:00:00',NULL),
 ('d10','u10',1,'2023-06-01 00:00:00',NULL);

INSERT INTO trades (user_id, status, created_at) VALUES
 ('u1',1,'2024-01-02 10:00:00'), ('u1',3,'2024-01-03 11:00:00'), ('u1',2,'2024-01-04 12:00:00'),
 ('u1',4,'2024-01-04 13:00:00'),
 ('u1',1,'2023-12-31 23:59:59'), ('u1',1,'2024-01-08 00:00:00'),
 ('u2',1,'2024-01-02 09:00:00'), ('u2',3,'2024-01-05 09:00:00'), ('u2',99,'2024-01-06 09:00:00'),
 ('u3',1,'2024-01-06 15:00:00'),
 ('u4',2,'2024-01-02 08:00:00'), ('u4',2,'2024-01-03 08:00:00'), ('u4',4,'2024-01-03 09:00:00'),
 ('u5',1,'2024-01-07 23:30:00'),
 ('u6',1,'2024-01-04 04:00:00'),
 ('u7',1,'2024-01-02 20:00:00'), ('u7',1,'2024-01-05 20:00:00'),
 ('u8',1,'2024-01-03 12:00:00'),
 ('u9',1,'2024-01-03 00:00:00'),
 ('u10',99,'2024-01-02 14:00:00'), ('u10',99,'2024-01-04 14:00:00');
"""

WEEK = "2024-01-01"

# The "baseline contract": exact legacy output on this fixture, KNOWN ERRORS INCLUDED. Any
# refactor of the legacy pipeline must reproduce it byte-for-byte; a v2 is a *new* metric.
LEGACY_BASELINE = [
    (None, 1, 1),   # u6: NULL country is kept as its own GROUP BY group, not dropped
    ("BR", 1, 0),   # u10: only unmapped statuses, still an "active trader"
    ("CA", 1, 2),   # u7 via their CA users row ...
    ("DE", 1, 0),   # u4: only failed/cancelled trades, still an "active trader"
    ("GB", 1, 6),   # u2: 2 trades x 3 active devices = 6 "completed trades"
    ("JP", 1, 1),   # u8: device activated after the week, counted anyway
    ("MX", 1, 2),   # ... and via their MX row (valid from June!), both trades each time
    ("US", 2, 4),   # u1 (2 completed) + u7 via their US row (2); u3 dropped (no device)
]

V2_EXPECTED = [
    (WEEK, "CA", 1, 2),       # u7: country at first completed trade (01-02, before the move)
    (WEEK, "FR", 1, 1),       # u5, kept although device deactivated later
    (WEEK, "GB", 1, 2),       # u2, no fan-out
    (WEEK, "JP", 1, 1),       # u8
    (WEEK, "UNKNOWN", 2, 2),  # u6 (NULL country) + u9 (missing from users)
    (WEEK, "US", 2, 3),       # u1 (2) + u3 web-only (1)
]


class Base(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(FIXTURE)
        self.db.execute(f"CREATE VIEW fct_user_week_trades AS {sql('v2_fct_user_week_trades.sql')}")

    def tearDown(self):
        self.db.close()

    def q(self, query, *args):
        return self.db.execute(query, args).fetchall()

    def legacy(self):
        return sorted(self.q(sql("legacy_weekly_active_traders.sql")), key=lambda r: (r[0] is not None, r[0]))

    def v2(self, week=WEEK):
        return [r for r in self.q(sql("v2_weekly_active_traders.sql")) if r[0] == week]


SNIPPET = (
    "SELECT u.country, COUNT(DISTINCT t.user_id) AS active_traders, SUM(CASE WHEN t.status = 1 OR t.status = 3 "
    "THEN 1 ELSE 0 END) AS completed_trades FROM users u, trades t, devices d WHERE u.user_id = t.user_id AND "
    "t.user_id = d.user_id AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' AND d.is_active = 1 "
    "GROUP BY u.country")


def legacy_body():
    """The legacy SQL with comments stripped and whitespace normalised."""
    body = " ".join(l for l in sql("legacy_weekly_active_traders.sql").splitlines() if not l.startswith("--"))
    return " ".join(body.split())


# Legacy row set before GROUP BY: one row per (users row, trade, active device).
LEGACY_TRADERS = ("SELECT DISTINCT t.user_id FROM users u, trades t, devices d WHERE u.user_id = t.user_id "
                  "AND t.user_id = d.user_id AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' "
                  "AND d.is_active = 1")


class TestLegacyCharacterization_PinsKnownErrors(Base):
    """Pins the legacy output EXACTLY, known errors included.

    Passing means the legacy output is UNCHANGED, not that it is correct. Each test names the
    error it pins, so a refactor that silently "fixes" (or worsens) one is caught and discussed.
    """

    def test_legacy_file_is_verbatim_snippet(self):
        self.assertEqual(legacy_body(), SNIPPET)

    def test_baseline_contract(self):
        self.assertEqual(self.legacy(), LEGACY_BASELINE)

    def test_device_fanout_inflates_completed_trades_not_active_traders(self):
        # Without devices join, u2 has 2 completed trades; legacy reports 6.
        gb = [r for r in self.legacy() if r[0] == "GB"][0]
        self.assertEqual(gb[2], 6)
        self.assertEqual(gb[1], 1)  # COUNT(DISTINCT user_id) is immune to the device fan-out

    def test_users_history_fanout_counts_user_in_every_country(self):
        # u7 has 3 users rows (CA, US, MX): counted as an active trader in ALL three countries,
        # and each of their 2 trades is counted once per country (6 "completed trades").
        rows = dict((r[0], r[1:]) for r in self.legacy())
        self.assertEqual((rows["CA"], rows["MX"]), ((1, 2), (1, 2)))
        self.assertEqual(rows["US"], (2, 4))  # u1 (2) + u7 again (2)
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades WHERE user_id='u7'")[0][0], 2)

    def test_completed_trades_additive_but_active_traders_not(self):
        # Global legacy = the verbatim snippet without the country grain.
        global_sql = legacy_body().replace("SELECT u.country, ", "SELECT ").replace(" GROUP BY u.country", "")
        global_active, global_completed = self.q(global_sql)[0]
        self.assertEqual((global_active, global_completed), (7, 16))
        self.assertEqual(sum(r[2] for r in self.legacy()), global_completed)  # SUM: additive
        self.assertEqual(sum(r[1] for r in self.legacy()), 9)                 # distinct: NOT (9 != 7)

    def test_totals_and_inflation(self):
        legacy_completed = sum(r[2] for r in self.legacy())
        true_completed = self.q(
            "SELECT COUNT(*) FROM trades t JOIN dim_trade_status s ON s.status_id=t.status "
            "WHERE s.is_completed=1 AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08'")[0][0]
        self.assertEqual((legacy_completed, true_completed), (16, 11))
        # Partly offsetting errors: legacy sum-of-country active_traders = 9 vs 8 under v2
        # (+1 u4 failed-only, +1 u10 unmapped-only, +2 u7 in 3 countries;
        #  -1 u3 web-only, -1 u5 deactivated, -1 u9 missing from users).
        self.assertEqual(sum(r[1] for r in self.legacy()), 9)

    def test_web_only_and_deactivated_users_dropped(self):
        legacy_users = {r[0] for r in self.q(LEGACY_TRADERS)}
        self.assertEqual(legacy_users, {"u1", "u2", "u4", "u6", "u7", "u8", "u10"})
        self.assertNotIn("u3", legacy_users)  # web-only
        self.assertNotIn("u5", legacy_users)  # device deactivated after the week

    def test_trader_missing_from_users_dropped_by_inner_join(self):
        # u9 has an active device and a completed trade, but no users row: the implicit inner
        # join removes them from every country AND from the total.
        self.assertEqual(self.q("SELECT COUNT(*) FROM devices WHERE user_id='u9' AND is_active=1")[0][0], 1)
        self.assertNotIn(("u9",), self.q(LEGACY_TRADERS))

    def test_device_filter_is_not_point_in_time(self):
        # Users who had a device active DURING the week vs legacy's "is_active today" filter.
        in_week = {r[0] for r in self.q(
            "SELECT DISTINCT t.user_id FROM trades t JOIN devices d ON d.user_id = t.user_id "
            "WHERE t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' "
            "AND d.activated_at < '2024-01-08' "
            "AND (d.deactivated_at IS NULL OR d.deactivated_at >= '2024-01-01')")}
        legacy_users = {r[0] for r in self.q(LEGACY_TRADERS)} | {"u9"}  # u9: dropped by users join
        self.assertEqual(legacy_users - in_week, {"u8"})  # device activated 02-01: wrongly counted
        self.assertEqual(in_week - legacy_users, {"u5"})  # device deactivated 02-01: wrongly dropped

    def test_rerun_after_device_deactivation_changes_history(self):
        before = self.legacy()
        self.db.execute("UPDATE devices SET is_active=0, deactivated_at='2024-03-01' WHERE device_id='d1'")
        after = self.legacy()
        self.assertNotEqual(before, after)  # same past week, different answer -> not point-in-time safe
        self.assertEqual(dict((r[0], r[1:]) for r in after)["US"], (1, 2))  # u1 vanished, u7 remains


class TestV2(Base):
    def test_v2_exact_output(self):
        self.assertEqual(self.v2(), V2_EXPECTED)

    def test_v2_totals(self):
        self.assertEqual(sum(r[2] for r in self.v2()), 8)   # u1,u2,u3,u5,u6,u7,u8,u9
        self.assertEqual(sum(r[3] for r in self.v2()), 11)  # matches raw completed trades

    def test_utc_week_boundaries(self):
        weeks = dict(((r[0], r[1]), r[2]) for r in self.q(
            "SELECT user_id, week_start, n_trades_any_status FROM fct_user_week_trades WHERE user_id='u1'"))
        self.assertEqual(weeks, {("u1", "2023-12-25"): 1, ("u1", "2024-01-01"): 4, ("u1", "2024-01-08"): 1})
        # Sunday 23:30 UTC belongs to the week starting Monday 2024-01-01
        self.assertEqual(self.q("SELECT week_start FROM fct_user_week_trades WHERE user_id='u5'"),
                         [("2024-01-01",)])

    def test_global_distinct_equals_country_sum_every_week(self):
        # Additive by construction: one country per active user-week.
        global_by_week = dict(self.q("SELECT week_start, COUNT(DISTINCT user_id) FROM fct_user_week_trades "
                                     "WHERE is_active_trader=1 GROUP BY week_start"))
        sum_by_week = {}
        for week, _, active, _ in self.q(sql("v2_weekly_active_traders.sql")):
            sum_by_week[week] = sum_by_week.get(week, 0) + active
        self.assertEqual(sum_by_week, global_by_week)
        self.assertEqual(global_by_week, {"2023-12-25": 1, WEEK: 8, "2024-01-08": 1})

    def test_v2_independent_of_device_state(self):
        before = self.v2()
        self.db.execute("UPDATE devices SET is_active=0")
        self.db.execute("DELETE FROM devices WHERE user_id='u2'")
        self.assertEqual(self.v2(), before)

    def test_device_activated_after_week_irrelevant(self):
        # u8 counts because of a completed trade, whatever their device history says.
        self.db.execute("DELETE FROM devices WHERE user_id='u8'")
        self.assertIn((WEEK, "JP", 1, 1), self.v2())

    def test_failed_or_cancelled_only_user_is_not_active_trader(self):
        self.assertEqual(self.q("SELECT is_active_trader, n_trades_any_status, country FROM fct_user_week_trades "
                                "WHERE user_id='u4'"), [(0, 3, None)])


class TestV2CountryAttribution(Base):
    """Country AS OF the user's first completed trade of the week (ASSUMPTION, owner to confirm)."""

    def country(self, user_id, week=WEEK):
        return self.q("SELECT country, n_completed_trades FROM fct_user_week_trades "
                      "WHERE user_id=? AND week_start=?", user_id, week)

    def test_mid_week_move_counts_once_trades_not_multiplied(self):
        # u7 trades 01-02 in CA and 01-05 after moving to US (01-03): one row, both trades, CA.
        self.assertEqual(self.country("u7"), [("CA", 2)])
        self.assertEqual(dict((r[1], r[2:]) for r in self.v2())["US"], (2, 3))  # u1 + u3 only

    def test_country_follows_first_completed_trade(self):
        # If the 01-02 trade had failed, the first completed trade is 01-05, after the move.
        self.db.execute("UPDATE trades SET status=2 WHERE user_id='u7' AND created_at='2024-01-02 20:00:00'")
        self.assertEqual(self.country("u7"), [("US", 1)])

    def test_null_valid_from_is_the_earliest_row(self):
        # u7's CA row has valid_from NULL: treated as valid before any dated row, not dropped.
        self.assertEqual(self.q("SELECT country FROM users WHERE user_id='u7' AND valid_from IS NULL"), [("CA",)])
        self.assertEqual(self.country("u7"), [("CA", 2)])

    def test_attribution_independent_of_users_row_order(self):
        before = self.q("SELECT * FROM fct_user_week_trades ORDER BY user_id, week_start")
        self.db.executescript("CREATE TABLE users_rev AS SELECT * FROM users ORDER BY rowid DESC; "
                              "DELETE FROM users; INSERT INTO users SELECT * FROM users_rev;")
        self.assertEqual(self.q("SELECT * FROM fct_user_week_trades ORDER BY user_id, week_start"), before)

    def test_trade_before_first_dated_row_is_unknown(self):
        # No users row valid at the trade time (and no NULL valid_from row) -> 'UNKNOWN', not dropped.
        self.db.execute("UPDATE users SET valid_from='2024-01-07' WHERE user_id='u3'")
        self.assertEqual(self.country("u3"), [("UNKNOWN", 1)])

    def test_missing_user_and_null_country_go_to_unknown(self):
        self.assertEqual(self.country("u9"), [("UNKNOWN", 1)])  # not in users
        self.assertEqual(self.country("u6"), [("UNKNOWN", 1)])  # NULL country


class TestDataQuality(Base):
    def dq(self, name):
        return self.q(named("v2_dq_checks.sql", name))

    def test_grain_one_row_per_user_week(self):
        # Also proves the users-history join picks exactly one country per user-week.
        self.assertEqual(self.dq("grain_duplicates"), [])

    def test_raw_users_is_not_unique(self):
        # Documents WHY v2 must not join raw users without picking one row: history rows exist.
        self.assertEqual(self.q("SELECT user_id FROM users GROUP BY user_id HAVING COUNT(*) > 1"), [("u7",)])

    def test_unmapped_status_trades_are_counted(self):
        self.assertEqual(self.dq("unmapped_status_trades"), [(3,)])  # u2 x1, u10 x2

    def test_user_with_only_unmapped_trades_is_surfaced(self):
        # u10 traded, but every status is unknown: not an active trader, yet visible in DQ.
        self.assertEqual(self.dq("users_with_only_unmapped_trades"), [("u10", WEEK, 2)])
        self.assertEqual(self.q("SELECT is_active_trader FROM fct_user_week_trades WHERE user_id='u10'"), [(0,)])

    def test_new_unmapped_status_is_detected_not_dropped(self):
        self.db.execute("INSERT INTO trades (user_id, status, created_at) VALUES ('u1',99,'2024-01-03 00:00:00')")
        self.assertEqual(self.dq("unmapped_status_trades"), [(4,)])


class TestLegacyCountryCut_PinsKnownErrors(Base):
    """The country cut keeps legacy logic, so it also pins legacy errors.

    Passing means the cut reproduces legacy UNCHANGED, not that it is correct; the reconcile query
    states explicitly where the country rows do not add up to the global number.
    """

    def test_country_cut_is_legacy_logic_relabelled(self):
        cut = self.q(named("country_cut_legacy_logic.sql", "country_cut"))
        relabelled = sorted(("UNKNOWN" if c is None else c, a, t) for c, a, t in LEGACY_BASELINE)
        self.assertEqual(sorted(cut), relabelled)

    def test_reconciliation_with_country_history(self):
        r = self.q(named("country_cut_legacy_logic.sql", "reconcile"))[0]
        # (sum_country_active, global_active, overcount, users_in_multiple_countries,
        #  sum_country_completed, global_completed)
        self.assertEqual(r, (9, 7, 2, 1, 16, 16))  # completed reconciles; u7 in 3 countries => +2

    def test_reconciliation_when_users_unique(self):
        self.db.execute("DELETE FROM users WHERE user_id='u7' AND country IN ('US', 'MX')")
        r = self.q(named("country_cut_legacy_logic.sql", "reconcile"))[0]
        self.assertEqual(r, (7, 7, 0, 0, 12, 12))  # NULL country does not break it; only multi-row users do


class TestEx4DeviceVsUserRate(unittest.TestCase):
    """100 weekly active users; 34 traded; 7 of those traded on 2 devices -> 41% vs 34%."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("CREATE TABLE wau (user_id TEXT); CREATE TABLE trades_wk (user_id TEXT, device_id TEXT);")
        self.db.executemany("INSERT INTO wau VALUES (?)", [(f"w{i}",) for i in range(100)])
        rows = []
        for i in range(34):
            rows += [(f"w{i}", f"w{i}-phone"), (f"w{i}", f"w{i}-phone")]  # repeat trades, same device
            if i < 7:
                rows.append((f"w{i}", f"w{i}-web"))                       # same user, second device
        self.db.executemany("INSERT INTO trades_wk VALUES (?, ?)", rows)

    def rate(self, name):
        return self.db.execute(named("ex4_device_vs_user_rate.sql", name)).fetchone()[0]

    def test_device_level_inflates(self):
        self.assertEqual(self.rate("legacy_device_level"), 41.0)
        self.assertEqual(self.rate("user_level"), 34.0)

    def test_gap_equals_extra_devices(self):
        extra = self.db.execute("SELECT COUNT(*) - COUNT(DISTINCT user_id) FROM "
                                "(SELECT DISTINCT user_id, device_id FROM trades_wk)").fetchone()[0]
        self.assertEqual(extra, 7)  # gap of 7 points = 7 users x 1 extra device, on 100 WAU


class TestEx4OtherMechanisms(unittest.TestCase):
    """Two OTHER ways the same 34% can read ~41% (ex4_other_mechanisms.sql).

    These demonstrate POSSIBLE mechanisms on synthetic data, not the real cause; the real legacy
    SQL has to be read to know which (if any) applies.
    100 real WAU: w0..w33 completed a trade (34); w34..w40 only FAILED attempts (7);
    w83..w99 not KYC-verified, no trades (17). Plus 12 test accounts t0..t11 that all completed.
    """

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("CREATE TABLE wau_ex4 (user_id TEXT, is_kyc_verified INTEGER, is_test_account INTEGER);"
                              "CREATE TABLE trades_ex4 (user_id TEXT, status TEXT);")
        self.db.executemany("INSERT INTO wau_ex4 VALUES (?, ?, 0)", [(f"w{i}", int(i < 83)) for i in range(100)])
        self.db.executemany("INSERT INTO wau_ex4 VALUES (?, 1, 1)", [(f"t{i}",) for i in range(12)])
        # w0..w9 trade twice: repeat trades must not change any rate (all numerators are distinct users)
        trades = [(f"w{i}", "COMPLETED") for i in range(34)] + [(f"w{i}", "COMPLETED") for i in range(10)]
        trades += [(f"w{i}", "FAILED") for i in range(34, 41)] + [(f"t{i}", "COMPLETED") for i in range(12)]
        self.db.executemany("INSERT INTO trades_ex4 VALUES (?, ?)", trades)

    def rate(self, name):
        return self.db.execute(named("ex4_other_mechanisms.sql", name)).fetchone()[0]

    def test_baseline_is_34(self):
        self.assertEqual(self.rate("baseline"), 34.0)  # 34 / 100

    def test_attempted_instead_of_completed_numerator(self):
        self.assertEqual(self.rate("attempted_numerator"), 41.0)  # (34 + 7 failed-only) / 100

    def test_denominator_restricted_to_eligible_users(self):
        self.assertEqual(self.rate("eligible_only_denominator"), 41.0)  # 34 / 83 = 40.96

    def test_denominator_includes_test_accounts(self):
        self.assertEqual(self.rate("includes_test_accounts"), 41.1)  # (34 + 12) / 112 = 41.07


if __name__ == "__main__":
    unittest.main(verbosity=2)
