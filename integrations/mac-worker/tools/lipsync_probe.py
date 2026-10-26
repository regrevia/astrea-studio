#!/usr/bin/env python3
"""口型轨探针：合成 → 逐字边界 → 口型轨 → 统计。

    python3 tools/lipsync_probe.py --text "这条短片里，嘴形要跟着字走，声音也要像真人。" \
        --voice zh-CN-XiaoxiaoNeural --out /tmp/lipsync-probe

音色写法：``sami:BV411_streaming`` 走剪映 SAMI，其余当 edge-tts 的 zh-CN 音色。
输出：``<out>/audio.mp3``（SAMI 是 mp3 容器里的 opus，ffmpeg 能读）、
``<out>/track.csv``（逐格口型）、屏幕上的口型统计。edge-tts 音色还会顺手落
``audio.words.json``，同一句重跑零网络。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "export"))

import lipsync  # noqa: E402 - 探针必须复用出片用的同一份实现

MPT_HOME = Path(
    os.environ.get(
        "MPT_HOME",
        str(Path.home() / "CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo"),
    )
)
_MPT_PYTHON = MPT_HOME / ".venv/bin/python"
_SAMI_CODE = (
    "import sys\n"
    "from app.services.sami_tts import sami_tts\n"
    "sami_tts(sys.argv[1], sys.argv[2], float(sys.argv[4]), sys.argv[3])\n"
)


def synth_sami(text: str, voice: str, mp3: Path, rate: float = 1.0) -> None:
    """SAMI 配音：worker 跑系统 python3，没有 websockets，只能交给 MPT 解释器。"""
    if not _MPT_PYTHON.exists():
        raise SystemExit(f"MPT venv 不存在：{_MPT_PYTHON}")
    env = dict(os.environ)
    env["MPT_HOME"] = str(MPT_HOME)
    env["PYTHONPATH"] = str(MPT_HOME)
    r = subprocess.run(
        [str(_MPT_PYTHON), "-c", _SAMI_CODE, text, voice, str(mp3), f"{rate:.3f}"],
        capture_output=True, text=True, cwd=str(MPT_HOME), env=env, timeout=180,
    )
    if not mp3.exists() or mp3.stat().st_size < 1000:
        raise SystemExit(f"SAMI 合成失败：{(r.stderr or r.stdout or '无输出').strip()[-400:]}")


def synth_edge(text: str, voice: str, mp3: Path, rate: str) -> dict:
    """edge-tts 合成并落 sidecar；返回 sidecar 内容（含原始 WordBoundary）。"""
    os.environ["LIPSYNC_VOICE"] = voice
    os.environ["LIPSYNC_RATE"] = rate
    side = lipsync.words_json(text, mp3, mp3.with_suffix(".words.json"))
    return json.loads(side.read_text(encoding="utf-8"))


def duration_of(media: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(media)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise SystemExit(f"ffprobe 读不出时长：{media}") from None


def runs(track: list[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for st in track:
        if out and out[-1][0] == st:
            out[-1] = (st, out[-1][1] + 1)
        else:
            out.append((st, 1))
    return out


def report(track: list[str], meta: dict, fps: int) -> dict:
    segs = runs(track)
    dwell = [n / fps * 1000 for _, n in segs]
    switches = max(0, len(segs) - 1)
    share = {st: round(n / len(track) * 100, 1) for st, n in Counter(track).most_common()}
    return {
        "source": meta["source"],
        "chars": f"{meta['char_count']}/{meta['spoken_count']}",
        "coverage": meta["coverage"],
        "frames": len(track),
        "seconds": meta["duration"],
        "share_pct": share,
        "switches": switches,
        "runs": len(segs),
        "dwell_ms_max": round(max(dwell), 1) if dwell else 0,
        "dwell_ms_min": round(min(dwell), 1) if dwell else 0,
        "dwell_ms_mean": round(sum(dwell) / len(dwell), 1) if dwell else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="口型轨探针：合成 + 逐字边界 + 统计")
    ap.add_argument("--text", required=True, help="要念的句子")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural",
                    help="zh-CN-XxxNeural，或 sami:<speaker_id>")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--fps", type=int, default=50)
    ap.add_argument("--rate", default="+0%", help="edge-tts 语速，如 -8%%；SAMI 传 1.0/0.92")
    ap.add_argument("--json", action="store_true", help="只输出统计 JSON")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mp3 = out / "audio.mp3"
    words_n = 0
    if args.voice.lower().startswith("sami:"):
        rate = 1.0
        try:
            rate = float(str(args.rate).rstrip("%")) / 100 + 1 if "%" in str(args.rate) else float(args.rate)
        except ValueError:
            rate = 1.0
        synth_sami(args.text, args.voice, mp3, rate)
    else:
        blob = synth_edge(args.text, args.voice, mp3, args.rate)
        words_n = len(blob.get("chars") or [])

    duration = duration_of(mp3)
    track, meta = lipsync.build_words(args.text, mp3, duration, fps=args.fps)
    stats = report(track, meta, args.fps)
    stats["voice"] = args.voice
    stats["rate"] = args.rate
    stats["word_boundaries"] = words_n

    csv_path = out / "track.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "t", "state"])
        for i, st in enumerate(track):
            w.writerow([i, round(i / args.fps, 3), st])

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    else:
        print(f"voice      {stats['voice']}  rate {stats['rate']}")
        print(f"audio      {mp3}  {duration:.3f}s")
        print(f"timing     source={stats['source']}  chars {stats['chars']}  "
              f"coverage {stats['coverage']}  boundaries {words_n}")
        print(f"track      {stats['frames']} 帧 @{args.fps}Hz  {stats['runs']} 段  "
              f"切换 {stats['switches']} 次")
        print(f"dwell      min {stats['dwell_ms_min']}ms  mean {stats['dwell_ms_mean']}ms  "
              f"max {stats['dwell_ms_max']}ms")
        print("share      " + "  ".join(f"{k} {v}%" for k, v in stats["share_pct"].items()))
        print(f"csv        {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
