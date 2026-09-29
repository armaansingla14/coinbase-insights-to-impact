# Coinbase SEA "Insights to Impact": supporting code and tests

The written answers are in `submission.md` (rendered to `submission.pdf`). This repo holds the full, runnable
versions of the abridged code in that document, plus the tests behind every number it quotes.
All data is synthetic. All code and tests run on Python 3.9+ **standard library only** (sqlite3, csv, unittest,
zoneinfo); there is nothing to install. Only `build_pdf.sh` (optional, regenerates the PDF) needs the python-markdown
package and Chrome.

## Layout

```
submission.md / submission.pdf      the answers (<= 1,500 words excluding code blocks)
run_all_tests.sh                    runs every suite below; exits non-zero on any failure
wordcount.py                        prose word counter (excludes fenced code blocks)
build_pdf.sh                        submission.md -> submission.pdf (python-markdown + headless Chrome under WSL)

ex1/  Exercise 1: onboarding funnel data model
  fct_onboarding_funnel.sql         full Snowflake model: stg_onboarding_events -> int_identity_map ->
                                    int_onboarding_quarantine -> fct_onboarding_events -> fct_onboarding_funnel,
                                    plus the data-quality test list
  sqlite/onboarding_funnel_sqlite.sql  same model ported to SQLite (same layers and columns)
  sqlite/build.py                   builds the model on the fixtures and prints the funnel + quarantine
  sqlite/dq_checks.py               every data-quality check from the SQL file, as executable code
  tests/fixtures.py                 15 hand-built messy users + 400 generated users
  tests/test_funnel.py              31 unit tests (row-level expectations, invariants, late data, DQ checks)

ex2/  Exercise 2: signup reconciliation (A 52K / B 45K / C 57K)
  make_fake_data.py                 generates data/system_{a,b,c}.csv with planted problems (fixed seed)
  reconcile_signups.py              finds the divergences blind: C duplicates first, grain, overlap,
                                    A->B bridge with UNEXPLAINED residual, candidate single number
  test_reconcile.py                 13 unit tests (planted truth recovered exactly, malformed rows, DST, schema errors)

ex3/  Exercise 3 (inherited pipeline) and Exercise 4 (41% vs 34%) evidence
  legacy_weekly_active_traders.sql  the inherited snippet, verbatim
  country_cut_legacy_logic.sql      country cut that keeps the legacy logic (reconciles to baseline)
  v2_fct_user_week_trades.sql       v2 intermediate: one row per user-week, no devices join, status lookup
  v2_weekly_active_traders.sql      v2 output by country (dim_user = one row per user, UNKNOWN bucket)
  ex4_device_vs_user_rate.sql       device-level vs user-level "% of WAU who traded" (41.0% vs 34.0%)
  test_pipeline.py                  24 unit tests on a 7-user fixture + 100-user Ex4 fixture (in-memory SQLite)
```

## Running the tests

Run everything (from the repo root):

```bash
./run_all_tests.sh
```

Or one suite at a time:

| Suite | Command |
|---|---|
| Ex1 model, 31 tests | `python3 -m unittest discover -s ex1/tests -v` |
| Ex1 build + DQ checks on fixtures | `python3 ex1/sqlite/build.py` |
| Ex2 reconciliation, 13 tests | `python3 -m unittest discover -s ex2 -v` |
| Ex2 generate data (run first; CSVs are not committed) | `python3 ex2/make_fake_data.py` (fixed seed: 52,000 / 45,000 / 57,000 rows) |
| Ex2 end-to-end run | `python3 ex2/reconcile_signups.py` (options: `--day YYYY-MM-DD --data DIR`) |
| Ex3 + Ex4, 24 tests | `python3 -m unittest -v ex3/test_pipeline.py` |
| Word count | `python3 wordcount.py submission.md` |

## Notes and limits

- The Snowflake SQL was not run against a live Snowflake account. It is tested through the SQLite port, which
  replaces Snowflake-only functions (QUALIFY, TRY_TO_TIMESTAMP_*, CONVERT_TIMEZONE, ...) with equivalents that
  have their own tests. Two behaviours were not verifiable without Snowflake: parsing `+0530` (no colon) offsets
  with automatic format detection, and local times that fall in a daylight-saving gap or overlap.
- Ex2 numbers (e.g. 50,000 candidate, 43,293 verified, 0 UNEXPLAINED) come from the synthetic data; real
  systems would need owner-confirmed definitions.
