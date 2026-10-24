#!/usr/bin/env bash
# 把面板需要的宿主状态镜像到 ~/CreatorStudio/state/。
#
# 为什么需要：launchd 后台进程读不到 ~/Desktop（macOS TCC 保护），而 cookie
# 和 MPT 源码都在 Desktop 下的仓库里。所以由这个脚本（在可交互/正常权限下跑）
# 把关键文件复制到非保护目录，能力服务再从那里读。
#
# 只复制元数据与源码常量，不复制 .venv、模型权重等大件。
set -euo pipefail

REPO="$HOME/Desktop/work/Studio/creator-studio/integrations/vendor"
STATE="$HOME/CreatorStudio/state"
SAU_OUT="$STATE/sau/cookies"
MPT_OUT="$STATE/MoneyPrinterTurbo/app/services"

mkdir -p "$SAU_OUT" "$MPT_OUT"

# 1) cookies：只同步 json（登录凭证），跳过大图/二维码
if [ -d "$REPO/social-auto-upload/cookies" ]; then
    rsync -a --include='*.json' --exclude='*' \
        "$REPO/social-auto-upload/cookies/" "$SAU_OUT/" 2>/dev/null || \
        cp "$REPO"/social-auto-upload/cookies/*.json "$SAU_OUT/" 2>/dev/null || true
fi

# 2) SAU venv 里的 python 路径（能力服务的 check 子命令要用）
if [ -x "$REPO/social-auto-upload/.venv/bin/python" ]; then
    ln -sfn "$REPO/social-auto-upload/.venv" "$STATE/sau/.venv" 2>/dev/null || true
fi
# sau_cli.py 也要（check 子命令入口）
cp "$REPO/social-auto-upload/sau_cli.py" "$STATE/sau/" 2>/dev/null || true
# sau_commands 包
if [ -d "$REPO/social-auto-upload/sau_commands" ]; then
    rsync -a --exclude '__pycache__' \
        "$REPO/social-auto-upload/sau_commands/" "$STATE/sau/sau_commands/" 2>/dev/null || true
fi

# 3) MPT 的音色常量（只要 sami_tts.py 这一个文件）
cp "$REPO/MoneyPrinterTurbo/app/services/sami_tts.py" "$MPT_OUT/" 2>/dev/null || true

echo "synced -> $STATE"
ls -1 "$SAU_OUT" 2>/dev/null | head -5
