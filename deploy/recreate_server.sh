#!/usr/bin/env bash
# 用本地已构建的镜像重建 aitoearn-server 容器。
#
# 两个坑都踩过，所以这里不做「删了再 docker run」：
#   1. 先删容器再启动，镜像名/网络名写错就直接把服务删没了（nginx 502）。
#   2. 根目录 docker-compose.yml 是模板，服务名带 pull_policy: always，
#      Docker Hub 在本机不可达（registry EOF），一拉别的镜像就整体失败。
# 现在统一走 render_config.py 生成的运行时 compose（pull_policy: never），
# 并在重建后 reload nginx：容器 IP 变了，nginx 启动时解析的旧 IP 会 502。
set -euo pipefail

IMAGE="${1:-creator-studio/aitoearn-server:local}"
NAME=creatorstudio-aitoearn-server-1
COMPOSE="$HOME/CreatorStudio/config/compose.yml"
ENVFILE="$HOME/CreatorStudio/config/compose.env"

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "镜像不存在: $IMAGE（先跑 bash deploy/build_images.sh aitoearn-server）" >&2
  exit 1
}
[ -f "$COMPOSE" ] || { echo "缺少运行时配置: $COMPOSE（先跑 python3 deploy/render_config.py）" >&2; exit 1; }

echo "镜像: $IMAGE"
STUDIO_IMAGE_SERVER="$IMAGE" docker compose -p creatorstudio \
  --env-file "$ENVFILE" -f "$COMPOSE" \
  up -d --no-deps --pull never --force-recreate aitoearn-server

echo "等待 healthy..."
for i in $(seq 1 30); do
  sleep 4
  st=$(docker inspect "$NAME" --format '{{.State.Health.Status}}' 2>/dev/null || echo absent)
  echo "  [$i] $st"
  [ "$st" = "healthy" ] && break
done

# 容器 IP 会变，nginx 需要重新解析上游，否则 /api 一直 502
docker exec creatorstudio-nginx-1 nginx -s reload >/dev/null 2>&1 || true
echo "=== aitoearn-server up"
