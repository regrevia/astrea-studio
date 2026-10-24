#!/usr/bin/env python3
"""vertical_short.py — 竖屏factcard → 母版 mp4，纯 ffmpeg/Pillow，不起剪映。

剪映草稿要人点导出，这条不需要：卡面、立绘、配音全部先在本地落成文件，
再交给 timeline_render 合成一条 1080×1920 的 mp4。同一条 spec 也能进容器跑。

版式与 ``factcard`` 引擎一致：卡面就是 ``render_previews`` 烧好字的那张，
立绘复用 ``jianying_engine._character_frame``（含影子与描边柔光），
所以「剪映草稿」和「mp4」看到的不是两种片子。

用法::

    python3 export/vertical_short.py /tmp/one.json --outdir /tmp/vs1 --mp4 /tmp/vs1.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE.parent
sys.path.insert(0, str(WORKER))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKER / "tools"))

import factcard  # noqa: E402
import jianying_engine as JE  # noqa: E402
import make_episode as MEP  # noqa: E402
import timeline_render as TR  # noqa: E402
from slides_render import _tts  # noqa: E402

DEFAULT_VOICE = factcard.DEFAULT_VOICE
# 收尾卡比正文多停一点：观众要时间读完那句结论再走
TAIL_HOLD = 0.35
FADE = 0.22
XFADE = 0.40
TAIL = 1.00  # 话说完再定一秒：立刻黑屏是最廉价的收尾  # 相邻两屏叠化时长：整屏硬切是「PPT 翻页」最重的一笔


class VerticalError(RuntimeError):
    """素材/配音/渲染任一步失败。"""


def _dur(path: Path) -> float:
    """时长只用 worker 配好的 ffprobe：launchd 的 PATH 里没有 homebrew。"""
    r = subprocess.run([TR.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise VerticalError(f"读不出配音时长: {path} {r.stderr.strip()[:200]}") from None


def _scene_duration(audio: float, last: bool) -> float:
    """画面长度跟着配音走，多留的只有气口。"""
    pad = 0.18 + (TAIL_HOLD if last else 0.0)
    return max(1.6, min(9.0, audio + pad))


def _fit_title(text: str, max_w: int, sizes=(66, 60, 54, 48)) -> tuple[int, list[str]]:
    """标题挑一个能排进版心的字号，最多两行；排不下就换小一号。"""
    from PIL import Image, ImageDraw

    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for size in sizes:
        lines = factcard._wrap_text(measure, text, factcard._font(size), max_w)
        if len(lines) <= 2:
            return size, lines
    return sizes[-1], lines


def _measure():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _text_layer(line: str, size: int, workdir: Path | None, name: str, *,
                fill=(255, 255, 255, 240)) -> tuple[tuple[int, int], Path]:
    """把一行字渲成紧贴字形的透明 PNG，返回 ((w,h), path)。"""
    from PIL import Image, ImageDraw

    font = factcard._font(size)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    box = probe.textbbox((0, 0), line, font=font)
    pad = max(4, size // 8)
    w = max(2, box[2] - box[0]) + pad * 2
    w += w % 2  # 时间轴按 scale=w:-2 缩放，宽度必须偶数
    h = max(1, box[3] - box[1]) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - box[0], pad - box[1]), line, font=font, fill=fill)
    path = Path(workdir or "/tmp") / name
    img.save(path, "PNG")
    return (w, h), path


def _block_layers(text: str, size: int, top: int, workdir: Path, prefix: str, *,
                  align: str, left: int, max_w: int, fill, spacing: float = 1.42) -> list[dict]:
    """一段正文按 factcard 的排版规则切成逐行贴片（行高、断句、对齐都跟卡面一致）。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    draw = _measure()
    font = factcard._font(size)
    out: list[dict] = []
    y = top
    for n, ln in enumerate([ln for ln in factcard._wrap_text(draw, text, font, max_w) if ln]):
        (w, _h), png = _text_layer(ln, size, workdir, f"{prefix}_l{n}.png", fill=fill)
        x = left + (max_w - w) // 2 if align == "center" else left
        out.append({"png": png, "x": int(x), "y": int(y), "w": int(w)})
        y += int(size * spacing)
    return out


