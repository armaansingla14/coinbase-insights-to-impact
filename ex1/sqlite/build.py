"""Build the SQLite port of the onboarding funnel model (stdlib only).

Registers Python UDFs that reproduce the Snowflake functions used by ex1/fct_onboarding_funnel.sql,
then executes ex1/sqlite/onboarding_funnel_sqlite.sql against a connection that already holds the raw tables.
"""
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
MODEL_SQL = HERE / "onboarding_funnel_sqlite.sql"
FMT = "%Y-%m-%d %H:%M:%S"

RAW_DDL = """
CREATE TABLE raw_app_events    (event_id TEXT, user_ref TEXT, event_name TEXT, ts_raw TEXT, loaded_at TEXT);
CREATE TABLE raw_kyc_events    (event_id TEXT, user_ref TEXT, status TEXT, ts_epoch_s NUMERIC, loaded_at TEXT);
CREATE TABLE raw_ledger_events (txn_id TEXT, account_id INTEGER, txn_type TEXT, status TEXT,
                                ts_local TEXT, tz_name TEXT, loaded_at TEXT);
CREATE TABLE core_accounts     (account_id INTEGER, user_uuid TEXT, created_at TEXT);
CREATE TABLE core_anon_links   (anonymous_id TEXT, user_uuid TEXT);
"""


def _fmt(dt):
    """Render a naive (UTC) datetime as sortable text; keep sub-second precision only when present."""
    return dt.strftime(FMT) + (f".{dt.microsecond:06d}" if dt.microsecond else "")


def _parse(s):
    """Snowflake AUTO-ish ISO parsing: 'YYYY-MM-DD[ T]HH:MM[:SS[.f]][Z|+HH:MM|+HHMM]' or date only."""
    if s is None:
        return None
    try:
        return datetime.fromisoformat(str(s).strip())
    except ValueError:
        return None


def regexp_like(subject, pattern):
    """REGEXP_LIKE: Snowflake anchors the pattern to the whole string."""
    if subject is None or pattern is None:
        return None
    return int(re.fullmatch(pattern, subject) is not None)


def try_to_timestamp_tz(s):
    """TRY_TO_TIMESTAMP_TZ: returns an offset-aware ISO string, NULL on failure.
    Offset-less input would take the session TZ in Snowflake; the model never relies on that (it routes
    offset-less strings to TRY_TO_TIMESTAMP_NTZ), so here we treat it as UTC."""
    dt = _parse(s)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def try_to_timestamp_ntz(s):
    """TRY_TO_TIMESTAMP_NTZ: wall-clock value, any offset is dropped; NULL on failure."""
    dt = _parse(s)
    return None if dt is None else _fmt(dt.replace(tzinfo=None))


def to_timestamp_ntz(v):
    """TO_TIMESTAMP_NTZ: numeric -> epoch seconds interpreted as UTC; string -> parsed wall clock.
    Unlike the TRY_ variant this raises on bad input (the Snowflake statement would fail)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if isinstance(v, float) and math.isnan(v):
            raise ValueError("NaN epoch")
        return _fmt(datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None))
    out = try_to_timestamp_ntz(v)
    if out is None:
        raise ValueError(f"Timestamp '{v}' is not recognized")
    return out


def convert_timezone(*args):
    """CONVERT_TIMEZONE(target_tz, ts_tz)            -> TIMESTAMP_TZ in target (we return it as NTZ text,
                                                       i.e. the ::TIMESTAMP_NTZ cast in the Snowflake model)
       CONVERT_TIMEZONE(source_tz, target_tz, ts_ntz) -> wall clock in source_tz re-expressed in target_tz."""
    if len(args) == 2:
        target, ts = args
        if ts is None:
            return None
        dt = datetime.fromisoformat(ts)
        return _fmt(dt.astimezone(ZoneInfo(target)).replace(tzinfo=None))
    if len(args) == 3:
        source, target, ts = args
        if ts is None or source is None:
            return None
        dt = datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo(source))  # raises on unknown tz, like Snowflake
        return _fmt(dt.astimezone(ZoneInfo(target)).replace(tzinfo=None))
    raise sqlite3.ProgrammingError("convert_timezone takes 2 or 3 arguments")


def datediff_minute(a, b):
    """DATEDIFF('minute', a, b): number of minute boundaries crossed (not floor of elapsed time)."""
    if a is None or b is None:
        return None
    ta = datetime.fromisoformat(a).replace(second=0, microsecond=0)
    tb = datetime.fromisoformat(b).replace(second=0, microsecond=0)
    return int((tb - ta).total_seconds() // 60)


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.create_function("regexp_like", 2, regexp_like, deterministic=True)
    conn.create_function("try_to_timestamp_tz", 1, try_to_timestamp_tz, deterministic=True)
    conn.create_function("try_to_timestamp_ntz", 1, try_to_timestamp_ntz, deterministic=True)
    conn.create_function("to_timestamp_ntz", 1, to_timestamp_ntz, deterministic=True)
    conn.create_function("convert_timezone", -1, convert_timezone, deterministic=True)
    conn.create_function("datediff_minute", 2, datediff_minute, deterministic=True)
    conn.row_factory = sqlite3.Row
    return conn


def create_raw_tables(conn):
    conn.executescript(RAW_DDL)


def build_model(conn):
    """(Re)build all model layers. Idempotent: views are dropped and recreated, the funnel table is rebuilt."""
    conn.executescript(MODEL_SQL.read_text())
    conn.commit()


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(HERE.parent / "tests"))
    from fixtures import load_fixtures  # noqa: E402
    c = connect()
    create_raw_tables(c)
    load_fixtures(c)
    build_model(c)
    cols = [r[1] for r in c.execute("PRAGMA table_info(fct_onboarding_funnel)")]
    print(" | ".join(cols[:10]))
    for r in c.execute("SELECT * FROM fct_onboarding_funnel WHERE user_uuid LIKE 'a%' ORDER BY user_uuid"):
        print(" | ".join(str(r[k]) for k in cols[:10]))
    print("quarantine:")
    for r in c.execute("SELECT reason, source, user_ref, source_event_id FROM int_onboarding_quarantine"):
        print("  ", tuple(r))
