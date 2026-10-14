"""Reconcile daily signup counts across Systems A, B, C (python3 stdlib only).

Usage: python3 reconcile_signups.py [--day 2026-09-24] [--data DIR]

Assumptions (to confirm with system owners):
  A.created_at = ISO-8601 with offset (UTC); B.signup_ts_local = naive America/Los_Angeles;
  C.event_ts_ms = epoch ms UTC. Canonical key = lower(trim(user_id)); when a user appears more
  than once, keep the first row by (ts_utc, ingest_batch_id, all values), so file order never
  matters. Internal/test accounts are identified by email domain.
Naive Pacific times: a wall time skipped by spring-forward is rejected; one repeated by fall-back
  is resolved to the earlier instant (fold=0) and counted in the output.
Rows with a blank ID or unparseable timestamp are counted and reported, never silently dropped.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

UTC = timezone.utc


def load_pacific():
    """America/Los_Angeles from the IANA database. No fixed-offset fallback: UTC-7 would silently
    put every PST-season row (Nov-Mar) in the wrong hour, and near midnight the wrong day."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")  # handles PST/PDT switches
    except (ImportError, KeyError) as e:  # ZoneInfoNotFoundError is a KeyError
        raise RuntimeError("America/Los_Angeles not found: no IANA tz database here "
                           "(e.g. bare Windows). Install it: pip install tzdata") from e


PT = load_pacific()

REPORT_DAY = date(2026, 9, 24)
DATA = Path(__file__).parent / "data"
TEST_DOMAINS = ("coinbase-test.com", "example.com")
OUTSIDE = "outside the report window"
AMBIGUOUS = "ambiguous local time (DST overlap)"
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


class NonexistentLocalTime(ValueError):
    """A naive Pacific wall time that never happened (skipped by the spring-forward jump)."""


def naive_pt(v):
    """'YYYY-MM-DD HH:MM:SS' read as Pacific wall time (fold=0). Anything after the seconds,
    such as 'Z' or '-07:00', fails strptime: this column is supposed to be naive."""
    return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PT)


def parse_naive_pt(v):
    local = naive_pt(v)
    if local.astimezone(UTC).astimezone(PT).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise NonexistentLocalTime(v)  # 02:30 on spring-forward day round-trips to 03:30
    return local.astimezone(UTC)  # fall-back overlap: fold=0 is the earlier (PDT) instant


def is_ambiguous_pt(v):
    """True if the wall time occurs twice (fall-back); call only on times parse_naive_pt accepted."""
    local = naive_pt(v)
    return local.utcoffset() != local.replace(fold=1).utcoffset()


PARSERS = {
    "iso": parse_iso,
    "epoch_ms": lambda v: datetime.fromtimestamp(float(v) / 1000, tz=UTC),
    "naive_pt": parse_naive_pt,
}


def day_windows(day):
    """[start, end) in UTC for the UTC calendar day and the Pacific calendar day."""
    def bounds(tz):
        return tuple(datetime.combine(d, time(), tz).astimezone(UTC) for d in (day, day + timedelta(days=1)))
    return bounds(UTC), bounds(PT)


def load(path, id_col, ts_col, ts_fmt):
    """Return (good rows with 'uid' and 'ts_utc' added, Counter of rejected-row reasons,
    Counter of notes on kept rows, i.e. ambiguous local times)."""
    good, bad, notes = [], Counter(), Counter()
    with open(path, newline="", encoding="utf-8-sig") as f:  # utf-8-sig strips a BOM
        reader = csv.DictReader(f)
        reader.fieldnames = [c.strip() for c in reader.fieldnames or []]
        missing = {id_col, ts_col} - set(reader.fieldnames)
        if missing:  # a schema change should stop the run, not show up as "blank IDs"
            raise ValueError(f"{path.name}: missing column(s) {sorted(missing)}")
        for r in reader:
            r["uid"] = (r.get(id_col) or "").strip().lower()
            ts, ts_error = (r.get(ts_col) or "").strip(), "unparseable timestamp"
            try:
                r["ts_utc"] = PARSERS[ts_fmt](ts)
            except NonexistentLocalTime:
                r["ts_utc"], ts_error = None, "nonexistent local time (DST gap)"
            except (ValueError, OverflowError, OSError):
                r["ts_utc"] = None
            if not r["uid"]:
                bad["blank user_id"] += 1
            elif r["ts_utc"] is None:
                bad[ts_error] += 1
            elif None in r or None in r.values():
                bad["wrong number of columns"] += 1
            else:
                good.append(r)
                if ts_fmt == "naive_pt" and is_ambiguous_pt(ts):
                    notes[AMBIGUOUS] += 1
    return good, bad, notes


