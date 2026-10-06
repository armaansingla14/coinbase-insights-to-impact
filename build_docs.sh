#!/usr/bin/env bash
# Render submission.md -> submission.pdf (headless Chrome) and submission.docx (Word, for Google Docs import).
set -euo pipefail
cd "$(dirname "$0")"
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
WINTMP="$(wslpath "$(cmd.exe /c 'echo %TEMP%' 2>/dev/null | tr -d '\r')")"

python3 - <<'EOF'
import markdown
body = markdown.markdown(open("submission.md").read(), extensions=["tables", "fenced_code"])
css = """
@page { size: Letter; margin: 0.7in 0.75in; }
body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.42; color: #1a1a1a; }
h1 { font-size: 19pt; color: #0052ff; margin: 0 0 6px; }
h2 { font-size: 14pt; color: #0052ff; border-bottom: 2px solid #0052ff; padding-bottom: 3px; margin-top: 22px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #d0d5dd; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #eef3ff; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 4px; padding: 8px 10px; font-size: 8.3pt; line-height: 1.3; white-space: pre-wrap; page-break-inside: avoid; }
code { font-family: Consolas, 'Courier New', monospace; font-size: 0.92em; }
p code, li code, td code { background: #f0f2f5; padding: 0 3px; border-radius: 3px; }
blockquote { border-left: 4px solid #0052ff; background: #f5f8ff; margin: 8px 0; padding: 6px 12px; }
hr { border: none; }
li { margin: 2px 0; }
"""
open("submission.html", "w").write(f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>")
EOF

cp submission.html "$WINTMP/cb_submission.html"
"$CHROME" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$(wslpath -w "$WINTMP/cb_submission.pdf")" "$(wslpath -w "$WINTMP/cb_submission.html")" 2>/dev/null
cp "$WINTMP/cb_submission.pdf" submission.pdf
chmod 644 submission.pdf
echo "wrote submission.pdf ($(stat -c %s submission.pdf) bytes)"


# submission.docx for Google Docs import (stdlib OOXML writer; Word automation is blocked on this machine).
python3 md_to_docx.py submission.md submission.docx
chmod 644 submission.docx
