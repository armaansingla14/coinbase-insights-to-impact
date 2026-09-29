"""Reconcile daily signup counts across Systems A, B, C (python3 stdlib only).

Usage: python3 reconcile_signups.py [--day 2026-09-24] [--data DIR]

Assumptions (to confirm with system owners):
  A.created_at = ISO-8601 with offset (UTC); B.signup_ts_local = naive America/Los_Angeles;
  C.event_ts_ms = epoch ms UTC. Canonical key = lower(trim(user_id)); when a user appears more
  than once, keep the earliest row. Internal/test accounts are identified by email domain.
Rows with a blank ID or unparseable timestamp are counted and reported, never silently dropped.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
try:
    from zoneinfo import ZoneInfo
    PT = ZoneInfo("America/Los_Angeles")  # handles PST/PDT switches
except Exception:  # no IANA tz database available (e.g. bare Windows install)
    PT = timezone(timedelta(hours=-7), "PDT (fixed fallback)")

REPORT_DAY = date(2026, 9, 24)
DATA = Path(__file__).parent / "data"
TEST_DOMAINS = ("coinbase-test.com", "example.com")
SOURCES = {  # system: (file, id column, timestamp column, timestamp format)
    "A": ("system_a.csv", "user_id", "created_at", "iso"),
    "B": ("system_b.csv", "USER_ID", "signup_ts_local", "naive_pt"),
    "C": ("system_c.csv", "user_id", "event_ts_ms", "epoch_ms"),
}


def parse_iso(v):
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("ISO timestamp without UTC offset")
    return dt.astimezone(UTC)


PARSERS = {
    "iso": parse_iso,
    "epoch_ms": lambda v: datetime.fromtimestamp(float(v) / 1000, tz=UTC),
    "naive_pt": lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PT).astimezone(UTC),
}


def day_windows(day):
    """[start, end) in UTC for the UTC calendar day and the Pacific calendar day."""
    def bounds(tz):
        return tuple(datetime.combine(d, time(), tz).astimezone(UTC) for d in (day, day + timedelta(days=1)))
    return bounds(UTC), bounds(PT)


def load(path, id_col, ts_col, ts_fmt):
    """Return (good rows with 'uid' and 'ts_utc' added, Counter of rejected-row reasons)."""
    good, bad = [], Counter()
    with open(path, newline="", encoding="utf-8-sig") as f:  # utf-8-sig strips a BOM
        reader = csv.DictReader(f)
        reader.fieldnames = [c.strip() for c in reader.fieldnames or []]
        missing = {id_col, ts_col} - set(reader.fieldnames)
        if missing:  # a schema change should stop the run, not show up as "blank IDs"
            raise ValueError(f"{path.name}: missing column(s) {sorted(missing)}")
        for r in reader:
            r["uid"] = (r.get(id_col) or "").strip().lower()
            try:
                r["ts_utc"] = PARSERS[ts_fmt]((r.get(ts_col) or "").strip())
            except (ValueError, OverflowError, OSError):
                r["ts_utc"] = None
            if not r["uid"]:
                bad["blank user_id"] += 1
            elif r["ts_utc"] is None:
                bad["unparseable timestamp"] += 1
            elif None in r or None in r.values():
                bad["wrong number of columns"] += 1
            else:
                good.append(r)
    return good, bad


def duplicates(rows):
    """user_id -> its rows, earliest first (ties: lowest batch id), for IDs seen more than once."""
    by_uid = defaultdict(list)
    for r in rows:
        by_uid[r["uid"]].append(r)
    return {u: sorted(rs, key=lambda r: (r["ts_utc"], r.get("ingest_batch_id", "")))
            for u, rs in by_uid.items() if len(rs) > 1}


def first_per_user(rows):
    keep = {}
    for r in rows:
        if r["uid"] not in keep or r["ts_utc"] < keep[r["uid"]]["ts_utc"]:
            keep[r["uid"]] = r
    return keep


def is_test(r):
    return (r.get("email") or "").rsplit("@", 1)[-1].strip().lower() in TEST_DOMAINS


def in_window(ts, window):
    return window[0] <= ts < window[1]


def overlap_label(pattern):
    return "+".join(pattern) + ("" if pattern == "ABC" else " only")


def why_not_in_b(r, pt_day):
    if is_test(r):
        return "test/internal account"
    if r["ts_utc"] < pt_day[0]:
        return f"created before {pt_day[0]:%H:%M} UTC (previous Pacific day)"
    if r["ts_utc"] >= pt_day[1]:
        return "created after the Pacific day ended"
    if not (r.get("email_verified_at") or "").strip():
        return "email not verified"
    return "UNEXPLAINED"


def why_not_in_a(r, utc_day, pt_day):
    if r["ts_utc"] >= utc_day[1] and in_window(r["ts_utc"], pt_day):
        return "created on next UTC day (still Pacific report day)"
    return "UNEXPLAINED"


def bridge(a, b, utc_day, pt_day):
    """a, b: uid -> row (deduped). Attribute every user in one system but not the other to a reason."""
    only_a = {u: why_not_in_b(a[u], pt_day) for u in a.keys() - b.keys()}
    only_b = {u: why_not_in_a(b[u], utc_day, pt_day) for u in b.keys() - a.keys()}
    return Counter(only_a.values()), Counter(only_b.values()), sorted(
        u for u, why in {**only_a, **only_b}.items() if why == "UNEXPLAINED")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day", type=date.fromisoformat, default=REPORT_DAY, help="report day, YYYY-MM-DD")
    ap.add_argument("--data", type=Path, default=DATA, help="directory holding system_[abc].csv")
    args = ap.parse_args(argv)
    utc_day, pt_day = day_windows(args.day)

    raw, bad, systems = {}, {}, {}
    for s, (file, id_col, ts_col, fmt) in SOURCES.items():
        systems[s], bad[s] = load(args.data / file, id_col, ts_col, fmt)
        raw[s] = len(systems[s]) + sum(bad[s].values())

    print("== 1. Duplicate user_ids, starting with System C (keep earliest row per user) ==")
    for s in ("C", "A", "B"):
        rows = systems[s]
        dups = duplicates(rows)
        if not dups:
            print(f"{s}: none")
            continue
        print(f"{s}: {len(dups):,} user_ids duplicated; {sum(len(rs) - 1 for rs in dups.values()):,} extra rows")
        print("   copies per id:", dict(sorted(Counter(len(rs) for rs in dups.values()).items())))
        differing = Counter(col for rs in dups.values() for col in rs[0]
                            if col not in ("uid", "ts_utc") and len({r[col] for r in rs}) > 1)
        print("   columns that differ within a group:", dict(differing) or "none (exact copies)")
        if "ingest_batch_id" in rows[0]:
            extra = Counter(r["ingest_batch_id"] for rs in dups.values() for r in rs[1:])
            print("   batches holding the extra rows:", extra.most_common(3))
            n_retry = sum(v for k, v in extra.items() if "_retry" in k)
            print(f"   extra rows in *_retry batches: {n_retry:,} of {sum(extra.values()):,} "
                  f"across {len(extra)} batches")

    print(f"\n== 2. Grain check for {args.day}: rows vs distinct users, and UTC time range ==")
    for s, rows in systems.items():
        ts = [r["ts_utc"] for r in rows]
        span = f"{min(ts):%m-%d %H:%M} -> {max(ts):%m-%d %H:%M} UTC" if ts else "no valid rows"
        print(f"{s}: {raw[s]:>7,} rows | {len({r['uid'] for r in rows}):>7,} distinct | {span}")
        if bad[s]:
            print(f"   rejected rows: {dict(bad[s])}")

    print("\n== 3. Overlap of distinct user_ids ==")
    ids = {s: {r["uid"] for r in rows} for s, rows in systems.items()}
    membership = Counter("".join(s for s in "ABC" if u in ids[s]) for u in set().union(*ids.values()))
    for pattern, n in sorted(membership.items(), key=lambda kv: -kv[1]):
        print(f"  in {overlap_label(pattern):<10}{n:>8,}")

    print("\n== 4. Explain every A-vs-B difference (anything left over is UNEXPLAINED) ==")
    a, b = first_per_user(systems["A"]), first_per_user(systems["B"])
    only_a, only_b, unexplained = bridge(a, b, utc_day, pt_day)
    steps = [("- A rejected rows", -sum(bad["A"].values())), ("- A duplicate rows", len(a) - len(systems["A"]))]
    steps += [(f"- {why}", -n) for why, n in only_a.most_common()]
    steps += [(f"+ {why}", n) for why, n in only_b.most_common()]
    steps += [("+ B duplicate rows", len(systems["B"]) - len(b)), ("+ B rejected rows", sum(bad["B"].values()))]
    print(f"{'A raw':<56}{raw['A']:>8,}")
    for label, n in steps:
        if n:
            print(f"  {label:<54}{n:>+8,}")
    bridged = raw["A"] + sum(n for _, n in steps)
    print(f"{'= bridged to B':<56}{bridged:>8,}   (B raw {raw['B']:,})")
    print(f"UNEXPLAINED users: {len(unexplained):,}"
          + (f"  <-- investigate before quoting a number, e.g. {unexplained[:5]}" if unexplained else ""))

    print("\n== 5. Candidate single number ==")
    real = [r for r in a.values() if not is_test(r) and in_window(r["ts_utc"], utc_day)]
    verified = sum(bool((r.get("email_verified_at") or "").strip()) for r in real)
    print(f"Accounts created, UTC day, excl. test/internal, deduped: {len(real):,}")
    print(f"  of which email-verified: {verified:,}")
    return {"raw": raw, "bad": bad, "only_a": only_a, "only_b": only_b, "unexplained": unexplained,
            "bridged": bridged, "candidate": len(real), "verified": verified, "membership": membership}


if __name__ == "__main__":
    main()
