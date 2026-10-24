#!/usr/bin/env bash
# Install (or remove) the Creator Studio worker as a per-user LaunchAgent.
#
# The worker used to be started by hand with nohup, which meant it died on
# logout and could be started twice by accident — two workers then race on the
# same render lane. launchd makes it a single supervised process that comes back
# after a crash and starts at login.
#
# Run this from a normal Terminal window. It must NOT be run from inside an
# automation harness: launchctl refuses to bootstrap agents from a supervised
# context, and that guard is deliberate.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="local.creatorstudio.worker"
PLIST_SRC="$REPO/deploy/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-install}" in
  install)
    if [ ! -f "$PLIST_SRC" ]; then
      echo "missing $PLIST_SRC" >&2
      exit 1
    fi
    mkdir -p "$HOME/CreatorStudio/logs" "$HOME/Library/LaunchAgents"
    # launchd's python cannot read ~/Desktop: macOS TCC blocks a background agent
    # from the protected Desktop folder, so the worker dies instantly with
    # "Operation not permitted". Mirror the runtime tree somewhere unprotected and
    # point the plist there; re-sync on every install so code edits still land.
    RUNTIME="$HOME/CreatorStudio/runtime"
    echo "syncing runtime to $RUNTIME"
    mkdir -p "$RUNTIME"
    rsync -a --delete "$REPO/integrations/" "$RUNTIME/integrations/"
    # Kill any hand-started worker first, or launchd's copy fights it for jobs.
    pkill -f "mac-worker/worker.py" 2>/dev/null && {
      echo "stopped a hand-started worker"
      sleep 2
    }
    # 仓库里的 plist 是模板（${HOME} 写成 __HOME__），安装时再渲染成本机绝对路径，
    # 这样仓库里不会出现某个人的用户名，而 launchd 仍然拿到真实路径。
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
  *)
    echo "usage: $0 [install|uninstall|status|restart]" >&2
    exit 2
    ;;
esac
