#!/usr/bin/env bash
# Build the web (Next.js) image.
#
# aitoearn-web is a separate repo with its own Dockerfile — it is NOT an nx
# project, so the backend helper (scripts/build-docker.mjs) fails on it with
# "项目 aitoearn-web 构建失败". Build it here with plain docker build instead.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$REPO/project/aitoearn-web"
TAG="${STUDIO_BUILD_TAG:-local}"
TARGET="creator-studio/aitoearn-web:${TAG}"

if [ ! -d "$WEB" ]; then
  echo "!! web source missing at $WEB" >&2
  exit 1
fi

# Docker Hub is unreliable on this machine: `load metadata for node:lts-alpine`
# fails with a bare "EOF" when reaching registry-1.docker.io directly. buildx
# does not use Docker Desktop's proxy, so pass a reachable proxy through the
# environment explicitly (build_images.sh does the same for the backend).
PROXY="${STUDIO_BUILD_PROXY:-http://127.0.0.1:7897}"
# Probe the proxy port, not registry-1.docker.io: /v2/ answers 401 by design and
# curl reports a failed handshake as 000, so an HTTP-code probe would both
# misread a healthy proxy and a blocked one.
PROXY_HOSTPORT="${PROXY#*://}"
PROXY_HOST="${PROXY_HOSTPORT%%:*}"
PROXY_PORT="${PROXY_HOSTPORT##*:}"
if nc -z "$PROXY_HOST" "$PROXY_PORT" 2>/dev/null; then
  export HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
  export http_proxy="$PROXY" https_proxy="$PROXY"
  export NO_PROXY="localhost,127.0.0.1,host.docker.internal"
  echo "=== building through $PROXY"
else
  echo "=== no build proxy listening at $PROXY_HOST:$PROXY_PORT; building direct"
fi

echo "=== building $TARGET from $WEB"
# buildx does not inherit the shell proxy: without these args every RUN inside
# the image talks to the internet directly, and corepack's pnpm download dies
# with a bare "fetch failed" (HTTP_PROXY/HTTPS_PROXY are predefined build args).
PROXY_ARGS=()
if [ -n "${HTTP_PROXY:-}" ]; then
  # 容器里的 127.0.0.1 是容器自己，必须换成 host.docker.internal 才连得上宿主代理。
  IN_PROXY="${HTTP_PROXY/\/\/127.0.0.1/\/\/host.docker.internal}"
  IN_PROXY="${IN_PROXY/\/\/localhost/\/\/host.docker.internal}"
  PROXY_ARGS+=(--build-arg "HTTP_PROXY=$IN_PROXY" --build-arg "HTTPS_PROXY=$IN_PROXY")
  PROXY_ARGS+=(--build-arg "http_proxy=$IN_PROXY" --build-arg "https_proxy=$IN_PROXY")
  PROXY_ARGS+=(--build-arg "NO_PROXY=${NO_PROXY:-localhost,127.0.0.1}")
  echo "=== in-container proxy $IN_PROXY"
fi

docker build \
  --platform linux/arm64 \
  --build-arg NEXT_TELEMETRY_DISABLED=1 \
  "${PROXY_ARGS[@]}" \
  -t "$TARGET" \
  "$WEB"

echo "=== $TARGET ready"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep "creator-studio/aitoearn-web" || true
