"""Proves the Exercise 1 onboarding-funnel model logic on messy fixture data (stdlib only).

Run:  python3 -m unittest discover -s ex1/tests -v      (from the project root)
"""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sqlite"))
sys.path.insert(0, str(HERE))

import build            # noqa: E402
import dq_checks as dq  # noqa: E402
import fixtures as fx   # noqa: E402

SNOWFLAKE_SQL = HERE.parent / "fct_onboarding_funnel.sql"


def fresh(with_clean=True):
    conn = build.connect()
    build.create_raw_tables(conn)
    expected = fx.load_fixtures(conn, with_clean=with_clean)
    build.build_model(conn)
    return conn, expected


def funnel_row(conn, user):
    r = conn.execute("SELECT * FROM fct_onboarding_funnel WHERE user_uuid = ?", (user,)).fetchone()
    return None if r is None else {k: r[k] for k in fx.COLS}


class TestUdfSemantics(unittest.TestCase):
    """The SQLite UDFs must behave like the Snowflake functions they stand in for."""

    def test_timestamp_parsing(self):
        self.assertEqual(build.convert_timezone("UTC", build.try_to_timestamp_tz("2024-03-01T15:35:00+05:30")),
                         "2024-03-01 10:05:00")
        self.assertEqual(build.convert_timezone("UTC", build.try_to_timestamp_tz("2024-03-11T05:30:00+0530")),
                         "2024-03-11 00:00:00")
        self.assertEqual(build.convert_timezone("UTC", build.try_to_timestamp_tz("2024-03-01T10:00:00Z")),
                         "2024-03-01 10:00:00")
        self.assertIsNone(build.try_to_timestamp_tz("not-a-date"))
        self.assertIsNone(build.try_to_timestamp_ntz(None))

    def test_convert_timezone_arg_order_and_dst(self):
        # CONVERT_TIMEZONE(source_tz, target_tz, ntz): 07:30 New York wall clock in winter = 12:30 UTC ...
        self.assertEqual(build.convert_timezone("America/New_York", "UTC", "2024-03-01 07:30:00"), "2024-03-01 12:30:00")
        # ... and after the 2024-03-10 DST switch the offset is -4
        self.assertEqual(build.convert_timezone("America/New_York", "UTC", "2024-03-11 09:00:00"), "2024-03-11 13:00:00")
        # swapped arguments give a different (wrong) answer -> order matters and is tested
        self.assertNotEqual(build.convert_timezone("UTC", "America/New_York", "2024-03-01 07:30:00"), "2024-03-01 12:30:00")

    def test_epoch_seconds_are_utc(self):
        self.assertEqual(build.to_timestamp_ntz(1709290800), "2024-03-01 11:00:00")
        with self.assertRaises(ValueError):
            build.to_timestamp_ntz("garbage")

    def test_regexp_like_is_full_match(self):
        pat = ".*(Z|[+-][0-9]{2}:?[0-9]{2})"
        for s, has_offset in [("2024-03-01T10:00:00Z", 1), ("2024-03-01T10:00:00+05:30", 1),
                              ("2024-03-01T10:00:00+0530", 1), ("2024-03-01 10:00:00", 0),
                              ("2024-03-01", 0), ("2024-03-01T10:00:00-08:00", 1)]:
            self.assertEqual(build.regexp_like(s, pat), has_offset, s)

    def test_datediff_minute_counts_boundaries(self):
        self.assertEqual(build.datediff_minute("2024-01-01 10:00:59", "2024-01-01 10:01:00"), 1)
        self.assertEqual(build.datediff_minute("2024-01-01 10:00:00", "2024-01-01 10:00:59"), 0)


class TestFunnelOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn, cls.expected = fresh()

    def test_every_handcrafted_user(self):
        for name, exp in fx.EXPECTED.items():
            with self.subTest(user=name):
                self.assertEqual(funnel_row(self.conn, fx.U[name]), exp)

    def test_every_generated_user_matches_independent_python(self):
        clean = {u: e for u, e in self.expected.items() if u.startswith("c")}
        self.assertEqual(len(clean), fx.CLEAN_N)
        mismatches = [u for u, e in clean.items() if funnel_row(self.conn, u) != e]
        self.assertEqual(mismatches, [], f"first mismatch: {mismatches[:1]}")

    def test_row_count_equals_expected_population(self):
        n = self.conn.execute("SELECT COUNT(*) FROM fct_onboarding_funnel").fetchone()[0]
        self.assertEqual(n, len(self.expected))

    def test_unmatched_and_anchorless_users_not_in_funnel(self):
        self.assertIsNone(funnel_row(self.conn, fx.U["U13"]))
        refs = [r[0] for r in self.conn.execute("SELECT user_uuid FROM fct_onboarding_funnel")]
        self.assertFalse(any(r.startswith(("anon", "ledger_account")) for r in refs))

    def test_quarantine_contents(self):
        got = {tuple(r) for r in self.conn.execute(
            "SELECT reason, source, user_ref, source_event_id FROM int_onboarding_quarantine")}
        self.assertEqual(got, fx.EXPECTED_QUARANTINE)

    def test_retries_visible_and_tiebreak_deterministic(self):
        rows = {r["step"]: r for r in self.conn.execute(
            "SELECT * FROM fct_onboarding_events WHERE user_uuid = ?", (fx.U["U02"],))}
        self.assertEqual({k: v["n_source_events"] for k, v in rows.items()}, fx.EXPECTED_N_SOURCE_EVENTS_U02)
        self.assertEqual(rows["first_deposit"]["source_event_id"], "t-0201")   # same ts -> lowest id wins
        self.assertEqual(rows["identity_verified"]["source_event_id"], "k-0202")  # earliest approval, not first row

    def test_failed_deposit_and_rejected_kyc_excluded(self):
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM stg_onboarding_events WHERE source_event_id IN ('t-0701','t-7b01','k-0200','k-0300')"
        ).fetchone()[0], 0)

    def test_uppercase_uuid_in_accounts_still_joins_ledger(self):
        # Regression for original bug: stg_ledger emitted the raw (upper-case) account UUID while
        # int_identity_map is lower-case, so U01's deposit and trade silently dropped out of the fact.
        n = self.conn.execute("SELECT COUNT(*) FROM fct_onboarding_events WHERE user_uuid = ? AND source='ledger'",
                              (fx.U["U01"],)).fetchone()[0]
        self.assertEqual(n, 2)

    def test_original_adjacent_out_of_order_logic_missed_gap_case(self):
        # Regression for original bug: adjacent-pair comparison is NULL-blind. U14 has KYC before sign-up with
        # no email event in between; the original expression evaluates to FALSE, the fixed model flags it.
        old = self.conn.execute("""
            SELECT COALESCE(email_verified_at < signup_at OR identity_verified_at < email_verified_at
                            OR first_deposit_at < identity_verified_at OR first_trade_at < first_deposit_at, 0)
            FROM fct_onboarding_funnel WHERE user_uuid = ?""", (fx.U["U14"],)).fetchone()[0]
        self.assertEqual(old, 0)
        self.assertEqual(funnel_row(self.conn, fx.U["U14"])["is_out_of_order"], 1)

    def test_has_missing_step_equals_bruteforce_definition(self):
        cols = ["email_verified_at", "identity_verified_at", "first_deposit_at", "first_trade_at"]
        for r in self.conn.execute("SELECT * FROM fct_onboarding_funnel"):
            vals = [r[c] for c in cols]
            brute = any(vals[i] is None and any(v is not None for v in vals[i + 1:]) for i in range(len(vals)))
            self.assertEqual(bool(r["has_missing_step"]), brute, r["user_uuid"])

    def test_is_out_of_order_equals_bruteforce_definition(self):
        cols = ["email_verified_at", "identity_verified_at", "first_deposit_at", "first_trade_at"]
        for r in self.conn.execute("SELECT * FROM fct_onboarding_funnel"):
            sig = None if r["signup_event_missing"] else r["signup_at"]   # event-based comparison only
            vals = [sig] + [r[c] for c in cols]
            brute = any(vals[i] is not None and vals[j] is not None and vals[j] < vals[i]
                        for i in range(5) for j in range(i + 1, 5))
            self.assertEqual(bool(r["is_out_of_order"]), brute, r["user_uuid"])

    def test_reached_flags_monotonic(self):
        bad = self.conn.execute("""SELECT COUNT(*) FROM fct_onboarding_funnel WHERE
            reached_first_trade > reached_first_deposit OR reached_first_deposit > reached_identity_verified
            OR reached_identity_verified > reached_email_verified""").fetchone()[0]
        self.assertEqual(bad, 0)


