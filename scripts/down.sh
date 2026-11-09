#!/usr/bin/env bash
# Delete the local k3d cluster. Model artefacts in models/ are left untouched.
set -euo pipefail
cd "$(dirname "$0")/.."

CLUSTER="${CLUSTER:-kd-cloud}"

if ! command -v k3d >/dev/null 2>&1; then
  echo "k3d is not installed; nothing to delete" >&2
  exit 0
fi

if k3d cluster list 2>/dev/null | awk '{print $1}' | grep -qx "$CLUSTER"; then
  k3d cluster delete "$CLUSTER"
else
  echo "cluster $CLUSTER does not exist"
fi
