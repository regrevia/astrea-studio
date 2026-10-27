#!/usr/bin/env bash
# 把本机编译出来的后端产物热补进正在跑的服务容器，绕开重建镜像。
#
# 为什么需要：本机 docker build 拉基础镜像时报 TLS 错误，改了后端代码就无法进容器，
# 而容器里的 apps/<app>/src 就是编译后的 JS（pm2-runtime 直接跑 src/main.js）。
# 因此在本机 nx build 之后，把 dist 里的 JS 覆盖进容器同名目录再重启即可。
#
# 用法：scripts/dev/patch-backend-container.sh [容器名] [应用名]
# 前置：project/aitoearn-backend 已装依赖；本机可 docker exec 目标容器。
set -euo pipefail

CONTAINER="${1:-creatorstudio-aitoearn-server-1}"
APP="${2:-aitoearn-server}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/project/aitoearn-backend"
TAR_DIR="$(mktemp -d)"

cleanup() { rm -rf "$TAR_DIR"; }
trap cleanup EXIT

echo "== 1/4 编译 $APP"
cd "$BACKEND"
env NX_DAEMON=false NX_TUI=false NX_NO_CLOUD=true NX_ISOLATE_PLUGINS=false \
  ./node_modules/.bin/nx build "$APP"

APP_DIST="dist/apps/$APP/src"
[ -d "$APP_DIST" ] || { echo "编译产物不存在：$APP_DIST" >&2; exit 1; }

echo "== 2/4 打包产物"
tar -C "$APP_DIST" -cf "$TAR_DIR/app.tar" .
# 容器里 libs/*/src 同样是编译后的 JS，workspace 依赖靠 node_modules/@yikart/* 软链指过去。
# 只覆盖各 lib 的 src，不动容器里已有的 package.json。
if [ -d dist/libs ]; then
  ( cd dist/libs && find . -mindepth 2 -maxdepth 2 -type d -name src -print0 | xargs -0 tar -cf "$TAR_DIR/libs.tar" )
fi

echo "== 3/4 覆盖进容器 $CONTAINER"
docker cp "$TAR_DIR/app.tar" "$CONTAINER:/tmp/app.tar"
docker exec "$CONTAINER" sh -lc "cd /app/apps/$APP/src && tar -xf /tmp/app.tar && rm -f /tmp/app.tar"
if [ -f "$TAR_DIR/libs.tar" ]; then
  docker cp "$TAR_DIR/libs.tar" "$CONTAINER:/tmp/libs.tar"
  docker exec "$CONTAINER" sh -lc 'cd /app/libs && tar -xf /tmp/libs.tar && rm -f /tmp/libs.tar'
fi

echo "== 4/4 重启并等健康检查"
docker restart "$CONTAINER" >/dev/null
for _ in $(seq 1 30); do
  code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz || true)"
  if [ "$code" = "200" ]; then echo "健康检查通过"; exit 0; fi
  sleep 2
done
echo "健康检查未通过，查看容器日志：docker logs --tail 80 $CONTAINER" >&2
exit 1
