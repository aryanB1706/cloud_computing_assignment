#!/usr/bin/env bash
# One-command bring-up for the local cloud:
#   k3d cluster -> namespaces/secrets -> SeaweedFS -> buckets+data -> images
#   -> model artefacts -> app Deployment/Service/Ingress/HPA
set -euo pipefail

cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-kd-cloud}"
SERVICE_IMAGE="${SERVICE_IMAGE:-command-parser-service:0.1.0}"
WORKER_IMAGE="${WORKER_IMAGE:-command-parser-worker:0.1.0}"
S3_PORT="${S3_PORT:-8333}"
PY="${PY:-.venv/bin/python}"
[[ -x "$PY" ]] || PY="python3"

for tool in docker k3d kubectl python3 sha256sum; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "ERROR: '$tool' is required for make up but was not found." >&2
    echo "Install Docker, k3d and kubectl first (see README prerequisites)." >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: the Docker daemon is not reachable." >&2
  exit 1
fi

if k3d cluster list 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER"; then
  echo "cluster $CLUSTER already exists"
else
  echo "creating cluster $CLUSTER from cluster/k3d.yaml"
  k3d cluster create --config cluster/k3d.yaml
fi

kubectl wait --for=condition=Ready node --all --timeout=180s

kubectl apply -f k8s/00-namespaces.yaml
bash scripts/create_secrets.sh
kubectl apply -f k8s/seaweedfs.yaml
kubectl -n data rollout status deployment/seaweedfs --timeout=240s

echo "starting SeaweedFS port-forward on 127.0.0.1:${S3_PORT}"
kubectl -n data port-forward svc/seaweedfs-s3 "${S3_PORT}:8333" \
  >/tmp/seaweedfs-portforward.log 2>&1 &
PF_PID=$!
cleanup() { kill "$PF_PID" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 30); do
  if (echo >"/dev/tcp/127.0.0.1/${S3_PORT}") >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

set -a
# shellcheck disable=SC1091
source .env.local
set +a
export S3_ENDPOINT="http://127.0.0.1:${S3_PORT}"

"$PY" scripts/init_storage.py --endpoint "$S3_ENDPOINT"

echo "building images"
docker build -f service/Dockerfile -t "$SERVICE_IMAGE" .
docker build -f worker/Dockerfile -t "$WORKER_IMAGE" .
k3d image import -c "$CLUSTER" "$SERVICE_IMAGE"
k3d image import -c "$CLUSTER" "$WORKER_IMAGE"

if [[ ! -f models/v1/model.tar.gz ]]; then
  echo "models/v1/model.tar.gz not found; trying GitHub Release"
  bash scripts/fetch_model.sh || true
fi

for version in v1 v2; do
  if [[ -f "models/${version}/model.tar.gz" ]]; then
    "$PY" scripts/init_storage.py --endpoint "$S3_ENDPOINT" --skip-commands \
      --models-dir "models/${version}" --version "$version"
  fi
done

HAS_MODEL=0
[[ -f models/v1/model.tar.gz ]] && HAS_MODEL=1

kubectl apply -f k8s/app.yaml -f k8s/ingress.yaml -f k8s/hpa.yaml

if [[ "$HAS_MODEL" -eq 1 ]]; then
  if ! kubectl -n app rollout status deployment/command-parser --timeout=600s; then
    echo "WARNING: deployment did not become Ready; inspect with:" >&2
    echo "  kubectl -n app get pods" >&2
    echo "  kubectl -n app logs deploy/command-parser" >&2
  fi
else
  echo "WARNING: no v1 model in object storage yet."
  echo "Train and upload it (make train, make seed-models) or publish/fetch the Release asset,"
  echo "then run: kubectl -n app rollout restart deployment/command-parser"
fi

cat <<EOF

Bring-up complete.
  Ingress:    http://localhost:8080/parse
  Health:     http://localhost:8080/healthz   http://localhost:8080/readyz
  Storage:    kubectl -n data port-forward svc/seaweedfs-s3 8333:8333
  Status:     kubectl -n app get deploy,svc,ingress,hpa,pods
EOF