class TestLateArrivingData(unittest.TestCase):
    def test_late_batch_changes_first_occurrence_and_fills_gap(self):
        conn, _ = fresh(with_clean=False)
        self.assertEqual(funnel_row(conn, fx.U["U08"]), fx.EXPECTED["U08"])
        fx.load_late_batch(conn)
        build.build_model(conn)
        self.assertEqual(funnel_row(conn, fx.U["U08"]), fx.EXPECTED_U08_AFTER_LATE)
        # every other user unchanged
        for name, exp in fx.EXPECTED.items():
            if name != "U08":
                self.assertEqual(funnel_row(conn, fx.U[name]), exp, name)

    def test_rebuild_is_idempotent(self):
        conn, _ = fresh()
        q = "SELECT * FROM fct_onboarding_funnel ORDER BY user_uuid"
        first = [tuple(r)[:-1] for r in conn.execute(q)]   # drop _loaded_at
        build.build_model(conn)
        self.assertEqual(first, [tuple(r)[:-1] for r in conn.execute(q)])


class TestDataQualityChecks(unittest.TestCase):
    """Baseline statuses on the fixture, then each check is proven to FIRE on an injected fault."""

    def status(self, conn):
        return {r.name: r.status for r in dq.run_all(conn, fx.NOW)}

    def test_baseline(self):
        conn, _ = fresh()
        results = {r.name: r for r in dq.run_all(conn, fx.NOW)}
        for name, r in results.items():
            expected = "warn" if name == "no_signup_anchor" else "pass"   # U13 is a planted anchorless user
            self.assertEqual(r.status, expected, f"{name}: {r.detail}")
        self.assertEqual(results["no_signup_anchor"].metric, 1)
        self.assertGreater(results["identity match rate"].metric, 0)    # the 2 planted unmatched refs are counted
        self.assertGreater(results["is_out_of_order rate"].metric, 0)

    def inject(self, **rows):
        conn, _ = fresh()
        fx.insert(conn, **rows)
        build.build_model(conn)
        return conn

    def test_fanout_anon_link_detected(self):
        conn = self.inject(links=[("anon-u01", fx.U["U02"])])     # same anon id linked to a second user
        s = self.status(conn)
        self.assertEqual(s["identity_map.user_ref unique (no fan-out)"], "error")
        self.assertEqual(s["event conservation (staged = usable + quarantined)"], "error")

    def test_duplicate_funnel_row_detected(self):
        conn, _ = fresh()
        conn.execute("INSERT INTO fct_onboarding_funnel SELECT * FROM fct_onboarding_funnel LIMIT 1")
        conn.execute("INSERT INTO fct_onboarding_funnel (user_uuid) VALUES (NULL)")
        self.assertEqual(dq.funnel_grain(conn).status, "error")

    def test_events_grain_and_accepted_values_detected(self):
        conn, _ = fresh()
        conn.execute("CREATE TABLE ev AS SELECT * FROM fct_onboarding_events")
        conn.execute("INSERT INTO ev SELECT * FROM ev LIMIT 1")
        conn.execute("INSERT INTO ev (user_uuid, step, event_ts_utc) VALUES ('x', 'kyc_pending', '2024-03-01')")
        conn.execute("DROP VIEW fct_onboarding_events")
        conn.execute("ALTER TABLE ev RENAME TO fct_onboarding_events")
        self.assertEqual(dq.events_grain(conn).status, "error")
        self.assertEqual(dq.events_accepted_steps(conn).status, "error")

    def test_match_rate_thresholds(self):
        # baseline: 2 unmatched of 578 distinct refs = 0.35% (pass); +1 -> 0.52% (warn); +5 -> 1.2% (error)
        few = [(f"x-{i}", f"anon-lost-{i}", "sign_up", "2024-03-06T09:00:00Z", fx.LOAD_1) for i in range(1)]
        many = [(f"y-{i}", f"anon-gone-{i}", "sign_up", "2024-03-06T09:00:00Z", fx.LOAD_1) for i in range(5)]
        self.assertEqual(dq.identity_match_rate(self.inject(app=few)).status, "warn")
        self.assertEqual(dq.identity_match_rate(self.inject(app=many)).status, "error")

    def test_reconciliation_detects_cohort_mismatch(self):
        # accounts stamped just before midnight, sign-up events just after -> different UTC days
        accts = [(50_000 + i, f"r{i:07d}", "2024-03-05 23:59:00") for i in range(5)]
        app = [(f"r-{i}", f"r{i:07d}", "sign_up", "2024-03-06T00:01:00Z", fx.LOAD_1) for i in range(5)]
        self.assertEqual(dq.reconciliation(self.inject(app=app, accounts=accts)).status, "error")

    def test_out_of_order_rate_detected(self):
        app = [(f"o-{i}", fx.U["U10"] if i == 0 else f"c{i:07d}-0000-4000-8000-{i:012d}", "email_confirmed",
                "2024-02-01T00:00:00Z", fx.LOAD_1) for i in range(1, 12)]   # email long before sign-up
        self.assertEqual(dq.out_of_order_rate(self.inject(app=app)).status, "warn")

    def test_assumed_tz_jump_detected(self):
        # a "client release" drops the offset for 10 sign-ups on 2024-03-12
        accts = [(60_000 + i, f"z{i:07d}", "2024-03-12 05:00:00") for i in range(10)]
        app = [(f"z-{i}", f"z{i:07d}", "sign_up", "2024-03-12 05:00:00", fx.LOAD_1) for i in range(10)]
        self.assertEqual(dq.assumed_tz_jump(self.inject(app=app, accounts=accts)).status, "warn")

    def test_freshness_detected(self):
        conn, _ = fresh()
        self.assertEqual(dq.freshness(conn, "2024-03-13 12:00:00").status, "warn")

    def test_future_and_pre2012_timestamps_detected(self):
        self.assertEqual(dq.timestamp_bounds(self.inject(
            kyc=[("k-f", fx.kyc_ref(fx.U["U10"]), "approved", fx.epoch("2030-01-01 00:00:00"), fx.LOAD_1)]),
            fx.NOW).status, "error")
        self.assertEqual(dq.timestamp_bounds(self.inject(     # epoch 0: classic parse/default failure
            kyc=[("k-0", fx.kyc_ref(fx.U["U10"]), "approved", 0, fx.LOAD_1)]), fx.NOW).status, "error")


