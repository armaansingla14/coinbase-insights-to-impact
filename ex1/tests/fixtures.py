"""Deliberately messy raw data for the onboarding funnel model, plus the expected output per user.

Two parts:
  * HAND-CRAFTED users (U01..U15): one per edge case, with expected values worked out by hand.
  * CLEAN_N generated users: realistic volume so rate-based DQ checks have a meaningful denominator; their
    expected output is computed independently in Python (not by SQL) and compared row by row.
"""
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NOW = "2024-03-12 12:00:00"          # pinned "current time" for freshness / future-timestamp checks
LOAD_1 = "2024-03-12 11:00:00"       # first batch load time
LOAD_LATE = "2024-03-14 02:00:00"    # late-arriving batch

CLEAN_N = 400


def uid(n):
    return f"a{n:07d}-0000-4000-8000-{n:012d}"


U = {k: uid(i) for i, k in enumerate(
    ["U01", "U02", "U03", "U04", "U05", "U07", "U07B", "U08", "U09", "U10", "U11", "U12", "U13", "U14", "U15"], 1)}


def epoch(s):
    return calendar.timegm(datetime.fromisoformat(s).timetuple())


def kyc_ref(u):          # KYC system: upper-case, padded
    return f"  {u.upper()} "


# ---------------------------------------------------------------------------------------------------------
# Raw rows. app: (event_id, user_ref, event_name, ts_raw, loaded_at)
#           kyc: (event_id, user_ref, status, ts_epoch_s, loaded_at)
#           ledger: (txn_id, account_id, txn_type, status, ts_local, tz_name, loaded_at)
#           accounts: (account_id, user_uuid, created_at)      anon_links: (anonymous_id, user_uuid)
# ---------------------------------------------------------------------------------------------------------
ACCOUNTS = [
    (101, U["U01"].upper(), "2024-03-01 09:59:00"),   # upper-case UUID in the system of record
    (102, U["U02"], "2024-03-02 07:59:00"),
    (103, U["U03"], "2024-03-03 11:59:00"),
    (104, U["U04"], "2024-03-04 09:00:00"),
    (105, U["U05"], "2024-03-05 06:00:00"),
    (107, U["U07"], "2024-03-07 08:00:00"),
    (117, U["U07B"], "2024-03-07 08:30:00"),
    (108, U["U08"], "2024-03-08 07:59:00"),
    (109, U["U09"], "2024-03-09 10:00:00"),
    (110, U["U10"], "2024-03-10 23:30:00"),
    (111, U["U11"], "2024-03-11 00:00:00"),
    (112, U["U12"], "2024-03-12 09:59:00"),
    (114, U["U14"], "2024-03-04 09:59:00"),
    (115, U["U15"], "2024-03-12 06:00:00"),
    # U13 deliberately has NO account row
]
ANON_LINKS = [
    ("anon-u01", U["U01"]),
    ("Anon-U09", U["U09"]),            # link table itself not normalised
    ("anon-u13", U["U13"]),            # links to a user that has no account
]

