"""Small, separate fixture whose expected outputs are written BY HAND as literals (see test_funnel.py,
class TestHandCalculated). Nothing here is derived by a Python re-implementation of the model.

Weeks (Monday, UTC):  A = 2024-03-04 (US spring-forward on Sun 2024-03-10),
                      B = 2024-10-28 (US fall-back on Sun 2024-11-03),
                      C = 2024-11-04 (matures exactly at NOW), D = 2024-11-11 (immature, excluded).
"""
from fixtures import epoch, insert

NOW = "2024-11-18 00:00:00"          # week C + 14 days == NOW -> included (boundary is <=)
LOAD_1 = "2024-11-17 00:00:00"
LOAD_LATE = "2024-11-17 06:00:00"
NY = "America/New_York"


def uid(n):
    return f"b{n:07d}-0000-4000-8000-{n:012d}"


H = {f"H{n:02d}": uid(n) for n in range(1, 22)}

ACCOUNTS = [
    (201, H["H01"], "2024-03-04 10:00:00"), (202, H["H02"], "2024-03-04 12:00:00"),
    (203, H["H03"], "2024-03-05 10:00:00"), (204, H["H04"], "2024-03-06 00:00:00"),
    (205, H["H05"], "2024-03-07 08:00:00"), (206, H["H06"], "2024-03-07 09:00:00"),
    (207, H["H07"], "2024-03-08 10:00:00"), (208, H["H08"], "2024-03-08 12:00:00"),
    (209, H["H09"], "2024-03-09 10:00:00"), (210, H["H10"], "2024-03-09 11:00:00"),
    (211, H["H11"], "2024-03-09 12:00:00"), (212, H["H12"], "2024-03-10 09:00:00"),
    (213, H["H13"], "2024-03-10 10:00:00"), (214, H["H14"], "2024-03-06 15:00:00"),
    (215, H["H15"], "2024-03-07 14:00:00"), (216, H["H16"], "2024-11-02 12:00:00"),
    (217, H["H17"], "2024-10-28 00:00:00"), (218, H["H18"], "2024-11-03 23:59:59"),
    (219, H["H19"], "2024-11-04 09:00:00"), (220, H["H20"], "2024-11-11 09:00:00"),
    (221, H["H21"], "2024-11-05 10:00:00"),
]
ANON_LINKS = [
    ("anon-H08", H["H08"]), ("dev-H08-phone", H["H08"]), ("dev-H08-web", H["H08"]),  # one user, 3 ids
    ("anon-Case", H["H09"]), ("anon-case", H["H10"]),         # differ only by case: two different devices
    ("anon-shared", H["H15"]), ("anon-shared", H["H05"]),     # one id linked to two users -> ambiguous
]

