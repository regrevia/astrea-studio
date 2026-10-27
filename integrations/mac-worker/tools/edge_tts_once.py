#!/usr/bin/env python3
"""用 edge-tts 合成一句配音，并把逐字时间落成 sidecar。

跑在 MPT 的 venv python 里（系统 python3 没有 edge_tts）：

    edge_tts_once.py --text /tmp/t.txt --voice zh-CN-XiaoxiaoNeural \
        --rate -8% --out a.mp3 --words a.words.json

sidecar 格式与 export/lipsync.py 的约定一致：

    {"chars": [{"ch": "这", "t": 0.10, "d": 0.16}, ...]}

edge-tts 的中文 WordBoundary 是按词分组的（"短片"一组），这里组内按字数均分
展开成逐字；t 相对音频起点（秒）。audio 与 words 都是全量写完再落盘，半成品
不会留在缓存目录里。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import edge_tts

# 与 export/lipsync.py 的 _PUNCT 同一份：标点/空白不占口型时间
PUNCT = set("，。！？；：、,.!?;:…—～~ \t\n\r\"'“”‘’（）()【】[]《》<>·-")

RETRIES = 3
RETRY_SLEEP = 2.0


def _units(word: str) -> list[str]:
    return [c for c in word if c not in PUNCT and not c.isspace()]


def _pitch_hz(val: str, base_hz: float = 200.0) -> str:
    """edge-tts 只收整数 Hz（校验 ``^[+-]\\d+Hz$``），`+2%` 这类相对写法要折算。

    基准按成人说话的 200Hz 取整：+2% → +4Hz，-3% → -6Hz，夹在 ±100Hz 避免拉坏。
    """
    txt = str(val or "").strip()
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)%", txt)
    if m:
        hz = max(-100.0, min(100.0, float(m.group(1)) / 100.0 * base_hz))
        return f"{hz:+.0f}Hz"
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)Hz", txt)
    if m:
        return f"{float(m.group(1)):+.0f}Hz"
    return "+0Hz"


async def _synth(text: str, voice: str, rate: str, pitch: str,
                 out: Path) -> list[dict[str, float | str]]:
    chars: list[dict[str, float | str]] = []
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch,
                                boundary="WordBoundary")
    with out.open("wb") as fh:
        async for chunk in comm.stream():
            kind = chunk.get("type")
            if kind == "audio":
                fh.write(chunk["data"])
            elif kind == "WordBoundary":
                word = str(chunk.get("text") or "")
                units = _units(word)
                if not units:
                    continue
                start = float(chunk["offset"]) / 1e7
                span = max(0.0, float(chunk["duration"]) / 1e7)
                step = span / len(units)
                for i, ch in enumerate(units):
                    chars.append({"ch": ch, "t": round(start + i * step, 4),
                                  "d": round(step, 4)})
    return chars


def main() -> int:
    ap = argparse.ArgumentParser(description="edge-tts 单句合成 + 逐字 sidecar")
    ap.add_argument("--text", required=True, help="UTF-8 纯文本文件")
    ap.add_argument("--voice", required=True, help="如 zh-CN-XiaoxiaoNeural")
    ap.add_argument("--rate", default="+0%", help="如 -8%%，范围 ±30%% 由调用方夹")
    ap.add_argument("--pitch", default="+0Hz", help="如 +4Hz / +2%（%按200Hz基准折算）")
    ap.add_argument("--out", required=True, help="输出 mp3")
    ap.add_argument("--words", required=True, help="输出逐字 sidecar json")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    words_path = Path(args.words).expanduser()
    text = Path(args.text).expanduser().read_text(encoding="utf-8").strip()
    if not text:
        print("edge_tts_once: empty text", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    words_path.parent.mkdir(parents=True, exist_ok=True)

    pitch = _pitch_hz(args.pitch)
    last: Exception | None = None
    chars: list[dict[str, float | str]] = []
    for attempt in range(1, RETRIES + 1):
        try:
            chars = asyncio.run(_synth(text, args.voice, args.rate,
                                       pitch, out))
            if out.stat().st_size <= 0:
                raise RuntimeError("edge-tts returned empty audio")
            if not chars:
                raise RuntimeError("edge-tts returned no WordBoundary")
            break
        except Exception as exc:  # 网络抖动/服务端 5xx：重试，别把半成品留盘
            last = exc
            try:
                out.unlink()
            except OSError:
                pass
            if attempt < RETRIES:
                time.sleep(RETRY_SLEEP)
    else:
        print(f"edge_tts_once: failed after {RETRIES} tries: {last}",
              file=sys.stderr)
        return 1

    blob = {
        "voice": args.voice,
        "rate": args.rate,
        "pitch": pitch,
        "chars": chars,
    }
    tmp = words_path.with_suffix(words_path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, words_path)
    print(json.dumps({"bytes": out.stat().st_size, "chars": len(chars)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
