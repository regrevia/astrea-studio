#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cl_bench.py — 立绘层（char_layer）合成速度基准，12 秒 / 360 帧固定输入。

为什么单列一份：立绘是全片最贵的一步，改一行动画代码就重跑一遍，得有个能
把「画」和「编码」分开数的尺子。跑两遍还能顺便验证内容寻址缓存：第二遍应该
`"reused": true` 并且整段几秒内返回。

用法：
    python3 tools/cl_bench.py                 # 冷跑（8 进程）+ 热跑（命中缓存）
    python3 tools/cl_bench.py --jobs 1        # 单进程，对照值
    python3 tools/cl_bench.py --rebuild       # 强制重画，验证缓存失效路径
    python3 tools/cl_bench.py --engine ~/CreatorStudio/runtime/integrations/mac-worker
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENGINE = Path(os.environ.get("CREATOR_STUDIO_MAC_WORKER", str(REPO / "integrations" / "mac-worker")))
WORK = Path(os.environ.get("CL_BENCH_WORK", "/tmp/clbench"))
AUDIO = Path(os.environ.get("PROMO_AUDIO", "/tmp/envoy-promo-work3/audio"))
POSES = ["stand", "point", "wave", "think", "shrug", "wave"]
SEG = 2.0
FPS = 30


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=0, help="0 = 让引擎自己选（上限 8）")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--runs", type=int, default=2, help="连跑几次，看第二遍是否命中缓存")
    ap.add_argument("--engine", default=str(ENGINE))
    args = ap.parse_args(argv)

    if args.jobs:
        os.environ["CHAR_LAYER_JOBS"] = str(args.jobs)
    sys.path.insert(0, str(Path(args.engine) / "export"))
    sys.path.insert(0, str(Path(args.engine)))
    import char_layer as CL          # noqa: E402
    import character as CHR          # noqa: E402

    work = WORK
    work.mkdir(parents=True, exist_ok=True)
    root = str(CHR.outfit_root("06_researcher"))
    segs, t = [], 0.0
    for i, pose in enumerate(POSES):
        a = AUDIO / f"a{i:02d}.mp3"
        if not a.exists():
            raise SystemExit(f"缺配音 {a}（先跑 tools/promo/build.sh --from tts）")
        segs.append({
            "start": t, "end": t + SEG, "width": 786, "x": 470, "y": 962,
            "pose": pose, "mouth": "closed", "eyes": "open", "expression": None,
            "outfit": "06_researcher", "anim": None, "anim_mode": "loop", "anim_t0": t,
            "fade_in": 0.0, "fade_out": 0.0, "speaking": True,
            "lip_audio": str(a), "lip_text": "", "lip_t0": t,
            "enter": {"from": "bottom", "duration": 0.55, "ease": "out-cubic"} if i == 0 else None,
            "_outfit_root": root,
        })
        t += SEG

    for run in range(max(1, args.runs)):
        t0 = time.time()
        mov, bx, by = CL.build(segs, width=1080, height=1920, fps=FPS, workdir=work,
                               duration=t, render=CHR.render, key_green=CHR._key_green,
                               seed=20261104, rebuild=args.rebuild and run == 0,
                               log=lambda m: print(m, flush=True))
        print(f"BENCH run{run} {t:.1f}s video / {int(t * FPS)} frames -> "
              f"{time.time() - t0:.1f}s wall  box=[{bx},{by}]", flush=True)


if __name__ == "__main__":
    main()
