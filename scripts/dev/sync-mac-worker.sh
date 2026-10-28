#!/usr/bin/env bash
# 把仓库里的 mac-worker 同步到运行目录并重启 launchd worker。
# 为什么必须有这一步：launchd 跑的是 ~/CreatorStudio/runtime/integrations/mac-worker，
# 改了仓库里的代码不跑这个脚本，线上 worker 还是旧代码（导出失败原因会是旧文案）。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$REPO/integrations/mac-worker"
DST="${MAC_WORKER_DIR:-$HOME/CreatorStudio/runtime/integrations/mac-worker}"
[ -d "$SRC" ] || { echo "缺少源目录 $SRC" >&2; exit 2; }
[ -d "$DST" ] || { echo "缺少运行目录 $DST" >&2; exit 2; }
rsync -a --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  --exclude 'node_modules' --exclude '.venv' \
  "$SRC/" "$DST/"
echo "已同步 → $DST"
launchctl kickstart -k "gui/$(id -u)/local.creatorstudio.worker" 2>/dev/null \
  && echo "worker 已重启" || echo "worker 重启失败（检查 local.creatorstudio.worker）"
sleep 2
tail -3 "$HOME/CreatorStudio/logs/worker-stdout.log" | cut -c1-160 || true
