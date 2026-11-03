#!/usr/bin/env bash
# Build the web (Next.js) image.
#
# aitoearn-web is a separate repo with its own Dockerfile — it is NOT an nx
# project, so the backend helper (scripts/build-docker.mjs) fails on it with
# "项目 aitoearn-web 构建失败".
#
# Why the build runs on the host instead of inside the image: in this Docker VM
# `next build` reliably hangs at "Collecting page data ..." — the parent and its
# eight jest-worker children all sit at 0 CPU and the build never finishes
# (reproduced three times, killed after 40+ minutes each). The exact same source
# builds in ~50s on the host, so the stall is the VM's worker scheduling, not the
# code. This script therefore does:
#   1. `next build` on the host (the checkout's own node_modules),
#   2. stage the standalone output into a temp dir,
#   3. `docker build` a thin image that only copies those artifacts
#      (deploy/Dockerfile.web-prebuilt) — no network, no pnpm inside the image.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$REPO/project/aitoearn-web"
TAG="${STUDIO_BUILD_TAG:-local}"
TARGET="creator-studio/aitoearn-web:${TAG}"
BASE_IMAGE="${STUDIO_IMAGE_NODE:-creator-studio/node:22-alpine}"

if [ ! -d "$WEB" ]; then
  echo "!! web source missing at $WEB" >&2
  exit 1
fi
if [ ! -x "$WEB/node_modules/.bin/next" ]; then
  echo "!! $WEB/node_modules/.bin/next missing — run pnpm install in $WEB first" >&2
  exit 1
fi
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  echo "!! base image $BASE_IMAGE missing — run deploy/build_images.sh first" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. host build
# ---------------------------------------------------------------------------
echo "=== next build on host ($WEB)"
# Production env files must be present: the build inlines NEXT_PUBLIC_* values.
export NEXT_TELEMETRY_DISABLED=1
export CI=1
( cd "$WEB" && ./node_modules/.bin/next build )

BUILD_ID="$(cat "$WEB/.next/BUILD_ID")"
echo "=== host build done, BUILD_ID=$BUILD_ID"
if [ ! -d "$WEB/.next/standalone" ]; then
  echo "!! .next/standalone missing — next.config.mjs must keep output: 'standalone'" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. stage artifacts (project/aitoearn-web/.dockerignore excludes .next, so we
#    cannot feed that tree to docker build directly)
# ---------------------------------------------------------------------------
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/studio-web-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/app/.next"
cp -R "$WEB/.next/standalone/." "$STAGE/app/"
cp -R "$WEB/.next/static" "$STAGE/app/.next/static"
if [ -d "$WEB/public" ]; then
  cp -R "$WEB/public" "$STAGE/app/public"
fi
cp "$REPO/deploy/Dockerfile.web-prebuilt" "$STAGE/Dockerfile"
echo "=== staged $(du -sh "$STAGE/app" | cut -f1) of artifacts"

# ---------------------------------------------------------------------------
# 3. image
# ---------------------------------------------------------------------------
echo "=== building $TARGET (base $BASE_IMAGE)"
docker build \
  --platform linux/arm64 \
  --build-arg "WEB_BASE_IMAGE=$BASE_IMAGE" \
  -f "$STAGE/Dockerfile" \
  -t "$TARGET" \
  "$STAGE"

# No network inside the image: the only way it can be broken is a bad copy, so
# assert the entry point and a real build id are there.
out="$(docker run --rm --entrypoint sh "$TARGET" -c 'ls server.js .next/static >/dev/null && cat .next/BUILD_ID')"
if [ "$out" != "$BUILD_ID" ]; then
  echo "!! image BUILD_ID '$out' != host '$BUILD_ID'" >&2
  exit 1
fi
echo "=== $TARGET ready (BUILD_ID=$out)"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep "creator-studio/aitoearn-web" || true
