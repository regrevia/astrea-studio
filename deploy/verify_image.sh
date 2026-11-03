#!/usr/bin/env bash
# 镜像内 Node 产物的完整性检查。
#
# 为什么需要：本机 Docker VM 上出现过构建缓存 / pnpm store 被半截字节覆写的情况，
# `docker build` 照样成功，但运行期 require 一个包里随机某行变成
# `provid    tadata: {` 之类的残片，报 SyntaxError。光看构建退出码发现不了，
# 必须在镜像产出后对文件内容本身做一次语法检查，坏镜像在构建阶段就拦下来。
#
# 用法：
#   bash deploy/verify_image.sh creator-studio/aitoearn-ai:local          # 快速：*.cjs + apps/libs 的 *.js
#   bash deploy/verify_image.sh creator-studio/aitoearn-ai:local --full   # 全量：镜像里所有 *.js（慢，约 5-10 分钟）
#
# 退出码：0 全部通过；1 有文件语法错误（脚本会列出前 20 个坏文件）。
set -uo pipefail

IMG="${1:?usage: verify_image.sh <image> [--full]}"
MODE="${2:-fast}"

echo "=== verify $IMG ($MODE)"

docker run --rm -e MODE="$MODE" --entrypoint sh "$IMG" -c '
set -u
list=/tmp/js-files.txt
: > "$list"
if [ "$MODE" = "full" ]; then
  find /app \( -name "*.js" -o -name "*.cjs" \) -type f >> "$list"
else
  find /app -name "*.cjs" -type f >> "$list"
  for d in /app/apps /app/libs; do
    [ -d "$d" ] && find "$d" -name "*.js" -type f >> "$list"
  done
fi
total=$(wc -l < "$list" | tr -d " ")
echo "checking $total files"
bad=0
while IFS= read -r f; do
  if ! node --check "$f" >/dev/null 2>&1; then
    bad=$((bad + 1))
    [ "$bad" -le 20 ] && echo "BAD $f"
  fi
done < "$list"
if [ "$bad" -gt 0 ]; then
  echo "FAILED: $bad of $total files are broken"
  exit 1
fi
echo "OK: all $total files parse"
'
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "!! 镜像 $IMG 产物损坏（node --check 失败），不要拿去跑" >&2
  exit 1
fi