APP = [
    ("h-0101", H["H01"], "sign_up", "2024-03-04T10:00:00Z", LOAD_1),
    ("h-0102", H["H01"], "email_confirmed", "2024-03-04T10:05:00Z", LOAD_1),
    ("h-0201", H["H02"], "sign_up", "2024-03-04T12:00:00Z", LOAD_1),
    ("h-0202", H["H02"], "email_confirmed", "2024-03-04T12:05:00Z", LOAD_1),
    ("h-0301", H["H03"], "sign_up", "2024-03-05T10:00:00Z", LOAD_1),
    ("h-0401", H["H04"], "sign_up", "2024-03-06T00:00:00Z", LOAD_1),
    ("h-0402", H["H04"], "email_confirmed", "2024-03-06T00:05:00Z", LOAD_1),
    ("h-0501", H["H05"], "sign_up", "2024-03-07T08:00:00Z", LOAD_1),
    ("h-0601", H["H06"], "sign_up", "2024-03-07T09:00:00Z", LOAD_1),
    ("h-0601", H["H06"], "sign_up", "2024-03-07T09:00:00Z", LOAD_1),              # exact replay
    ("h-0602", H["H06"], "sign_up", "2024-03-07T09:00:05Z", LOAD_1),              # client retry
    ("h-0603", H["H06"], "email_confirmed", "2024-03-07T09:10:00Z", LOAD_1),
    ("h-0701", "anon-H07-lost", "sign_up", "2024-03-08T10:00:00Z", LOAD_1),       # no link -> unmatched
    ("h-0702", H["H07"], "email_confirmed", "2024-03-08T10:10:00Z", LOAD_1),
    ("h-0801", " anon-H08 ", "sign_up", "2024-03-08T12:00:00Z", LOAD_1),
    ("h-0802", "dev-H08-phone", "email_confirmed", "2024-03-08T12:05:00Z", LOAD_1),
    ("h-0803", "dev-H08-web", "email_confirmed", "2024-03-08T12:30:00Z", LOAD_1),
    ("h-0901", "anon-Case", "sign_up", "2024-03-09T10:00:00Z", LOAD_1),
    ("h-1001", "anon-case", "sign_up", "2024-03-09T11:00:00Z", LOAD_1),
    ("h-1002", H["H10"], "email_confirmed", "2024-03-09T11:05:00Z", LOAD_1),
    ("h-1101", H["H11"], "sign_up", "2024-03-09T12:00:00Z", LOAD_1),
    ("h-1102", H["H11"], "email_confirmed", "2024-03-09T12:05:00Z", LOAD_1),
    ("h-1201", H["H12"], "sign_up", "2024-03-10T09:00:00Z", LOAD_1),
    ("h-1202", H["H12"], "email_confirmed", "2024-03-10T09:05:00Z", LOAD_1),
    ("h-1301", H["H13"], "sign_up", "2024-03-10T10:00:00Z", LOAD_1),
    ("h-1302", H["H13"], "email_confirmed", "2024-03-10T10:05:00Z", LOAD_1),
    ("h-1401", H["H14"], "sign_up", "2024-03-06T15:00:00Z", LOAD_1),
    ("h-1402", H["H14"], "email_confirmed", "2024-03-06T15:05:00Z", LOAD_1),
    ("h-1501", "anon-shared", "sign_up", "2024-03-07T14:00:00Z", LOAD_1),         # ambiguous -> quarantine
    ("h-1502", H["H15"], "email_confirmed", "2024-03-07T14:10:00Z", LOAD_1),
    ("h-1601", H["H16"], "sign_up", "2024-11-02T12:00:00Z", LOAD_1),
    ("h-1602", H["H16"], "email_confirmed", "2024-11-02T12:05:00Z", LOAD_1),
    ("h-1701", H["H17"], "sign_up", "2024-10-28T00:00:00Z", LOAD_1),
    ("h-1702", H["H17"], "email_confirmed", "2024-10-28T00:05:00Z", LOAD_1),
    ("h-1801", H["H18"], "sign_up", "2024-11-03T23:59:59Z", LOAD_1),
    ("h-1901", H["H19"], "sign_up", "2024-11-04T09:00:00Z", LOAD_1),
    ("h-1902", H["H19"], "email_confirmed", "2024-11-04T09:05:00Z", LOAD_1),
    ("h-2001", H["H20"], "sign_up", "2024-11-11T09:00:00Z", LOAD_1),
    ("h-2101", H["H21"], "sign_up", "2024-11-05T10:00:00Z", LOAD_1),
    ("h-2102", H["H21"], "email_confirmed", "2024-11-05T10:05:00Z", LOAD_1),
]

KYC = [
    ("hk-0101", H["H01"].upper(), "approved", epoch("2024-03-04 11:00:00"), LOAD_1),
    ("hk-0201", H["H02"], "approved", epoch("2024-03-04 13:00:00"), LOAD_1),
    ("hk-0401", H["H04"], "approved", epoch("2024-03-06 01:00:00"), LOAD_1),
    ("hk-0601", H["H06"], "approved", epoch("2024-03-07 10:00:00"), LOAD_1),
    ("hk-0701", H["H07"], "approved", epoch("2024-03-08 11:00:00"), LOAD_1),
    ("hk-0801", f" {H['H08'].upper()} ", "approved", epoch("2024-03-08 13:00:00"), LOAD_1),
    ("hk-1001", H["H10"], "approved", epoch("2024-03-09 11:30:00"), LOAD_1),
    ("hk-1101", H["H11"], "approved", epoch("2024-03-09 13:00:00"), LOAD_1),
    ("hk-1201", H["H12"], "approved", epoch("2024-03-10 10:00:00"), LOAD_1),
    # H13: no KYC event at all, but trades later
    ("hk-1401", H["H14"], "approved", epoch("2024-03-06 16:00:00"), LOAD_1),
    ("hk-1601", H["H16"], "approved", epoch("2024-11-02 13:00:00"), LOAD_1),
    ("hk-1701", H["H17"], "approved", epoch("2024-10-28 01:00:00"), LOAD_1),
    ("hk-1901", H["H19"], "approved", epoch("2024-11-04 10:00:00"), LOAD_1),
    ("hk-2101", H["H21"], "approved", "garbage", LOAD_1),       # bad epoch -> quarantine, build must not fail
]

