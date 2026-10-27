#!/usr/bin/env bash
# 把仓库里的平台代码同步到 $STUDIO_HOME/runtime（launchd 实际跑的那一份）。
#
# 为什么需要这一步：launchd 起的常驻进程读不到桌面下的仓库，所以线上跑的是
# runtime 拷贝。仓库改了不同步 = 线上还是老逻辑（口型漂移那类修复就白做了）。
# 这个脚本是幂等的，跑完再 kickstart 一次即可。
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2
REPO="$PWD"
STUDIO_HOME="${STUDIO_HOME:-$HOME/CreatorStudio}"
RT="$STUDIO_HOME/runtime"
DEST="$RT/integrations/mac-worker"

[ -d "$RT" ] || { echo "没有 $RT：先跑一次 bootstrap（deploy/bootstrap_mpt.sh）" >&2; exit 1; }

rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' --exclude 'vendor' \
  "$REPO/integrations/mac-worker/" "$DEST/"
mkdir -p "$RT/deploy"
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
  "$REPO/deploy/" "$RT/deploy/"
# 顶层的几个入口也保持一致（worker.py / motion_render_engine.py 是旧位置）
rsync -a "$REPO/integrations/mac-worker/worker.py" "$RT/worker.py" 2>/dev/null || true
rsync -a "$REPO/integrations/mac-worker/motion_render_engine.py" "$RT/motion_render_engine.py" 2>/dev/null || true

echo "同步完成：$DEST"
echo "重启服务：launchctl kickstart -k gui/$(id -u)/local.creatorstudio.capabilities"