APP = [
    # U01 happy path: anon id (mixed case, padded) for sign-up; +05:30 offset for email
    ("e-0101", " ANON-U01 ", "sign_up", "2024-03-01T10:00:00Z", LOAD_1),
    ("e-0102", f" {U['U01'].upper()}", "email_confirmed", "2024-03-01T15:35:00+05:30", LOAD_1),
    ("e-0103", " ANON-U01 ", "page_view", "2024-03-01T10:01:00Z", LOAD_1),       # irrelevant event name
    # U02 retries / replays
    ("e-0201", U["U02"], "sign_up", "2024-03-02T08:00:00Z", LOAD_1),
    ("e-0201", U["U02"], "sign_up", "2024-03-02T08:00:00Z", LOAD_1),               # exact replay
    ("e-0202", U["U02"], "sign_up", "2024-03-02T08:00:05Z", LOAD_1),               # client retry
    ("e-0203", U["U02"], "email_confirmed", "2024-03-02T08:10:00+00:00", LOAD_1),
    ("e-0204", U["U02"], "email_confirmed", "2024-03-02T08:20:00Z", LOAD_1),
    # U03 missing KYC approval (only a rejection) but a deposit exists
    ("e-0301", U["U03"], "sign_up", "2024-03-03T12:00:00Z", LOAD_1),
    ("e-0302", U["U03"], "email_confirmed", "2024-03-03T12:05:00Z", LOAD_1),
    # U04 out of order: email verified 5 minutes before sign-up
    ("e-0401", U["U04"], "sign_up", "2024-03-04T10:00:00Z", LOAD_1),
    ("e-0402", U["U04"], "email_confirmed", "2024-03-04T09:55:00Z", LOAD_1),
    # U14 out of order ACROSS A GAP: no email event, KYC before sign-up
    ("e-1401", U["U14"], "sign_up", "2024-03-04T10:00:00Z", LOAD_1),
    # U05 no sign-up event at all -> fallback to accounts.created_at
    ("e-0502", U["U05"], "email_confirmed", "2024-03-05T06:10:00Z", LOAD_1),
    # U07 failed deposit, then completed deposit
    ("e-0701", U["U07"], "sign_up", "2024-03-07T08:00:30Z", LOAD_1),
    ("e-0702", U["U07"], "email_confirmed", "2024-03-07T08:10:00Z", LOAD_1),
    # U07B only a failed deposit
    ("e-7b01", U["U07B"], "sign_up", "2024-03-07T08:30:00Z", LOAD_1),
    ("e-7b02", U["U07B"], "email_confirmed", "2024-03-07T08:40:00Z", LOAD_1),
    # U08 late-arriving (KYC + an earlier trade arrive in LATE batch, see LATE_* below)
    ("e-0801", U["U08"], "sign_up", "2024-03-08T08:00:00Z", LOAD_1),
    ("e-0802", U["U08"], "email_confirmed", "2024-03-08T08:30:00Z", LOAD_1),
    # U09 offset-less timestamp (assumed UTC) via anon id
    ("e-0901", "anon-u09", "sign_up", "2024-03-09 10:00:00", LOAD_1),
    # U11 basic-format offset (+0530) and a negative offset crossing midnight
    ("e-1101", U["U11"], "sign_up", "2024-03-11T05:30:00+0530", LOAD_1),
    ("e-1102", U["U11"], "email_confirmed", "2024-03-10T16:05:00-08:00", LOAD_1),
    # U12 unparseable email timestamp -> quarantine
    ("e-1201", U["U12"], "sign_up", "2024-03-12T10:00:00Z", LOAD_1),
    ("e-1202", U["U12"], "email_confirmed", "not-a-date", LOAD_1),
    # U13 resolves through anon link, but no sign-up event and no account -> no anchor
    ("e-1302", "anon-u13", "email_confirmed", "2024-03-06T12:00:00Z", LOAD_1),
    # U06 unmatched anonymous id -> quarantine
    ("e-0601", "anon-orphan-1", "sign_up", "2024-03-06T09:00:00Z", LOAD_1),
    # U15 freshness user: activity shortly before NOW in every source
    ("e-1501", U["U15"], "sign_up", "2024-03-12T06:00:00Z", LOAD_1),
    ("e-1502", U["U15"], "email_confirmed", "2024-03-12T06:05:00Z", LOAD_1),
]

KYC = [
    ("k-0101", kyc_ref(U["U01"]), "approved", epoch("2024-03-01 11:00:00"), LOAD_1),
    ("k-0200", kyc_ref(U["U02"]), "rejected", epoch("2024-03-02 08:30:00"), LOAD_1),
    ("k-0201", kyc_ref(U["U02"]), "approved", epoch("2024-03-02 09:30:00"), LOAD_1),
    ("k-0202", kyc_ref(U["U02"]), "approved", epoch("2024-03-02 09:00:00"), LOAD_1),   # arrives out of order
    ("k-0300", kyc_ref(U["U03"]), "rejected", epoch("2024-03-03 12:30:00"), LOAD_1),
    ("k-0401", kyc_ref(U["U04"]), "approved", epoch("2024-03-04 11:00:00"), LOAD_1),
    ("k-1401", kyc_ref(U["U14"]), "approved", epoch("2024-03-04 09:00:00"), LOAD_1),
    ("k-0501", kyc_ref(U["U05"]), "approved", str(epoch("2024-03-05 06:30:00")), LOAD_1),  # epoch as a string
    ("k-0701", kyc_ref(U["U07"]), "approved", epoch("2024-03-07 08:20:00"), LOAD_1),
    ("k-7b01", kyc_ref(U["U07B"]), "approved", epoch("2024-03-07 08:50:00"), LOAD_1),
    ("k-1501", kyc_ref(U["U15"]), "approved", epoch("2024-03-12 07:00:00"), LOAD_1),
]