def row_key(r):
    """Total order on one user's rows: earliest, then lowest batch id, then all values. Shared by
    duplicates() and first_per_user() so the kept row never depends on file order."""
    return r["ts_utc"], r.get("ingest_batch_id", ""), tuple(str(v) for v in r.values())


def by_user(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["uid"]].append(r)
    return {u: sorted(rs, key=row_key) for u, rs in groups.items()}


def duplicates(rows):
    """user_id -> its rows in row_key order (kept row first), for IDs seen more than once."""
    return {u: rs for u, rs in by_user(rows).items() if len(rs) > 1}


def first_per_user(rows):
    return {u: rs[0] for u, rs in by_user(rows).items()}


def is_test(r):
    return (r.get("email") or "").rsplit("@", 1)[-1].strip().lower() in TEST_DOMAINS


def in_window(ts, window):
    return window[0] <= ts < window[1]


def overlap_label(pattern):
    return "+".join(pattern) + ("" if pattern == "ABC" else " only")


def ranked(counter):
    """(key, n) largest first, ties by key: printed order must not depend on input order."""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


# Reason functions list EVERY reason that applies, in precedence order. The first one is the
# user's single bridge reason; users with more than one are shown on the "overlap" line.
# Precedence: out-of-scope rows, then who the user is, then when, then account state.
def why_not_in_b(r, utc_day, pt_day):
    """Why an A user (UTC day, all accounts) is missing from B (Pacific day, verified, no test)."""
    ts = r["ts_utc"]
    checks = [(OUTSIDE, not in_window(ts, utc_day)),
              ("test/internal account", is_test(r)),
              (f"created before {pt_day[0]:%H:%M} UTC (previous Pacific day)", ts < pt_day[0]),
              ("created after the Pacific day ended", ts >= pt_day[1]),
              ("email not verified", not (r.get("email_verified_at") or "").strip())]
    return [why for why, hit in checks if hit] or ["UNEXPLAINED"]


def why_not_in_a(r, utc_day, pt_day):
    """Why a B user is missing from A. A has no verified/test filter, so only timing explains it."""
    ts = r["ts_utc"]
    checks = [(OUTSIDE, not in_window(ts, pt_day)),
              ("created on next UTC day (still Pacific report day)",
               ts >= utc_day[1] and in_window(ts, pt_day))]
    return [why for why, hit in checks if hit] or ["UNEXPLAINED"]


def why_not_in_peer(r, window):
    """C vs A: same UTC-day definition and both include test accounts, so only scope explains it."""
    return [OUTSIDE] if not in_window(r["ts_utc"], window) else ["UNEXPLAINED"]


def bridge(x, y, why_only_x, why_only_y):
    """x, y: uid -> row (deduped). Returns ({reason: users} for x-only and for y-only, using each
    user's first reason; {reason combination: users} where more than one applied; sorted
    UNEXPLAINED uids)."""
    only_x = {u: why_only_x(x[u]) for u in x.keys() - y.keys()}
    only_y = {u: why_only_y(y[u]) for u in y.keys() - x.keys()}
    both = {**only_x, **only_y}
    overlap = Counter(" & ".join(whys) for whys in both.values() if len(whys) > 1)
    return (Counter(whys[0] for whys in only_x.values()), Counter(whys[0] for whys in only_y.values()),
            overlap, sorted(u for u, whys in both.items() if whys[0] == "UNEXPLAINED"))


