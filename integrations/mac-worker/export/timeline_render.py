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

# 叠加层（虚拟主播/贴纸/页面卡片）：图片或带 alpha 的视频，贴在成片上面。
# 只负责「贴上去 + 进/出场 + 呼吸动效」，不承担转场合成——那属于剪映/后期。
OVERLAY_MOTIONS = ("bob", "sway", "none")
OVERLAY_SIDES = ("left", "right", "top", "bottom")
OVERLAY_VIDEO_SUFFIXES = (".webm", ".mov", ".mp4", ".m4v", ".mkv", ".gif", ".avi")
STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


class SpecError(ValueError):
    """spec 本身有问题（缺字段、时间轴越界、素材不存在）。"""


# --------------------------------------------------------------- 加载与校验
def find_font() -> str:
    for cand in FONT_CANDIDATES:
        if cand and Path(cand).is_file():
            return cand
    raise SpecError(
        "找不到可用字体：设 TIMELINE_FONT 指向一个 .ttf/.ttc，或安装 Noto Sans CJK")


def _keys_at(keys: list[tuple[float, float]], at: float) -> float:
    """在关键帧折线上取某一秒的 zoom，越界就贴边。"""
    if at <= keys[0][0]:
        return keys[0][1]
    for (a0, z0), (a1, z1) in zip(keys, keys[1:]):
        if at <= a1:
            if a1 - a0 < 1e-9:
                return z1
            return z0 + (z1 - z0) * (at - a0) / (a1 - a0)
    return keys[-1][1]


def _volume_curve(steps: list[tuple[float, float]], ramp: float = 0.15) -> str:
    """分段音量变成 ffmpeg volume 表达式：切点前 0.15 秒线性过渡，之后才是新一档。

    越靠后的切点套在外层，所以 60 秒的片子也不会被前面的分支抢先命中。
    """
    prev = steps[0][1]
    expr = f"{prev:g}"
    for at, volume in steps[1:]:
        start = max(0.0, at - ramp)
        if start >= at:
            expr = f"if(gte(t,{at:g}),{volume:g},{expr})"
        else:
            slide = (f"({prev:g}+({volume:g}-{prev:g})*(t-{start:g})/{ramp:g})")
            expr = f"if(gte(t,{at:g}),{volume:g},if(gte(t,{start:g}),{slide},{expr}))"
        prev = volume
    return expr


def _pulse_keys(keys: list[tuple[float, float]], dur: float,
                beats: list[float], amount: float, decay: float) -> list[tuple[float, float]]:
    """卡点脉冲：每个拍点上把当前 zoom 顶高 amount，再在 decay 秒内回到原曲线。"""
    pts = list(keys)
    for t in beats:
        t = max(0.0, min(t, dur))
        pts.append((t, _keys_at(keys, t) * (1.0 + amount)))
        pts.append((min(dur, t + decay), _keys_at(keys, min(dur, t + decay))))
    pts.sort(key=lambda x: x[0])
    out: list[tuple[float, float]] = []
    for at, z in pts:
        if out and abs(at - out[-1][0]) < 1e-4:
            out[-1] = (at, max(out[-1][1], z))
        else:
            out.append((at, z))
    return out


