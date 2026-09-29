#!/usr/bin/env bash
# Run every test suite in the repo (Python 3 stdlib only). Exits non-zero if any suite fails.
set -uo pipefail
cd "$(dirname "$0")"
fail=0
run() { echo; echo "=== $1 ==="; shift; "$@" || { echo "FAILED: $*"; fail=1; }; }

run "Ex1: onboarding funnel model (SQLite port, 31 tests)" python3 -m unittest discover -s ex1/tests
run "Ex1: build model on fixtures + data-quality checks"  python3 ex1/sqlite/build.py
run "Ex2: generate synthetic CSVs (seeded, deterministic)" python3 ex2/make_fake_data.py
run "Ex2: signup reconciliation (13 tests)"               python3 -m unittest discover -s ex2
run "Ex2: end-to-end run on the synthetic CSVs"           python3 ex2/reconcile_signups.py
run "Ex3/Ex4: legacy vs v2 pipeline (24 tests)"           python3 -m unittest ex3/test_pipeline.py
run "Word count (limit 1,500 excl. code blocks)"          python3 wordcount.py submission.md

echo
if [ "$fail" -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "SOME SUITES FAILED"; fi
exit "$fail"
