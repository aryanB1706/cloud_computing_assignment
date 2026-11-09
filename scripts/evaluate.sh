#!/usr/bin/env bash
# Evaluate teacher, untrained Pythia and the trained student on validation.jsonl.
# All raw predictions and score reports are written to results/.
#
# Set SKIP_TEACHER=1 if Ollama/phi3.5 is not available on this machine.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p results
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
VALIDATION="data/validation.jsonl"

if [[ ! -f "$VALIDATION" ]]; then
  echo "missing $VALIDATION" >&2
  exit 1
fi

score() {
  local preds="$1" out="$2"
  "$PY" tools/score.py "$preds" "$VALIDATION" --out "$out"
}

if [[ "${SKIP_TEACHER:-0}" != "1" ]]; then
  teacher_url="${TEACHER_URL:-http://localhost:11434}"
  if ! curl -fsS -m 5 "${teacher_url}/api/tags" >/dev/null; then
    echo "Ollama is not reachable at ${teacher_url}." >&2
    echo "Start it (ollama serve) and pull phi3.5, or run with SKIP_TEACHER=1." >&2
    exit 1
  fi
  echo "== teacher (phi3.5, temperature 0, seed 42) =="
  "$PY" training/predict.py --kind teacher --output results/pred_teacher.jsonl
  score results/pred_teacher.jsonl results/teacher_validation.json
  if command -v ollama >/dev/null 2>&1; then
    ollama stop phi3.5 >/dev/null 2>&1 || true
  fi
fi

echo "== untrained EleutherAI/pythia-70m =="
"$PY" training/predict.py --kind student --model EleutherAI/pythia-70m \
  --output results/pred_base.jsonl
score results/pred_base.jsonl results/score_base.json

MODEL="${MODEL_PATH:-models/v1}"
if [[ -f "${MODEL}/model.tar.gz" ]]; then
  MODEL="${MODEL}/model.tar.gz"
elif [[ ! -d "$MODEL" ]]; then
  echo "student model not found at models/v1 (or models/v1/model.tar.gz)" >&2
  echo "Train it first: make train" >&2
  exit 1
fi

echo "== trained student v1 =="
"$PY" training/predict.py --kind student --model "$MODEL" \
  --output results/pred_student_v1.jsonl
score results/pred_student_v1.jsonl results/score_student_v1.json

if [[ -f models/v2/model.tar.gz || -d models/v2 ]]; then
  MODEL2="models/v2"
  [[ -f "models/v2/model.tar.gz" ]] && MODEL2="models/v2/model.tar.gz"
  echo "== trained student v2 =="
  "$PY" training/predict.py --kind student --model "$MODEL2" \
    --output results/pred_student_v2.jsonl
  score results/pred_student_v2.jsonl results/score_student_v2.json
fi

echo "== quality gate (student v1, --min-validity 95) =="
set +e
"$PY" tools/score.py results/pred_student_v1.jsonl "$VALIDATION" \
  --out results/score_student_v1.json --min-validity 95
GATE_STATUS=$?
set -e

"$PY" - <<'PY'
import json
from pathlib import Path

files = {
    "teacher": "results/teacher_validation.json",
    "untrained_pythia": "results/score_base.json",
    "student_v1": "results/score_student_v1.json",
    "student_v2": "results/score_student_v2.json",
}
summary = {}
for name, path in files.items():
    p = Path(path)
    if p.is_file():
        summary[name] = json.loads(p.read_text())
Path("results/evaluation_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
if summary:
    cols = ["json_validity", "intent_accuracy", "date_accuracy", "time_accuracy", "place_accuracy", "full_frame"]
    print("\nmodel".ljust(18) + "".join(c.rjust(16) for c in cols))
    for name, row in summary.items():
        print(name.ljust(18) + "".join(f"{row.get(c, 0):>16.2f}" for c in cols))
PY

if [[ "$GATE_STATUS" -ne 0 ]]; then
  echo "QUALITY GATE FAILED (exit $GATE_STATUS); see results/score_student_v1.json" >&2
  exit "$GATE_STATUS"
fi
echo "quality gate passed"
