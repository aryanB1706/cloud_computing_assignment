#!/usr/bin/env bash
# Create buckets and upload commands_pool.jsonl, using a temporary port-forward
# if SeaweedFS is not already reachable on localhost:8333.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${S3_PORT:-8333}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
PF_PID=""

if ! python3 - "$PORT" <<'PY'
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
PY
then
  kubectl -n data port-forward svc/seaweedfs-s3 "${PORT}:8333" >/tmp/seaweedfs-portforward.log 2>&1 &
  PF_PID=$!
  trap 'kill "$PF_PID" 2>/dev/null || true' EXIT
  sleep 3
fi

if [[ -f .env.local ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

"$PY" scripts/init_storage.py --endpoint "http://127.0.0.1:${PORT}" "$@"
