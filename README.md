# Coinbase SEA "Insights to Impact": supporting code and tests

Armaan Singla's submission to the Coinbase Strategy, Execution & Analytics bounty. The written answers to the
four exercises are in `submission.md`, exported to `submission.docx` (for Google Docs) and `submission.pdf`.
This repo holds the full, runnable versions of the abridged code in that document and the tests behind every
number it quotes.

## Result

This submission was selected as one of the 20 winning entries in Coinbase's Insights to Impact challenge
(October 2026), which came with a $250 USDC award and a call with a Coinbase Emerging Talent recruiter.
The submitted version is tagged `submitted` (commit `5c2717e`); `submission.md`, `.docx` and `.pdf` are
kept exactly as submitted.

**All data here is synthetic.** The scenarios come from the challenge brief; the CSVs and SQL fixtures were
generated or hand-built for testing. Nothing reflects real Coinbase data, systems or metrics.

## Requirements

- Python 3.9+ with the IANA tz database (Linux and macOS have it; on Windows run `pip install tzdata`).
  Code and tests use the **standard library only** (sqlite3, csv, unittest, zoneinfo).
- Optional, only to rebuild the documents: `build_docs.sh` needs the `markdown` package for the .docx, and
  WSL plus Windows Chrome for the PDF (paths are hard-coded for that setup).

## Running the tests

From the repo root:

```bash
bash run_all_tests.sh          # every suite; exits non-zero on any failure
```

| Suite | Command | Tests |
|---|---|---|
| Ex1 funnel model (SQLite port) | `python3 -m unittest discover -s ex1/tests` | 58 |
| Ex1 build + data-quality checks | `python3 ex1/sqlite/build.py` | prints results |
| Ex2 generate data (run before the end-to-end run) | `python3 ex2/make_fake_data.py` | fixed seed |
| Ex2 reconciliation | `python3 -m unittest discover -s ex2` | 28 |
| Ex2 end-to-end run | `python3 ex2/reconcile_signups.py [--day YYYY-MM-DD --data DIR]` | prints bridge |
| Ex3 + Ex4 pipeline | `python3 -m unittest ex3/test_pipeline.py` | 39 |
| Word count | `python3 wordcount.py submission.md` | fails above 1,500 |

Last full run: 125 tests, all passing.

## Layout

```
submission.md / .docx / .pdf        written answers (word count excludes code blocks)
run_all_tests.sh, wordcount.py      test runner; prose word counter
build_docs.sh, md_to_docx.py        rebuild submission.docx (stdlib OOXML writer) and submission.pdf

ex1/  Exercise 1: onboarding funnel
  fct_onboarding_funnel.sql         Snowflake model: staging -> identity map -> quarantine -> events -> funnel;
                                    header lists the business-rule assumptions (A1-A5)
  pm_funnel_query.sql               Snowflake PM query: 7-day deposit / funding / trade by Monday cohort week
  sqlite/                           SQLite port of both, build script, executable data-quality checks
  tests/fixtures.py                 15 hand-built messy users + 400 generated users
  tests/fixtures_handcalc.py        small fixture whose expected values were worked out by hand
  tests/test_funnel.py              unit, hand-calculated, metamorphic and data-quality tests

ex2/  Exercise 2: signup reconciliation (A 52K / B 45K / C 57K)
  make_fake_data.py                 synthetic extracts with planted causes (seed 42)
  reconcile_signups.py              duplicates, grain, A->B and C->A bridges with an UNEXPLAINED residual
  test_reconcile.py                 planted truth, malformed input, DST gap/overlap, file-order invariance

ex3/  Exercise 3 (inherited pipeline) and Exercise 4 (41% vs 34%)
  legacy_weekly_active_traders.sql  the inherited snippet, verbatim
  country_cut_legacy_logic.sql      country cut on unchanged legacy logic + explicit reconciliation output
  v2_fct_user_week_trades.sql       v2: one row per user-week, status lookup, country as of first completed trade
  v2_weekly_active_traders.sql      v2 output by country (additive across countries)
  v2_dq_checks.sql                  unmapped statuses, status-lookup uniqueness, user-week grain
  ex4_device_vs_user_rate.sql       device-level vs user-level "% of WAU who traded"
  ex4_other_mechanisms.sql          other mechanisms that can move the same rate
  test_pipeline.py                  legacy characterization (pins known errors), v2, DQ and Ex4 tests
```

## What the tests establish, and what they don't

Established, on synthetic data:
- The funnel model's logic, run through SQLite: observed vs. inferred milestones, conservative identity
  resolution, quarantine reasons, the half-open 7-day window (on the earliest event at or after sign-up)
  and per-week cohort maturity.
- The reconciliation script recovers every planted cause, explains each difference, and is unaffected by
  row order or duplicate rows.
- Legacy vs. v2 behaviour for each edge case in the Exercise 3 fixture, including where the legacy country
  cut does and doesn't add up.
- Exercise 4: the challenge itself states that the legacy 41% double-counts users who trade on multiple
  devices; the fixture reproduces that device-grain mechanism (34% -> 41%). Separately, counting attempted
  trades or narrowing the denominator can also produce a similar gap; those are hypothetical alternatives
  for checking, not claims about real data.

Not established:
- **Snowflake execution.** The Snowflake SQL was never run. SQLite stands in, with Snowflake-only functions
  replaced by tested equivalents. Unverified Snowflake behaviour includes `CONVERT_TIMEZONE` on DST gap and
  overlap times, `CONVERT_TIMEZONE` with an invalid zone name, parsing of offsets after the model
  normalizes compact `+0530`-style offsets to `+05:30` with `REGEXP_REPLACE`, and window `RANGE` frames.
- **Real-world causes.** Business rules (e.g. KYC required before funding; country as of first completed
  trade) are documented assumptions for the metric owner. The Exercise 2 causes, and any Exercise 4 cause
  beyond the device double count the challenge states, are hypotheses until checked against real data.
