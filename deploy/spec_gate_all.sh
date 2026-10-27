#!/bin/bash
# 一次跑完全部样稿的验收：三个选题各自出片，逐条看体检、文案、母版一致性。
# 用法：bash deploy/spec_gate_all.sh [条数]
# 项目 id 默认取平台最近一条任务的，也可以用 STUDIO_PROJECT_ID 指定。
set -uo pipefail
DEPLOY_DIR=$(cd "$(dirname "$0")" && pwd)
. "$DEPLOY_DIR/py3.sh"
PY3=$(studio_py3)
cd "$(dirname "$0")/.." || exit 1
COUNT="${1:-1}"
SPECS="deploy/specs/envoy_intro.json deploy/specs/kv_cache.json deploy/specs/softmax.json"

PROJECT_ID="${STUDIO_PROJECT_ID:-}"
if [ -z "$PROJECT_ID" ]; then
  TOKEN=$(cat "$HOME/CreatorStudio/config/user.token" 2>/dev/null || true)
  PROJECT_ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:8080/api/production/runs?limit=1" \
    | "$PY3" -c 'import json,sys
try:
  print(json.load(sys.stdin)["data"]["list"][0].get("projectId") or "")
except Exception:
  print("")')
fi
if [ -z "$PROJECT_ID" ]; then
  echo "=== 失败：读不到平台项目（先跑一条任务，或用 STUDIO_PROJECT_ID 指定）"
  exit 1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
FAILED=0
PASSED=0
for spec in $SPECS; do
  name=$(basename "$spec" .json)
  filled="$TMP/$name.json"
  sed "s/{{PROJECT_ID}}/$PROJECT_ID/" "$spec" > "$filled"
  echo "--- $name"
  out=$(STUDIO_BATCH=1 bash deploy/batch_smoke.sh "$filled" "$COUNT" 2>&1 | tail -3)
  echo "$out"
  if echo "$out" | grep -q '=== 通过'; then
    PASSED=$((PASSED + 1))
  else
    FAILED=$((FAILED + 1))
  fi
done
echo
if [ "$FAILED" -eq 0 ]; then
  echo "=== 通过：$PASSED 个选题出片全绿"
  exit 0
fi
echo "=== 失败：$FAILED 个选题没过（$PASSED 个通过）"
exit 1
