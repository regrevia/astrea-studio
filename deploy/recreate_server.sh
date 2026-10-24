#!/usr/bin/env bash
# 用本地已构建的镜像重建 aitoearn-server 容器。
# 为什么不用 docker compose：本机 docker-compose.yml 里还有 mongo/redis/ai 等
# 服务的 `pull_policy: always`，而 Docker Hub 在本机不可达（registry EOF），
# 导致 `up --force-recreate aitoearn-server` 在拉别的镜像时就整体失败。
set -euo pipefail

IMAGE="${1:-creator-studio/aitoearn-server:local}"
NAME=creatorstudio-aitoearn-server-1
ENVFILE="$HOME/CreatorStudio/config/compose.env"

# 从现有容器抄环境变量与启动参数，避免手写出错
# macOS 自带 bash 3.2 没有 mapfile，用 while read 兼容
ENVS=()
while IFS= read -r line; do
  [ -n "$line" ] && ENVS+=("$line")
done < <(docker inspect "$NAME" --format '{{range .Config.Env}}{{println .}}{{end}}')
MOUNTS=$(docker inspect "$NAME" --format '{{range .Mounts}}-v {{.Source}}:{{.Destination}} {{end}}')
NET=$(docker inspect "$NAME" --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')

echo "镜像: $IMAGE"
echo "网络: $NET"

docker rm -f "$NAME" >/dev/null 2>&1 || true

ENVARGS=()
for e in "${ENVS[@]}"; do
  case "$e" in
    PATH=*|HOSTNAME=*|HOME=*|NODE_VERSION=*|YARN_VERSION=*|PKG_RELEASE=*|NODE_ENV=*) ;;
    *) ENVARGS+=(-e "$e") ;;
  esac
done

# shellcheck disable=SC2086
docker run -d \
  --name "$NAME" \
  --network "$NET" \
  --network-alias aitoearn-server \
  --restart unless-stopped \
  "${ENVARGS[@]}" \
  $MOUNTS \
  --health-cmd "node -e \"require('http').get('http://localhost:3002/health', r => process.exit(r.statusCode === 200 ? 0 : 1))\"" \
  --health-interval 30s --health-timeout 10s --health-retries 3 --health-start-period 60s \
  --entrypoint docker-entrypoint.sh \
  "$IMAGE" \
  sh -c 'exec pm2-runtime start apps/${APP_NAME}/src/main.js --name ${APP_NAME} -- -c config.yaml'

echo "已启动，等待 healthy..."
for i in $(seq 1 30); do
  sleep 4
  st=$(docker inspect "$NAME" --format '{{.State.Health.Status}}' 2>/dev/null || echo unknown)
  echo "  [$i] $st"
  [ "$st" = "healthy" ] && break
done
