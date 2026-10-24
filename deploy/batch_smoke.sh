#!/usr/bin/env bash
# 批量冒烟：同一份 spec 连提 N 条 run，等全部结束，断言
#   - 每条都 succeeded
#   - 每条 run.output.videoQa.ok 为 true（成片体检无硬伤）
#   - 各条母版 md5 一致（同一输入必须渲染出同一条片子）
# 用法：bash deploy/batch_smoke.sh <spec.json> [条数]
set -uo pipefail

# 出片前先把文案判据回归跑一遍：判据一改，好稿被误判就会在这里先炸，不用等三分钟渲染
/usr/bin/python3 "$(dirname "$0")/../integrations/mac-worker/test_copy_calibration.py" || {
  echo "文案判据回归未通过，先修判据再出片"; exit 1; }
/usr/bin/python3 "$(dirname "$0")/../integrations/mac-worker/test_gesture_sync.py" || {
  echo "手势同步回归未通过，先修手势再出片"; exit 1; }
# 配音不稳，同稿两次出片的口型和时长就不一样，母版比对也失去意义
/usr/bin/python3 "$(dirname "$0")/test_tts_cache.py" || {
  echo "配音缓存回归未通过，先修配音再出片"; exit 1; }

SPEC="${1:?用法: batch_smoke.sh <spec.json> [条数]}"
N="${2:-3}"
API="${STUDIO_API:-http://127.0.0.1:8080}"
TOKEN="${STUDIO_TOKEN:-$HOME/CreatorStudio/config/user.token}"
[ -f "$TOKEN" ] && TOKEN=$(cat "$TOKEN")

post() {
  curl -s -m 120 -X POST "$API/api/production/runs" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data @"$SPEC" | /usr/bin/python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["runId"])'
}

get() {
  curl -s -m 20 -H "Authorization: Bearer $TOKEN" "$API/api/production/runs/$1"
}

IDS=()
if [ -n "${STUDIO_RUN_IDS:-}" ]; then
  # 想复查已经跑过的批次：STUDIO_RUN_IDS="id1 id2" 直接跳过提交
  read -r -a IDS <<< "$STUDIO_RUN_IDS"
  echo "复查 ${#IDS[@]} 条已存在的 run"
else
  echo "提交 $N 条..."
  for _ in $(seq 1 "$N"); do
    id=$(post) || { echo "提交失败"; exit 1; }
    echo "  $id"
    IDS+=("$id")
  done
fi

echo "等待渲染（每条约 2~3 分钟）..."
for round in $(seq 1 90); do
  sleep 15
  pending=0
  for id in "${IDS[@]}"; do
    st=$(get "$id" | /usr/bin/python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["run"]["status"])' 2>/dev/null || echo unknown)
    case "$st" in
      succeeded|failed|canceled) ;;
      *) pending=$((pending + 1)) ;;
    esac
  done
  echo "  [$round] 未完成 $pending"
  [ "$pending" -eq 0 ] && break
done

echo
fail=0
hashes=()
for id in "${IDS[@]}"; do
  line=$(get "$id" | /usr/bin/python3 -c '
import sys, json, hashlib, os, glob
run = json.load(sys.stdin)["data"]["run"]
out = run.get("output") or {}
qa = out.get("videoQa") or {}
# 体检结果里带成片绝对路径，直接用它；没有才按 run id 去 timeline 目录里找
paths = [qa.get("video")] if qa.get("video") else []
paths += glob.glob(os.path.expanduser("~/CreatorStudio/timeline/%s/**/master.mp4" % run["id"]), recursive=True)
paths = [x for x in paths if x and os.path.exists(x)]
h = hashlib.md5(open(paths[0], "rb").read()).hexdigest()[:10] if paths else "missing"
print("%s dur=%s qa=%s findings=%d md5=%s" % (
    run["status"], out.get("duration"), qa.get("ok"), len(qa.get("findings") or []), h))
' 2>/dev/null) || line="解析失败"
  echo "  $id $line"
  case "$line" in
    succeeded*dur=*qa=True*findings=0*) ;;
    *) fail=1 ;;
  esac
  hashes+=("$(echo "$line" | sed -n 's/.*md5=//p')")
done

uniq_count=$(printf '%s\n' "${hashes[@]}" | sort -u | wc -l | tr -d ' ')
echo
if [ "$fail" -ne 0 ]; then
  echo "=== 失败：有 run 未 succeeded 或体检不过"
  exit 1
fi
if [ "$uniq_count" -ne 1 ]; then
  echo "=== 失败：同一 spec 渲染出 $uniq_count 种母版（不幂等）"
  exit 1
fi
echo "=== 通过：$N 条全部 succeeded、体检干净、母版一致"