LEDGER = [
    # U01: America/New_York, before (EST, -5) and after (EDT, -4) the 2024-03-10 DST switch
    ("t-0101", 101, "deposit", "completed", "2024-03-01 07:30:00", "America/New_York", LOAD_1),
    ("t-0102", 101, "trade", "completed", "2024-03-11 09:00:00", "America/New_York", LOAD_1),
    # U02: two deposits with identical timestamps -> deterministic tie-break on source_event_id
    ("t-0202", 102, "deposit", "completed", "2024-03-02 10:00:00", "UTC", LOAD_1),
    ("t-0201", 102, "deposit", "completed", "2024-03-02 10:00:00", "UTC", LOAD_1),
    ("t-0203", 102, "withdrawal", "completed", "2024-03-02 11:00:00", "UTC", LOAD_1),  # not a funnel step
    # U03: deposit in Singapore time (+8)
    ("t-0301", 103, "deposit", "completed", "2024-03-03 21:00:00", "Asia/Singapore", LOAD_1),
    # U05
    ("t-0501", 105, "deposit", "completed", "2024-03-05 06:45:00", "UTC", LOAD_1),
    ("t-0502", 105, "trade", "completed", "2024-03-05 07:00:00", "UTC", LOAD_1),
    # U06: unknown account -> quarantine
    ("t-0601", 999, "deposit", "completed", "2024-03-06 10:00:00", "UTC", LOAD_1),
    # U07: failed then completed deposit; U07B only failed
    ("t-0701", 107, "deposit", "failed", "2024-03-07 09:00:00", "UTC", LOAD_1),
    ("t-0702", 107, "deposit", "completed", "2024-03-07 10:00:00", "UTC", LOAD_1),
    ("t-7b01", 117, "deposit", "failed", "2024-03-07 09:00:00", "UTC", LOAD_1),
    # U08: deposit + trade at 12:00 (a trade at 11:00 arrives late)
    ("t-0801", 108, "deposit", "completed", "2024-03-08 10:00:00", "Europe/London", LOAD_1),
    ("t-0802", 108, "trade", "completed", "2024-03-08 12:00:00", "Europe/London", LOAD_1),
    # U09: tz_name missing -> assumed UTC
    ("t-0901", 109, "deposit", "completed", "2024-03-09 12:00:00", None, LOAD_1),
    # U15 freshness
    ("t-1501", 115, "deposit", "completed", "2024-03-12 03:30:00", "America/New_York", LOAD_1),  # 07:30 UTC (EDT)
    ("t-1502", 115, "trade", "completed", "2024-03-12 08:00:00", "UTC", LOAD_1),
]

LATE_KYC = [("k-0801", kyc_ref(U["U08"]), "approved", epoch("2024-03-08 09:00:00"), LOAD_LATE)]
LATE_LEDGER = [("t-0803", 108, "trade", "completed", "2024-03-08 11:00:00", "Europe/London", LOAD_LATE)]

# Expected funnel rows for hand-crafted users (after the FIRST load). Booleans as 0/1.
COLS = ("signup_at", "signup_event_missing", "signup_cohort_date", "email_verified_at", "identity_verified_at",
        "first_deposit_at", "first_trade_at", "reached_email_verified", "reached_identity_verified",
        "reached_first_deposit", "reached_first_trade", "has_missing_step", "is_out_of_order", "has_assumed_tz",
        "minutes_signup_to_first_trade")


