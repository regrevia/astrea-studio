#!/usr/bin/env bash
# 把「时间轴 spec → 母版 → 多平台规格」抽成一个可独立发布的仓库。
#
# 为什么用脚本而不是手抄一份：这些模块在本仓库还要继续演进，手抄必然漂移。
# 脚本只做三件事——复制源码、复制公开 README/示例/CI 模板、生成平台清单真值，
# 然后就地跑一遍测试，确保「发布包」不是靠运气能跑的。
#
# 用法：
#   bash scripts/export_timeline_repo.sh                 # -> dist/creator-studio-timeline
#   bash scripts/export_timeline_repo.sh --out /tmp/x    # 指定输出目录
#   bash scripts/export_timeline_repo.sh --smoke         # 额外真渲染一支 9 秒母版
#
# 产物目录本身可以直接 git init 推到新仓库（只差 GitHub 建仓这一步）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/integrations/mac-worker"
TPL="$ROOT/docs/opensource/timeline-repo"
OUT="$ROOT/dist/creator-studio-timeline"
SMOKE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="${2:?--out 需要一个目录}"; shift 2 ;;
    --smoke) SMOKE=1; shift ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

for f in "$SRC/export/timeline_render.py" "$SRC/export/deliver.py" \
         "$SRC/export/platform_profiles.py" "$SRC/platform_registry.py" \
         "$TPL/README.md" "$TPL/examples/spec.json" "$ROOT/LICENSE"; do
  [ -f "$f" ] || { echo "缺少文件: $f" >&2; exit 1; }
done

# 只在 dist/ 下清理，避免参数写错时误删。
case "$OUT" in
  "$ROOT/dist/"*) rm -rf "$OUT" ;;
  *) if [ -e "$OUT" ]; then echo "输出目录已存在且不在 dist/ 下，拒绝覆盖: $OUT" >&2; exit 1; fi ;;
esac
mkdir -p "$OUT/examples"

# 1) 源码与测试（同一份文件，不复制粘贴改内容）
for f in timeline_render.py deliver.py platform_profiles.py \
         test_timeline_render.py test_deliver.py test_platform_profiles.py; do
  cp "$SRC/export/$f" "$OUT/$f"
done
cp "$ROOT/LICENSE" "$OUT/LICENSE"

# 2) 公开文档/示例/CI 模板
cp -R "$TPL/." "$OUT/"

# 3) 演示素材生成器：deploy/ 下的版本把用法写成 deploy/...，发布包里改成本地 examples/...
cp "$ROOT/deploy/make_demo_assets.py" "$OUT/examples/make_demo_assets.py"
python3 - "$OUT/examples/make_demo_assets.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
s = s.replace("python3 deploy/make_demo_assets.py --out ~/demo/asset",
              "python3 examples/make_demo_assets.py --out examples/assets")
s = s.replace("python3 deploy/make_demo_assets.py                    # -> /tmp/demo_c/asset",
              "python3 examples/make_demo_assets.py --out examples/assets")
open(p, "w", encoding="utf-8").write(s)
PY

# 4) 平台清单真值：发布包里没有 platform_registry.py，把 LABELS 抽成 expected_labels.py，
#    这样 test_platform_profiles.py 在两个仓库里比对的仍是同一份真值。
python3 - "$SRC/platform_registry.py" "$OUT/expected_labels.py" <<'PY'
import importlib.util, pprint, sys
src, dst = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("_registry", src)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
labels = dict(mod.LABELS)
body = (
    '"""平台清单真值（由 scripts/export_timeline_repo.sh 从 platform_registry.LABELS 抽出）。\n\n'
    "不要手改：改了就等于让 test_platform_profiles.py 自己和自己比对。\n"
    "本仓库没有 SAU 依赖，这份清单只是「平台 id → 中文名」的发布包副本。\n"
    '"""\n\n'
    "LABELS = " + pprint.pformat(labels, width=88, sort_dicts=True) + "\n"
)
open(dst, "w", encoding="utf-8").write(body)
print(f"  expected_labels.py: {len(labels)} 个平台")
PY

# 5) 公开面自检：发布包里不能出现个人路径、内网地址、凭证
echo "== 公开面自检 =="
if grep -REn '/Users/[a-z]|192\.168\.|10\.[0-9]+\.[0-9]+\.[0-9]+:|COMPANY_LLM_TOKEN|sk-[A-Za-z0-9]{16,}' "$OUT" \
    --exclude-dir=.git >/tmp/export_timeline_forbidden.txt 2>/dev/null; then
  echo "✗ 发布包里出现不该公开的内容：" >&2
  cat /tmp/export_timeline_forbidden.txt >&2
  exit 1
fi
echo "  ✓ 无个人路径 / 内网地址 / 凭证"

# 6) 就地跑测试：发布包必须自己就能绿
echo "== 发布包自测 =="
# 只看 unittest 的汇总行：被测代码会往 stdout 打计划预览，混进来会盖住 OK/FAILED。
UNIT=$(cd "$OUT" && python3 -m unittest discover -p 'test_*.py' 2>&1 1>/dev/null | tail -3 || true)
echo "$UNIT" | sed 's/^/  /'
echo "$UNIT" | grep -q '^OK' || { echo "✗ unittest 未通过" >&2; exit 1; }
PP=$(cd "$OUT" && python3 test_platform_profiles.py 2>&1 | tail -1 || true)
echo "  $PP"
case "$PP" in *34/34*) ;; *) echo "✗ 平台档案测试未 34/34" >&2; exit 1 ;; esac

# 7) 可选：真渲染一支母版 + 出一个平台规格（需要 ffmpeg；有字所以还需要 Pillow）
if [ "$SMOKE" = 1 ]; then
  echo "== 真渲染冒烟 =="
  ( cd "$OUT" \
    && python3 examples/make_demo_assets.py --out examples/assets >/dev/null \
    && python3 timeline_render.py --spec examples/spec.json --out /tmp/dist_smoke_master.mp4 \
    && python3 deliver.py --master /tmp/dist_smoke_master.mp4 --platform douyin \
         --outdir /tmp/dist_smoke_out --evidence /tmp/dist_smoke_deliver.json >/dev/null \
    && python3 - <<'PY'
import json, subprocess
info = json.loads(subprocess.run(
    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
     "/tmp/dist_smoke_master.mp4"], capture_output=True, text=True, check=True).stdout)
v = next(s for s in info["streams"] if s["codec_type"] == "video")
assert (v["width"], v["height"]) == (1080, 1920), v
assert abs(float(info["format"]["duration"]) - 9.0) <= 0.2, info["format"]["duration"]
row = json.load(open("/tmp/dist_smoke_deliver.json"))["platforms"][0]
assert row["status"] == "ok" and row["bytes"] > 0, row
print(f"  ✓ 母版 {v['width']}x{v['height']} + {row['platform']} {row['probe']['width']}x{row['probe']['height']} "
      f"{row['bytes'] // 1024} KiB")
PY
  )
fi

echo
echo "== 产物 =="
( cd "$OUT" && find . -type f -not -path './examples/assets/*' | sort | sed 's/^/  /' )
echo
echo "OK: $OUT"
echo "下一步：git init 后推到新仓库（发布前请按 docs/opensource/PLAN.md 第 3 节再过一遍门）。"
