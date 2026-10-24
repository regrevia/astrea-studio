#!/usr/bin/env bash
# Build the application images locally.
#
# Two things this script works around:
#   1. the upstream build helper shells out to nx, and the nx daemon wedges on
#      this machine (0% CPU, no children) — always run with NX_DAEMON=false;
#   2. the helper tags images `<app>:<UTCdate>-<shortsha>` while compose reads
#      `creator-studio/<app>:${STUDIO_BUILD_TAG}`. We resolve the produced tag
#      from docker itself after the build and retag it — never by guessing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO/project/aitoearn-backend"
TAG="${STUDIO_BUILD_TAG:-local}"
APPS=("$@")
# aitoearn-web is a separate Next.js repo — build it with build_web.sh.
[ ${#APPS[@]} -eq 0 ] && APPS=(aitoearn-server aitoearn-ai)

# This helper drives nx. aitoearn-web is not an nx project, so asking for it here
# fails deep inside the nx wrapper with a misleading "项目构建失败" and no compile
# error. Refuse up front and name the right script instead.
for app in "${APPS[@]}"; do
  if [ "$app" = "aitoearn-web" ]; then
    echo "error: aitoearn-web is not an nx project; build it with ./deploy/build_web.sh" >&2
    exit 2
  fi
done

# apk inside the build container cannot reach dl-cdn.alpinelinux.org directly on
# this machine ("TLS: unspecified error"), so `apk add ffmpeg` fails. Docker
# Desktop's own proxy is not used by buildx here; pass the host proxy through
# explicitly when one is reachable.
#
# Two addresses are needed. The proxy only listens on the host's 127.0.0.1, so
# the probe must use that. Inside the build container 127.0.0.1 is the container
# itself, so the value handed to buildx must be host.docker.internal (Docker
# Desktop forwards it to the host loopback). Probing with host.docker.internal
# always failed with exit 5 (unresolvable from the host), which made this script
# silently build direct and then die on `apk add` — the bug that surfaced as
# "process /bin/sh -c apk add --no-cache curl ffmpeg did not complete".
PROXY_PORT="${STUDIO_BUILD_PROXY_PORT:-7897}"
PROXY_CONTAINER="${STUDIO_BUILD_PROXY:-http://host.docker.internal:${PROXY_PORT}}"
# A caller passing STUDIO_BUILD_PROXY=http://127.0.0.1:7897 means "the proxy on
# my host". Inside a build container 127.0.0.1 is the container itself, so that
# value would silently kill `apk add`. Rewrite loopback to the host alias unless
# the caller already named a different host.
case "$PROXY_CONTAINER" in
  http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*)
    PROXY_CONTAINER="http://host.docker.internal:${PROXY_CONTAINER##*:}"
    ;;
esac
if nc -z 127.0.0.1 "$PROXY_PORT" 2>/dev/null; then
  # buildx passes HTTP_PROXY/HTTPS_PROXY into the build automatically; the
  # upstream helper hardcodes its own `docker buildx build` line, so the
  # environment is the only channel available.
  export HTTP_PROXY="$PROXY_CONTAINER" HTTPS_PROXY="$PROXY_CONTAINER"
  export http_proxy="$PROXY_CONTAINER" https_proxy="$PROXY_CONTAINER"
  export NO_PROXY="localhost,127.0.0.1,host.docker.internal"
  echo "=== building through $PROXY_CONTAINER (host port $PROXY_PORT)"
else
  echo "=== no build proxy listening on 127.0.0.1:$PROXY_PORT; building direct"
fi
export DOCKER_BUILDKIT=1

latest_tag() {  # newest non-:local tag for one app
  docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' \
    | grep -E "(^|/)${1}:" | grep -v ':local ' \
    | sort -k2 | tail -1 | awk '{print $1}'
}

for app in "${APPS[@]}"; do
  echo "=== building $app (NX_DAEMON=false)"
  # export, not a prefix: the helper shells out to `npx nx build` in a child
  # process, and only an exported NX_DAEMON=false reaches it.
  ( cd "$BACKEND" && export NX_DAEMON=false && node scripts/build-docker.mjs "$app" --platform linux/arm64 )

  produced="$(latest_tag "$app")"
  if [ -z "$produced" ]; then
    echo "!! no image named '$app' found after the build" >&2
    docker images | grep -i "$app" || true
    exit 1
  fi

  target="creator-studio/${app}:${TAG}"
  echo "=== tagging $produced -> $target"
  docker tag "$produced" "$target"
done

echo
docker images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' \
  | grep -E "creator-studio/aitoearn|aitoearn/aitoearn-ai" || true
