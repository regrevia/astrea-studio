#!/usr/bin/env bash
# 安装/卸载草稿守护（local.creatorstudio.draftguard）。
#
# 和 worker 一样，launchd 读不了 ~/Desktop（TCC），所以先 sync 运行时再注册。
# 守护属于「平台自己该做的事」，不需要人工点：装一次，登录后自动起。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="local.creatorstudio.draftguard"
PLIST_SRC="$REPO/deploy/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME="$HOME/CreatorStudio/runtime/integrations/mac-worker"

case "${1:-install}" in
  install)
    [ -f "$PLIST_SRC" ] || { echo "missing $PLIST_SRC" >&2; exit 1; }
    mkdir -p "$HOME/CreatorStudio/logs" "$HOME/Library/LaunchAgents"
    if [ -x "$REPO/scripts/sync_runtime.sh" ]; then
      bash "$REPO/scripts/sync_runtime.sh"
    fi
    # 手起过的守护会和 launchd 抢同一份流水账，先停掉
    pkill -f "draft_guard" 2>/dev/null && { echo "stopped a hand-started guard"; sleep 1; }
    sed "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
    echo "installed $LABEL"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST_DST"
    echo "removed $LABEL"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null \
      | grep -E "state|pid|last exit" || echo "$LABEL is not loaded"
    ;;
  restart)
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "restarted $LABEL"
    ;;
  once)
    # 手动跑一轮：dry-run 看它会做什么，不动任何草稿
    shift
    "$RUNTIME/.python3" -m export.draft_guard --once "$@"
    ;;
  *)
    echo "usage: $0 [install|uninstall|status|restart|once [--dry-run|--json]]" >&2
    exit 2
    ;;
esac