LEDGER = [
    ("ht-0101", 201, "trade", "completed", "2024-03-06 10:00:00", "UTC", LOAD_1),     # day 2
    ("ht-0102", 201, "deposit", "completed", "2024-03-12 10:00:00", "UTC", LOAD_1),   # day 8: outside
    ("ht-0201", 202, "trade", "completed", "2024-03-05 09:00:00", "UTC", LOAD_1),     # trade before deposit
    ("ht-0202", 202, "deposit", "completed", "2024-03-05 10:00:00", "UTC", LOAD_1),
    ("ht-0301", 203, "trade", "completed", "2024-03-05 09:00:00", "UTC", LOAD_1),     # 1h before sign-up
    ("ht-0401", 204, "deposit", "completed", "2024-03-13 00:00:00", "UTC", LOAD_1),   # exactly +7d
    ("ht-0402", 204, "trade", "completed", "2024-03-12 23:59:59", "UTC", LOAD_1),     # +7d - 1s
    ("ht-0601", 206, "deposit", "completed", "2024-03-08 10:00:00", "UTC", LOAD_1),
    ("ht-0701", 207, "deposit", "completed", "2024-03-09 10:00:00", "UTC", LOAD_1),
    ("ht-0801", 208, "deposit", "completed", "2024-03-09 12:00:00", "UTC", LOAD_1),
    ("ht-1001", 210, "trade", "completed", "2024-03-10 11:00:00", "UTC", LOAD_1),
    ("ht-1101", 211, "deposit", "completed", "2024-03-10 02:30:00", NY, LOAD_1),      # DST gap: no such time
    ("ht-1102", 211, "trade", "completed", "2024-03-10 03:30:00", NY, LOAD_1),        # EDT -> 07:30 UTC
    ("ht-1301", 213, "trade", "completed", "2024-03-11 10:00:00", "UTC", LOAD_1),     # card buy, no KYC event
    ("ht-1401", 214, "trade", "completed", "2024-03-07 15:00:00", "UTC", LOAD_1),     # card buy, no deposit
    ("ht-1601", 216, "deposit", "completed", "2024-11-03 01:30:00", NY, LOAD_1),      # DST overlap: twice
    ("ht-1701", 217, "deposit", "completed", "2024-10-29 00:00:00", "UTC", LOAD_1),
    ("ht-1901", 219, "trade", "completed", "2024-11-05 09:00:00", "UTC", LOAD_1),
    ("ht-2001", 220, "deposit", "completed", "2024-11-12 09:00:00", "UTC", LOAD_1),
]
LATE_LEDGER = [("ht-1201", 212, "deposit", "completed", "2024-03-11 10:00:00", "UTC", LOAD_LATE)]

# ------------------------------------------------------------------------------------------------------
# Hand-worked expectations (after the late batch). Columns:
FUNNEL_COLS = ("signup_at", "signup_event_missing", "email_verified_state", "identity_verified_state",
               "first_deposit_state", "first_trade_state", "first_deposit_at", "first_trade_at",
               "first_funding_at", "reached_funding", "has_missing_step", "is_out_of_order",
               "has_dst_ambiguous_ts")
