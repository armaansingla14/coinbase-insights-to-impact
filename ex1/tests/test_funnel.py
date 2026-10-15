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
import fixtures_handcalc as hc  # noqa: E402

SNOWFLAKE_SQL = HERE.parent / "fct_onboarding_funnel.sql"
SNOWFLAKE_PM_SQL = HERE.parent / "pm_funnel_query.sql"
SQLITE_PM_SQL = HERE.parent / "sqlite" / "pm_funnel_query.sql"


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

    def test_app_offset_normalization_in_staging(self):
        # Runs the real stg_onboarding_events view. Compact offsets are rewritten to +HH:MM before
        # parsing (the Snowflake model does the same with '\\1'); offset-free strings are assumed UTC
        # and flagged; garbage parses to NULL (-> quarantine). Expected values worked out by hand.
        conn = build.connect()
        build.create_raw_tables(conn)
        cases = {"o1": "2024-03-11T05:30:00+0530", "o2": "2024-03-11T05:30:00+05:30",
                 "o3": "2024-03-10T22:00:00-0400", "o4": "2024-03-11T02:00:00Z",
                 "o5": "2024-03-11 02:00:00", "o6": "not-a-date", "o7": "2024-03-11T09:15:00+1245"}
        conn.executemany("INSERT INTO raw_app_events VALUES (?, 'anon-x', 'sign_up', ?, '2024-03-12 00:00:00')",
                         list(cases.items()))
        build.build_model(conn)
        got = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT source_event_id, event_ts_utc, ts_was_assumed_utc FROM stg_onboarding_events")}
        self.assertEqual(got, {"o1": ("2024-03-11 00:00:00", 0), "o2": ("2024-03-11 00:00:00", 0),
                               "o3": ("2024-03-11 02:00:00", 0), "o4": ("2024-03-11 02:00:00", 0),
                               "o5": ("2024-03-11 02:00:00", 1), "o6": (None, 1),
                               "o7": ("2024-03-10 20:30:00", 0)})
        self.assertEqual(build.regexp_replace("2024-03-11T05:30:00+0530", "([+-][0-9]{2})([0-9]{2})$",
                                              r"\1:\2"), "2024-03-11T05:30:00+05:30")
        self.assertEqual(build.regexp_replace("2024-03-11 02:00:00", "([+-][0-9]{2})([0-9]{2})$",
                                              r"\1:\2"), "2024-03-11 02:00:00")  # no offset: untouched

    def test_convert_timezone_arg_order_and_dst(self):
        # CONVERT_TIMEZONE(source_tz, target_tz, ntz): 07:30 New York wall clock in winter = 12:30 UTC ...
        self.assertEqual(build.convert_timezone("America/New_York", "UTC", "2024-03-01 07:30:00"), "2024-03-01 12:30:00")
        # ... and after the 2024-03-10 DST switch the offset is -4
        self.assertEqual(build.convert_timezone("America/New_York", "UTC", "2024-03-11 09:00:00"), "2024-03-11 13:00:00")
        # swapped arguments give a different (wrong) answer -> order matters and is tested
        self.assertNotEqual(build.convert_timezone("UTC", "America/New_York", "2024-03-01 07:30:00"), "2024-03-01 12:30:00")

    def test_epoch_seconds_are_utc(self):
        self.assertEqual(build.to_timestamp_ntz(1709290800), "2024-03-01 11:00:00")
        with self.assertRaises(ValueError):         # TO_ (non-TRY) still fails loudly, like Snowflake ...
            build.to_timestamp_ntz("garbage")

    def test_try_to_number_returns_null_on_garbage(self):
        # ... which is why the model now reads the KYC epoch via TRY_TO_NUMBER (NULL -> quarantine)
        self.assertEqual(build.try_to_number(" 1709290800 "), 1709290800)
        self.assertEqual(build.try_to_number(1709290800), 1709290800)
        self.assertEqual(build.try_to_number("1.5"), 2)                 # NUMBER(38,0): half-up rounding
        for bad in ("garbage", "", "NaN", "Infinity", None):
            self.assertIsNone(build.try_to_number(bad), bad)
        self.assertIsNone(build.to_timestamp_ntz(build.try_to_number("garbage")))

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
        # Rule change: only the MANDATORY steps (email, KYC) can be missing; a trade no longer implies a
        # deposit, so "trade without deposit" is not a gap.
        for r in self.conn.execute("SELECT * FROM fct_onboarding_funnel"):
            email, idv, dep, trade = (r[c] for c in ("email_verified_at", "identity_verified_at",
                                                       "first_deposit_at", "first_trade_at"))
            brute = (email is None and any(v is not None for v in (idv, dep, trade))) or \
                    (idv is None and any(v is not None for v in (dep, trade)))
            self.assertEqual(bool(r["has_missing_step"]), brute, r["user_uuid"])

    def test_is_out_of_order_equals_bruteforce_definition(self):
        # Rule change: deposit and trade share a stage (parallel funding routes), so their relative order is free.
        cols = ["email_verified_at", "identity_verified_at", "first_deposit_at", "first_trade_at"]
        stage = [1, 2, 3, 4, 4]
        for r in self.conn.execute("SELECT * FROM fct_onboarding_funnel"):
            sig = None if r["signup_event_missing"] else r["signup_at"]   # event-based comparison only
            vals = [sig] + [r[c] for c in cols]
            brute = any(vals[i] is not None and vals[j] is not None and vals[j] < vals[i]
                        for i in range(5) for j in range(5) if stage[i] < stage[j])
            self.assertEqual(bool(r["is_out_of_order"]), brute, r["user_uuid"])

    def test_reached_flags_monotonic(self):
        # email >= KYC >= funding, funding = deposit OR trade. Deposit vs trade is deliberately NOT ordered.
        bad = self.conn.execute("""SELECT COUNT(*) FROM fct_onboarding_funnel WHERE
            reached_funding > reached_identity_verified OR reached_identity_verified > reached_email_verified
            OR reached_funding <> (reached_first_deposit OR reached_first_trade)""").fetchone()[0]
        self.assertEqual(bad, 0)

    def test_at_columns_are_observed_only(self):
        # *_at is never filled by inference: state 'observed' <=> timestamp present, for every step
        for step in ("email_verified", "identity_verified", "first_deposit", "first_trade"):
            bad = self.conn.execute(f"""SELECT COUNT(*) FROM fct_onboarding_funnel
                WHERE ({step}_state = 'observed') <> ({step}_at IS NOT NULL)""").fetchone()[0]
            self.assertEqual(bad, 0, step)


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
            # Rule change: U13 (anon link to a user without an account) used to be a "no_signup_anchor" warn;
            # its link is now an orphan_link and its event is quarantined, so every check passes.
            self.assertEqual(r.status, "pass", f"{name}: {r.detail}")
        self.assertEqual(results["no_signup_anchor"].metric, 0)
        self.assertGreater(results["identity match rate"].metric, 0)    # the 3 planted identity failures count
        self.assertGreater(results["is_out_of_order rate"].metric, 0)

    def inject(self, **rows):
        conn, _ = fresh()
        fx.insert(conn, **rows)
        build.build_model(conn)
        return conn

    def test_ambiguous_anon_link_quarantined_not_fanned_out(self):
        # Rule change: an anon id linked to two users used to fan out (and fail the uniqueness check);
        # now the ref is marked ambiguous_link, its events are quarantined and nothing is double counted.
        conn = self.inject(links=[("anon-u01", fx.U["U02"])])     # same anon id linked to a second user
        s = self.status(conn)
        self.assertEqual(s["ambiguous identity links"], "warn")
        self.assertEqual(s["identity_map.user_ref unique (no fan-out)"], "pass")
        self.assertEqual(s["event conservation (staged = usable + quarantined)"], "pass")
        self.assertIn(("ambiguous_link", "e-0101"), {tuple(r) for r in conn.execute(
            "SELECT reason, source_event_id FROM int_onboarding_quarantine")})
        self.assertEqual(funnel_row(conn, fx.U["U01"])["signup_event_missing"], 1)
        self.assertEqual(funnel_row(conn, fx.U["U02"])["signup_at"], fx.EXPECTED["U02"]["signup_at"])

    def test_identity_map_fanout_check_still_fires(self):
        conn, _ = fresh(with_clean=False)
        conn.execute("CREATE TABLE m AS SELECT * FROM int_identity_map")
        conn.execute("INSERT INTO m SELECT user_ref, link_status, 'someone-else', 1 FROM m LIMIT 1")
        for view in ("int_onboarding_quarantine", "fct_onboarding_events", "int_identity_map"):   # dependants
            conn.execute(f"DROP VIEW {view}")
        conn.execute("ALTER TABLE m RENAME TO int_identity_map")
        self.assertEqual(dq.identity_map_unique(conn).status, "error")

    def test_account_with_two_uuids_detected_and_ledger_quarantined(self):
        conn = self.inject(accounts=[(101, fx.U["U02"], "2024-03-01 09:59:00")])   # account 101 is U01's
        self.assertEqual(dq.account_uuid_unique(conn).status, "error")
        reasons = {r[0] for r in conn.execute("SELECT reason FROM int_onboarding_quarantine "
                                              "WHERE user_ref = 'ledger_account:101'")}
        self.assertEqual(reasons, {"ambiguous_link"})
        self.assertIsNone(funnel_row(conn, fx.U["U01"])["first_deposit_at"])     # not credited to anyone
        self.assertEqual(funnel_row(conn, fx.U["U02"])["first_deposit_at"], fx.EXPECTED["U02"]["first_deposit_at"])

    def test_no_signup_anchor_detected(self):
        conn = self.inject(accounts=[(70_000, "n0000000", None)],
                           app=[("n-1", "n0000000", "email_confirmed", "2024-03-06T09:00:00Z", fx.LOAD_1)])
        self.assertEqual(dq.no_signup_anchor(conn).status, "warn")

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
        # baseline: 3 unresolved of 745 distinct refs = 0.40% (pass); +1 -> 0.54% (warn); +5 -> 1.07% (error)
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
        final = text.split("CREATE OR REPLACE TABLE analytics.fct_onboarding_funnel")[1].split("\nFROM states")[0]
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


