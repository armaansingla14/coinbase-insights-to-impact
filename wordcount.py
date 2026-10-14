"""Count prose words in a markdown file, excluding fenced code blocks (per bounty rules).

Usage: python3 wordcount.py [--max N] submission.md
Headings, tables, lists and the AI disclosure all count; only fenced code blocks are excluded.
Sections are split on '## ' headings. Exits 1 if the total exceeds --max (default 1,500).
"""
import argparse
import re
import sys

ap = argparse.ArgumentParser()
ap.add_argument("path")
ap.add_argument("--max", type=int, default=1500, help="fail if the total word count exceeds this")
args = ap.parse_args()

text = open(args.path, encoding="utf-8").read()
prose = re.sub(r"```.*?```", "", text, flags=re.S)
sections, current = {}, "preamble"
for line in prose.splitlines():
    if line.startswith("## "):
        current = line[3:].strip()
    words = re.findall(r"[A-Za-z0-9$%][\w'’%$.,/-]*", re.sub(r"[#*|`>_-]{2,}", " ", line))
    sections[current] = sections.get(current, 0) + len(words)

total = 0
for name, n in sections.items():
    print(f"{n:>5}  {name}")
    total += n
excl = sum(n for name, n in sections.items() if "AI Disclosure" in name)
print(f"{total:>5}  TOTAL  ({total - excl} excluding AI disclosure; limit {args.max:,})")
if total > args.max:
    print(f"OVER LIMIT by {total - args.max} words", file=sys.stderr)
    sys.exit(1)