class TestSnowflakeSqliteParity(unittest.TestCase):
    """Cheap structural guard that the two versions of the model do not drift apart."""

    def test_same_output_columns(self):
        text = SNOWFLAKE_SQL.read_text()
        final = text.split("CREATE OR REPLACE TABLE analytics.fct_onboarding_funnel")[1].split("FROM spine")[0]
        final = re.sub(r"--[^\n]*", "", final.split("\nSELECT\n")[-1])
        items, depth, cur = [], 0, ""
        for ch in final:                       # split the select list on top-level commas
            depth += (ch == "(") - (ch == ")")
            if ch == "," and depth == 0:
                items.append(cur); cur = ""
            else:
                cur += ch
        items.append(cur)
        sf_cols = [re.findall(r"[A-Za-z_]+", it)[-1] for it in items]
        conn, _ = fresh(with_clean=False)
        sq_cols = [r[1] for r in conn.execute("PRAGMA table_info(fct_onboarding_funnel)")]
        self.assertEqual(sf_cols, sq_cols)

    def test_same_layers(self):
        text = SNOWFLAKE_SQL.read_text()
        for obj in ("stg_onboarding_events", "int_identity_map", "int_onboarding_quarantine",
                    "fct_onboarding_events", "fct_onboarding_funnel"):
            self.assertIn(f"analytics.{obj} AS", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
