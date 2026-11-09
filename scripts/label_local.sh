#!/usr/bin/env bash
# Run sharded teacher labelling on the host (no Kubernetes) with the local
# object-store backend. Useful for a laptop smoke test of the exact worker code.
#
#   SHARD_COUNT=4 PARALLEL=2 MAX_RECORDS_PER_SHARD=100 bash scripts/label_local.sh
#
# The worker is resumable: rerun the same command to continue where it stopped.
set -euo pipefail
cd "$(dirname "$0")/.."

SHARD_COUNT="${SHARD_COUNT:-4}"
PARALLEL="${PARALLEL:-2}"
MAX_RECORDS_PER_SHARD="${MAX_RECORDS_PER_SHARD:-}"
STORAGE_DIR="${LOCAL_STORAGE_DIR:-local-storage}"
TEACHER_URL="${TEACHER_URL:-http://localhost:11434}"
export STORAGE_BACKEND=local
export LOCAL_STORAGE_DIR="$STORAGE_DIR"
export SHARD_COUNT TEACHER_URL
export TEACHER_MODEL="${TEACHER_MODEL:-phi3.5}"
export PROMPT_PATH="${PROMPT_PATH:-prompts/teacher_prompt.txt}"
export TEACHER_NUM_PREDICT="${TEACHER_NUM_PREDICT:-64}"
export TEACHER_NUM_CTX="${TEACHER_NUM_CTX:-1024}"
export FLUSH_EVERY="${FLUSH_EVERY:-1}"
if [[ -n "$MAX_RECORDS_PER_SHARD" ]]; then
  export MAX_RECORDS="$MAX_RECORDS_PER_SHARD"
fi

PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"

# Seed the local "datasets" bucket with the command pool if needed.
"$PY" - <<'PY'
import os
from pathlib import Path
from worker.storage import LocalStore
root = Path(os.environ["LOCAL_STORAGE_DIR"])
store = LocalStore(root)
store.ensure_bucket("datasets")
store.ensure_bucket("models")
target = root / "datasets" / "commands_pool.jsonl"
if not target.exists():
    target.write_bytes(Path("data/commands_pool.jsonl").read_bytes())
    print("seeded local datasets/commands_pool.jsonl")
else:
    print("local datasets/commands_pool.jsonl already present")
PY

running=0
pids=()
for ((index = 0; index < SHARD_COUNT; index++)); do
  (
    SHARD_INDEX="$index" "$PY" -m worker.worker >"/tmp/label-shard-${index}.log" 2>&1
    echo "$?" >"/tmp/label-shard-${index}.status"
  ) &
  pids+=("$!")
  running=$((running + 1))
  if (( running >= PARALLEL )); then
    wait -n || true
    running=$((running - 1))
  fi
done
for pid in "${pids[@]}"; do wait "$pid" || true; done

status=0
for ((index = 0; index < SHARD_COUNT; index++)); do
  code=$(cat "/tmp/label-shard-${index}.status" 2>/dev/null || echo 1)
  echo "shard $index exit=$code log=/tmp/label-shard-${index}.log"
  [[ "$code" == "0" ]] || status=1
done
exit "$status"