def _stage(cfg: dict, idx: int, total: int) -> dict:
    """按屏给人物换机位：整片钉死在一个角上，再灵的口型也像贴图。

    开头略远（把话说给你听），正文推进，收尾靠近（话是对着你说的）。
    脚本里显式给了 width/x/y 就照用，这里只补空位。
    """
    out = dict(cfg)
    if idx == 0:
        want = {"width": 566, "x": 586, "y": 1062}
    elif idx == total - 1:
        want = {"width": 664, "x": 494, "y": 966}
    else:
        want = {"width": 620, "x": 560, "y": 1014}
    for k, v in want.items():
        if not out.get(k):
            out[k] = v
    return out


def _title_layers(text: str, workdir: Path, idx: int) -> list[dict]:
    """要点标题：贴左边强调条，和原来烧进卡面的位置一致。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    size, lines = _fit_title(text, 1004 - 140 - 24)
    top = 512 if len(lines) > 1 else 542
    out: list[dict] = []
    for n, ln in enumerate(lines):
        (w, _h), png = _text_layer(ln, size, workdir, f"t{idx:02d}_{n}.png",
                                   fill=(255, 255, 255, 226))
        out.append({"png": png, "x": 140, "y": int(top + n * size * 1.26), "w": int(w)})
    return out


def _text_layer(line: str, size: int, workdir: Path | None, name: str, *,
                align: str = "left", fill=(255, 255, 255, 240)):
    """把一行字渲成紧贴字形的透明 PNG，返回 (path, w, h) 与相对基线偏移。"""
    from PIL import Image, ImageDraw

    font = factcard._font(size)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    box = probe.textbbox((0, 0), line, font=font)
    pad = max(4, size // 8)
    w = max(2, box[2] - box[0]) + pad * 2
    w += w % 2  # 时间轴按 scale=w:-2 缩放，宽度必须偶数
    h = max(1, box[3] - box[1]) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - box[0], pad - box[1]), line, font=font, fill=fill)
    if workdir is None:
        workdir = Path("/tmp")
    path = Path(workdir) / name
    img.save(path, "PNG")
    return (w, h), path



# 口型循环：四拍一轮，读起来像在说话，又不会每帧都换脸
_MOUTH_CYCLE = ("open", "half", "round", "half")
# 姿态决定动幅：想事情就该比挥手安静，兴奋时起伏更大
_POSE_MOTION = {
    "happy": {"type": "bob", "amplitude": 9.0, "period": 1.9},
    "wave": {"type": "bob", "amplitude": 8.0, "period": 2.1},
    "point": {"type": "bob", "amplitude": 5.0, "period": 2.6},
    "think": {"type": "sway", "amplitude": 2.0, "period": 3.4},
    "shrug": {"type": "sway", "amplitude": 1.5, "period": 3.0},
    "idle": {"type": "bob", "amplitude": 6.0, "period": 2.4},
}


def _pose_motion(pose: str) -> dict:
    return dict(_POSE_MOTION.get(pose, _POSE_MOTION["idle"]))


def _energy_states(mp3: Path, dur: float) -> list[tuple[float, float]]:
    """解码成 16k 单声道，按 0.1 秒算音量，返回 (起点, 相对音量 0..1)。"""
    import array
    import subprocess
    import wave

    wav = mp3.with_suffix(".wav")
    if not wav.exists():
        subprocess.run([TR.FFMPEG, "-hide_banner", "-loglevel", "error",
                        "-i", str(mp3), "-ac", "1", "-ar", "16000",
                        "-y", str(wav)], check=True)
    with wave.open(str(wav), "rb") as f:
        rate = f.getframerate()
        pcm = array.array("h", f.readframes(f.getnframes()))
    step = max(1, int(rate * 0.1))
    levels: list[tuple[float, float]] = []
    for off in range(0, len(pcm) - step + 1, step):
        chunk = pcm[off:off + step]
        if not chunk:
            continue
        rms = (sum(v * v for v in chunk) / len(chunk)) ** 0.5
        levels.append((off / rate, rms))
    peak = max((v for _, v in levels), default=0.0) or 1.0
    if not levels:
        return [(0.0, 0.0)]
    # 归一化后静音底噪也算「在说」：按峰值的一半做参考，宁可多动嘴
    return [(a, min(1.0, v / peak)) for a, v in levels]


def _clause_switch(mp3: Path, dur: float, cfg: dict) -> float | None:
    """手势换在句内停顿处，而不是死板的 55%：人换手势总在说完半句的时候。"""
    try:
        levels = _energy_states(mp3, dur)
    except Exception:
        return None
    if len(levels) < 8:
        return None
    vals = [v for _, v in levels]
    floor = max(0.06, sorted(vals)[int(len(vals) * 0.15)])
    median = sorted(vals)[len(vals) // 2]
    # 句读的停顿比静音底噪响：门槛放到中位音量的一半，才抓得到逗号后的换气
    floor = min(floor * 1.6, median * 0.6)
    lo = int(len(levels) * 0.18)
    hi = int(len(levels) * 0.86)
    best, best_len, run, run_start = 0, 0.0, 0, None
    for i in range(lo, hi):
        if levels[i][1] <= floor:
            if run_start is None:
                run_start = i
            run += 1
            if run > best_len:
                best, best_len = run_start, run
        else:
            run, run_start = 0, None
    if best_len < 2:
        return None
    mid = levels[min(best + best_len // 2, len(levels) - 1)][0]
    frac = mid / max(0.4, dur)
    return frac if 0.25 <= frac <= 0.9 else None


def _audio_beats(mp3: Path, dur: float, cfg: dict) -> list[tuple[str, float]]:
    """口型跟着实际音量走：静音闭口、轻读半开、重音张开，长短句节奏就不同了。"""
    levels = _energy_states(mp3, dur)
    if len(levels) < 3:
        return []
    # 三窗口中值平滑，避免单帧抖动把嘴抖成抽搐
    smooth = [levels[0][1]]
    for i in range(1, len(levels) - 1):
        smooth.append(sorted(v for _, v in levels[i - 1:i + 2])[1])
    smooth.append(levels[-1][1])
    floor = max(0.06, sorted(smooth)[int(len(smooth) * 0.15)])
    states: list[str] = []
    for v in smooth:
        if v < floor:
            states.append("closed")
        elif v < floor + (1.0 - floor) * 0.55:
            states.append("half")
        else:
            states.append("open")
    segs: list[tuple[str, float]] = []
    min_seg = float(cfg.get("lip_min") or 0.18)
    for i, st in enumerate(states):
        at = levels[min(i, len(levels) - 1)][0]
        if segs and segs[-1][0] == st:
            segs[-1] = (st, segs[-1][1] + 0.1)
        elif segs and segs[-1][1] < min_seg:
            # 太短的一段口型看不出来，并进前一段，别让嘴在原地闪
            segs[-1] = (segs[-1][0], segs[-1][1] + 0.1)
        else:
            segs.append((st, 0.1))
    # 一句话说完嘴不会「啪」地闭上：先落到半开，再闭。句末留一点余开更像在呼吸
    eased: list[tuple[str, float]] = []
    release = float(cfg.get("lip_release") or 0.12)
    for st, sec in segs:
        if st == "closed" and sec > release * 2 and eased and eased[-1][0] != "closed":
            eased.append(("half", release))
            eased.append((st, sec - release))
        else:
            eased.append((st, sec))
    merged: list[tuple[str, float]] = []
    for st, sec in eased:
        if merged and merged[-1][0] == st:
            merged[-1] = (st, merged[-1][1] + sec)
        else:
            merged.append((st, sec))
    eased = merged
    total = sum(sec for _, sec in eased) or 1.0
    scale = dur / total
    return [(st, sec * scale) for st, sec in eased if st]


def _beats(say: str, dur: float, last: bool, cfg: dict,
           mp3: Path | None = None) -> list[tuple[str, float]]:
    """把一屏切成若干口型段；不说话的屏给一段闭口静止。"""
    if not say:
        return [("", max(0.6, dur))]
    if not cfg.get("lip_flap", True):
        return [("half", dur)]
    if mp3 is not None and cfg.get("lip_from_audio", True):
        try:
            heard = _audio_beats(mp3, dur, cfg)
        except Exception:      # 解码失败就退回固定周期，别为口型把整片打掉
            heard = []
        if len(heard) >= 2:
            return heard
    period = float(cfg.get("lip_period") or 0.42)
    count = max(2, min(8, int(round(dur / period)) or 2))
    seg = dur / count
    return [(_MOUTH_CYCLE[k % 4], seg) for k in range(count)]



_CHAR_CROPS: dict = {}


def _crop_frame(png: Path, workdir: Path, name: str) -> tuple[Path, int, int, int]:
    """立绘整幅画布是 1080×1920 的透明 PNG，一段口型就 8MB。

    按不透明内容裁到最小框再当叠加层：x/y 仍用画布坐标定位，
    渲染输入小一个数量级，切片再多也不会把 ffmpeg 拖垮。
    """
    cached = _CHAR_CROPS.get(name)
    if cached and cached[0].exists():
        return cached
    from PIL import Image

    src = Image.open(png).convert("RGBA")
    bb = src.getbbox() or (0, 0, src.width, src.height)
    pad = 2
    left = max(0, bb[0] - pad)
    top = max(0, bb[1] - pad)
    right = min(src.width, bb[2] + pad)
    bottom = min(src.height, bb[3] + pad)
    crop = src.crop((left, top, right, bottom))
    if crop.width % 2:
        crop = crop.crop((0, 0, crop.width - 1, crop.height))
    out = workdir / name
    crop.save(out)
    val = (out, left, top, crop.width)
    _CHAR_CROPS[name] = val
    return val


def build_spec(script: dict, workdir: Path, *, voice: str = "", logo: Path | None = None) -> dict:
    """脚本 → timeline spec（静帧卡 + 立绘 + 逐屏配音）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    voice = voice or str(script.get("voice") or DEFAULT_VOICE)
    # 口播稿可能是文件路径（和 make_episode 一样）：不认路径就会退回要点里的说明句，
    # 同一份脚本出的 mp4 比剪映草稿长几秒，听起来还不像人写的
    narration = script.get("narration")
    if isinstance(narration, str) and narration.strip():
        lines = Path(os.path.expanduser(narration.strip())).read_text(
            encoding="utf-8").splitlines()
        points_raw, narration = MEP.apply_narration(list(script.get("points") or []), lines, None)
    else:
        points_raw = script.get("points") or []
    spec = factcard.build_spec(
        topic=str(script.get("topic") or "").strip(),
        hook=str(script.get("hook") or factcard.DEFAULT_HOOK),
        points_raw=points_raw,
        cta=str(script.get("cta") or factcard.DEFAULT_CTA),
        voice=voice,
        kicker=str(script.get("kicker") or factcard.DEFAULT_KICKER),
        narration=narration if isinstance(narration, dict) else None,
        logo=logo,
        out_dir=workdir,
        draw_text=False,
    )
    scenes = spec["scenes"]
    total = len(scenes)
    # 卡面文字自己排：预览版是给剪映层用的相对坐标，烧进 mp4 会溢出画面
    # 标题取原始文本：剪映层那份被压掉空格，"KV Cache" 会粘成 "KVCache"
    raw_bodies = []
    for raw in points_raw:
        # 三段式是「标题::卡面正文::口播」：卡面只放第二段，
        # 口播那段是给耳朵的，混进卡面就会看见一串 ::
        parts = str(raw).split("::")
        raw_bodies.append(" ".join(parts[1].split()) if len(parts) > 1 else "")
    hook_text = " ".join(str(script.get("hook") or factcard.DEFAULT_HOOK).split())
    cta_text = " ".join(str(script.get("cta") or factcard.DEFAULT_CTA).split())
    center_w = factcard.W - 248
    cards: list[Path] = []
    layer_sets: list[list[dict]] = []
    kicker = str(script.get("kicker") or factcard.DEFAULT_KICKER)
    for i, sc in enumerate(scenes):
        card = Path(str(sc["image"]))
        if i == total - 1 and not script.get("cta_card"):
            # 收尾空卡 + 一句飘着的字，最容易读成「没做完」：给收尾也铺一块卡面
            card = factcard.render_card(
                workdir / f"cta{i:02d}.png", kicker=kicker, index="",
                palette_idx=len(factcard.PALETTES) - 1, progress=total - 1,
                total=total, slug="cta", hook=False, draw_body=False)
        cards.append(card)
        if i == 0:
            layers = _block_layers(hook_text, factcard.HOOK_SIZE, 800, workdir, f"h{i:02d}",
                                   align="center", left=124, max_w=center_w,
                                   fill=(255, 255, 255, 214))
        elif i == total - 1:
            # 卡面左上角是空的强调条，配一句栏目名才不像是漏排了
            label = script.get("cta_label")
            if label is None:
                label = "轮到你了"
            layers = (_title_layers(str(label), workdir, i) if str(label).strip() else [])
            layers += _block_layers(cta_text, factcard.HOOK_SIZE, 700, workdir, f"h{i:02d}",
                                    align="center", left=124, max_w=center_w,
                                    fill=(255, 255, 255, 214))
        else:
            layers = _title_layers(str(sc.get("text") or ""), workdir, i)
            body = raw_bodies[i - 1] if 0 < i <= len(raw_bodies) else ""
            if body:
                size, _n = factcard._body_size(body, _measure())
                top = 700 + (56 if size >= 110 else 40 if size > factcard.BODY_SIZE else 0)
                layers += _block_layers(body, size, top, workdir, f"b{i:02d}",
                                        align="left", left=140, max_w=factcard.W - 264,
                                        fill=(255, 255, 255, 236))
        layer_sets.append(layers)
    char_cfg = script.get("character")
    if char_cfg is True:
        char_cfg = {}
    elif char_cfg is False:
        char_cfg = None
    elif char_cfg is None:
        char_cfg = {}

    video, overlay, audio = [], [], []
    last_ov = None
    t = 0.0
    for i, (scene, card) in enumerate(zip(scenes, cards)):
        text_layers = layer_sets[i]
        say = str(scene.get("say") or "").strip()
        mp3 = workdir / f"a{i:02d}.mp3"
        if say:
            _tts(say, mp3, voice)
            dur = _scene_duration(_dur(mp3), i == total - 1)
            audio.append({"src": str(mp3), "start": round(t, 3), "volume": 1.0})
        else:
            dur = 2.0
        z0, z1 = (1.0, 1.045) if i % 2 == 0 else (1.045, 1.0)
        video.append({
            "src": str(card), "out": round(dur, 3), "zoom": [z0, z1],
            "fade_in": FADE if i == 0 else 0.0,
            "fade_out": FADE if i == total - 1 else 0.0,
        })
        # 上一屏末尾把下一张卡整幅叠化上来：两屏之间是融过去的，不是切过去的
        if i > 0:
            overlay.append({
                "src": str(cards[i]),
                "start": round(max(0.0, t - XFADE), 3),
                "end": round(t, 3),
                "x": 0.0, "y": 0.0, "width": 0, "opacity": 1.0,
                "fade": XFADE, "fade_mode": "in",
                "motion": {"type": "none", "amplitude": 0.0, "period": 1.0},
            })
        # 一行一行落：每行 0.12s 间隔淡入，比整屏切过去更像有人在讲
        # 叠化中段就把字提上来，否则新屏会先空 0.4 秒
        lead = XFADE * 0.5 if i > 0 else 0.0
        # 收尾定住的那一秒里字要还在，否则卡面是空的
        text_end = t + dur + (TAIL if i == total - 1 else 0.0)
        for n, layer in enumerate(text_layers):
            at = max(0.0, t - lead + 0.10 + 0.12 * n)
            overlay.append({
                "src": str(layer["png"]),
                "start": round(at, 3),
                "end": round(text_end, 3),
                "x": float(layer["x"]), "y": float(layer["y"]),
                "opacity": 1.0, "width": int(layer["w"]),
                "fade": 0.30,
                "motion": {"type": "none", "amplitude": 0.0, "period": 1.0},
            })
        if char_cfg is not None:
            # 一屏一张立绘就是贴图：按口型拍点切成几段，手势在中段换一次，
            # 角色才是在"讲这句话"，而不是被贴上来定格。
            pose = str(char_cfg.get("pose") or JE._scene_pose(char_cfg, i, total, scene))
            alt_pose = (None if char_cfg.get("pose") else
                        JE._second_half_pose(char_cfg, i, total, scene))
            if not char_cfg.get("gesture_sync", True):
                alt_pose = None
            motion = _pose_motion(pose)
            char_scene = _stage(char_cfg, i, total)
            beats = _beats(say, dur, i == total - 1, char_cfg,
                           mp3=mp3 if say else None)
            switch_at = 0.55
            if alt_pose and say:
                heard = _clause_switch(mp3, dur, char_cfg)
                if heard:
                    switch_at = heard
            elapsed = 0.0
            prev_ov = None      # 上一段口型：换姿态时要和它交叉，不能硬替换
            prev_pose = None
            for mouth, seg in beats:
                use_pose = pose
                if alt_pose and dur > 0 and (elapsed / dur) >= switch_at:
                    use_pose = alt_pose
                frame = JE._character_frame(
                    char_scene, i, total, workdir, spec["width"], spec["height"],
                    speaking=bool(say), scene=scene, pose=use_pose, mouth=mouth,
                )
                if frame is not None:
                    frame, fx, fy, fw = _crop_frame(
                        frame, workdir, f"c{i:02d}_{use_pose}_{mouth}.png")
                    start = t + elapsed
                    end = start + seg
                    head = elapsed == 0.0
                    if head and i == 0:
                        start = max(0.0, start - FADE)
                    elif head:
                        # 人物跟着叠化一起露面，别在换屏瞬间凭空出现
                        start = max(0.0, start - lead)
                    fade_in = FADE if head else 0.0
                    if head and i > 0:
                        fade_in = max(fade_in, 0.25)
                    fade_out = FADE if (i == total - 1 and end >= t + dur - 1e-6) else 0.0
                    ov = {
                        "src": str(frame),
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "x": float(fx), "y": float(fy), "opacity": 1.0,
                        "width": fw,
                        "fade": max(fade_in, fade_out),
                        # 相位对齐到绝对时间：口型分段不打断呼吸节奏
                        "motion": {**motion, "phase": round(start, 3)},
                    }
                    if head and i == 0:
                        # 首屏从画面下沿升上来，别一开片就杵在那儿
                        ov["enter"] = {"from": "bottom", "duration": 0.55,
                                       "ease": "out-cubic"}
                    if prev_ov is not None and use_pose != prev_pose:
                        # 换手势瞬间直接替换会「跳一下」，两头各淡 0.15s 叠过去
                        start = max(0.0, start - 0.15)
                        ov["start"] = round(start, 3)
                        ov["fade"] = max(ov["fade"], 0.15)
                        prev_ov["end"] = round(start + 0.15, 3)
                        prev_ov["fade"] = max(prev_ov["fade"], 0.15)
                    overlay.append(ov)
                    prev_ov, prev_pose = ov, use_pose
                    last_ov = ov
                elapsed += seg
        t += dur

    # 收尾定住：最后一张卡再推一点、人物留到最后，然后才淡出
    if cards:
        video.append({"src": str(cards[-1]), "out": round(TAIL, 3),
                      "zoom": [1.015, 1.05], "fade_in": 0.0,
                      "fade_out": round(TAIL * 0.55, 3)})
        if last_ov is not None:
            last_ov["end"] = round(t + TAIL, 3)
            last_ov["fade"] = max(last_ov.get("fade") or 0.0,
                                  round(TAIL * 0.55, 3))
        t += TAIL

    return {
        "canvas": {"width": spec["width"], "height": spec["height"], "fps": spec["fps"]},
        "video": video,
        "overlay": overlay,
        "audio": audio,
        "assetBase": str(workdir),
        "meta": {"engine": "vertical_short", "duration": round(t, 3),
                 "scenes": total, "voice": voice},
    }


