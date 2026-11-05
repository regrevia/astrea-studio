#!/usr/bin/env bash
# build.sh — Envoy 宣传片整条链路，一条命令。
#
#   文案（deploy/presenter/promo_envoy.py 的 SCENES）
#     → 配音 tools/promo/tts_envoy.py
#     → （同一步里已产出逐字 .words.json 与 .beats.json，无需另跑对齐工具）
#     → 卡面 + 人物层 + 时间线 promo_envoy.py
#     → 渲染 timeline_render.py
#     → 响度（线性增益 + 限幅）
#     → 交付 tools/verify_delivery.py（16 KB 冷读校验，避开这块 USB 盘的大块读串数据）
#
# 每一步都幂等：产物比输入新就跳过。改文案就 `--from tts`；只换了卡面排版就
# `--from spec`；什么都没改只想要回同一个文件，直接跑，全程约 10 秒（人物层命中缓存）。
#
# 用法：
#   tools/promo/build.sh                    # 从头到尾，能跳的全跳
#   PROMO_ANGLE=proof PROMO_SEED=2 tools/promo/build.sh   # 让生成器出新稿（过 copy_lint）
#   PROMO_ANGLE=best tools/promo/build.sh                # 3×9 候选打分选最高分那版
#   tools/promo/build.sh --from spec        # 从卡面/时间线开始
#   tools/promo/build.sh --from render      # 只重渲 + 混音（改了 timeline_render 时）
#   tools/promo/build.sh --force            # 全部重做（人物层也会重建，约 40 秒）
#   tools/promo/build.sh --no-deliver       # 只出到 ${PROMO_WORK}，不碰交付盘
#
# 目录（都可用环境变量改）：PROMO_WORK=$HOME/CreatorStudio/work/envoy-promo
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${STUDIO_PY:-$HOME/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python}"
WORK="${PROMO_WORK:-$HOME/CreatorStudio/work/envoy-promo}"
AUD="$WORK/audio"
SPEC="$WORK/timeline.json"
RAW="$WORK/promo_raw.mp4"
OUT="$WORK/promo.mp4"
DELIVER="${PROMO_DELIVER:-/Volumes/US202/CreatorStudio/deliver/envoy-promo.mp4}"
# 成片响度：线性增益 + 限幅。不要换动态 loudnorm，会抽气。
# 限幅留 0.80（≈−1.9 dBTP）：0.84 时真峰 −1.4，已经越过 −1.5 dBTP 那条线。
AF="${PROMO_AF:-volume=2.6dB,alimiter=limit=0.80:level=false:attack=3:release=60}"

FROM="copy"
FORCE=0
DELIVER_ON=1
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --no-deliver) DELIVER_ON=0; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

steps=(copy tts spec render mux deliver)
case "$FROM" in
  copy|tts|spec|render|mux|deliver) ;;
  *) echo "--from 只认: ${steps[*]}" >&2; exit 2 ;;
esac
START=0
for i in "${!steps[@]}"; do [ "${steps[$i]}" = "$FROM" ] && START=$i; done
want() { local i; for i in "${!steps[@]}"; do
  [ "${steps[$i]}" = "$1" ] && { [ "$i" -ge "$START" ]; return $?; }
done; return 1; }
# 上游任何一步要做，下游都别想跳
dirty=""

# 比输入新就不做；$FORCE 或上游做过就强制做
fresh() { [ "$FORCE" = 0 ] && [ -z "$dirty" ] && [ -f "$1" ] && [ "$1" -nt "$2" ]; }
mark() { dirty="1"; }

mkdir -p "$WORK"
log() { printf '\n== %s ==\n' "$*"; }

