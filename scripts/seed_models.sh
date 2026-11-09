#!/usr/bin/env bash
# Upload local models/v1 and models/v2 artefacts to the models bucket.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${S3_PORT:-8333}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"
PF_PID=""
if ! python3 - "$PORT" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(1)
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

for version in v1 v2; do
  if [[ -d "models/${version}" ]]; then
    "$PY" scripts/init_storage.py --endpoint "http://127.0.0.1:${PORT}" --skip-commands \
      --models-dir "models/${version}" --version "$version"
  else
    echo "models/${version} not found, skipping"
  fi
done
