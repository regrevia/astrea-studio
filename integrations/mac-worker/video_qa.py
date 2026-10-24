"""成片体检：出片之后先自检一遍，长出问题的片段不进交付。

只查能机器判定、且用户一眼看得出毛病的三件事：
时长对不对、有没有整帧发黑/发灰的空镜、每个镜头该有的字和人物是不是都在。
文案不在这里查（见 copy_check.py），这里只盯画面。

用法：
    python -m video_qa <video.mp4> --spec <spec.json> [--json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

FFMPEG = "/opt/homebrew/bin/ffmpeg"
STEP = 0.25
# 整帧标准差低于这个值，等于画面没有内容（黑场、纯色、还是导出的空镜）
FLAT_STD = 6.0
# 文字区、人物区：判断这两块有没有东西，比整帧更容易抓住「字没贴上」
TEXT_BOX = (124, 470, 1004, 1000)
CHAR_BOX = (280, 900, 1040, 1700)


def _frames(path: Path, start: float, end: float, fps: float) -> np.ndarray:
    out = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-i", str(path), "-vf", f"fps={fps},scale=270:480", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(-1, 480, 270)


def _box_std(frames: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    sx, sy = 270 / 1080, 480 / 1920
    part = frames[:, int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
    return part.reshape(len(part), -1).std(axis=1)


def check(video: Path, spec: Path) -> dict:
    meta = json.loads(spec.read_text(encoding="utf-8"))
    want = meta.get("meta", {}).get("duration") if isinstance(meta, dict) else None
    got = float(subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip())
    frames = _frames(video, 0, got, 1 / STEP)
    whole = frames.reshape(len(frames), -1).std(axis=1)
    text = _box_std(frames, TEXT_BOX)
    char = _box_std(frames, CHAR_BOX)
    findings = []
    if want and abs(got - float(want)) > 0.35:
        findings.append({"code": "duration_mismatch", "level": "warn",
                         "detail": f"{got:.2f}s vs {float(want):.2f}s"})
    for i in np.flatnonzero(whole < FLAT_STD)[:5]:
        findings.append({"code": "blank_frame", "level": "error",
                         "detail": f"{i * STEP:.2f}s"})
    # 卡面上有字的时间段，应该占满整条片子（叠化时也不该两块同时消失）
    if len(text) and (text < FLAT_STD).mean() > 0.03:
        findings.append({"code": "text_gap", "level": "warn",
                         "detail": f"{(text < FLAT_STD).mean() * 100:.0f}% 的帧卡面没字"})
    if len(char) and (char < FLAT_STD).mean() > 0.03:
        findings.append({"code": "character_gap", "level": "warn",
                         "detail": f"{(char < FLAT_STD).mean() * 100:.0f}% 的帧没人"})
    return {"video": str(video), "duration": got, "findings": findings,
            "ok": not [f for f in findings if f["level"] == "error"]}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="成片体检")
    ap.add_argument("video")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check(Path(a.video), Path(a.spec))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
