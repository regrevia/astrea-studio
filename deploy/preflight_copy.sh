#!/usr/bin/env bash
# 出片前的五道回归：文案判据、手势同步、立绘接图、配音缓存、人物层判据。
#
#   bash deploy/preflight_copy.sh            # 全跑，任一不过就退出 1
#   STUDIO_PREFLIGHT_TIMEOUT=600 bash ...    # 单个脚本的超时（秒）
#
# 回归里有真合成（PIL 抠图 + PNG 编码），慢是正常的，卡住不是：
# 每个脚本都套 alarm，宁可报超时也不要让门禁无声无息地停在这一步。
set -uo pipefail

. "$(cd "$(dirname "$0")" && pwd)/py3.sh"
PY3=$(studio_py3)

HERE=$(cd "$(dirname "$0")" && pwd)
MW="$HERE/../integrations/mac-worker"
LIMIT="${STUDIO_PREFLIGHT_TIMEOUT:-420}"

run() {
  local label="$1" script="$2"
  printf '  %s ... ' "$label"
  perl -e 'alarm shift; exec @ARGV' "$LIMIT" "$PY3" "$script" >/tmp/preflight_$(basename "$script" .py).log 2>&1
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "通过"
  elif [ "$rc" -ge 128 ]; then
    echo "超时（${LIMIT}s），日志 /tmp/preflight_$(basename "$script" .py).log"
    return 1
  else
    echo "未通过，日志 /tmp/preflight_$(basename "$script" .py).log"
    tail -5 /tmp/preflight_$(basename "$script" .py).log | sed 's/^/      /'
    return 1
  fi
}

fail=0
run "文案判据" "$MW/test_copy_calibration.py" || fail=1
run "手势同步" "$MW/test_gesture_sync.py" || fail=1
run "立绘接图" "$MW/test_character_assets.py" || fail=1
run "配音缓存" "$HERE/test_tts_cache.py" || fail=1
run "人物层判据" "$MW/test_layer_qa.py" || fail=1
[ "$fail" -eq 0 ] || { echo "回归未过，先修再出片"; exit 1; }
echo "五道回归全过"
