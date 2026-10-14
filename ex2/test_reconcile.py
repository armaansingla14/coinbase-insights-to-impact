"""Tests for reconcile_signups.py (stdlib unittest).  Run: python3 -m unittest -v test_reconcile.py"""
import contextlib
import csv
import io
import random
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import reconcile_signups as rs  # noqa: E402

UTC = timezone.utc


def run_main(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = rs.main(list(argv))
    return result, out.getvalue()


def write(path, text):
    path.write_text(text, encoding="utf-8")


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


# Planted populations, copied from make_fake_data.py's add(...) calls (not from the reconciler).
PRE_07_REAL, POST_07_UNVERIFIED, TEST_ACCOUNTS, NEXT_UTC_DAY = 12_000, 5_500, 2_000, 12_500
C_RETRY, C_RETRY2 = 4_000, 1_000
PRE_07 = "created before 07:00 UTC (previous Pacific day)"
NEXT_DAY = "created on next UTC day (still Pacific report day)"


class PlantedTruth(unittest.TestCase):
    """Regenerate the synthetic data and check the script recovers exactly what was planted.
    Expected bucket sizes come from the generator's constants, or are recounted straight from the
    CSV text with string comparisons; never from the reconciler's own classification code."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = data = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(HERE / "make_fake_data.py"), str(data)],
                       check=True, capture_output=True)
        cls.result, cls.out = run_main("--data", str(data), "--day", "2026-09-24")
        cls.systems = {s: rs.load(data / f, i, t, fmt)[0] for s, (f, i, t, fmt) in rs.SOURCES.items()}
        a = read_csv(data / "system_a.csv")[1:]  # user_id, created_at, email, email_verified_at, ...
        pre = [r for r in a if r[1] < "2026-09-24T07:00:00Z"]  # ISO 'Z' strings sort as time
        cls.pre_unverified = sum(1 for r in pre if not r[2].endswith("@coinbase-test.com") and not r[3])
        cls.pre_test = sum(1 for r in pre if r[2].endswith("@coinbase-test.com"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_raw_counts(self):
        self.assertEqual(self.result["raw"], {"A": 52_000, "B": 45_000, "C": 57_000})
        self.assertTrue(all(not b for b in self.result["bad"].values()))

    def test_c_duplicates(self):
        dups = rs.duplicates(self.systems["C"])
        self.assertEqual(len(dups), 4_000)
        self.assertEqual(sum(len(r) - 1 for r in dups.values()), 5_000)
        self.assertEqual(len(rs.first_per_user(self.systems["C"])), 52_000)
        # the kept (earliest) copy is always the original batch, never a retry
        self.assertTrue(all("_retry" not in r[0]["ingest_batch_id"] for r in dups.values()))
        self.assertTrue(all("_retry" in x["ingest_batch_id"] for r in dups.values() for x in r[1:]))
        self.assertEqual(rs.duplicates(self.systems["A"]), {})
        self.assertEqual(rs.duplicates(self.systems["B"]), {})

    def test_bridge_a_to_b_buckets_match_planted_populations(self):
        r = self.result
        self.assertEqual(r["unexplained"], [])
        # precedence test > previous Pacific day > unverified, so each bucket is one planted group
        self.assertEqual(r["only_a"], {"test/internal account": TEST_ACCOUNTS, PRE_07: PRE_07_REAL,
                                       "email not verified": POST_07_UNVERIFIED})
        self.assertEqual(r["only_b"], {NEXT_DAY: NEXT_UTC_DAY})
        self.assertEqual(r["distinct"], {"A": 52_000, "B": 45_000, "C": 52_000})

    def test_bridge_overlap_counts_recounted_from_csv(self):
        self.assertTrue(1_100 < self.pre_unverified < 1_300)  # 10% of 12,000, seeded RNG
        self.assertGreater(self.pre_test, 0)
        self.assertEqual(self.result["overlap"], {
            f"{PRE_07} & email not verified": self.pre_unverified,
            f"test/internal account & {PRE_07}": self.pre_test})
        self.assertIn(f"{PRE_07} & email not verified: {self.pre_unverified:,}", self.out)
        self.assertIn("grain = distinct accounts after dedup", self.out)

    def test_c_bridge_matches_planted_retries(self):
        c = read_csv(self.data / "system_c.csv")[1:]
        self.assertEqual(sum(1 for r in c if r[4].endswith("_retry")), C_RETRY)
        self.assertEqual(sum(1 for r in c if r[4].endswith("_retry2")), C_RETRY2)
        r = self.result
        self.assertEqual(r["raw"]["C"] - r["distinct"]["C"], C_RETRY + C_RETRY2)
        self.assertEqual((r["only_c"], r["only_a_vs_c"], r["unexplained_c"]), ({}, {}, []))
        self.assertIn("only in C: 0 | only in A: 0", self.out)
        self.assertIn("UNEXPLAINED users (C vs A): 0\n", self.out)

    def test_overlap_and_candidate(self):
        self.assertEqual(self.result["membership"], {"ABC": 32_500, "AC": 19_500, "B": 12_500})
        self.assertEqual(self.result["candidate"], PRE_07_REAL + 32_500 + POST_07_UNVERIFIED)
        self.assertIn("in A+C only", self.out)
        self.assertNotIn("in ABC only", self.out)
        self.assertIn("UNEXPLAINED users: 0\n", self.out)


A_HEADER = "user_id,created_at,email,email_verified_at,country,platform\n"
B_HEADER = "USER_ID,signup_ts_local,country\n"
C_HEADER = "user_id,event_ts_ms,email,platform,ingest_batch_id\n"


class EdgeCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_all(self, a, b, c):
        write(self.dir / "system_a.csv", A_HEADER + a)
        write(self.dir / "system_b.csv", B_HEADER + b)
        write(self.dir / "system_c.csv", C_HEADER + c)
        return run_main("--data", str(self.dir), "--day", "2026-09-24")

    def test_malformed_rows_counted_not_crashing(self):
        a = ("u1,2026-09-24T10:00:00Z,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n"
             ",2026-09-24T11:00:00Z,x@gmail.com,,US,ios\n"            # blank id
             "u2,not-a-date,u2@gmail.com,,US,ios\n"                   # garbage ts
             "u3,,u3@gmail.com,,US,ios\n"                             # blank ts
             "u4,2026-09-24T12:00:00,u4@gmail.com,,US,ios\n"          # ISO without offset
             "u5,2026-09-24T12:00:00Z,u5@gmail.com\n")                # short row
        b = "U1,2026-09-24 03:00:00,US\nU9,31/02/2026,US\n"
        c = "u1,1790244000000,u1@gmail.com,ios,b20\nu7,abc,u7@gmail.com,ios,b20\nu8,99999999999999999999,u8@gmail.com,ios,b1\n"
        result, out = self.write_all(a, b, c)
        self.assertEqual(result["bad"]["A"], {"blank user_id": 1, "unparseable timestamp": 3,
                                              "wrong number of columns": 1})
        self.assertEqual(result["bad"]["B"], {"unparseable timestamp": 1})
        self.assertEqual(result["bad"]["C"], {"unparseable timestamp": 2})
        self.assertEqual(result["raw"], {"A": 6, "B": 2, "C": 3})
        # u1 is the only good A row and matches B's U1; nothing else to explain
        self.assertEqual((result["only_a"], result["only_b"], result["unexplained"]), ({}, {}, []))
        self.assertIn("rejected rows", out)

    def test_bom_whitespace_and_case(self):
        write(self.dir / "system_a.csv", "\ufeff user_id , created_at ,email,email_verified_at,country,platform\n"
                                         "  U1  , 2026-09-24T10:00:00+00:00 ,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n")
        write(self.dir / "system_b.csv", B_HEADER + "u1,2026-09-24 03:00:00,US\n")
        write(self.dir / "system_c.csv", C_HEADER)
        result, out = run_main("--data", str(self.dir))
        self.assertEqual(result["membership"], {"AB": 1})
        self.assertEqual(result["unexplained"], [])
        self.assertIn("C:       0 rows |       0 distinct | no valid rows", out)

    def test_duplicates_in_a_keep_earliest_and_report_ts_diff(self):
        a = ("u1,2026-09-24T10:00:00Z,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n"
             "u1,2026-09-24T09:00:00Z,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n"
             "U1 ,2026-09-24T11:00:00Z,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n")
        result, out = self.write_all(a, "U1,2026-09-24 02:00:00,US\n", "")
        rows = rs.load(self.dir / "system_a.csv", "user_id", "created_at", "iso")[0]
        self.assertEqual(rs.first_per_user(rows)["u1"]["ts_utc"], datetime(2026, 9, 24, 9, tzinfo=UTC))
        self.assertIn("A: 1 user_ids duplicated; 2 extra rows", out)
        self.assertIn("'created_at': 1", out)
        self.assertIn("- A duplicate rows", out)
        self.assertEqual(result["distinct"], {"A": 1, "B": 1, "C": 0})
        self.assertEqual((result["only_a"], result["only_b"], result["unexplained"]), ({}, {}, []))

    def test_genuinely_unexplained_user_is_flagged(self):
        a = ("u1,2026-09-24T10:00:00Z,u1@gmail.com,2026-09-24T10:05:00Z,US,ios\n"
             "u2,2026-09-24T15:00:00Z,u2@gmail.com,2026-09-24T15:05:00Z,US,ios\n")  # verified, in PT day, not test
        b = ("U1,2026-09-24 03:00:00,US\n"
             "U3,2026-09-24 09:00:00,US\n")  # 16:00 UTC, inside both days, but not in A
        result, out = self.write_all(a, b, "")
        self.assertEqual(result["unexplained"], ["u2", "u3"])
        self.assertEqual(result["only_a"], {"UNEXPLAINED": 1})
        self.assertEqual(result["only_b"], {"UNEXPLAINED": 1})
        self.assertIn("UNEXPLAINED users: 2  <-- investigate", out)
        self.assertIn("B only", out)

    def test_schema_change_fails_loudly(self):
        write(self.dir / "system_b.csv", "user_uuid,signup_ts_local,country\nU1,2026-09-24 03:00:00,US\n")
        with self.assertRaisesRegex(ValueError, r"missing column\(s\) \['USER_ID'\]"):
            rs.load(self.dir / "system_b.csv", "USER_ID", "signup_ts_local", "naive_pt")
        write(self.dir / "empty.csv", "")
        with self.assertRaisesRegex(ValueError, "missing column"):
            rs.load(self.dir / "empty.csv", "user_id", "created_at", "iso")

    def test_c_users_missing_from_a_get_readable_label(self):
        result, out = self.write_all("", "", "u5,1790244000000,u5@gmail.com,ios,b20\n")
        self.assertEqual(result["membership"], {"C": 1})
        self.assertIn("in C only", out)
        self.assertEqual(result["only_c"], {"UNEXPLAINED": 1})

    def test_c_bridge_counts(self):
        a = ("u1,2026-09-24T10:00:00Z,u1@gmail.com,,US,ios\n"
             "u2,2026-09-24T11:00:00Z,u2@gmail.com,,US,ios\n")
        c = ("u1,1790244000000,u1@gmail.com,ios,b20\n"           # 2026-09-24 10:00 UTC
             "u1,1790244000000,u1@gmail.com,ios,b20_retry\n"     # replayed copy
             "u3,1790244000000,u3@gmail.com,ios,b20\n"           # in the window, missing from A
             "u4,1790330400000,u4@gmail.com,ios,b20\n")          # 2026-09-25 10:00 UTC
        result, out = self.write_all(a, "", c)
        self.assertEqual(result["distinct"]["C"], 3)
        self.assertEqual(result["only_c"], {"UNEXPLAINED": 1, rs.OUTSIDE: 1})
        self.assertEqual(result["only_a_vs_c"], {"UNEXPLAINED": 1})
        self.assertEqual(result["unexplained_c"], ["u2", "u3"])
        self.assertIn("only in C: 2 | only in A: 1", out)
        self.assertIn("- C duplicate rows", out)

    def test_rows_outside_report_window_get_their_own_reason(self):
        a = "u1,2026-09-23T20:00:00Z,u1@gmail.com,2026-09-23T20:05:00Z,US,ios\n"  # previous UTC day
        b = "U2,2026-09-23 23:59:59,US\n"                                         # previous PT day
        result, out = self.write_all(a, b, "")
        self.assertEqual(result["only_a"], {rs.OUTSIDE: 1})
        self.assertEqual(result["only_b"], {rs.OUTSIDE: 1})
        self.assertEqual(result["overlap"], {f"{rs.OUTSIDE} & {PRE_07}": 1})
        self.assertEqual(result["unexplained"], [])

    def test_boundary_0659_and_0700_utc_on_pdt_day(self):
        a = ("u1,2026-09-24T06:59:59Z,u1@gmail.com,2026-09-24T08:00:00Z,US,ios\n"
             "u2,2026-09-24T07:00:00Z,u2@gmail.com,2026-09-24T08:00:00Z,US,ios\n")
        b = "U2,2026-09-24 00:00:00,US\n"  # 07:00:00 UTC, first second of the Pacific day
        result, _ = self.write_all(a, b, "")
        self.assertEqual(result["membership"], {"A": 1, "AB": 1})
        self.assertEqual(result["only_a"], {PRE_07: 1})
        self.assertEqual(result["only_b"], {})

    def test_spring_forward_gap_rejected_and_fall_back_counted(self):
        write(self.dir / "system_b.csv", B_HEADER + "U1,2026-03-08 02:30:00,US\n"   # never happened
                                                   "U2,2026-11-01 01:30:00,US\n"   # happened twice
                                                   "U3,2026-11-01 00:30:00,US\n")  # unambiguous
        rows, bad, notes = rs.load(self.dir / "system_b.csv", "USER_ID", "signup_ts_local", "naive_pt")
        self.assertEqual(bad, {"nonexistent local time (DST gap)": 1})
        self.assertEqual(notes, {rs.AMBIGUOUS: 1})
        self.assertEqual(rows[0]["ts_utc"], datetime(2026, 11, 1, 8, 30, tzinfo=UTC))  # earlier: PDT
        write(self.dir / "system_a.csv", A_HEADER)
        write(self.dir / "system_c.csv", C_HEADER)
        _, out = run_main("--data", str(self.dir), "--day", "2026-11-01")
        self.assertIn("ambiguous local time (DST overlap): 1 rows kept, read as the earlier instant", out)
        self.assertIn("'nonexistent local time (DST gap)': 1", out)

    def test_naive_pt_with_offset_or_z_is_rejected(self):
        b = ("U1,2026-09-24 00:00:00Z,US\nU2,2026-09-24 00:00:00-07:00,US\n"
             "U3,2026-09-24T00:00:00+00:00,US\nU4,2026-09-24 00:00:00,US\n")
        result, _ = self.write_all("", b, "")
        self.assertEqual(result["bad"]["B"], {"unparseable timestamp": 3})
        self.assertEqual(result["distinct"]["B"], 1)


class OrderAndDuplication(unittest.TestCase):
    """Metamorphic checks on the full synthetic data: results must not depend on file order, and
    an extra copy of a row must change only the duplicate-row count."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name) / "base"
        subprocess.run([sys.executable, str(HERE / "make_fake_data.py"), str(cls.base)],
                       check=True, capture_output=True)
        cls.result, cls.out = run_main("--data", str(cls.base))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def variant(self, name, change):
        """Copy the data to a new dir, applying change(system, rows) to each file's data rows."""
        out = Path(self.tmp.name) / name
        out.mkdir()
        for s, (file, *_) in rs.SOURCES.items():
            header, *rows = read_csv(self.base / file)
            write_csv(out / file, [header] + change(s, rows))
        return run_main("--data", str(out))

    def test_reversed_and_shuffled_rows_give_identical_results(self):
        for name, change in [("reversed", lambda s, rows: rows[::-1]),
                             ("shuffled", lambda s, rows: random.Random(7).sample(rows, len(rows)))]:
            with self.subTest(name):
                result, out = self.variant(name, change)
                self.assertEqual(result, self.result)
                self.assertEqual(out, self.out)

    def test_duplicating_a_row_changes_only_duplicate_count(self):
        result, out = self.variant("dup", lambda s, rows: rows + rows[:1] if s in "AB" else rows)
        for key in ("distinct", "only_a", "only_b", "overlap", "unexplained", "only_c", "membership"):
            self.assertEqual(result[key], self.result[key], key)
        self.assertEqual(result["raw"], {"A": 52_001, "B": 45_001, "C": 57_000})
        self.assertIn("A: 1 user_ids duplicated; 1 extra rows", out)
        self.assertIn("  - A duplicate rows", out)

    def test_first_per_user_ignores_order_even_on_full_ties(self):
        rows = [dict(uid="u1", ts_utc=datetime(2026, 9, 24, tzinfo=UTC), ingest_batch_id="b1", platform=p)
                for p in ("web", "ios", "android")]
        self.assertEqual(rs.first_per_user(rows), rs.first_per_user(rows[::-1]))
        self.assertEqual(rs.duplicates(rows), rs.duplicates(rows[::-1]))
        self.assertEqual(rs.first_per_user(rows)["u1"]["platform"], "android")


class Timezones(unittest.TestCase):
    def test_pacific_window_follows_dst(self):
        _, summer = rs.day_windows(date(2026, 9, 24))
        _, winter = rs.day_windows(date(2026, 12, 1))
        _, fall_back = rs.day_windows(date(2026, 11, 1))
        self.assertEqual(summer[0], datetime(2026, 9, 24, 7, tzinfo=UTC))
        self.assertEqual(winter[0], datetime(2026, 12, 1, 8, tzinfo=UTC))
        self.assertEqual(fall_back[1] - fall_back[0], rs.timedelta(hours=25))

    def test_naive_pt_parser(self):
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-09-24 00:00:00"), datetime(2026, 9, 24, 7, tzinfo=UTC))
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-12-01 00:00:00"), datetime(2026, 12, 1, 8, tzinfo=UTC))

    def test_pdt_date_window_starts_0700_utc(self):
        utc_day, pt_day = rs.day_windows(date(2026, 9, 24))
        self.assertEqual(pt_day, (datetime(2026, 9, 24, 7, tzinfo=UTC), datetime(2026, 9, 25, 7, tzinfo=UTC)))
        self.assertEqual(utc_day, (datetime(2026, 9, 24, tzinfo=UTC), datetime(2026, 9, 25, tzinfo=UTC)))

    def test_pst_date_window_starts_0800_utc(self):
        _, pt_day = rs.day_windows(date(2026, 1, 15))
        self.assertEqual(pt_day, (datetime(2026, 1, 15, 8, tzinfo=UTC), datetime(2026, 1, 16, 8, tzinfo=UTC)))
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-01-15 23:30:00"), datetime(2026, 1, 16, 7, 30, tzinfo=UTC))

    def test_utc_day_vs_pacific_day(self):
        utc_day, pt_day = rs.day_windows(date(2026, 9, 24))
        late = datetime(2026, 9, 25, 3, tzinfo=UTC)   # 09-25 in UTC, 09-24 20:00 in Pacific
        early = datetime(2026, 9, 24, 3, tzinfo=UTC)  # 09-24 in UTC, 09-23 20:00 in Pacific
        self.assertEqual((rs.in_window(late, utc_day), rs.in_window(late, pt_day)), (False, True))
        self.assertEqual((rs.in_window(early, utc_day), rs.in_window(early, pt_day)), (True, False))

    def test_dst_gap_and_overlap_parsing(self):
        with self.assertRaises(rs.NonexistentLocalTime):
            rs.PARSERS["naive_pt"]("2026-03-08 02:30:00")
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-03-08 03:00:00"), datetime(2026, 3, 8, 10, tzinfo=UTC))
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-11-01 01:30:00"), datetime(2026, 11, 1, 8, 30, tzinfo=UTC))
        self.assertTrue(rs.is_ambiguous_pt("2026-11-01 01:30:00"))
        self.assertFalse(rs.is_ambiguous_pt("2026-11-01 02:00:00"))
        self.assertFalse(rs.is_ambiguous_pt("2026-09-24 01:30:00"))

    def test_missing_tz_database_raises_instead_of_fixed_offset(self):
        import zoneinfo
        with mock.patch.object(zoneinfo, "ZoneInfo", side_effect=zoneinfo.ZoneInfoNotFoundError("x")):
            with self.assertRaisesRegex(RuntimeError, "tzdata"):
                rs.load_pacific()
        self.assertEqual(rs.load_pacific().key, "America/Los_Angeles")

    def test_parsers_agree_on_same_instant(self):
        inst = datetime(2026, 9, 24, 13, 30, tzinfo=UTC)
        self.assertEqual(rs.PARSERS["iso"]("2026-09-24T13:30:00Z"), inst)
        self.assertEqual(rs.PARSERS["iso"]("2026-09-24T06:30:00-07:00"), inst)
        self.assertEqual(rs.PARSERS["epoch_ms"](str(int(inst.timestamp() * 1000))), inst)
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-09-24 06:30:00"), inst)


if __name__ == "__main__":
    unittest.main()
