#!/bin/bash
# 对口型范式「换稿 / 换声不回归」三腿复核。用法：bash deploy/variant_matrix.sh [输出目录]
# 每腿用全新 workdir 走同一条 paper 管线，出片后统一过口型门控 + video_qa。
# 这个脚本存在的意义：判据是按每个角色自己的配音重算的，所以换稿换声必须能重跑同一套证据，
# 而不是「按 f44 调好的参数」。
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$HOME/CreatorStudio/deliver/qa/matrix}
PY=${PY:-$HOME/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python}
export PYTHONPATH=${PYTHONPATH:-$HOME/CreatorStudio/runtime/lib}
export STUDIO_SKIP_PREFLIGHT=1
M=${M:-$HOME/CreatorStudio/materials/shots/envoy}
mkdir -p "$OUT"

run() {
  local K=$1 SPEC=$2 FILM=$3 W=$OUT/work_$1
  rm -rf "$W"
  "$PY" "$ROOT/deploy/schematic_preview.py" "$SPEC" \
    --shots "sketch:link" "$M/q_how_i.png#470#contain#340" "sketch:timeline" \
            "$M/q_bench_f.png#537#contain#452" "sketch:gap" "sketch:install" \
    --caps "" "" "" "" "" "" --style paper --out "$FILM" --workdir "$W" > "$OUT/render_$K.log" 2>&1 \
    || { echo "[$K] RENDER_FAIL"; return 1; }
  /opt/homebrew/bin/ffmpeg -v error -i "$FILM" -ac 1 -ar 16000 -f wav -y "$OUT/film_$K.wav"
  "$PY" "$ROOT/integrations/mac-worker/tools/flat_lip_states.py" \
        "$W/char_layer_lip.json" "$OUT/states_$K.json"
  "$PY" "$ROOT/integrations/mac-worker/tools/lipsync_qa.py" --audio "$OUT/film_$K.wav" \
        --states "$OUT/states_$K.json" --fps 30 > "$OUT/gate_$K.txt" 2>&1
  echo "[$K] lipsync_exit=$? $(grep -E '判定' "$OUT/gate_$K.txt" | tail -1)"
  "$PY" "$ROOT/integrations/mac-worker/video_qa.py" --spec "$SPEC" "$FILM" > "$OUT/videoqa_$K.txt" 2>&1
  echo "[$K] videoqa_exit=$? $(tr -d '\n' < "$OUT/videoqa_$K.txt" | tail -c 120)"
}

run base "$ROOT/deploy/specs/envoy_real.json"      "$OUT/envoy-base-paper.mp4"
run a    "$ROOT/deploy/specs/envoy_variant_a.json" "$OUT/envoy-a-paper.mp4"
run b    "$ROOT/deploy/specs/envoy_variant_b.json" "$OUT/envoy-b-paper.mp4"
echo MATRIX_DONE
