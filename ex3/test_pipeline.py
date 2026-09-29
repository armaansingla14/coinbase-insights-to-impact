"""Exercise 3 / 4 evidence: run the legacy snippet and the v2 model on a tiny fixture.

stdlib only:  python3 -m unittest -v ex3/test_pipeline.py   (or: python3 ex3/test_pipeline.py)

Fixture week: 2024-01-01 (Mon) .. 2024-01-07 (Sun), timestamps stored as UTC text.
Status codes (fictional, to be confirmed with the owning team):
  1 FILLED (completed), 2 FAILED, 3 PARTIALLY_FILLED (completed), 4 CANCELLED.

  user  story                                    devices (is_active)       trades in week
  u1    single device                            d1(1)                     2 completed, 1 failed
  u2    multi-device -> fan-out                  d2a(1) d2b(1) d2c(1)      2 completed
  u3    web-only, no device -> legacy drops      -                         1 completed
  u4    only failed trades                       d4(1)                     2 failed
  u5    device deactivated AFTER the week        d5(0, deactivated 02-01)  1 completed
  u6    NULL country                             d6(1)                     1 completed
  u7    2 rows in users (CA -> US history)       d7(1)                     1 completed
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
CREATE TABLE devices (device_id TEXT, user_id TEXT, is_active INTEGER, deactivated_at TEXT);
CREATE TABLE trades (trade_id INTEGER PRIMARY KEY, user_id TEXT, status INTEGER, created_at TEXT);
CREATE TABLE dim_trade_status (status_id INTEGER PRIMARY KEY, status_name TEXT, is_completed INTEGER);

INSERT INTO dim_trade_status VALUES (1,'FILLED',1),(2,'FAILED',0),(3,'PARTIALLY_FILLED',1),(4,'CANCELLED',0);

INSERT INTO users VALUES
 ('u1','US','2023-01-01'), ('u2','GB','2023-01-01'), ('u3','US','2023-01-01'),
 ('u4','DE','2023-01-01'), ('u5','FR','2023-01-01'), ('u6',NULL,'2023-01-01'),
 ('u7','CA','2023-01-01'), ('u7','US','2024-01-03');

INSERT INTO devices VALUES
 ('d1','u1',1,NULL),
 ('d2a','u2',1,NULL), ('d2b','u2',1,NULL), ('d2c','u2',1,NULL),
 ('d4','u4',1,NULL),
 ('d5','u5',0,'2024-02-01 00:00:00'),
 ('d6','u6',1,NULL),
 ('d7','u7',1,NULL);

INSERT INTO trades (user_id, status, created_at) VALUES
 ('u1',1,'2024-01-02 10:00:00'), ('u1',3,'2024-01-03 11:00:00'), ('u1',2,'2024-01-04 12:00:00'),
 ('u1',1,'2023-12-31 23:59:59'), ('u1',1,'2024-01-08 00:00:00'),
 ('u2',1,'2024-01-02 09:00:00'), ('u2',3,'2024-01-05 09:00:00'),
 ('u3',1,'2024-01-06 15:00:00'),
 ('u4',2,'2024-01-02 08:00:00'), ('u4',2,'2024-01-03 08:00:00'),
 ('u5',1,'2024-01-07 23:30:00'),
 ('u6',1,'2024-01-04 04:00:00'),
 ('u7',1,'2024-01-05 20:00:00');
"""

WEEK = "2024-01-01"

# The "baseline contract": exact legacy output on this fixture. Any refactor of the legacy
# pipeline must reproduce it byte-for-byte; a v2 is a *new* metric, not a refactor.
LEGACY_BASELINE = [
    (None, 1, 1),   # u6: NULL country is kept as its own GROUP BY group, not dropped
    ("CA", 1, 1),   # u7 via their old users row
    ("DE", 1, 0),   # u4: only failed trades, still an "active trader"
    ("GB", 1, 6),   # u2: 2 trades x 3 active devices = 6 "completed trades"
    ("US", 2, 3),   # u1 (2 completed) + u7 via their new users row (1); u3 dropped (no device)
]

