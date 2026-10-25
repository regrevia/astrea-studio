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
/usr/bin/python3 "$(dirname "$0")/../integrations/mac-worker/test_character_assets.py" || {
  echo "立绘接图回归未通过，先修接图再出片"; exit 1; }
# 配音不稳，同稿两次出片的口型和时长就不一样，母版比对也失去意义
/usr/bin/python3 "$(dirname "$0")/test_tts_cache.py" || {
  echo "配音缓存回归未通过，先修配音再出片"; exit 1; }

SPEC="${1:?用法: batch_smoke.sh <spec.json> [条数]}"
N="${2:-3}"
API="${STUDIO_API:-http://127.0.0.1:8080}"
TOKEN="${STUDIO_TOKEN:-$HOME/CreatorStudio/config/user.token}"
[ -f "$TOKEN" ] && TOKEN=$(cat "$TOKEN")

get() {
  curl -s -m 20 -H "Authorization: Bearer $TOKEN" "$API/api/production/runs/$1"
}

# <spec.json> 可以是「脚本稿」，也可以是已经带 projectId + spec 的完整请求体。
# 脚本稿直接甩给 /runs 会缺 projectId/spec：接口回 code!=0，脚本只剩一句 KeyError。
IDS=()
if [ -n "${STUDIO_RUN_IDS:-}" ]; then
  # 想复查已经跑过的批次：STUDIO_RUN_IDS="id1 id2" 直接跳过提交
  read -r -a IDS <<< "$STUDIO_RUN_IDS"
  echo "复查 ${#IDS[@]} 条已存在的 run"
else
  # 走平台批量接口：一次拿到全部 runId，串行入队在服务端
  echo "批量提交 $N 条（/api/production/runs/batch）..."
  # macOS 自带 bash 3.2 没有 mapfile：用 while read 收 id
  while IFS= read -r line; do
    [ -n "$line" ] && IDS+=("$line")
  done < <(/usr/bin/python3 - "$SPEC" "$N" <<'PYAPI'
import json, subprocess, sys, os
spec_path, count = sys.argv[1], int(sys.argv[2])
raw = json.load(open(spec_path))
if "projectId" in raw and "spec" in raw:
    body = dict(raw)
else:
    spec = dict(raw)
    spec.setdefault("mode", "vertical_short")
    spec.setdefault("engine", "timeline")
    spec.setdefault("template", "factcard")
    body = {
        "projectId": os.environ.get("STUDIO_PROJECT", "6adc2161656a13996166bbfc"),
        "title": str(spec.get("topic") or os.path.basename(spec_path)),
        "brief": os.environ.get("STUDIO_BRIEF", "批量冒烟"),
        "engine": spec.get("engine"),
        "template": spec.get("template"),
        "spec": spec,
    }
body["count"] = count
api = os.environ.get("STUDIO_API", "http://127.0.0.1:8080")
tok = os.environ.get("STUDIO_TOKEN") or open(
    os.path.expanduser("~/CreatorStudio/config/user.token")).read().strip()
out = subprocess.run(["curl", "-s", "-m", "180", "-X", "POST",
                      api + "/api/production/runs/batch",
                      "-H", "Authorization: Bearer " + tok,
                      "-H", "Content-Type: application/json",
                      "--data", json.dumps(body, ensure_ascii=False)],
                     capture_output=True, text=True).stdout
try:
    data = json.loads(out)
except ValueError:
    print("batch 接口返回的不是 JSON：%s" % out[:200], file=sys.stderr)
    sys.exit(1)
if data.get("code") != 0:
    print("batch 接口返回 %s %s" % (data.get("code"), data.get("message")), file=sys.stderr)
    sys.exit(1)
for rid in data["data"]["runIds"]:
    print(rid)
PYAPI
  )
  [ "${#IDS[@]}" -eq "$N" ] || { echo "批量接口返回 ${#IDS[@]} 条，期望 $N 条"; exit 1; }
  printf '  %s\n' "${IDS[@]}"
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
# 念白留白：顶到切点是半句话被切走，拖太久是画面空等。留白要看得见
gap = "n/a"
if paths:
    spec_path = os.path.join(os.path.dirname(paths[0]), "spec.json")
    try:
        meta = (json.load(open(spec_path)) or {}).get("meta") or {}
        pace = meta.get("pacing") or {}
        lo, hi = float(pace["min_gap"]), float(pace["max_gap"])
        gap = "%.2f..%.2f" % (lo, hi)
        if lo < 0.05 or hi > 1.20:
            gap = "bad(" + gap + ")"
    except Exception:
        pass
# 文案判据：读起来像人写的这一条是平台自己算的，不绿就是套模板腔
cc = out.get("copyCheck") or {}
copy_state = "ok" if cc.get("ok") else ("bad" if cc else "n/a")
print("%s dur=%s qa=%s findings=%d md5=%s gap=%s copy=%s" % (
    run["status"], out.get("duration"), qa.get("ok"),
    len(qa.get("findings") or []), h, gap, copy_state))
' 2>/dev/null) || line="解析失败"
  echo "  $id $line"
  case "$line" in
    succeeded*dur=*qa=True*findings=0*gap=bad*|*copy=bad*) fail=1 ;;
    succeeded*dur=*qa=True*findings=0*gap=*copy=ok*) ;;
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
echo "=== 通过：${#IDS[@]} 条全部 succeeded、体检干净、母版一致"
