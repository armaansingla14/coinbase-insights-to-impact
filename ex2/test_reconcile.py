"""Tests for reconcile_signups.py (stdlib unittest).  Run: python3 -m unittest -v test_reconcile.py"""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

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


class PlantedTruth(unittest.TestCase):
    """Regenerate the synthetic data and check the script recovers exactly what was planted."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data = Path(cls.tmp.name)
        subprocess.run([sys.executable, str(HERE / "make_fake_data.py"), str(data)],
                       check=True, capture_output=True)
        cls.result, cls.out = run_main("--data", str(data), "--day", "2026-09-24")
        cls.systems = {s: rs.load(data / f, i, t, fmt)[0] for s, (f, i, t, fmt) in rs.SOURCES.items()}

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

    def test_bridge_a_to_b(self):
        r = self.result
        self.assertEqual(r["bridged"], 45_000)
        self.assertEqual(r["unexplained"], [])
        self.assertEqual(r["only_a"], {"created before 07:00 UTC (previous Pacific day)": 12_000,
                                       "email not verified": 5_500, "test/internal account": 2_000})
        self.assertEqual(r["only_b"], {"created on next UTC day (still Pacific report day)": 12_500})

    def test_overlap_and_candidate(self):
        self.assertEqual(self.result["membership"], {"ABC": 32_500, "AC": 19_500, "B": 12_500})
        self.assertEqual(self.result["candidate"], 50_000)
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
        self.assertEqual(result["bridged"], result["raw"]["B"])  # the bridge still balances
        self.assertEqual(result["unexplained"], [])
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
        rows, _ = rs.load(self.dir / "system_a.csv", "user_id", "created_at", "iso")
        self.assertEqual(rs.first_per_user(rows)["u1"]["ts_utc"], datetime(2026, 9, 24, 9, tzinfo=UTC))
        self.assertIn("A: 1 user_ids duplicated; 2 extra rows", out)
        self.assertIn("'created_at': 1", out)
        self.assertIn("- A duplicate rows", out)
        self.assertEqual(result["bridged"], 1)
        self.assertEqual(result["unexplained"], [])

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


class Timezones(unittest.TestCase):
    def test_pacific_window_follows_dst(self):
        if not hasattr(rs.PT, "key"):
            self.skipTest("zoneinfo database unavailable; fixed-offset fallback in use")
        _, summer = rs.day_windows(date(2026, 9, 24))
        _, winter = rs.day_windows(date(2026, 12, 1))
        _, fall_back = rs.day_windows(date(2026, 11, 1))
        self.assertEqual(summer[0], datetime(2026, 9, 24, 7, tzinfo=UTC))
        self.assertEqual(winter[0], datetime(2026, 12, 1, 8, tzinfo=UTC))
        self.assertEqual(fall_back[1] - fall_back[0], rs.timedelta(hours=25))

    def test_naive_pt_parser(self):
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-09-24 00:00:00"), datetime(2026, 9, 24, 7, tzinfo=UTC))
        if hasattr(rs.PT, "key"):
            self.assertEqual(rs.PARSERS["naive_pt"]("2026-12-01 00:00:00"), datetime(2026, 12, 1, 8, tzinfo=UTC))

    def test_parsers_agree_on_same_instant(self):
        inst = datetime(2026, 9, 24, 13, 30, tzinfo=UTC)
        self.assertEqual(rs.PARSERS["iso"]("2026-09-24T13:30:00Z"), inst)
        self.assertEqual(rs.PARSERS["iso"]("2026-09-24T06:30:00-07:00"), inst)
        self.assertEqual(rs.PARSERS["epoch_ms"](str(int(inst.timestamp() * 1000))), inst)
        self.assertEqual(rs.PARSERS["naive_pt"]("2026-09-24 06:30:00"), inst)


if __name__ == "__main__":
    unittest.main()