def print_steps(start_label, start, steps, width=56):
    """Print a running bridge; steps are (label, signed n) or ('=', label) subtotal markers."""
    total = start
    print(f"{start_label:<{width}}{start:>8,}")
    for label, n in steps:
        if label == "=":
            print(f"{'= ' + n:<{width}}{total:>8,}")
        elif n:
            total += n
            print(f"  {label:<{width - 2}}{n:>+8,}")
    return total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--day", type=date.fromisoformat, default=REPORT_DAY, help="report day, YYYY-MM-DD")
    ap.add_argument("--data", type=Path, default=DATA, help="directory holding system_[abc].csv")
    args = ap.parse_args(argv)
    utc_day, pt_day = day_windows(args.day)

    raw, bad, notes, systems = {}, {}, {}, {}
    for s, (file, id_col, ts_col, fmt) in SOURCES.items():
        systems[s], bad[s], notes[s] = load(args.data / file, id_col, ts_col, fmt)
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
        print("   columns that differ within a group:", dict(ranked(differing)) or "none (exact copies)")
        if "ingest_batch_id" in rows[0]:
            extra = Counter(r["ingest_batch_id"] for rs in dups.values() for r in rs[1:])
            print("   batches holding the extra rows:", ranked(extra)[:3])
            n_retry = sum(v for k, v in extra.items() if "_retry" in k)
            print(f"   extra rows in *_retry batches: {n_retry:,} of {sum(extra.values()):,} "
                  f"across {len(extra)} batches")

    print(f"\n== 2. Grain check for {args.day}: rows vs distinct users, and UTC time range ==")
    deduped = {s: first_per_user(rows) for s, rows in systems.items()}
    for s, rows in systems.items():
        ts = [r["ts_utc"] for r in rows]
        span = f"{min(ts):%m-%d %H:%M} -> {max(ts):%m-%d %H:%M} UTC" if ts else "no valid rows"
        print(f"{s}: {raw[s]:>7,} rows | {len(deduped[s]):>7,} distinct | {span}")
        if bad[s]:
            print(f"   rejected rows: {dict(ranked(bad[s]))}")
        for note, n in ranked(notes[s]):
            print(f"   {note}: {n:,} rows kept, read as the earlier instant")

    print("\n== 3. Overlap of distinct user_ids ==")
    membership = Counter("".join(s for s in "ABC" if u in deduped[s])
                         for u in set().union(*deduped.values()))
    for pattern, n in sorted(membership.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  in {overlap_label(pattern):<10}{n:>8,}")

    print("\n== 4. Explain every A-vs-B difference; grain = distinct accounts after dedup ==")
    a, b, c = deduped["A"], deduped["B"], deduped["C"]
    only_a, only_b, overlap, unexplained = bridge(a, b, lambda r: why_not_in_b(r, utc_day, pt_day),
                                                  lambda r: why_not_in_a(r, utc_day, pt_day))
    steps = [("- A rejected rows", -sum(bad["A"].values())), ("- A duplicate rows", len(a) - len(systems["A"])),
             ("=", "A distinct accounts")]
    steps += [(f"- {why}", -n) for why, n in ranked(only_a)]
    steps += [(f"+ {why}", n) for why, n in ranked(only_b)]
    steps += [("=", "B distinct accounts"),
              ("+ B duplicate rows", len(systems["B"]) - len(b)), ("+ B rejected rows", sum(bad["B"].values()))]
    bridged = print_steps("A raw", raw["A"], steps)
    print(f"{'= bridged to B':<56}{bridged:>8,}   (B raw {raw['B']:,})")
    print("overlap (users matching >1 reason; counted above under the first):"
          + ("" if overlap else " none"))
    for combo, n in ranked(overlap):
        print(f"  {combo}: {n:,}")
    print(f"UNEXPLAINED users: {len(unexplained):,}"
          + (f"  <-- investigate before quoting a number, e.g. {unexplained[:5]}" if unexplained else ""))

    print("\n== 5. Explain C vs A (same UTC-day definition: only duplicates should differ) ==")
    only_c, only_a_c, _, unexplained_c = bridge(c, a, lambda r: why_not_in_peer(r, utc_day),
                                                lambda r: why_not_in_peer(r, utc_day))
    print(f"only in C: {sum(only_c.values()):,} | only in A: {sum(only_a_c.values()):,}")
    steps = [("- C rejected rows", -sum(bad["C"].values())), ("- C duplicate rows", len(c) - len(systems["C"])),
             ("=", "C distinct accounts")]
    steps += [(f"- only in C: {why}", -n) for why, n in ranked(only_c)]
    steps += [(f"+ only in A: {why}", n) for why, n in ranked(only_a_c)]
    print_steps("C raw", raw["C"], steps + [("=", "A distinct accounts")])
    print(f"UNEXPLAINED users (C vs A): {len(unexplained_c):,}"
          + (f"  <-- e.g. {unexplained_c[:5]}" if unexplained_c else ""))

    print("\n== 6. Candidate single number ==")
    real = [r for r in a.values() if not is_test(r) and in_window(r["ts_utc"], utc_day)]
    verified = sum(bool((r.get("email_verified_at") or "").strip()) for r in real)
    print(f"Accounts created, UTC day, excl. test/internal, deduped: {len(real):,}")
    print(f"  of which email-verified: {verified:,}")
    return {"raw": raw, "bad": bad, "notes": notes, "distinct": {s: len(d) for s, d in deduped.items()},
            "only_a": only_a, "only_b": only_b, "overlap": overlap, "unexplained": unexplained,
            "bridged": bridged, "only_c": only_c, "only_a_vs_c": only_a_c, "unexplained_c": unexplained_c,
            "candidate": len(real), "verified": verified, "membership": membership}


if __name__ == "__main__":
    main()