def row(signup, miss_signup, cohort, email, idv, dep, trade, r_e, r_i, r_d, r_t, missing, ooo, tz, mins):
    return dict(zip(COLS, (signup, miss_signup, cohort, email, idv, dep, trade, r_e, r_i, r_d, r_t,
                           missing, ooo, tz, mins)))


EXPECTED = {
    "U01": row("2024-03-01 10:00:00", 0, "2024-03-01", "2024-03-01 10:05:00", "2024-03-01 11:00:00",
               "2024-03-01 12:30:00", "2024-03-11 13:00:00", 1, 1, 1, 1, 0, 0, 0, 10 * 1440 + 180),
    "U02": row("2024-03-02 08:00:00", 0, "2024-03-02", "2024-03-02 08:10:00", "2024-03-02 09:00:00",
               "2024-03-02 10:00:00", None, 1, 1, 1, 0, 0, 0, 0, None),
    "U03": row("2024-03-03 12:00:00", 0, "2024-03-03", "2024-03-03 12:05:00", None,
               "2024-03-03 13:00:00", None, 1, 1, 1, 0, 1, 0, 0, None),
    "U04": row("2024-03-04 10:00:00", 0, "2024-03-04", "2024-03-04 09:55:00", "2024-03-04 11:00:00",
               None, None, 1, 1, 0, 0, 0, 1, 0, None),
    "U05": row("2024-03-05 06:00:00", 1, "2024-03-05", "2024-03-05 06:10:00", "2024-03-05 06:30:00",
               "2024-03-05 06:45:00", "2024-03-05 07:00:00", 1, 1, 1, 1, 0, 0, 0, None),
    "U07": row("2024-03-07 08:00:30", 0, "2024-03-07", "2024-03-07 08:10:00", "2024-03-07 08:20:00",
               "2024-03-07 10:00:00", None, 1, 1, 1, 0, 0, 0, 0, None),
    "U07B": row("2024-03-07 08:30:00", 0, "2024-03-07", "2024-03-07 08:40:00", "2024-03-07 08:50:00",
                None, None, 1, 1, 0, 0, 0, 0, 0, None),
    "U08": row("2024-03-08 08:00:00", 0, "2024-03-08", "2024-03-08 08:30:00", None,
               "2024-03-08 10:00:00", "2024-03-08 12:00:00", 1, 1, 1, 1, 1, 0, 0, 240),
    "U09": row("2024-03-09 10:00:00", 0, "2024-03-09", None, None,
               "2024-03-09 12:00:00", None, 1, 1, 1, 0, 1, 0, 1, None),
    "U10": row("2024-03-10 23:30:00", 1, "2024-03-10", None, None, None, None, 0, 0, 0, 0, 0, 0, 0, None),
    "U11": row("2024-03-11 00:00:00", 0, "2024-03-11", "2024-03-11 00:05:00", None, None, None,
               1, 0, 0, 0, 0, 0, 0, None),
    "U12": row("2024-03-12 10:00:00", 0, "2024-03-12", None, None, None, None, 0, 0, 0, 0, 0, 0, 0, None),
    "U14": row("2024-03-04 10:00:00", 0, "2024-03-04", None, "2024-03-04 09:00:00", None, None,
               1, 1, 0, 0, 1, 1, 0, None),
    "U15": row("2024-03-12 06:00:00", 0, "2024-03-12", "2024-03-12 06:05:00", "2024-03-12 07:00:00",
               "2024-03-12 07:30:00", "2024-03-12 08:00:00", 1, 1, 1, 1, 0, 0, 0, 120),
}
# U08 after the late batch: KYC gap filled, earlier trade replaces the 12:00 one
EXPECTED_U08_AFTER_LATE = dict(EXPECTED["U08"], identity_verified_at="2024-03-08 09:00:00",
                               first_trade_at="2024-03-08 11:00:00", has_missing_step=0,
                               minutes_signup_to_first_trade=180)

EXPECTED_QUARANTINE = {("unmatched_ref", "app", "anon-orphan-1", "e-0601"),
                       ("unmatched_ref", "ledger", "ledger_account:999", "t-0601"),
                       ("unparseable_ts", "app", U["U12"], "e-1202")}