# 0) 文案（可选）-----------------------------------------------------
# 给了 PROMO_ANGLE / PROMO_SEED 就让生成器出稿并过 lint；不给就用 promo_envoy.py 里的手写稿。
SCRIPT=""
if want copy && { [ -n "${PROMO_ANGLE:-}" ] || [ -n "${PROMO_SEED:-}" ]; }; then
  SCRIPT="$WORK/script.json"
  ANGLE="${PROMO_ANGLE:-pain}"
  if [ "$ANGLE" = "best" ]; then
    # 让打分器扫 3 角度 × 9 seed，只收过 lint 的，取总分最高那版（同分看节奏）
    log "copy → ${SCRIPT}（angle=best，扫 3×9 候选）"
    "$PY" "$REPO/tools/promo/copy_score.py" --rank --top 8 \
      --angles "${PROMO_CAND_ANGLES:-pain,proof,candor}" \
      --seeds "${PROMO_CAND_SEEDS:-0,1,2,3,4,5,6,7,8}" \
      --workdir "$WORK/cand" --out "$SCRIPT"
  else
    log "copy → ${SCRIPT}（angle=$ANGLE seed=${PROMO_SEED:-0}）"
    "$PY" "$REPO/tools/promo/script_gen.py" --angle "$ANGLE" \
      --seed "${PROMO_SEED:-0}" --out "$SCRIPT"
  fi
  "$PY" "$REPO/tools/promo/copy_lint.py" "$SCRIPT"   # 不过闸门就不许配音
  export PROMO_SCRIPT="$SCRIPT"
  mark
fi
TTS_SRC="$REPO/deploy/presenter/promo_envoy.py"
[ -n "$SCRIPT" ] && TTS_SRC="$SCRIPT"

# 1) 配音 -------------------------------------------------------------
# 稿子换了必须重配：用稿子的哈希当戳，不能只看 mtime（默认稿和新生成稿
# 的 mtime 关系说明不了音频是谁配的，混用会让时间轴和旁白对不上）。
mkdir -p "$AUD"
SCRIPT_ID="default"
[ -n "$SCRIPT" ] && SCRIPT_ID="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()[:16])' "$SCRIPT")"
STAMP="$AUD/.script_id"
if want tts && { [ "$(cat "$STAMP" 2>/dev/null || true)" != "$SCRIPT_ID" ] \
                 || ! fresh "$AUD/a05.mp3" "$TTS_SRC"; }; then
  log "tts → ${AUD}（稿 ${SCRIPT_ID}）"
  PROMO_WORK="$WORK" PROMO_AUDIO="$AUD" "$PY" "$REPO/tools/promo/tts_envoy.py"
  printf '%s\n' "$SCRIPT_ID" > "$STAMP"
  mark
fi

# 2) 卡面 + 人物层 + 时间线 spec --------------------------------------
if want spec && ! fresh "$SPEC" "$AUD/a05.mp3"; then
  log "spec → $SPEC"
  PROMO_WORK="$WORK" PROMO_AUDIO="$AUD" "$PY" "$REPO/deploy/presenter/promo_envoy.py" \
    --workdir "$WORK" --spec "$SPEC"
  mark
fi

# 3) 渲染 -------------------------------------------------------------
ENGINE="$HOME/CreatorStudio/runtime/integrations/mac-worker/export"
if want render && ! fresh "$RAW" "$SPEC"; then
  log "render → $RAW"
  ( cd "$ENGINE" && "$PY" timeline_render.py --spec "$SPEC" --out "$RAW" )
  mark
fi

# 4) 响度 -------------------------------------------------------------
if want mux && ! fresh "$OUT" "$RAW"; then
  log "mux（${AF}）→ $OUT"
  ffmpeg -v error -y -i "$RAW" -c:v copy -af "$AF" -c:a aac -b:a 192k -ar 48000 "$OUT"
  mark
fi

# 5) 交付：小块冷读拷贝 + 回读校验 ------------------------------------
if [ "$DELIVER_ON" = 1 ] && want deliver; then
  if [ -f "$OUT" ] && { [ "$FORCE" = 1 ] || [ -n "$dirty" ] || [ ! -f "$DELIVER" ]; }; then
    log "deliver → $DELIVER"
    mkdir -p "$(dirname "$DELIVER")"
    EXPECT="$("$PY" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$OUT")"
    "$PY" "$REPO/tools/verify_delivery.py" "$OUT" --copy "$DELIVER" --expect "$EXPECT"
  fi
fi

log "done"
"$PY" - "$OUT" <<'PY'
import subprocess, sys, json, os
p = sys.argv[1]
r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,r_frame_rate:format=duration,size,bit_rate",
                    "-of", "json", p], capture_output=True, text=True)
d = json.loads(r.stdout)
s = d["streams"][0]; f = d["format"]
print(f"  成片 {p}")
print(f"  {s['width']}x{s['height']} {s['r_frame_rate']}fps  {float(f['duration']):.3f}s "
      f"{int(f['size'])/1048576:.1f}MB {int(f['bit_rate'])/1000:.0f}kbps")
PY
