"""Generate synthetic signup extracts for Systems A, B, C with known, planted discrepancies.

Report day: 2026-09-24. Planted truth:
  * 50,000 real accounts created on the UTC day; 12,000 of them before 07:00 UTC.
  * 2,000 internal/test accounts (@coinbase-test.com) created the same UTC day.
  * 12,500 real accounts created 2026-09-25 00:00-07:00 UTC (still 2026-09-24 in Pacific time).
  * 5,500 of the post-07:00 real users never verified email.

System A: account-created table, UTC day, includes test accounts           -> 52,000 rows
System B: "verified signups", Pacific-time day, excludes test, IDs uppercased, naive local ts -> 45,000 rows
System C: event stream, UTC day, includes test, one ingest batch replayed  -> 57,000 rows (52,000 distinct)
"""
import csv
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data"  # optional output dir (used by tests)
OUT.mkdir(parents=True, exist_ok=True)

DAY = datetime(2026, 9, 24, tzinfo=timezone.utc)
PT_OFFSET = timedelta(hours=-7)  # PDT in September
COUNTRIES = ["US", "GB", "CA", "DE", "FR", "BR", "IN", "SG"]
PLATFORMS = ["ios", "android", "web"]


def ts_between(start_h, end_h, base=DAY):
    return base + timedelta(seconds=random.uniform(start_h * 3600, end_h * 3600))


users = []  # dicts: id, ts (aware UTC), email, verified, is_test
n = 0


def add(count, start_h, end_h, base=DAY, verified=True, is_test=False, verified_rate=None):
    global n
    for _ in range(count):
        n += 1
        uid = f"u{n:07d}"
        v = verified if verified_rate is None else random.random() < verified_rate
        domain = "coinbase-test.com" if is_test else random.choice(["gmail.com", "yahoo.com", "proton.me", "outlook.com"])
        users.append(dict(id=uid, ts=ts_between(start_h, end_h, base), email=f"{uid}@{domain}",
                          verified=v, is_test=is_test, country=random.choice(COUNTRIES),
                          platform=random.choice(PLATFORMS)))


add(12_000, 0, 7, verified_rate=0.9)                     # real, early UTC day (outside PT day)
add(32_500, 7, 24, verified=True)                        # real, verified, inside both days
add(5_500, 7, 24, verified=False)                        # real, never verified email
add(2_000, 0, 24, is_test=True, verified=True)           # internal / QA accounts
add(12_500, 0, 7, base=DAY + timedelta(days=1), verified=True)  # next UTC day, still PT day

utc_day = [u for u in users if DAY <= u["ts"] < DAY + timedelta(days=1)]
pt_start = DAY - PT_OFFSET  # 2026-09-24 00:00 PT == 07:00 UTC
pt_day = [u for u in users if pt_start <= u["ts"] < pt_start + timedelta(days=1)]

# System A: users table extract (ISO-8601 UTC strings)
with open(OUT / "system_a.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["user_id", "created_at", "email", "email_verified_at", "country", "platform"])
    for u in utc_day:
        va = (u["ts"] + timedelta(minutes=random.randint(1, 90))).isoformat().replace("+00:00", "Z") if u["verified"] else ""
        w.writerow([u["id"], u["ts"].isoformat().replace("+00:00", "Z"), u["email"], va, u["country"], u["platform"]])

# System B: growth reporting mart (verified only, excludes test, naive Pacific timestamps, uppercased IDs)
with open(OUT / "system_b.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["USER_ID", "signup_ts_local", "country"])
    for u in pt_day:
        if u["verified"] and not u["is_test"]:
            w.writerow([u["id"].upper(), (u["ts"] + PT_OFFSET).strftime("%Y-%m-%d %H:%M:%S"), u["country"]])

# System C: event stream (epoch millis); one ingest batch replayed -> duplicate rows
rows = []
for u in utc_day:
    ms = int(u["ts"].timestamp() * 1000)
    batch = f"b{int(u['ts'].timestamp() // 1800) % 48:02d}"  # 30-min micro-batches
    rows.append([u["id"], ms, u["email"], u["platform"], batch])
replayed = [r for r in rows if r[4] in {f"b{i}" for i in range(26, 32)}]  # 13:00-16:00 UTC batches re-ingested
dupes = random.sample(replayed, 4_000)
extra = [r[:4] + [r[4] + "_retry"] for r in dupes] + [r[:4] + [r[4] + "_retry2"] for r in random.sample(dupes, 1_000)]
rows += extra
random.shuffle(rows)
with open(OUT / "system_c.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["user_id", "event_ts_ms", "email", "platform", "ingest_batch_id"])
    w.writerows(rows)

for name in "abc":
    with open(OUT / f"system_{name}.csv") as f:
        print(f"system_{name}.csv: {sum(1 for _ in f) - 1:,} rows")