EXPECTED_N_SOURCE_EVENTS_U02 = {"signup": 3, "email_verified": 2, "identity_verified": 2, "first_deposit": 2}


# ---------------------------------------------------------------------------------------------------------
# Clean generated users (still multi-format) with an independent Python expectation
# ---------------------------------------------------------------------------------------------------------
TZS = ["America/New_York", "Europe/London", "Asia/Singapore", "UTC"]
FMT = "%Y-%m-%d %H:%M:%S"


def clean_users():
    app, kyc, ledger, accounts, links, expected = [], [], [], [], [], {}
    for i in range(CLEAN_N):
        u = f"c{i:07d}-0000-4000-8000-{i:012d}"
        acct = 10_000 + i
        signup = datetime(2024, 3, 1, 1, 0) + timedelta(days=i % 11, hours=i % 19, minutes=(7 * i) % 60)
        depth = 1 + i % 5                                   # 1..5 steps reached
        steps = [signup + timedelta(minutes=m) for m in (0, 4 + i % 9, 30 + i % 40, 90 + i % 100, 200 + i % 120)]
        accounts.append((acct, u, signup.strftime(FMT)))
        # sign-up: via anon id for even users, via (sometimes upper-case) uuid otherwise; alternating offsets
        if i % 2 == 0:
            links.append((f"anon-c{i}", u))
            ref = f"ANON-C{i}" if i % 4 == 0 else f" anon-c{i} "
        else:
            ref = u.upper() if i % 3 == 0 else u
        if i % 3 == 0:
            ts = (signup + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S") + "+01:00"
        else:
            ts = signup.strftime("%Y-%m-%dT%H:%M:%SZ")
        app.append((f"ce-{i}-1", ref, "sign_up", ts, LOAD_1))
        if depth >= 2:
            app.append((f"ce-{i}-2", u, "email_confirmed",
                        steps[1].replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Kolkata")).isoformat(),
                        LOAD_1))
        if depth >= 3:
            kyc.append((f"ck-{i}", kyc_ref(u), "approved", epoch(steps[2].strftime(FMT)), LOAD_1))
        for k, kind in ((3, "deposit"), (4, "trade")):
            if depth > k:
                tz = TZS[i % 4]
                local = steps[k].replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).strftime(FMT)
                ledger.append((f"ct-{i}-{k}", acct, kind, "completed", local, tz, LOAD_1))
        at = [s.strftime(FMT) if n < depth else None for n, s in enumerate(steps)]
        expected[u] = row(at[0], 0, at[0][:10], at[1], at[2], at[3], at[4],
                          int(depth >= 2), int(depth >= 3), int(depth >= 4), int(depth >= 5), 0, 0, 0,
                          200 + i % 120 if depth == 5 else None)
    return app, kyc, ledger, accounts, links, expected


def insert(conn, app=(), kyc=(), ledger=(), accounts=(), links=()):
    conn.executemany("INSERT INTO raw_app_events VALUES (?,?,?,?,?)", app)
    conn.executemany("INSERT INTO raw_kyc_events VALUES (?,?,?,?,?)", kyc)
    conn.executemany("INSERT INTO raw_ledger_events VALUES (?,?,?,?,?,?,?)", ledger)
    conn.executemany("INSERT INTO core_accounts VALUES (?,?,?)", accounts)
    conn.executemany("INSERT INTO core_anon_links VALUES (?,?)", links)
    conn.commit()


def load_fixtures(conn, with_clean=True):
    """Load the first batch. Returns expected rows keyed by canonical user_uuid."""
    insert(conn, APP, KYC, LEDGER, ACCOUNTS, ANON_LINKS)
    expected = {U[k]: v for k, v in EXPECTED.items()}
    if with_clean:
        app, kyc, ledger, accounts, links, exp = clean_users()
        insert(conn, app, kyc, ledger, accounts, links)
        expected.update(exp)
    return expected


def load_late_batch(conn):
    insert(conn, kyc=LATE_KYC, ledger=LATE_LEDGER)
