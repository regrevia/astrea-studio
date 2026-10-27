#!/usr/bin/env bash
# 提交一份规格稿并盯到出片，把成片体检的关键项打出来。
#
#   bash deploy/submit_spec.sh deploy/specs/envoy_intro.json [条数]
#   STUDIO_PROJECT=<id> STUDIO_SPEC_COUNT=2 bash deploy/submit_spec.sh specs/foo.json
#
# 规格稿里的 projectId 允许写 {{PROJECT_ID}}：脚本会去问平台要一个能用的项目，
# 占位符原样交给接口只会回 12850 Production project not found。
set -uo pipefail

SPEC="${1:?用法: submit_spec.sh <spec.json> [条数]}"
N="${STUDIO_SPEC_COUNT:-${2:-1}}"
API="${STUDIO_API:-http://127.0.0.1:8080}"
TOKEN="${STUDIO_TOKEN:-$HOME/CreatorStudio/config/user.token}"
[ -f "$TOKEN" ] && TOKEN=$(cat "$TOKEN")
HERE=$(cd "$(dirname "$0")" && pwd)
. "$(cd "$(dirname "$0")" && pwd)/py3.sh"
PY3=$(studio_py3)

if [ "${STUDIO_SKIP_PREFLIGHT:-0}" = "1" ]; then
  echo "跳过出片前回归（STUDIO_SKIP_PREFLIGHT=1）"
else
  bash "$HERE/preflight_copy.sh" || exit 1
fi

BODY=$("$PY3" - "$SPEC" "$N" "$API" "$TOKEN" <<'PY'
import json, os, subprocess, sys
spec_path, count, api, tok = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
raw = json.load(open(spec_path))

def curl_json(args):
    out = subprocess.run(["curl", "-s", "-m", "60", "-H", "Authorization: Bearer " + tok] + args,
                         capture_output=True, text=True).stdout
    return json.loads(out or "{}")

body = dict(raw) if ("projectId" in raw and "spec" in raw) else {
    "projectId": os.environ.get("STUDIO_PROJECT", ""),
    "title": str(raw.get("topic") or os.path.basename(spec_path)),
    "brief": os.environ.get("STUDIO_BRIEF", "规格稿出片"),
    "engine": raw.get("engine"), "template": raw.get("template"), "spec": raw,
}
if str(body.get("projectId") or "").startswith("{{") or not body.get("projectId"):
    body["projectId"] = os.environ.get("STUDIO_PROJECT") or ""
if not body["projectId"]:
    data = curl_json([api + "/api/production/projects?limit=1"]).get("data") or []
    if not data:
        print("平台没有可用项目", file=sys.stderr)
        sys.exit(1)
    body["projectId"] = data[0]["id"]
body["count"] = count
print(json.dumps(body, ensure_ascii=False))
PY
) || exit 1

echo "提交 $N 条（$(basename "$SPEC")）"
OUT=$(curl -s -m 180 -X POST "$API/api/production/runs/batch" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" --data "$BODY")
IDS=$(printf '%s' "$OUT" | "$PY3" -c '
import json,sys
d=json.load(sys.stdin)
if d.get("code") != 0:
    print("ERR %s %s" % (d.get("code"), str(d.get("message"))[:160]), file=sys.stderr); sys.exit(1)
print(" ".join(d["data"]["runIds"]))') || exit 1
printf '  %s\n' $IDS

for i in $(seq 1 60); do
  sleep 15
  LINE=$(curl -s -m 25 -H "Authorization: Bearer $TOKEN" "$API/api/production/runs?limit=20" \
    | "$PY3" -c '
import json,sys
want=set(sys.argv[1:]); d=json.load(sys.stdin).get("data") or {}
done, rows = 0, []
for r in d.get("list") or []:
    if r.get("id") not in want: continue
    st=r.get("status"); o=r.get("output") or {}; q=o.get("videoQa") or {}
    rows.append("%s %s dur=%s qa=%s findings=%s copy=%s %s" % (
        r["id"][:8], st, o.get("duration"), q.get("ok"), len(q.get("findings") or []),
        (o.get("copyCheck") or {}).get("ok"), (r.get("lastError") or "")[:80]))
    if st in ("succeeded","failed","canceled"): done += 1
print("|".join(rows) + "|#%d/%d" % (done, len(want)))' $IDS)
  echo "  $(date +%H:%M:%S) $LINE"
  case "$LINE" in *"#${N}/${N}") echo "$LINE" | tr '|' '\n' | grep -E "^(.*) (succeeded|failed)" || true; break;; esac
done