def _copy_report(script: dict) -> dict | None:
    """竖屏这条链路也要体检文案：写稿的人看的是同一个结论。"""
    try:
        from copy_check import check_copy
    except Exception:  # noqa: BLE001
        return None
    narr = script.get("narration")
    narr = narr if isinstance(narr, dict) else {}
    pts = [str(x) for x in (script.get("points") or [])]
    says = [x.split("::")[2] if x.count("::") >= 2 else "" for x in pts]
    spoken = [str(narr.get("intro") or "")] if narr.get("intro") else []
    fallback = [str(x) for x in (narr.get("points") or [])]
    spoken += [x for x in (says or fallback) if x]
    if narr.get("cta"):
        spoken.append(str(narr["cta"]))
    try:
        return check_copy(
            topic=str(script.get("topic") or ""),
            hook=str(script.get("hook") or ""),
            points=pts,
            cta=str(script.get("cta") or ""),
            narration=spoken,
        )
    except Exception as exc:  # noqa: BLE001 - 体检失败不影响出片
        return {"findings": [], "metrics": {}, "ok": True, "error": str(exc)[:160]}


def render(script: dict, workdir: Path, mp4: Path, *,
           voice: str = "", logo: Path | None = None) -> dict:
    workdir = Path(os.path.expanduser(str(workdir)))
    spec = build_spec(script, workdir, voice=voice, logo=logo)
    spec_path = workdir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = TR.load_spec(spec_path, base=workdir)
    ev = TR.render(loaded, mp4)
    copy_report = _copy_report(script)
    out = {
        "engine": "vertical_short",
        "videoPath": ev["output"],
        "specPath": str(spec_path),
        "duration": ev["duration"],
        "clips": ev["clips"],
        "texts": ev["texts"],
        "overlays": len(spec["overlay"]),
        "audios": ev["audios"],
        "renderSeconds": ev["seconds"],
    }
    if copy_report is not None:
        out["copyCheck"] = copy_report
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="竖屏 factcard 直接出 mp4（无剪映）")
    ap.add_argument("script", help="脚本 JSON：topic/hook/points/cta，或含这些字段的数组取第一条")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--mp4", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--logo", default="")
    args = ap.parse_args(argv)

    raw = json.loads(Path(os.path.expanduser(args.script)).read_text(encoding="utf-8"))
    script = raw[0] if isinstance(raw, list) else raw
    outdir = Path(args.outdir or "/tmp/vertical_short")
    mp4 = Path(args.mp4 or (outdir / "master.mp4"))
    logo = Path(args.logo).expanduser() if args.logo else (
        Path(str(script["logo"])).expanduser() if script.get("logo") else None)
    try:
        ev = render(script, outdir, mp4, voice=args.voice, logo=logo)
    except (VerticalError, TR.SpecError, ValueError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
