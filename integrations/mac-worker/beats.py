#!/usr/bin/env python3
"""音乐卡点：从音频估 BPM、出拍点网格，给渲染/剪映草稿当节奏依据。

做法（纯 numpy，不装 librosa）：
1. ffmpeg 解成 22.05k 单声道 PCM；
2. 20ms 一帧算能量，取正向差分当 onset 包络（突变处 = 鼓点）；
3. 对 onset 包络做自相关，在 60–200 BPM 的滞后区间里找峰，得到周期；
4. 用这个周期扫相位，选让拍点落在 onset 峰上最多的偏移；
5. 输出 beats（秒）+ bpm + offset，直接能喂给 timeline spec 的 music.beats。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 22050
HOP = 512  # ~23.2ms


def pcm(path: str) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError("ffmpeg 解码失败: %s" % (p.stderr or b"").decode()[:200])
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def onset_envelope(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    if n < 4:
        return np.zeros(0)
    frames = x[:n * HOP].reshape(n, HOP)
    energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    if onset.max() > 0:
        onset = onset / onset.max()
    return onset


def estimate(x: np.ndarray, bpm_range=(60, 200)) -> dict:
    env = onset_envelope(x)
    fps = SR / HOP
    if len(env) < int(2 * fps):
        return {"bpm": 0.0, "offset": 0.0, "beats": [], "confidence": 0.0}
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lo = int(fps * 60.0 / bpm_range[1])
    hi = int(fps * 60.0 / bpm_range[0])
    if hi >= len(ac):
        hi = len(ac) - 1
    seg = ac[lo:hi + 1]
    if len(seg) == 0 or seg.max() <= 0:
        return {"bpm": 0.0, "offset": 0.0, "beats": [], "confidence": 0.0}
    lag = lo + int(np.argmax(seg))
    # 倍频纠错：自相关在 2T/0.5T 上同样有峰，直接取最大常把 92 BPM 报成 185。
    # 在 80–160 BPM 的常用区间里挑分数最高的那个倍频。
    cands = [lag]
    for mult in (2, 3):
        if lag * mult < len(ac):
            cands.append(lag * mult)
    if lag % 2 == 0:
        cands.append(lag // 2)
    best = None
    for c in cands:
        if c <= 0 or c >= len(ac):
            continue
        bpm = 60.0 * fps / c
        if not (72.0 <= bpm <= 165.0):
            continue
        score = float(ac[c]) / (ac[0] + 1e-9)
        if best is None or score > best[1]:
            best = (c, score)
    if best:
        lag = best[0]
    period = lag / fps
    conf = float(ac[lag] / (ac[0] + 1e-9))
    # 相位：让拍点尽量压在 onset 峰上
    best_off, best_score = 0.0, -1.0
    for off in np.arange(0, period, 0.02):
        idx = np.arange(off * fps, len(env), period * fps).astype(int)
        idx = idx[idx < len(env)]
        if len(idx) == 0:
            continue
        score = float(env[idx].mean())
        if score > best_score:
            best_off, best_score = float(off), score
    duration = len(x) / SR
    beats = list(np.arange(best_off, duration, period))
    return {"bpm": round(60.0 / period, 2), "offset": round(best_off, 3),
            "beats": [round(float(b), 3) for b in beats], "confidence": round(conf, 3)}


def analyze(path: str, bpm_range=(60, 200)) -> dict:
    return estimate(pcm(path), bpm_range)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法: beats.py <audio> [--json out.json] [--max-beats N]", file=sys.stderr)
        return 2
    src = argv[0]
    out = None
    limit = 0
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
    if "--max-beats" in argv:
        limit = int(argv[argv.index("--max-beats") + 1])
    res = analyze(src)
    if limit:
        res["beats"] = res["beats"][:limit]
    res["src"] = str(Path(src).expanduser())
    if out:
        Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("bpm=%.2f offset=%.3f beats=%d confidence=%.3f" % (
        res["bpm"], res["offset"], len(res["beats"]), res["confidence"]))
    print("前 8 拍: %s" % ", ".join("%.2f" % b for b in res["beats"][:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