def _zoom_expr(points: list[tuple[float, float]], frames: int, fps: float) -> str:
    """关键帧推拉：把 (秒, zoom) 折线交给 zoompan 的 z 表达式。

    zoompan 的 `on` 是输出帧号，所以先把秒换成帧，再做分段线性插值。
    只有首尾两点时退化成一条直线（和老的 zoom 行为逐字一致）。
    """
    pts = [(min(max(0.0, float(at)) * fps, float(frames)), float(z)) for at, z in points]
    if len(pts) < 2:
        return f"{pts[0][1]:.4f}" if pts else "1.0"
    if len(pts) == 2 and pts[0][0] <= 0 and pts[1][0] >= frames:
        z0, z1 = pts[0][1], pts[1][1]
        return f"{z0:.4f}+({z1 - z0:.4f})*on/{frames}"
    # 倒着套 if：最后一帧之后保持最后一个值，往前每段一个区间
    expr = f"{pts[-1][1]:.4f}"
    for i in range(len(pts) - 1, 0, -1):
        f0, z0 = pts[i - 1]
        f1, z1 = pts[i]
        if f1 - f0 < 1e-6:
            seg = f"{z1:.4f}"
        else:
            seg = f"{z0:.4f}+({z1 - z0:.4f})*(on-{f0:.2f})/{f1 - f0:.2f}"
        expr = f"if(lt(on,{f1:.2f}),{seg},{expr})"
    return expr


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


OVERLAY_EASES = ("linear", "out-quad", "out-cubic", "in-cubic", "out-back")


def _overlay_edge(raw, key: str, idx: int):
    """解析 overlay 的入场/出场（{"from"/"to": 边, "duration": 秒, "ease": 曲线}）。"""
    if not raw:
        return None
    side = str(raw.get(key) or ("right" if key == "from" else "left"))
    if side not in OVERLAY_SIDES:
        raise SpecError(
            f"overlay[{idx}] {key} 只能是 {OVERLAY_SIDES}，得到 {side!r}")
    dur = float(raw.get("duration") if raw.get("duration") is not None else 0.45)
    if dur <= 0:
        raise SpecError(f"overlay[{idx}] {key} duration 必须大于 0，得到 {dur}")
    ease = str(raw.get("ease") or "linear")
    if ease not in OVERLAY_EASES:
        raise SpecError(
            f"overlay[{idx}] {key}.ease 只能是 {OVERLAY_EASES}，得到 {ease!r}")
    return {"side": side, "duration": dur, "ease": ease}


