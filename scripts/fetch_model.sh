#!/usr/bin/env bash
# Download the v1 model Release asset and verify it against model.sha256.
#
# Repo resolution order: GITHUB_REPO env -> git origin -> skip.
# Release tag defaults to "final" (override with RELEASE_TAG).
set -euo pipefail
cd "$(dirname "$0")/.."

TAG="${RELEASE_TAG:-final}"
DEST="${MODEL_DIR:-models/v1}"
mkdir -p "$DEST"

if [[ -f "$DEST/model.tar.gz" && "${FORCE:-0}" != "1" ]]; then
  echo "$DEST/model.tar.gz already exists (use FORCE=1 to re-download)"
  exit 0
fi

REPO="${GITHUB_REPO:-}"
if [[ -z "$REPO" ]] && git remote get-url origin >/dev/null 2>&1; then
  REPO="$(git remote get-url origin | sed -E 's#.*github.com[:/]([^/]+/[^/.]+)(\.git)?#\1#')"
fi
if [[ -z "$REPO" ]]; then
  echo "No GitHub repository configured; set GITHUB_REPO=owner/repo or add an origin remote." >&2
  exit 1
fi

echo "downloading $TAG assets from $REPO into $DEST"
if command -v gh >/dev/null 2>&1; then
  gh release download "$TAG" -R "$REPO" -p model.tar.gz -p model.sha256 -D "$DEST"
else
  base="https://github.com/${REPO}/releases/download/${TAG}"
  curl -fL "$base/model.tar.gz" -o "$DEST/model.tar.gz"
  curl -fL "$base/model.sha256" -o "$DEST/model.sha256"
fi

(
  cd "$DEST"
  sha256sum -c model.sha256
)
echo "download and checksum verification complete: $DEST/model.tar.gz"
