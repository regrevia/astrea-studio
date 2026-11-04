#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts_envoy.py — 宣传片配音：逐拍合成，拼成每场景一条整段 mp3。

文案不在这个文件里：直接 import `deploy/presenter/promo_envoy.py` 的 SCENES，
念每段的 `say`。`say` 按句末标点（。？！；）切成「拍」，一拍一合、拍间插 GAP 秒
静音，最后拼成 `a0X.mp3`，并在旁边写两个 sidecar：

- `a0X.words.json`：逐字时间轴（edge-tts WordBoundary，比强制对齐准，口型吃这个）
- `a0X.beats.json`：每拍的 [起, 止]，卡面换页和人物换姿态吃这个

产物文件名/位置是 promo_envoy 的输入契约，不要改。

用法：
    python3 tools/promo/tts_envoy.py                 # 出到 $PROMO_WORK/audio
    PROMO_WORK=/tmp/x python3 tools/promo/tts_envoy.py --only a02
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy" / "presenter"))

import edge_tts  # noqa: E402
from promo_envoy import SCENES  # noqa: E402  文案只有这一份

WORK = Path(os.environ.get("PROMO_WORK", str(Path.home() / "CreatorStudio/work/envoy-promo")))
AUD = Path(os.environ.get("PROMO_AUDIO", str(WORK / "audio")))
VOICE = os.environ.get("PROMO_VOICE", "zh-CN-XiaoxiaoNeural")
RATE = os.environ.get("PROMO_RATE", "+14%")
GAP = float(os.environ.get("PROMO_GAP", "0.16"))
SPLIT = re.compile(r"(?<=[。？！；])")


def dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def beats_of(sc: dict) -> list[str]:
    """换气位 = 句末标点。别按空格切：AI / PDF / 88 秒 里的空格会把一句切碎。"""
    return [b.strip() for b in SPLIT.split(str(sc["say"])) if b.strip()]


async def synth(text: str, mp3: Path) -> list[dict]:
    words: list[dict] = []
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with open(mp3, "wb") as fh:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                fh.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append({"ch": ch["text"], "t": ch["offset"] / 1e7,
                              "d": ch["duration"] / 1e7})
    return words


async def one(sc: dict) -> None:
    key = Path(sc["audio"]).stem
    beats = beats_of(sc)
    chars: list[dict] = []
    lines: list[str] = []
    spans: list[list[float]] = []
    cursor = 0.0
    for i, text in enumerate(beats):
        raw = AUD / f"{key}_b{i}_raw.mp3"
        for w in await synth(text, raw):
            chars.append({"ch": w["ch"], "t": round(cursor + w["t"], 3),
                          "d": round(w["d"], 3)})
        wav = AUD / f"{key}_b{i}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw),
                        "-ar", "48000", "-ac", "2", str(wav)], check=True)
        d = dur(raw)
        spans.append([round(cursor, 3), round(cursor + d, 3)])
        lines.append(f"file '{wav}'")
        cursor += d
        if i != len(beats) - 1:                      # 拍间补静音，别让两拍粘连
            sil = AUD / f"{key}_gap{i}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                            f"anullsrc=r=48000:cl=stereo:d={GAP}", str(sil)], check=True)
            lines.append(f"file '{sil}'")
            cursor += GAP
    lst = AUD / f"{key}.concat.txt"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mp3 = AUD / sc["audio"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-ar", "48000", "-ac", "2",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)], check=True)
    mp3.with_suffix(".words.json").write_text(json.dumps(
        {"voice": VOICE, "rate": RATE, "text": " ".join(beats), "chars": chars},
        ensure_ascii=False, indent=1), encoding="utf-8")
    mp3.with_suffix(".beats.json").write_text(json.dumps(spans), encoding="utf-8")
    cov = sum(c["d"] for c in chars)
    print(f"{key:4s} mp3={dur(mp3):6.3f}s words={len(chars):3d} beats={len(beats)} "
          f"cov={cov:6.3f}s end={spans[-1][1]:6.3f}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="只重做这些场景，如 a02")
    args = ap.parse_args()
    AUD.mkdir(parents=True, exist_ok=True)
    total = 0.0
    for sc in SCENES:
        key = Path(sc["audio"]).stem
        if args.only and key not in args.only:
            continue
        await one(sc)
        total += dur(AUD / sc["audio"])
    if not args.only:
        print(f"TOTAL {total:.3f}s / {len(SCENES)} scenes  "
              f"video≈{total + 0.34 * len(SCENES):.3f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