def _ease_progress(expr_u: str, ease: str) -> str:
    """把线性进度 [0,1] 换成缓动后的进度表达式（ffmpeg expr 方言）。"""
    u = f"clip(({expr_u}),0,1)"
    if ease == "out-quad":
        return f"(1-pow(1-{u},2))"
    if ease == "out-cubic":
        return f"(1-pow(1-{u},3))"
    if ease == "in-cubic":
        return f"pow({u},3)"
    if ease == "out-back":
        return f"(1+2.70158*pow({u}-1,3)+1.70158*pow({u}-1,2))"
    return u


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
        still = src.suffix.lower() in STILL_SUFFIXES
        zoom = c.get("zoom") or (1.0, 1.0)
        if isinstance(zoom, dict):
            zoom = (float(zoom.get("from") or 1.0), float(zoom.get("to") or 1.0))
        zoom = (float(zoom[0]), float(zoom[1]))
        if min(zoom) < 1.0:
            raise SpecError(f"video[{i}] zoom 不能小于 1（会露出画布边）")
        raw_keys = c.get("keyframes") or []
        if not isinstance(raw_keys, list):
            raise SpecError(f"video[{i}] keyframes 要是数组，得到 {type(raw_keys).__name__}")
        keys: list[tuple[float, float]] = []
        dur_raw = end - start
        for k, item in enumerate(raw_keys):
            if not isinstance(item, dict):
                raise SpecError(f"video[{i}].keyframes[{k}] 要是 {{at, zoom}} 对象")
            at = item.get("at")
            if at is None:
                raise SpecError(f"video[{i}].keyframes[{k}] 缺 at（段内秒数）")
            at = float(at)
            if at < -1e-6 or at > dur_raw + 1e-6:
                raise SpecError(f"video[{i}].keyframes[{k}] 的 at({at}) 超出本段 0~{dur_raw:.2f}s")
            z = float(item.get("zoom", zoom[0]))
            if z < 1.0:
                raise SpecError(f"video[{i}].keyframes[{k}] zoom 不能小于 1（会露出画布边）")
            keys.append((max(0.0, at), z))
        if keys:
            keys.sort(key=lambda x: x[0])
            if keys[0][0] > 1e-6:
                keys.insert(0, (0.0, zoom[0]))
        else:
            keys = [(0.0, zoom[0]), (dur_raw, zoom[1])]
        clips.append({
            "src": src, "in": start, "out": end,
            "dur": end - start, "fit": fit,
            "speed": float(c.get("speed") or 1.0),
            "still": still, "zoom": zoom, "keys": keys,
            # 卡点脉冲只给「没自己写关键帧的静卡」，手写的节奏说了算
            "pulse": bool(c.get("pulse", True)), "explicit_keys": bool(raw_keys),
            "fade_in": float(c.get("fade_in") or 0.0),
            "fade_out": float(c.get("fade_out") or 0.0),
            "xfade_in": float(c.get("xfade_in") or 0.0),
        })
    if not clips:
        raise SpecError("video 至少要有 1 段")

    # 真转场：xfade 会吃掉两段交叠的时长。为不让后面每一屏的绝对时间错位，
    # 让前一段多渲染一段过渡（内容不变，多出的部分全被交叠吃掉）。
    # 于是 total 仍是各屏时长之和，叠层/字幕/配音的 start 一个都不用改。
    for i, clip in enumerate(clips):
        nxt = clips[i + 1]["xfade_in"] if i + 1 < len(clips) else 0.0
        if nxt and nxt > clip["dur"] - 0.05:
            raise SpecError(f"video[{i}] 的下一段 xfade_in({nxt}) 超过本段时长")
        clip["render"] = clip["dur"] + nxt

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
            "volume_expr": None,
            "loop": bool(a.get("loop")),
            "fade": float(a.get("fade") or 0.0),
            "out": float(a.get("out") or 0.0),
            "role": str(a.get("role") or ""),
        })

    music = raw.get("music")
    beats: list[float] = []
    music_info = None
    if music is not None:
        if not isinstance(music, dict):
            raise SpecError(f"music 要是对象，得到 {type(music).__name__}")
        m_src = Path(os.path.expanduser(str(music.get("src") or "")))
        if not m_src.is_absolute():
            m_src = (here / m_src).resolve()
        if not m_src.is_file():
            raise SpecError(f"music 素材不存在: {m_src}")
        m_vol = float(music.get("volume") if music.get("volume") is not None else 0.35)
        if not 0.0 < m_vol <= 2.0:
            raise SpecError(f"music.volume 要在 0~2，得到 {m_vol}")
        m_in = float(music.get("in") or 0.0)
        if m_in < 0:
            raise SpecError(f"music.in 不能为负，得到 {m_in}")
        m_loop = bool(music.get("loop", True))
        # 分段音量：开场垫一层、中段让给卖点、收尾抬一点推行动。按时间轴切，不做渐隐。
        m_steps = music.get("volume_steps") or []
        if m_steps:
            if not isinstance(m_steps, list):
                raise SpecError("music.volume_steps 要是数组")
            step_list = []
            for index, step in enumerate(m_steps):
                if not isinstance(step, dict):
                    raise SpecError(f"music.volume_steps[{index}] 要是对象")
                at = float(step.get("at") if step.get("at") is not None else 0.0)
                vol = float(step.get("volume") if step.get("volume") is not None else m_vol)
                if at < 0:
                    raise SpecError(f"music.volume_steps[{index}].at 不能为负")
                if not 0.0 < vol <= 2.0:
                    raise SpecError(f"music.volume_steps[{index}].volume 要在 0~2")
                step_list.append((at, vol))
            if any(step_list[k + 1][0] <= step_list[k][0] for k in range(len(step_list) - 1)):
                raise SpecError("music.volume_steps 必须按 at 严格递增")
            if step_list[0][0] > 0:
                step_list.insert(0, (0.0, m_vol))
            m_steps = step_list
        m_bpm = music.get("bpm")
        m_offset = float(music.get("offset") or 0.0)
        if m_offset < 0:
            raise SpecError(f"music.offset 不能为负，得到 {m_offset}")
        raw_beats = music.get("beats") or []
        if raw_beats:
            if not isinstance(raw_beats, list):
                raise SpecError(f"music.beats 要是数组，得到 {type(raw_beats).__name__}")
            beats = [float(x) for x in raw_beats]
            if any(b < 0 for b in beats):
                raise SpecError("music.beats 不能有负数")
            if any(beats[k + 1] <= beats[k] for k in range(len(beats) - 1)):
                raise SpecError("music.beats 必须严格递增（单位秒）")
        elif m_bpm is not None:
            m_bpm = float(m_bpm)
            if m_bpm <= 0:
                raise SpecError(f"music.bpm 必须大于 0，得到 {m_bpm}")
            step = 60.0 / m_bpm
            k = 0
            while m_offset + k * step <= total + 1e-9:
                beats.append(round(m_offset + k * step, 6))
                k += 1
        else:
            m_bpm = None
        beats = [b for b in beats if b <= total + 1e-6]
        volume_expr = None
        if m_steps:
            volume_expr = _volume_curve(m_steps)
            m_vol = m_steps[0][1]
        music_info = {"src": str(m_src), "volume": m_vol, "in": m_in,
                      "loop": m_loop, "bpm": m_bpm, "offset": m_offset,
                      "beats": beats}
        audios.append({"src": m_src, "start": 0.0, "in": m_in, "volume": m_vol,
                       "volume_expr": volume_expr,
                       "loop": m_loop, "fade": 0.0, "out": 0.0, "role": "bgm"})
        if volume_expr:
            music_info["volume_steps"] = [{"at": at, "volume": vol} for at, vol in m_steps]

        # 卡点：拍点上给静卡顶一下。改的是关键帧，不是画面时长——
        # 竖屏的语音/字幕时间来自配音，动时长会把人声和画面对不上。
        pulse = music.get("pulse") or {}
        if pulse:
            if not isinstance(pulse, dict):
                raise SpecError(f"music.pulse 要是对象，得到 {type(pulse).__name__}")
            amount = float(pulse.get("amount") or 0.045)
            decay = float(pulse.get("decay") or 0.16)
            if amount < 0 or amount > 0.4:
                raise SpecError(f"music.pulse.amount 要在 0~0.4，得到 {amount}")
            if not 0.03 <= decay <= 1.0:
                raise SpecError(f"music.pulse.decay 要在 0.03~1，得到 {decay}")
            applied = 0
            cursor = 0.0
            for clip in clips:
                dur = clip["dur"] / (clip["speed"] or 1.0)
                if clip["pulse"] and not clip["explicit_keys"] and beats:
                    local = [b - cursor for b in beats
                             if cursor - 1e-6 <= b < cursor + dur - 1e-6]
                    if local:
                        clip["keys"] = _pulse_keys(clip["keys"], dur, local, amount, decay)
                        applied += len(local)
                cursor += dur
            music_info["pulse"] = {"amount": amount, "decay": decay, "applied": applied}

    overlays = []
    for i, o in enumerate(raw.get("overlay") or []):
        src = Path(os.path.expanduser(str(o.get("src") or "")))
        if not src.is_absolute():
            src = (here / src).resolve()
        if not src.is_file():
            raise SpecError(f"overlay[{i}] 素材不存在: {src}")
        start = float(o.get("start") or 0.0)
        end = float(o.get("end") if o.get("end") is not None else total)
        if end <= start:
            raise SpecError(f"overlay[{i}] end({end}) 必须大于 start({start})")
        if start >= total - 1e-6:
            raise SpecError(f"overlay[{i}] start({start}) 超出总时长({total:.2f}s)")
        ov_width = o.get("width")
        ov_width = None if ov_width in (None, 0) else int(ov_width)
        if ov_width is not None and (ov_width <= 0 or ov_width % 2):
            raise SpecError(f"overlay[{i}] width 必须是正偶数，得到 {ov_width}")
        motion = dict(o.get("motion") or {})
        mtype = str(motion.get("type") or "none")
        if mtype not in OVERLAY_MOTIONS:
            raise SpecError(
                f"overlay[{i}] motion.type 只能是 {OVERLAY_MOTIONS}，得到 {mtype!r}")
        overlays.append({
            "src": src,
            "kind": "video" if src.suffix.lower() in OVERLAY_VIDEO_SUFFIXES else "image",
            "start": start,
            "end": min(end, total),
            "x": float(o.get("x") or 0.0),
            "y": float(o.get("y") or 0.0),
            "width": ov_width,
            "opacity": float(o.get("opacity") if o.get("opacity") is not None else 1.0),
            "fade": float(o.get("fade") or 0.0),
            # 转场用的整卡叠化只需要淡入，淡出会把刚铺上的画面又拉回黑
            "fade_mode": str(o.get("fade_mode") or "both"),
            "enter": _overlay_edge(o.get("enter"), "from", i),
            "exit": _overlay_edge(o.get("exit"), "to", i),
            "motion": {
                "type": mtype,
                "amplitude": float(motion.get("amplitude") or 8.0),
                "period": float(motion.get("period") or 2.2),
                # 同一角色被切成多段口型时，正弦相位要接着上一段走，
                # 否则每 0.4 秒归零一次，人物像在抖。
                "phase": float(motion.get("phase") or 0.0),
                # 人物统一成「横摆 + 轻呼吸」两个轴：以前横摆和竖晃按姿势二选一，
                # 换姿势那一刻横轴或竖轴直接归零，人就凭空挪一格。
                "amp_y": float(motion.get("amp_y") or 0.0),
                "period_y": float(motion.get("period_y") or 0.0),
            },
        })

    return {
        "raw": raw,
        "canvas": {"width": width, "height": height, "fps": fps},
        "duration": round(total, 6),
        "video": clips,
        "text": texts,
        "overlay": overlays,
        "audio": audios,
        "music": music_info,
        "beats": beats,
        "font": str(raw.get("font") or find_font()),
    }