O, I, R, N = "observed", "inferred", "rejected_event", "not_reached"
EXPECTED_FUNNEL = {
    "H01": ("2024-03-04 10:00:00", 0, O, O, O, O, "2024-03-12 10:00:00", "2024-03-06 10:00:00",
            "2024-03-06 10:00:00", 1, 0, 0, 0),
    "H02": ("2024-03-04 12:00:00", 0, O, O, O, O, "2024-03-05 10:00:00", "2024-03-05 09:00:00",
            "2024-03-05 09:00:00", 1, 0, 0, 0),
    "H03": ("2024-03-05 10:00:00", 0, I, I, N, O, None, "2024-03-05 09:00:00",
            "2024-03-05 09:00:00", 1, 1, 1, 0),
    "H04": ("2024-03-06 00:00:00", 0, O, O, O, O, "2024-03-13 00:00:00", "2024-03-12 23:59:59",
            "2024-03-12 23:59:59", 1, 0, 0, 0),
    "H05": ("2024-03-07 08:00:00", 0, N, N, N, N, None, None, None, 0, 0, 0, 0),
    "H06": ("2024-03-07 09:00:00", 0, O, O, O, N, "2024-03-08 10:00:00", None,
            "2024-03-08 10:00:00", 1, 0, 0, 0),
    "H07": ("2024-03-08 10:00:00", 1, O, O, O, N, "2024-03-09 10:00:00", None,
            "2024-03-09 10:00:00", 1, 0, 0, 0),
    "H08": ("2024-03-08 12:00:00", 0, O, O, O, N, "2024-03-09 12:00:00", None,
            "2024-03-09 12:00:00", 1, 0, 0, 0),
    "H09": ("2024-03-09 10:00:00", 0, N, N, N, N, None, None, None, 0, 0, 0, 0),
    "H10": ("2024-03-09 11:00:00", 0, O, O, N, O, None, "2024-03-10 11:00:00",
            "2024-03-10 11:00:00", 1, 0, 0, 0),
    "H11": ("2024-03-09 12:00:00", 0, O, O, R, O, None, "2024-03-10 07:30:00",
            "2024-03-10 07:30:00", 1, 0, 0, 0),
    "H12": ("2024-03-10 09:00:00", 0, O, O, O, N, "2024-03-11 10:00:00", None,
            "2024-03-11 10:00:00", 1, 0, 0, 0),
    "H13": ("2024-03-10 10:00:00", 0, O, I, N, O, None, "2024-03-11 10:00:00",
            "2024-03-11 10:00:00", 1, 1, 0, 0),
    "H14": ("2024-03-06 15:00:00", 0, O, O, N, O, None, "2024-03-07 15:00:00",
            "2024-03-07 15:00:00", 1, 0, 0, 0),
    "H15": ("2024-03-07 14:00:00", 1, O, N, N, N, None, None, None, 0, 0, 0, 0),
    "H16": ("2024-11-02 12:00:00", 0, O, O, O, N, "2024-11-03 05:30:00", None,
            "2024-11-03 05:30:00", 1, 0, 0, 1),
    "H17": ("2024-10-28 00:00:00", 0, O, O, O, N, "2024-10-29 00:00:00", None,
            "2024-10-29 00:00:00", 1, 0, 0, 0),
    "H18": ("2024-11-03 23:59:59", 0, N, N, N, N, None, None, None, 0, 0, 0, 0),
    "H19": ("2024-11-04 09:00:00", 0, O, O, N, O, None, "2024-11-05 09:00:00",
            "2024-11-05 09:00:00", 1, 0, 0, 0),
    "H20": ("2024-11-11 09:00:00", 0, I, I, O, N, "2024-11-12 09:00:00", None,
            "2024-11-12 09:00:00", 1, 1, 0, 0),
    "H21": ("2024-11-05 10:00:00", 0, O, R, N, N, None, None, None, 0, 0, 0, 0),
}
# H12 before its late deposit arrives
EXPECTED_H12_BEFORE_LATE = ("2024-03-10 09:00:00", 0, O, O, N, N, None, None, None, 0, 0, 0, 0)

# (cohort_week, signups, observed_deposit_7d, funding_7d, trade_7d); week D (2024-11-11) is immature.
#   A: 15 users H01-H15. deposits in window: H02 H06 H07 H08 H12 = 5
#      trades in window: H01 H02 H04 H10 H11 H13 H14 = 7; funding = union = 11
#      not counted: H01 deposit (day 8), H03 trade (before sign-up), H04 deposit (exactly +7d),
#      H11 deposit (nonexistent local time, quarantined)
#   B: H16 H17 H18; deposits H16 (DST-overlap, 05:30 UTC) H17 = 2; no trades
#   C: H19 H21; H19 trade = 1
EXPECTED_PM = [("2024-03-04", 15, 5, 11, 7),
               ("2024-10-28", 3, 2, 2, 0),
               ("2024-11-04", 2, 0, 1, 1)]
EXPECTED_PM_BEFORE_LATE = [("2024-03-04", 15, 4, 10, 7),       # H12's deposit not loaded yet
                           ("2024-10-28", 3, 2, 2, 0),
                           ("2024-11-04", 2, 0, 1, 1)]

EXPECTED_QUARANTINE = {("unmatched_ref", "app", "anon-H07-lost", "h-0701"),
                       ("ambiguous_link", "app", "anon-shared", "h-1501"),
                       ("nonexistent_local_time", "ledger", "ledger_account:211", "ht-1101"),
                       ("unparseable_ts", "kyc", H["H21"], "hk-2101")}


def only(*names):
    """Raw rows of just these users (ids encode the user: h-0101, hk-0101, ht-0101, account 201)."""
    nn = {name[1:] for name in names}
    return dict(app=[r for r in APP if r[0][2:4] in nn], kyc=[r for r in KYC if r[0][3:5] in nn],
                ledger=[r for r in LEDGER if r[0][3:5] in nn],
                accounts=[a for a in ACCOUNTS if str(a[0])[1:] in nn],
                links=[lk for lk in ANON_LINKS if lk[1] in {H[n] for n in names}])


def load(conn, late=True, app=APP, kyc=KYC, ledger=LEDGER, accounts=ACCOUNTS, links=ANON_LINKS):
    insert(conn, app, kyc, ledger, accounts, links)
    if late:
        insert(conn, ledger=LATE_LEDGER)
