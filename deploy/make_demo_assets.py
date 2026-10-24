#!/usr/bin/env python3
"""生成 C 路线的合成演示素材，让端到端验收不依赖任何私有文件。

为什么要合成：`p2_timeline_e2e.py` 需要几个真实视频和一段 BGM，而这台机器上
既有素材要么来自剪映、要么来自客户项目，都不该进仓库、也不该成为他人跑通
验收的前置条件。用 ffmpeg 的 lavfi 源现场造三支片子，画面上带颜色和时间码，
出了问题一眼能看出是哪一段渲染错了。

用法：
  python3 deploy/make_demo_assets.py                    # -> /tmp/demo_c/asset
  python3 deploy/make_demo_assets.py --out ~/demo/asset

产物：a.mp4 / b.mp4 / c.mp4（各 3 秒，1080x1920@30，带音轨）、bgm.m4a（8 秒）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"

# 三种颜色 + 三种图案，确保切片顺序错了也能从画面上看出来。
CLIPS = [
    ("a.mp4", "0x1f6feb", "testsrc2"),
    ("b.mp4", "0x2ea043", "smptebars"),
    ("c.mp4", "0xb3391f", "rgbtestsrc"),
]
CLIP_SECONDS = 3
CANVAS = "1080x1920"
FPS = 30


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{proc.stderr[-1500:]}")


def make_clip(dst: Path, color: str, pattern: str) -> None:
    # 图案铺满整幅再叠一层半透明纯色，既保留可辨认的图案又让三支片子色调分明。
    filters = (
        f"color=c={color}:s={CANVAS}:r={FPS}:d={CLIP_SECONDS}[bg];"
        f"[0:v]format=yuv420p[fg];"
        f"[bg][fg]blend=all_mode=overlay:all_opacity=0.55[v]"
    )
    run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"{pattern}=size={CANVAS}:rate={FPS}:duration={CLIP_SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={CLIP_SECONDS}",
        "-filter_complex", filters,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ])


def make_bgm(dst: Path, seconds: int = 8) -> None:
    # 两个正弦叠加 + 淡入淡出：比纯正弦更像"音乐"，也方便用响度看混音是否正确。
    run([
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}",
        "-filter_complex",
        f"[0:a][1:a]amix=inputs=2:duration=longest,"
        f"afade=t=in:d=0.5,afade=t=out:st={seconds - 1}:d=1[a]",
        "-map", "[a]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", str(dst),
    ])


def probe(path: Path) -> dict:
    proc = subprocess.run([
        FFPROBE, "-v", "error", "-show_entries",
        "stream=codec_type,width,height,duration", "-of", "json", str(path),
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffprobe failed on {path}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/demo_c/asset")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    for name, color, pattern in CLIPS:
        dst = out / name
        make_clip(dst, color, pattern)
        print(f"  {name:8s} {dst.stat().st_size // 1024} KiB")
    bgm = out / "bgm.m4a"
    make_bgm(bgm)
    print(f"  bgm.m4a  {bgm.stat().st_size // 1024} KiB")

    # 素材是后续验收的输入，顺手打印真实探测结果，别让"文件存在"冒充"能用"。
    for name, *_ in CLIPS:
        info = probe(out / name)
        stream = next(s for s in info["streams"] if s.get("codec_type") == "video")
        print(f"  probe {name}: {stream['width']}x{stream['height']} {stream.get('duration')}s")
    print(f"\nOK: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
