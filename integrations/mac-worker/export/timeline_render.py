#!/usr/bin/env python3
"""timeline_render.py — 用我们自己的 timeline spec 直接渲染母版。不依赖剪映、不碰 GUI。

它补的是 C 路线缺的那块「渲染层」：剪映面板能做的事这里做，做不了的人工才去剪映。
母版出来后再交给 ``deliver.py`` 出各平台规格，两步都是纯后台进程。

设计取舍
--------
* **文字走 Pillow 画 PNG，再用 overlay 合成**，不用 ffmpeg 的 drawtext/subtitles：
  这台机器的 ffmpeg 9 构建没有 freetype/libass（``ffmpeg -filters`` 里没有
  drawtext/ass/subtitles），装了也用不了。Pillow 方案顺带拿到了描边、圆角底衬、
  多行居中的完全控制权，而且不依赖系统 fontconfig。
* **音轨与画面分离**：视频片段只贡献画面，声音一律来自 ``audio`` 列表。
  这样"配乐 0.25 音量 + 旁白 1.0 + 一段原声"这种组合不用在 clip 上打补丁，
  也避免了 concat 要求每段都有音轨的老问题。
* **不做转场**：v1 用硬切（和剪映"无转场"一致）。xfade 要求首尾帧重叠并重算
  时间轴，等真的需要再加，不要一开始就把它塞进来当复杂度。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# 字体候选：容器（Linux/Noto）优先排在前面，本机 macOS 用后面的。
# 想指定就用 TIMELINE_FONT 环境变量。
FONT_CANDIDATES = (
    os.environ.get("TIMELINE_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
)

VIDEO_FITS = ("cover", "contain")


class SpecError(ValueError):
    """spec 本身有问题（缺字段、时间轴越界、素材不存在）。"""


# --------------------------------------------------------------- 加载与校验
def find_font() -> str:
    for cand in FONT_CANDIDATES:
        if cand and Path(cand).is_file():
            return cand
    raise SpecError(
        "找不到可用字体：设 TIMELINE_FONT 指向一个 .ttf/.ttc，或安装 Noto Sans CJK")


def _color(value, default: str) -> tuple:
    """'#rrggbb' / '#rrggbbaa' → (r,g,b,a)。也接受已经是元组的。"""
    if value is None:
        value = default
    if isinstance(value, (list, tuple)):
        v = list(value) + [255] * (4 - len(value))
        return tuple(int(x) for x in v[:4])
    s = str(value).strip().lstrip("#")
    if len(s) == 6:
        s += "ff"
    if len(s) != 8:
        raise SpecError(f"颜色格式应为 #rrggbb 或 #rrggbbaa，得到 {value!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))


def load_spec(path, base: Path | None = None) -> dict:
    """读 spec 并补全默认值/解析相对路径。返回可直接送给 render 的 dict。"""
    p = Path(os.path.expanduser(str(path)))
    if not p.is_file():
        raise SpecError(f"spec 不存在: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    here = base or p.parent

    canvas = raw.get("canvas") or {}
    width = int(canvas.get("width") or 1920)
    height = int(canvas.get("height") or 1080)
    fps = float(canvas.get("fps") or 30)
    if width <= 0 or height <= 0:
        raise SpecError(f"canvas 尺寸非法: {width}x{height}")
    if width % 2 or height % 2:
        raise SpecError(f"canvas 宽高必须是偶数（h264），得到 {width}x{height}")

    clips = []
    for i, c in enumerate(raw.get("video") or []):
        src = Path(os.path.expanduser(str(c.get("src") or "")))
        if not src.is_absolute():
            src = (here / src).resolve()
        if not src.is_file():
            raise SpecError(f"video[{i}] 素材不存在: {src}")
        start = float(c.get("in") or 0.0)
        end = c.get("out")
        if end is None:
            raise SpecError(f"video[{i}] 缺 out（不猜时长，避免静默出片）")
        end = float(end)
        if end <= start:
            raise SpecError(f"video[{i}] out({end}) 必须大于 in({start})")
        fit = c.get("fit") or "cover"
        if fit not in VIDEO_FITS:
            raise SpecError(f"video[{i}] fit 只能是 {VIDEO_FITS}，得到 {fit!r}")
        clips.append({
            "src": src, "in": start, "out": end,
            "dur": end - start, "fit": fit,
            "speed": float(c.get("speed") or 1.0),
        })
    if not clips:
        raise SpecError("video 至少要有 1 段")

    total = sum(c["dur"] / (c["speed"] or 1.0) for c in clips)

    texts = []
    for i, t in enumerate(raw.get("text") or []):
        txt = str(t.get("text") or "")
        if not txt:
            continue
        start = float(t.get("start") or 0.0)
        end = float(t.get("end") if t.get("end") is not None else total)
        if end <= start:
            raise SpecError(f"text[{i}] end({end}) 必须大于 start({start})")
        if start >= total - 1e-6:
            raise SpecError(f"text[{i}] start({start}) 超出总时长({total:.2f}s)")
        texts.append({
            "text": txt, "start": start, "end": min(end, total),
            "x": float(t.get("x") if t.get("x") is not None else 0.5),
            "y": float(t.get("y") if t.get("y") is not None else 0.9),
            "size": float(t.get("size") or 0.055),
            "color": _color(t.get("color"), "#ffffff"),
            "stroke": float(t.get("stroke") if t.get("stroke") is not None else 2.0),
            "stroke_color": _color(t.get("stroke_color"), "#000000"),
            "box": _color(t.get("box"), "#00000000"),
            "radius": float(t.get("radius") if t.get("radius") is not None else 0.5),
            "align": t.get("align") or "center",
            "fade": float(t.get("fade") or 0.0),
        })

    audios = []
    for i, a in enumerate(raw.get("audio") or []):
        src = Path(os.path.expanduser(str(a.get("src") or "")))
        if not src.is_absolute():
            src = (here / src).resolve()
        if not src.is_file():
            raise SpecError(f"audio[{i}] 素材不存在: {src}")
        audios.append({
            "src": src,
            "start": float(a.get("start") or 0.0),
            "in": float(a.get("in") or 0.0),
            "volume": float(a.get("volume") if a.get("volume") is not None else 1.0),
            "loop": bool(a.get("loop")),
            "fade": float(a.get("fade") or 0.0),
            "out": float(a.get("out") or 0.0),
            "role": str(a.get("role") or ""),
        })

    return {
        "raw": raw,
        "canvas": {"width": width, "height": height, "fps": fps},
        "duration": round(total, 6),
        "video": clips,
        "text": texts,
        "audio": audios,
        "font": str(raw.get("font") or find_font()),
    }


# --------------------------------------------------------------- 文字图层
def render_text_png(item: dict, canvas: dict, font_path: str, out: Path) -> tuple:
    """把一条字幕渲染成 RGBA PNG，返回 (路径, 宽, 高)。

    所有尺寸都相对画布高度算，所以 1080p 和 4K 出片视觉一致——
    硬编码像素值会在换分辨率时全线错位。
    """
    from PIL import Image, ImageDraw, ImageFont

    cw, ch = canvas["width"], canvas["height"]
    px = max(8, int(round(ch * item["size"])))
    font = ImageFont.truetype(font_path, px, index=0)
    lines = str(item["text"]).split("\n")
    stroke = max(0, int(round(px * item["stroke"] / 100.0)))

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    boxes = [probe.textbbox((0, 0), ln, font=font, stroke_width=stroke) for ln in lines]
    line_h = max(b[3] - b[1] for b in boxes)
    line_gap = max(1, int(px * 0.18))
    text_w = max(b[2] - b[0] for b in boxes)
    block_h = line_h * len(lines) + line_gap * (len(lines) - 1)

    pad_x = int(px * 0.45)
    pad_y = int(px * 0.28)
    w = text_w + pad_x * 2
    h = block_h + pad_y * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if item["box"][3] > 0:
        r = int(min(w, h) * 0.5 * item["radius"])
        draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=item["box"])

    y = pad_y
    for ln, b in zip(lines, boxes):
        lw = b[2] - b[0]
        if item["align"] == "left":
            x = pad_x - b[0]
        elif item["align"] == "right":
            x = w - pad_x - lw - b[0]
        else:
            x = (w - lw) / 2 - b[0]
        draw.text((x, y - b[1]), ln, font=font, fill=item["color"],
                  stroke_width=stroke, stroke_fill=item["stroke_color"])
        y += line_h + line_gap

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out, w, h


def _text_position(item: dict, canvas: dict, img_w: int, img_h: int) -> tuple:
    """归一化坐标 → 左上角像素坐标。x/y 指的是文字块**中心**。"""
    cw, ch = canvas["width"], canvas["height"]
    x = int(round(item["x"] * cw - img_w / 2))
    y = int(round(item["y"] * ch - img_h / 2))
    # 夹在画面内，避免字幕被切一半
    x = max(0, min(x, cw - img_w))
    y = max(0, min(y, ch - img_h))
    return x, y


# --------------------------------------------------------------- 组装命令
@dataclass
class RenderPlan:
    spec: dict
    dst: Path
    text_pngs: list = field(default_factory=list)  # [(path,w,h,item)]
    workdir: Path | None = None

    @property
    def canvas(self) -> dict:
        return self.spec["canvas"]

    def argv(self) -> list:
        c = self.canvas
        W, H, FPS = c["width"], c["height"], c["fps"]
        total = self.spec["duration"]
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error"]

        # 1) 视频片段：输入级 seek（快），逐段归一化后 concat
        for clip in self.spec["video"]:
            cmd += ["-ss", f"{clip['in']:g}",
                    "-t", f"{clip['dur'] / (clip['speed'] or 1):g}",
                    "-i", str(clip["src"])]

        n_video = len(self.spec["video"])
        # 2) 字幕 PNG：loop 成整段视频，便于用 fade 的绝对时间
        base = n_video
        for i, (png, w, h, item) in enumerate(self.text_pngs):
            cmd += ["-loop", "1", "-framerate", f"{FPS:g}",
                    "-t", f"{total:g}", "-i", str(png)]
        # 3) 音频
        audio_base = base + len(self.text_pngs)
        if self.spec["audio"]:
            for a in self.spec["audio"]:
                dur = max(0.0, total - a["start"])
                seg = ["-ss", f"{a['in']:g}"]
                if a["out"] > a["in"]:
                    seg += ["-t", f"{a['out'] - a['in']:g}"]
                else:
                    seg += ["-t", f"{dur:g}"]
                if a["loop"]:
                    seg = ["-stream_loop", "-1"] + seg
                cmd += seg + ["-i", str(a["src"])]

        # ---- filter_complex
        parts = []
        for i, clip in enumerate(self.spec["video"]):
            speed = clip["speed"] or 1.0
            if speed != 1.0:
                parts.append(f"[{i}:v]setpts=PTS/{speed:g}[s{i}]")
                src = f"[s{i}]"
            else:
                src = f"[{i}:v]"
            scale = (f"scale={W}:{H}:force_original_aspect_ratio="
                     f"{'increase' if clip['fit'] == 'cover' else 'decrease'}")
            if clip["fit"] == "cover":
                chain = f"{scale},crop={W}:{H}"
            else:
                chain = f"{scale},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black"
            parts.append(
                f"{src}{chain},fps={FPS:g},setsar=1,format=yuv420p[v{i}]")
        concat_in = "".join(f"[v{i}]" for i in range(n_video))
        parts.append(f"{concat_in}concat=n={n_video}:v=1:a=0[vc]")

        last = "[vc]"
        for k, (png, w, h, item) in enumerate(self.text_pngs):
            idx = base + k
            x, y = _text_position(item, c, w, h)
            chain = f"[{idx}:v]format=rgba"
            if item["fade"] > 0:
                f = item["fade"]
                chain += (f",fade=t=in:st={item['start']:g}:d={f:g}:alpha=1"
                          f",fade=t=out:st={max(item['start'], item['end'] - f):g}"
                          f":d={f:g}:alpha=1")
            chain += f"[t{k}]"
            parts.append(chain)
            nxt = f"[vt{k}]"
            parts.append(
                f"{last}[t{k}]overlay={x}:{y}:enable='between(t,"
                f"{item['start']:g},{item['end']:g})'{nxt}")
            last = nxt
        parts.append(f"{last}null[vout]")

        # 音频链
        if self.spec["audio"]:
            for k, a in enumerate(self.spec["audio"]):
                idx = audio_base + k
                delay = int(round(a["start"] * 1000))
                chain = (f"[{idx}:a]aresample={48000},aformat=sample_fmts=fltp:"
                         f"channel_layouts=stereo,volume={a['volume']:g}")
                if a["fade"] > 0:
                    chain += (f",afade=t=in:st=0:d={a['fade']:g}")
                    tail = max(0.0, total - a["start"] - a["fade"])
                    chain += f",afade=t=out:st={tail:g}:d={a['fade']:g}"
                if delay:
                    chain += f",adelay={delay}|{delay}"
                chain += f",apad=whole_dur={total:g}[a{k}]"
                parts.append(chain)
            amix_in = "".join(f"[a{k}]" for k in range(len(self.spec["audio"])))
            parts.append(
                f"{amix_in}amix=inputs={len(self.spec['audio'])}:duration=longest:"
                f"normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,atrim=0:{total:g}[aout]")
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=0:{total:g}[aout]")

        cmd += ["-filter_complex", ";".join(parts),
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                "-profile:v", "high", "-pix_fmt", "yuv420p",
                "-r", f"{FPS:g}", "-t", f"{total:g}",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
                str(self.dst)]
        return cmd


def plan(spec: dict, dst, workdir=None) -> RenderPlan:
    dst = Path(os.path.expanduser(str(dst)))
    work = Path(workdir) if workdir else Path(
        tempfile.mkdtemp(prefix="timeline-"))
    pngs = []
    for i, item in enumerate(spec["text"]):
        png = work / f"text{i:02d}.png"
        path, w, h = render_text_png(item, spec["canvas"], spec["font"], png)
        pngs.append((path, w, h, item))
    return RenderPlan(spec=spec, dst=dst, text_pngs=pngs, workdir=work)


def render(spec: dict, dst, workdir=None, timeout: int = 7200,
           keep_workdir: bool = False) -> dict:
    """渲染母版。返回证据 dict；失败抛 RuntimeError（带 ffmpeg stderr 尾巴）。"""
    rp = plan(spec, dst, workdir)
    rp.dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = rp.argv()
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    cost = time.time() - t0
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败({r.returncode}): {r.stderr.strip()[-800:]}")
    if not rp.dst.is_file() or rp.dst.stat().st_size == 0:
        raise RuntimeError("ffmpeg 返回 0 但没有产物")
    if not keep_workdir and rp.workdir:
        shutil.rmtree(rp.workdir, ignore_errors=True)
    return {
        "output": str(rp.dst),
        "bytes": rp.dst.stat().st_size,
        "duration": spec["duration"],
        "canvas": spec["canvas"],
        "clips": len(spec["video"]),
        "texts": len(spec["text"]),
        "audios": len(spec["audio"]),
        "font": spec["font"],
        "seconds": round(cost, 1),
        "cmd": cmd,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="timeline spec → 母版 mp4（headless，不依赖剪映）")
    ap.add_argument("--spec", required=True, help="timeline JSON 路径")
    ap.add_argument("--out", required=True, help="母版输出路径")
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只打印 ffmpeg 命令")
    args = ap.parse_args(argv)

    if not shutil.which(FFMPEG):
        print(f"找不到 {FFMPEG}", file=sys.stderr)
        return 3
    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(f"spec 错误: {exc}", file=sys.stderr)
        return 2

    c = spec["canvas"]
    print(f"spec: {args.spec}")
    print(f"  画布 {c['width']}x{c['height']}@{c['fps']:g}  时长 {spec['duration']:.2f}s")
    print(f"  {len(spec['video'])} 段画面 / {len(spec['text'])} 条字幕 / "
          f"{len(spec['audio'])} 条音轨  字体 {Path(spec['font']).name}")

    if args.dry_run:
        rp = plan(spec, args.out)
        print("\n" + " ".join(rp.argv()))
        return 0
    try:
        ev = render(spec, args.out, keep_workdir=args.keep_workdir)
    except RuntimeError as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 1
    print(f"✓ {ev['output']}  {ev['bytes'] / 1048576:.2f} MB  "
          f"{ev['seconds']:.1f}s 渲染")
    if args.json:
        print(json.dumps({k: v for k, v in ev.items() if k != "cmd"},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
