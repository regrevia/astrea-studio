#!/usr/bin/env bash
# 在本机跑一份前端 standalone 构建，给 nginx 的 :3001 上游用。
#
# 为什么需要：镜像里的前端是构建产物，改界面要重建镜像才能生效，而本机 docker build
# 拉基础镜像不稳。nginx 配置把前端指到 host.docker.internal:3001，因此在本机
# next build 之后起一个 standalone server，刷新页面就能看到改动。
#
# 用法：scripts/dev/serve-web-local.sh          # 构建并后台起在 :3001
#       scripts/dev/serve-web-local.sh --no-build
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WEB="$ROOT/project/aitoearn-web"
STANDALONE="$WEB/.next/standalone"
LOG="${STUDIO_WEB_LOG:-/tmp/studio-web-3001.log}"

if [ "${1:-}" != "--no-build" ]; then
  echo "== 构建前端"
  ( cd "$WEB" && ./node_modules/.bin/next build )
  mkdir -p "$STANDALONE/.next"
  rm -rf "$STANDALONE/.next/static"
  cp -a "$WEB/.next/static" "$STANDALONE/.next/static"
fi

[ -f "$STANDALONE/server.js" ] || { echo "没有构建产物：$STANDALONE/server.js，先不带 --no-build 跑一次" >&2; exit 1; }

# 自动登录靠 AUTO_LOGIN_TOKEN：web 只在启动时读一次，缺了页面会停在登录页。
TOKEN_FILE="${STUDIO_TOKEN_FILE:-$ROOT/../token.txt}"
if [ ! -f "$TOKEN_FILE" ]; then
  TOKEN_FILE=/tmp/auto_token.txt
fi
if [ ! -f "$TOKEN_FILE" ]; then
  echo "找不到登录 token：设置 STUDIO_TOKEN_FILE 指向容器里的 /data/init/token.txt" >&2
  exit 1
fi

if lsof -n -P -iTCP:3001 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "== 3001 已有进程，先停掉"
  kill "$(lsof -n -P -t -iTCP:3001 -sTCP:LISTEN)" 2>/dev/null || true
  sleep 1
fi

echo "== 启动 standalone（日志 $LOG）"
( cd "$STANDALONE" && AUTO_LOGIN_TOKEN="$(cat "$TOKEN_FILE")" PORT=3001 HOSTNAME=127.0.0.1 nohup node server.js > "$LOG" 2>&1 & )

for _ in $(seq 1 30); do
  code="$(curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/zh-CN/studio || true)"
  if [ "$code" = "200" ]; then
    echo "前端已就绪：http://127.0.0.1:8080/zh-CN/studio"
    exit 0
  fi
  sleep 1
done
echo "启动未通过，看日志：tail -40 $LOG" >&2
exit 1