# --------------------------------------------------------------- 文字图层
def _font_scale(raw_size, canvas_height: int) -> float:
    """字幕字号统一按画布高度的比例算。

    有人把 size 写成像素（例如 72），直接乘画布高度会被 PIL 当成非法字号报
    `invalid pixel size`；这里把 >1 的值按像素理解并换算回比例，再夹到合理区间。
    """
    size = float(raw_size)
    if size <= 0:
        raise SpecError("字幕字号必须大于 0")
    if size > 1:
        size = size / float(canvas_height)
    return min(size, 0.6)


def render_text_png(item: dict, canvas: dict, font_path: str, out: Path) -> tuple:
    """把一条字幕渲染成 RGBA PNG，返回 (路径, 宽, 高)。

    所有尺寸都相对画布高度算，所以 1080p 和 4K 出片视觉一致——
    硬编码像素值会在换分辨率时全线错位。
    """
    from PIL import Image, ImageDraw, ImageFont

    cw, ch = canvas["width"], canvas["height"]
    px = max(8, int(round(ch * _font_scale(item["size"], ch))))
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


# ------------------------------------------------------------- 叠加层表达式
def _overlay_axis_expr(item: dict, axis: str, canvas: dict) -> str:
    """生成 overlay 的 x 或 y 表达式：进/出场位移 + 呼吸动效，全部用绝对时间 t。

    基座是画布像素坐标；slide 从画布外滑入，bob/sway 用正弦让人物"活着"。
    """
    W, H = canvas["width"], canvas["height"]
    base = item["x"] if axis == "x" else item["y"]
    span = item["width"] or W
    ov_w = item["width"] or W
    expr = f"{base:.3f}"

    enter = item.get("enter")
    if enter and enter["side"] in (("left", "right") if axis == "x" else ("top", "bottom")):
        d = enter["duration"]
        start = item["start"]
        if axis == "x":
            off = -ov_w if enter["side"] == "left" else W
        else:
            off = -span if enter["side"] == "top" else H
        p = _ease_progress(f"(t-{start:.3f})/{d:.3f}",
                           enter.get("ease") or "linear")
        expr = (f"if(lt(t,{start + d:.3f}),{off:.3f}+({base:.3f}-({off:.3f}))"
                f"*{p},{expr})")

    leave = item.get("exit")
    if leave and leave["side"] in (("left", "right") if axis == "x" else ("top", "bottom")):
        d = leave["duration"]
        t0 = max(item["start"], item["end"] - d)
        if axis == "x":
            off = -ov_w if leave["side"] == "left" else W
        else:
            off = -span if leave["side"] == "top" else H
        p = _ease_progress(f"(t-{t0:.3f})/{d:.3f}",
                           leave.get("ease") or "linear")
        expr = (f"if(gt(t,{t0:.3f}),{base:.3f}+({off:.3f}-({base:.3f}))"
                f"*{p},{expr})")

    motion = item.get("motion") or {}
    mtype = motion.get("type") or "none"
    amp = motion.get("amplitude") or 0.0
    period = motion.get("period") or 2.2
    amp_y = motion.get("amp_y") or 0.0
    period_y = motion.get("period_y") or 0.0
    if mtype == "orbit" and axis == "y" and amp_y and period_y:
        # 竖轴幅度整片固定，只有横轴跟着语气换摆幅：竖轴一变，切点上人物
        # 就上下错位；不变就没有错位，只剩节奏一致的呼吸。
        phase = float(motion.get("phase") or 0.0)
        expr = (f"({expr})+{float(amp_y):.3f}"
                f"*sin(2*PI*(t-{item['start'] - phase:.3f})/{float(period_y):.3f})")
    if mtype == "orbit":
        active = axis == "x"
    else:
        active = amp and mtype in ("bob", "sway") and (
            (mtype == "bob" and axis == "y") or (mtype == "sway" and axis == "x"))
    if active and amp:
        phase = float(motion.get("phase") or 0.0)
        # 摆幅换挡：切点前后幅度不同，直接换等于在切点上把人物挪一下。
        # 用一段线性过渡把幅度从上一段的接过来，动作连续、幅度也连续。
        amp_from = motion.get("amp_from")
        ramp = float(motion.get("ramp") or 0.0)
        if amp_from is not None and ramp > 0 and abs(float(amp_from) - amp) > 0.01:
            amp_expr = (f"({float(amp_from):.3f}+({amp:.3f}-{float(amp_from):.3f})"
                        f"*min(1,max(0,(t-{item['start']:.3f})/{ramp:.3f})))")
        else:
            amp_expr = f"{amp:.3f}"
        expr = (f"({expr})+{amp_expr}"
                f"*sin(2*PI*(t-{item['start'] - phase:.3f})/{period:.3f})")

    return expr


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
            if clip.get("still"):
                # 静帧只进一次，帧数由 zoompan 的 d 决定；加 -loop 会变成 d 倍帧数
                cmd += ["-i", str(clip["src"])]
            else:
                cmd += ["-ss", f"{clip['in']:g}",
                        "-t", f"{clip['render'] / (clip['speed'] or 1):g}",
                        "-i", str(clip["src"])]

        n_video = len(self.spec["video"])
        # 2) 字幕 PNG：loop 成整段视频，便于用 fade 的绝对时间
        base = n_video
        for i, (png, w, h, item) in enumerate(self.text_pngs):
            cmd += ["-loop", "1", "-framerate", f"{FPS:g}",
                    "-t", f"{total:g}", "-i", str(png)]
        # 3) 叠加层：图片 loop 成整段；视频循环播放（alpha 需要显式 vp9 解码器）
        overlay_base = base + len(self.text_pngs)
        for o in self.spec["overlay"]:
            if o["kind"] == "video":
                dec = ["-c:v", "libvpx-vp9"] if o["src"].suffix.lower() == ".webm" else []
                cmd += ["-stream_loop", "-1"] + dec + ["-i", str(o["src"])]
            else:
                cmd += ["-loop", "1", "-framerate", f"{FPS:g}",
                        "-t", f"{total:g}", "-i", str(o["src"])]
        # 4) 音频
        audio_base = overlay_base + len(self.spec["overlay"])
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
            dur = clip["render"] / speed
            label = f"[{i}:v]"
            if clip.get("still"):
                z0, z1 = clip.get("zoom") or (1.0, 1.0)
                frames = max(1, int(round(dur * FPS)))
                keys = clip.get("keys") or [(0.0, z0), (dur, z1)]
                moving = any(abs(z - keys[0][1]) > 1e-3 for _, z in keys[1:])
                if moving:
                    # 静卡不动就是 PPT；有关键帧就按折线推，一段里可以推近-停-拉远
                    chain = (f"zoompan=z='{_zoom_expr(keys, frames, FPS)}':"
                             f"d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                             f"s={W}x{H}:fps={FPS:g},trim=duration={dur:g}")
                else:
                    chain = f"scale={W}:{H},fps={FPS:g},trim=duration={dur:g}"
            else:
                if speed != 1.0:
                    parts.append(f"[{i}:v]setpts=PTS/{speed:g}[s{i}]")
                    label = f"[s{i}]"
                scale = (f"scale={W}:{H}:force_original_aspect_ratio="
                         f"{'increase' if clip['fit'] == 'cover' else 'decrease'}")
                if clip["fit"] == "cover":
                    chain = f"{scale},crop={W}:{H},fps={FPS:g}"
                else:
                    chain = f"{scale},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,fps={FPS:g}"
            if not clip.get("still"):
                keys = clip.get("keys") or []
                moving = len(keys) > 1 and any(abs(z - keys[0][1]) > 1e-3 for _, z in keys[1:])
                if moving:
                    # 活动素材的推拉：d=1 让 zoompan 一帧进一帧出，on 就等于帧序号，
                    # 和静卡那条分支共用同一套 _zoom_expr 折线表达式；时长不变，
                    # 所以后面的 concat/xfade 起点一个都不用改。
                    frames = max(1, int(round(dur * FPS)))
                    chain += (f",zoompan=z='{_zoom_expr(keys, frames, FPS)}':"
                              f"d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                              f"s={W}x{H}:fps={FPS:g}")
            fi, fo = clip.get("fade_in") or 0.0, clip.get("fade_out") or 0.0
            if fi > 0:
                chain += f",fade=t=in:st=0:d={fi:g}"
            if fo > 0:
                chain += f",fade=t=out:st={max(0.0, dur - fo):g}:d={fo:g}"
            parts.append(f"{label}{chain},setsar=1,format=yuv420p[v{i}]")
        # 拼成一条时间线：有 xfade 就真交叠（背景是一层层融过去的，不会在切点
        # 抖一下光），没有就硬切。两种都要求后面每屏的绝对起点仍是各屏时长之和。
        xf = [clip["xfade_in"] for clip in self.spec["video"]]
        if any(x > 0 for x in xf):
            if any(x <= 0 for x in xf[1:]):
                raise SpecError("video 一旦启用 xfade_in，就必须每段都设置（第一段可为 0）")
            starts, acc = [], 0.0
            for clip in self.spec["video"]:
                starts.append(acc)
                acc += clip["dur"] / (clip["speed"] or 1.0)
            last_v = "[v0]"
            for i in range(1, n_video):
                d = xf[i]
                parts.append(f"{last_v}[v{i}]xfade=transition=fade:"
                             f"duration={d:g}:offset={starts[i] - d:g}[xf{i}]")
                last_v = f"[xf{i}]"
            parts.append(f"{last_v}null[vc]")
        else:
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

        # 叠加层：先 scale/alpha 处理，再按表达式定位；in/out 之外整体不显示
        for k, o in enumerate(self.spec["overlay"]):
            idx = overlay_base + k
            chain = f"[{idx}:v]"
            if o["kind"] == "video":
                chain += f"setpts=PTS+{o['start']:.3f}/TB,"
            if o["width"]:
                chain += f"scale={o['width']}:-2,"
            chain += "format=rgba"
            if o["opacity"] < 1.0:
                chain += f",colorchannelmixer=aa={o['opacity']:g}"
            if o["fade"] > 0:
                f, mode = o["fade"], o.get("fade_mode") or "both"
                if mode in ("both", "in"):
                    chain += f",fade=t=in:st={o['start']:.3f}:d={f:g}:alpha=1"
                if mode in ("both", "out"):
                    chain += (f",fade=t=out:st={max(o['start'], o['end'] - f):.3f}"
                              f":d={f:g}:alpha=1")
            parts.append(f"{chain}[o{k}]")
            nxt = f"[vo{k}]"
            x = _overlay_axis_expr(o, "x", c)
            y = _overlay_axis_expr(o, "y", c)
            parts.append(
                f"{last}[o{k}]overlay=x='{x}':y='{y}':eof_action=pass:"
                f"enable='between(t,{o['start']:.3f},{o['end']:.3f})'{nxt}")
            last = nxt

        parts.append(f"{last}null[vout]")

        # 音频链
        if self.spec["audio"]:
            for k, a in enumerate(self.spec["audio"]):
                idx = audio_base + k
                delay = int(round(a["start"] * 1000))
                if a.get("volume_expr"):
                    vol_arg = f"volume='{a['volume_expr']}':eval=frame"
                else:
                    vol_arg = f"volume={a['volume']:g}"
                chain = (f"[{idx}:a]aresample={48000},aformat=sample_fmts=fltp:"
                         f"channel_layouts=stereo,{vol_arg}")
                if a["fade"] > 0:
                    chain += (f",afade=t=in:st=0:d={a['fade']:g}")
                    tail = max(0.0, total - a["start"] - a["fade"])
                    chain += f",afade=t=out:st={tail:g}:d={a['fade']:g}"
                if delay:
                    chain += f",adelay={delay}|{delay}"
                chain += f",apad=whole_dur={total:g}[a{k}]"
                parts.append(chain)
            amix_in = "".join(f"[a{k}]" for k in range(len(self.spec["audio"])))
            # 分段音量要的是「开场压住、中段让开」的相对关系；loudnorm 单遍会按时间追着改增益，
            # 把这条曲线抹平，所以有分段音量时只用限幅兜峰值。
            if any(a.get("volume_expr") for a in self.spec["audio"]):
                tail = "alimiter=limit=0.95"
            else:
                tail = "loudnorm=I=-16:TP=-1.5:LRA=11"
            parts.append(
                f"{amix_in}amix=inputs={len(self.spec['audio'])}:duration=longest:"
                f"normalize=0,{tail},atrim=0:{total:g}[aout]")
        else:
            parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                f"atrim=0:{total:g}[aout]")

        cmd += ["-filter_complex", ";".join(parts),
                "-map", "[vout]", "-map", "[aout]",
                # veryfast 对 medium：同一条 20s 片 105s vs 146s，跟 medium 的
                # 画面差 PSNR 50.7dB（肉眼看不出），文件反而小 8%
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
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
        "beats": len(spec.get("beats") or []),
        "bgm": bool(spec.get("music")),
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