# ------------------------------------------------------------------------------------------------------
# Hand-calculated mini fixture (fixtures_handcalc.py): every expected value is a literal worked out by hand.
# ------------------------------------------------------------------------------------------------------
def build_handcalc(late=True, **overrides):
    conn = build.connect()
    build.create_raw_tables(conn)
    hc.load(conn, late=late, **overrides)
    build.build_model(conn)
    return conn


def pm_result(conn, now=hc.NOW):
    return [tuple(r) for r in conn.execute(SQLITE_PM_SQL.read_text(), {"now": now})]


def hc_row(conn, name):
    r = conn.execute("SELECT * FROM fct_onboarding_funnel WHERE user_uuid = ?", (hc.H[name],)).fetchone()
    return None if r is None else tuple(r[c] for c in hc.FUNNEL_COLS)


def funnel_snapshot(conn):
    """All funnel rows, minus _loaded_at (the only column allowed to differ between builds)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fct_onboarding_funnel)") if r[1] != "_loaded_at"]
    return [tuple(r) for r in conn.execute(f"SELECT {', '.join(cols)} FROM fct_onboarding_funnel "
                                           "ORDER BY user_uuid")]


class TestHandCalculated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = build_handcalc()

    def test_pm_query_hand_calculated(self):
        self.assertEqual(pm_result(self.conn), hc.EXPECTED_PM)

    def test_every_user_hand_calculated(self):
        for name, exp in hc.EXPECTED_FUNNEL.items():
            with self.subTest(user=name):
                self.assertEqual(hc_row(self.conn, name), exp)
        n = self.conn.execute("SELECT COUNT(*) FROM fct_onboarding_funnel").fetchone()[0]
        self.assertEqual(n, 22)

    def test_quarantine_hand_calculated(self):
        got = {tuple(r) for r in self.conn.execute(
            "SELECT reason, source, user_ref, source_event_id FROM int_onboarding_quarantine")}
        self.assertEqual(got, hc.EXPECTED_QUARANTINE)

    def test_deposit_after_day7_but_trade_on_day2_counts_as_funding(self):
        # H01 alone in a cohort: COALESCE(deposit, trade) would say "no deposit in 7d" AND "no funding"
        conn = build_handcalc(late=False, **hc.only("H01"))
        self.assertEqual(pm_result(conn), [("2024-03-04", 1, 0, 1, 1)])

    def test_window_boundaries(self):
        # H04: trade at signup + 7d - 1s is counted, deposit at exactly signup + 7d is not
        conn = build_handcalc(late=False, **hc.only("H04"))
        self.assertEqual(pm_result(conn), [("2024-03-04", 1, 0, 1, 1)])

    def test_trade_before_signup_not_counted(self):
        conn = build_handcalc(late=False, **hc.only("H03"))
        self.assertEqual(pm_result(conn), [("2024-03-04", 1, 0, 0, 0)])

    def test_presignup_first_trade_does_not_hide_later_trade_in_window(self):
        # H22: first trade 1h before sign-up, second trade on day 3. Audit timestamps keep the
        # pre-sign-up trade; the 7-day window uses the earliest trade at or after sign-up.
        conn = build_handcalc(late=False, **hc.only("H22"))
        row = conn.execute("SELECT first_trade_at, first_trade_after_signup_at, is_out_of_order "
                           "FROM fct_onboarding_funnel").fetchone()
        self.assertEqual(tuple(row), ("2024-03-06 11:00:00", "2024-03-09 12:00:00", 1))
        self.assertEqual(pm_result(conn), [("2024-03-04", 1, 0, 1, 1)])
        # regression guard: the old window on first_trade_at would have missed this user
        old = conn.execute("SELECT COUNT(*) FROM fct_onboarding_funnel WHERE first_trade_at >= signup_at "
                           "AND first_trade_at < datetime(signup_at, '+7 days')").fetchone()[0]
        self.assertEqual(old, 0)

    def test_after_signup_columns_hand_calculated(self):
        want = {"H01": ("2024-03-12 10:00:00", "2024-03-06 10:00:00"),   # deposit day 8, trade day 2
                "H02": ("2024-03-05 10:00:00", "2024-03-05 09:00:00"),
                "H03": (None, None),                                       # only trade is before sign-up
                "H14": (None, "2024-03-07 15:00:00"),
                "H22": (None, "2024-03-09 12:00:00")}
        for name, exp in want.items():
            with self.subTest(user=name):
                got = self.conn.execute("SELECT first_deposit_after_signup_at, first_trade_after_signup_at "
                                        "FROM fct_onboarding_funnel WHERE user_uuid = ?", (hc.H[name],)).fetchone()
                self.assertEqual(tuple(got), exp)

    def test_maturity_boundary(self):
        # week C (2024-11-04) needs now >= 2024-11-18 00:00:00; one second earlier it is not reported
        self.assertEqual([r[0] for r in pm_result(self.conn, "2024-11-17 23:59:59")], ["2024-03-04", "2024-10-28"])
        self.assertEqual([r[0] for r in pm_result(self.conn, "2024-11-25 00:00:00")][-1], "2024-11-11")

    def test_repeated_signup_and_multiple_device_ids(self):
        rows = {(r["user_uuid"], r["step"]): r["n_source_events"] for r in self.conn.execute(
            "SELECT user_uuid, step, n_source_events FROM fct_onboarding_events")}
        self.assertEqual(rows[(hc.H["H06"], "signup")], 3)            # replay + retry, first one wins
        self.assertEqual(rows[(hc.H["H08"], "email_verified")], 2)    # phone + web ids, same user
        self.assertEqual(rows[(hc.H["H08"], "signup")], 1)            # via the third id, ' anon-H08 '

    def test_anon_ids_differing_only_by_case_not_merged(self):
        m = {r[0]: (r[1], r[2]) for r in self.conn.execute(
            "SELECT user_ref, link_status, user_uuid FROM int_identity_map WHERE user_ref LIKE 'anon-%ase'")}
        self.assertEqual(m, {"anon-Case": ("resolved", hc.H["H09"]), "anon-case": ("resolved", hc.H["H10"])})

    def test_dst_gap_and_overlap(self):
        # gap: 02:30 does not exist in New York on 2024-03-10 -> NULL + quarantined, never shifted to 07:30
        # overlap: 01:30 happens twice on 2024-11-03 -> earlier instant (EDT, 05:30 UTC) and flagged
        got = {r[0]: (r[1], r[2], r[3]) for r in self.conn.execute(
            "SELECT source_event_id, event_ts_utc, ts_dst_ambiguous, ts_null_reason FROM stg_onboarding_events "
            "WHERE source_event_id IN ('ht-1101', 'ht-1102', 'ht-1601', 'ht-0101')")}
        self.assertEqual(got, {"ht-1101": (None, 0, "nonexistent_local_time"),
                               "ht-1102": ("2024-03-10 07:30:00", 0, None),
                               "ht-1601": ("2024-11-03 05:30:00", 1, None),
                               "ht-0101": ("2024-03-06 10:00:00", 0, None)})

    def test_late_arriving_event(self):
        conn = build_handcalc(late=False)
        self.assertEqual(hc_row(conn, "H12"), hc.EXPECTED_H12_BEFORE_LATE)
        self.assertEqual(pm_result(conn), hc.EXPECTED_PM_BEFORE_LATE)
        hc.load(conn, late=False, app=[], kyc=[], ledger=hc.LATE_LEDGER, accounts=[], links=[])
        build.build_model(conn)
        self.assertEqual(hc_row(conn, "H12"), hc.EXPECTED_FUNNEL["H12"])
        self.assertEqual(pm_result(conn), hc.EXPECTED_PM)

    def test_bad_kyc_epoch_quarantined_not_fatal(self):
        # used to raise inside TO_TIMESTAMP_NTZ and abort the whole build
        self.assertEqual(hc_row(self.conn, "H21")[3], "rejected_event")


class TestMetamorphic(unittest.TestCase):
    """Changes that must NOT move the funnel rows or the PM query result."""

    @classmethod
    def setUpClass(cls):
        base = build_handcalc()
        cls.funnel, cls.pm = funnel_snapshot(base), pm_result(base)

    def assertUnchanged(self, conn):
        self.assertEqual(pm_result(conn), self.pm)
        self.assertEqual(funnel_snapshot(conn), self.funnel)

    def test_duplicating_events(self):
        self.assertUnchanged(build_handcalc(app=hc.APP + hc.APP[:5], kyc=hc.KYC + hc.KYC[:3],
                                            ledger=hc.LEDGER + hc.LEDGER[:4]))

    def test_adding_another_device_link(self):
        self.assertUnchanged(build_handcalc(links=hc.ANON_LINKS + [("dev-H08-tablet", hc.H["H08"]),
                                                                   ("dev-H05-tv", hc.H["H05"])]))

    def test_reordering_inserted_rows(self):
        self.assertUnchanged(build_handcalc(app=hc.APP[::-1], kyc=hc.KYC[::-1], ledger=hc.LEDGER[::-1],
                                            accounts=hc.ACCOUNTS[::-1], links=hc.ANON_LINKS[::-1]))

    def test_rebuilding_twice(self):
        conn = build_handcalc()
        build.build_model(conn)
        build.build_model(conn)
        self.assertUnchanged(conn)

    def test_late_event_outside_window(self):
        # a later trade for H01 on day 20: neither its first trade nor its 7-day window changes
        late = [("ht-0199", 201, "trade", "completed", "2024-03-24 10:00:00", "UTC", hc.LOAD_LATE)]
        self.assertUnchanged(build_handcalc(ledger=hc.LEDGER + late))

    def test_late_first_event_outside_window_keeps_pm_result(self):
        # H05's FIRST deposit on day 10 changes its funnel row, but not the 7-day PM numbers
        late = [("ht-0599", 205, "deposit", "completed", "2024-03-17 08:00:00", "UTC", hc.LOAD_LATE)]
        self.assertEqual(pm_result(build_handcalc(ledger=hc.LEDGER + late)), self.pm)


class TestPmQueryParity(unittest.TestCase):
    def test_same_output_columns_and_definitions(self):
        conn = build_handcalc()
        cur = conn.execute(SQLITE_PM_SQL.read_text(), {"now": hc.NOW})
        cols = [d[0] for d in cur.description]
        self.assertEqual(cols, ["cohort_week", "signups", "observed_deposit_7d", "funding_7d", "trade_7d"])
        sf = SNOWFLAKE_PM_SQL.read_text()
        for c in cols:
            self.assertRegex(sf, rf"AS {c}\b")
        for fragment in ("DATEADD('day', 1 - DAYOFWEEKISO(signup_at), DATE(signup_at))", "SYSDATE()",
                         "DATEADD('day', 14, cohort_week", "COUNT_IF(deposit_in_7d OR trade_in_7d)"):
            self.assertIn(fragment, sf)
        self.assertNotIn("CURRENT_TIMESTAMP()", sf.split("\nWITH ")[1])     # code, not the header comment


if __name__ == "__main__":
    unittest.main(verbosity=2)
