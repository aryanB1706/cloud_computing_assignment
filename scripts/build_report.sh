#!/usr/bin/env bash
# Build report/report.pdf. Prefer pandoc (Markdown -> XeLaTeX), then fall back
# to an existing report.tex.
set -euo pipefail
cd "$(dirname "$0")/.."

PANDOC="${PANDOC:-pandoc}"
if ! command -v "$PANDOC" >/dev/null 2>&1 && [[ -x /tmp/pandoc-3.13/bin/pandoc ]]; then
  PANDOC=/tmp/pandoc-3.13/bin/pandoc
fi

PANDOC_DATA_ARGS=()
if [[ -n "${PANDOC_DATA_DIR:-}" ]]; then
  PANDOC_DATA_ARGS+=(--data-dir "$PANDOC_DATA_DIR")
fi

if [[ -f report/report.md ]]; then
  if ! command -v "$PANDOC" >/dev/null 2>&1 || ! command -v xelatex >/dev/null 2>&1; then
    echo "Pandoc and xelatex are required to build report/report.md." >&2
    exit 1
  fi
  "$PANDOC" report/report.md \
    "${PANDOC_DATA_ARGS[@]}" \
    -o report/report.pdf \
    --pdf-engine=xelatex \
    -V geometry:margin=1.5cm \
    -V fontsize=9pt \
    -V mainfont="Noto Serif" \
    -V sansfont="Noto Sans" \
    -V colorlinks=true \
    -V linkcolor=blue \
    --highlight-style=tango
  "$PANDOC" report/report.md "${PANDOC_DATA_ARGS[@]}" -s -t latex -o report/report.tex
  echo "wrote report/report.pdf and report/report.tex"
  exit 0
fi

if [[ -f report/report.tex ]] && command -v xelatex >/dev/null 2>&1; then
  (
    cd report
    xelatex -interaction=nonstopmode -halt-on-error report.tex >/tmp/report-xelatex-1.log
    xelatex -interaction=nonstopmode -halt-on-error report.tex >/tmp/report-xelatex-2.log
  )
  echo "wrote report/report.pdf"
  exit 0
fi

echo "Install pandoc+xelatex (recommended) or xelatex; report/report.md is the source." >&2
exit 1