V2_EXPECTED = [
    (WEEK, "CA", 1, 1),       # u7, signup country
    (WEEK, "FR", 1, 1),       # u5, kept although device deactivated later
    (WEEK, "GB", 1, 2),       # u2, no fan-out
    (WEEK, "UNKNOWN", 1, 1),  # u6
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


class TestLegacyCharacterization(Base):
    def test_legacy_file_is_verbatim_snippet(self):
        body = " ".join(l for l in sql("legacy_weekly_active_traders.sql").splitlines() if not l.startswith("--"))
        self.assertEqual(" ".join(body.split()), SNIPPET)

    def test_baseline_contract(self):
        self.assertEqual(self.legacy(), LEGACY_BASELINE)

    def test_fanout_inflates_completed_trades_not_active_traders(self):
        # Without devices join, u2 has 2 completed trades; legacy reports 6.
        gb = [r for r in self.legacy() if r[0] == "GB"][0]
        self.assertEqual(gb[2], 6)
        self.assertEqual(gb[1], 1)  # COUNT(DISTINCT user_id) is immune to the device fan-out

    def test_users_fanout_double_counts_across_countries(self):
        # u7 has two users rows: counted as an active trader in BOTH CA and US,
        # and their single trade is counted twice in completed_trades.
        rows = dict((r[0], r) for r in self.legacy())
        self.assertEqual(rows["CA"][1:], (1, 1))
        self.assertEqual(self.q("SELECT COUNT(*) FROM trades WHERE user_id='u7'")[0][0], 1)

    def test_totals_and_inflation(self):
        legacy_completed = sum(r[2] for r in self.legacy())
        true_completed = self.q(
            "SELECT COUNT(*) FROM trades t JOIN dim_trade_status s ON s.status_id=t.status "
            "WHERE s.is_completed=1 AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08'")[0][0]
        self.assertEqual((legacy_completed, true_completed), (11, 8))
        # Offsetting errors: legacy sum-of-country active_traders = 6 = true value, by accident
        # (u4 failed-only and u7 double-country added; u3 web-only and u5 deactivated dropped).
        self.assertEqual(sum(r[1] for r in self.legacy()), 6)

    def test_web_only_and_deactivated_users_dropped(self):
        legacy_users = {r[0] for r in self.q(
            "SELECT DISTINCT t.user_id FROM users u, trades t, devices d WHERE u.user_id = t.user_id "
            "AND t.user_id = d.user_id AND t.created_at >= '2024-01-01' AND t.created_at < '2024-01-08' "
            "AND d.is_active = 1")}
        self.assertEqual(legacy_users, {"u1", "u2", "u4", "u6", "u7"})
        self.assertNotIn("u3", legacy_users)  # web-only
        self.assertNotIn("u5", legacy_users)  # device deactivated after the week

    def test_rerun_after_device_deactivation_changes_history(self):
        before = self.legacy()
        self.db.execute("UPDATE devices SET is_active=0, deactivated_at='2024-03-01' WHERE device_id='d1'")
        after = self.legacy()
        self.assertNotEqual(before, after)  # same past week, different answer -> not point-in-time safe
        self.assertEqual(dict((r[0], r[1:]) for r in after)["US"], (1, 1))  # u1 vanished


class TestV2(Base):
    def test_v2_exact_output(self):
        self.assertEqual(self.v2(), V2_EXPECTED)

    def test_v2_totals(self):
        self.assertEqual(sum(r[2] for r in self.v2()), 6)   # u1,u2,u3,u5,u6,u7
        self.assertEqual(sum(r[3] for r in self.v2()), 8)   # matches raw completed trades

    def test_grain_one_row_per_user_week(self):
        dup = self.q("SELECT user_id, week_start, COUNT(*) FROM fct_user_week_trades "
                     "GROUP BY 1, 2 HAVING COUNT(*) > 1")
        self.assertEqual(dup, [])

    def test_utc_week_boundaries(self):
        weeks = dict(((r[0], r[1]), r[2]) for r in self.q(
            "SELECT user_id, week_start, n_trades_any_status FROM fct_user_week_trades WHERE user_id='u1'"))
        self.assertEqual(weeks, {("u1", "2023-12-25"): 1, ("u1", "2024-01-01"): 3, ("u1", "2024-01-08"): 1})
        # Sunday 23:30 UTC belongs to the week starting Monday 2024-01-01
        self.assertEqual(self.q("SELECT week_start FROM fct_user_week_trades WHERE user_id='u5'"),
                         [("2024-01-01",)])

    def test_country_sums_to_global(self):
        global_active = self.q("SELECT COUNT(*) FROM fct_user_week_trades "
                               "WHERE week_start=? AND is_active_trader=1", WEEK)[0][0]
        self.assertEqual(sum(r[2] for r in self.v2()), global_active)

    def test_v2_independent_of_device_state(self):
        before = self.v2()
        self.db.execute("UPDATE devices SET is_active=0")
        self.db.execute("DELETE FROM devices WHERE user_id='u2'")
        self.assertEqual(self.v2(), before)

    def test_failed_only_user_is_not_active_trader(self):
        self.assertEqual(self.q("SELECT is_active_trader, n_trades_any_status FROM fct_user_week_trades "
                                "WHERE user_id='u4'"), [(0, 2)])

    def test_trader_missing_from_users_goes_to_unknown(self):
        self.db.execute("INSERT INTO trades (user_id, status, created_at) VALUES ('u9',1,'2024-01-03 00:00:00')")
        self.assertIn((WEEK, "UNKNOWN", 2, 2), self.v2())


class TestDataQuality(Base):
    def test_dim_user_one_row_per_user(self):
        dim_user = re.search(r"WITH dim_user AS \((.*?)\n\)", sql("v2_weekly_active_traders.sql"), re.S).group(1)
        dup = self.q(f"SELECT user_id FROM ({dim_user}) GROUP BY user_id HAVING COUNT(*) > 1")
        self.assertEqual(dup, [])

    def test_raw_users_is_not_unique(self):
        # Documents WHY v2 must not join raw users: this fixture (like reality) has history rows.
        self.assertEqual(self.q("SELECT user_id FROM users GROUP BY user_id HAVING COUNT(*) > 1"), [("u7",)])

    def test_every_status_is_mapped(self):
        self.assertEqual(self.q("SELECT SUM(n_unmapped_status) FROM fct_user_week_trades")[0][0], 0)

    def test_unmapped_status_is_detected_not_dropped(self):
        self.db.execute("INSERT INTO trades (user_id, status, created_at) VALUES ('u1',99,'2024-01-03 00:00:00')")
        self.assertEqual(self.q("SELECT SUM(n_unmapped_status) FROM fct_user_week_trades")[0][0], 1)


class TestAdditiveCountryCut(Base):
    def test_country_cut_is_legacy_logic_relabelled(self):
        cut = self.q(named("country_cut_legacy_logic.sql", "country_cut"))
        relabelled = sorted(("UNKNOWN" if c is None else c, a, t) for c, a, t in LEGACY_BASELINE)
        self.assertEqual(sorted(cut), relabelled)

    def test_reconciliation_with_country_history(self):
        r = self.q(named("country_cut_legacy_logic.sql", "reconcile"))[0]
        # (sum_country_active, global_active, sum_country_completed, global_completed, users_with_multi_rows)
        self.assertEqual(r, (6, 5, 11, 11, 1))  # completed reconciles; active_traders over-sums by 1 (u7)

    def test_reconciliation_when_users_unique(self):
        self.db.execute("DELETE FROM users WHERE user_id='u7' AND country='US'")
        r = self.q(named("country_cut_legacy_logic.sql", "reconcile"))[0]
        self.assertEqual(r, (5, 5, 10, 10, 0))  # NULL country does not break it; only multi-row users do


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
