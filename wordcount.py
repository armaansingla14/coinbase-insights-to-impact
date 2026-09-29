"""Count prose words in a markdown file, excluding fenced code blocks (per bounty rules).

Usage: python3 wordcount.py submission.md
Sections are split on '## ' headings; text under a heading containing 'AI Disclosure' is reported separately.
"""
import re
import sys

text = open(sys.argv[1]).read()
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
print(f"{total:>5}  TOTAL  ({total - excl} excluding AI disclosure; limit 1,500)")
