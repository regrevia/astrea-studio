#!/usr/bin/env bash
# Recreate one application container after its image has been rebuilt.
#
# Why this script exists instead of `docker compose up --force-recreate`:
# the running stack was created by an older compose revision on the network
# `creatorstudio_aitoearn-network`, while the current docker-compose.yml
# declares `networks.aitoearn-network.name: aitoearn-network`. Compose then
# tries to delete the old network, finds five live endpoints (redis, rustfs,
# mongodb, server, nginx) and aborts *after* it has already removed the
# target containers. The site returns 502 until they are recreated by hand.
# Recreating on the existing network keeps nginx working (it proxies to the
# service names aitoearn-web / aitoearn-ai / aitoearn-server).
#
# Second trap: docker-compose.yml mounts the repository template
# project/aitoearn-backend/apps/aitoearn-ai/config/config.yaml, whose redis and
# mongodb passwords are placeholders ("password"). The live credentials are in
# ~/CreatorStudio/config/aitoearn-ai.yaml, so the ai container must mount the
# runtime file. Mounting the template yields WRONGPASS and an unhealthy ai.
#
# Usage: bash deploy/recreate_app.sh aitoearn-ai|aitoearn-web|aitoearn-server
set -euo pipefail

SERVICE="${1:?usage: recreate_app.sh <aitoearn-ai|aitoearn-web|aitoearn-server>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_CONFIG="${STUDIO_CONFIG_DIR:-$HOME/CreatorStudio/config}"
NETWORK="${STUDIO_NETWORK:-creatorstudio_aitoearn-network}"
HEALTH_ARGS=(--health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 60s)

wait_healthy() {
  local name="$1" i status
  for i in $(seq 1 30); do
    status="$(docker inspect "$name" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' 2>/dev/null || echo missing)"
    case "$status" in
      healthy) echo "=== $name healthy"; return 0 ;;
      exited|dead|missing) echo "!! $name is $status" >&2; docker logs --tail 30 "$name" >&2 || true; return 1 ;;
    esac
    sleep 5
  done
  echo "!! $name did not become healthy in 150s" >&2
  docker logs --tail 30 "$name" >&2 || true
  return 1
}

case "$SERVICE" in
  aitoearn-ai)
    IMAGE="${STUDIO_IMAGE_AI:-creator-studio/aitoearn-ai:local}"
    CFG="$RUNTIME_CONFIG/aitoearn-ai.yaml"
    [ -s "$CFG" ] || { echo "!! missing runtime config $CFG" >&2; exit 1; }
    docker rm -f aitoearn-ai >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    docker run -d --name aitoearn-ai --network "$NETWORK" --restart unless-stopped \
      -v "$CFG:/app/config.yaml:rw" \
      --health-cmd "node -e \"require('http').get('http://localhost:3010/health', r => process.exit(r.statusCode === 200 ? 0 : 1))\"" \
      "${HEALTH_ARGS[@]}" \
      "$IMAGE" >/dev/null
    ;;
  aitoearn-web)
    IMAGE="${STUDIO_IMAGE_WEB:-creator-studio/aitoearn-web:local}"
    # The Next.js container refuses to boot without the auto-login token that
    # the one-shot aitoearn-init service wrote into the init volume.
    docker rm -f aitoearn-web >/dev/null 2>&1 || true
    docker run -d --name aitoearn-web --network "$NETWORK" --restart unless-stopped \
      -v creatorstudio_init-data:/data/init:ro \
      -e NODE_ENV=production -e NEXT_TELEMETRY_DISABLED=1 \
      --health-cmd "node -e \"require('http').get('http://localhost:3000', r => process.exit(r.statusCode < 500 ? 0 : 1)).on('error', () => process.exit(1))\"" \
      --health-interval 10s --health-timeout 5s --health-retries 6 \
      --entrypoint /bin/sh \
      "$IMAGE" -c 'AUTO_LOGIN_TOKEN=$(cat /data/init/token.txt 2>/dev/null || true); if [ -z "$AUTO_LOGIN_TOKEN" ]; then echo "AUTO_LOGIN_TOKEN is missing" >&2; exit 1; fi; export AUTO_LOGIN_TOKEN; exec node server.js' >/dev/null
    ;;
  aitoearn-server)
    # server has its own script because it runs under pm2-runtime.
    exec bash "$REPO/deploy/recreate_server.sh" "${STUDIO_IMAGE_SERVER:-}"
    ;;
  *)
    echo "!! unknown service $SERVICE" >&2
    exit 2
    ;;
esac

# nginx resolves upstream container names once at config load, so a recreated
# container on a new IP stays unreachable (502) until nginx is reloaded.
echo "=== reloading nginx"
docker restart creatorstudio-nginx-1 >/dev/null
wait_healthy "$SERVICE"
curl -s -m 15 -o /dev/null -w "=== studio http %{http_code}\n" http://127.0.0.1:8080/zh-CN/studio || true
