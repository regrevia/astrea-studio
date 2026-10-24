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
XFADE = 0.40  # 相邻两屏叠化时长：整屏硬切是「PPT 翻页」最重的一笔


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


def _beats(say: str, dur: float, last: bool, cfg: dict) -> list[tuple[str, float]]:
    """把一屏切成若干口型段；不说话的屏给一段闭口静止。"""
    if not say:
        return [("", max(0.6, dur))]
    if not cfg.get("lip_flap", True):
        return [("half", dur)]
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
        parts = str(raw).split("::", 1)
        raw_bodies.append(" ".join(parts[1].split()) if len(parts) > 1 else "")
    hook_text = " ".join(str(script.get("hook") or factcard.DEFAULT_HOOK).split())
    cta_text = " ".join(str(script.get("cta") or factcard.DEFAULT_CTA).split())
    center_w = factcard.W - 248
    cards: list[Path] = []
    layer_sets: list[list[dict]] = []
    for i, sc in enumerate(scenes):
        cards.append(Path(str(sc["image"])))
        if i == 0:
            layers = _block_layers(hook_text, factcard.HOOK_SIZE, 800, workdir, f"h{i:02d}",
                                   align="center", left=124, max_w=center_w,
                                   fill=(255, 255, 255, 214))
        elif i == total - 1:
            layers = _block_layers(cta_text, factcard.HOOK_SIZE, 700, workdir, f"h{i:02d}",
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
    if char_cfg is False:
        char_cfg = None
    elif char_cfg is None:
        char_cfg = {}

    video, overlay, audio = [], [], []
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
        for n, layer in enumerate(text_layers):
            at = max(0.0, t - lead + 0.10 + 0.12 * n)
            overlay.append({
                "src": str(layer["png"]),
                "start": round(at, 3),
                "end": round(t + dur, 3),
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
            beats = _beats(say, dur, i == total - 1, char_cfg)
            switch_at = 0.55
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
                elapsed += seg
        t += dur

    return {
        "canvas": {"width": spec["width"], "height": spec["height"], "fps": spec["fps"]},
        "video": video,
        "overlay": overlay,
        "audio": audio,
        "assetBase": str(workdir),
        "meta": {"engine": "vertical_short", "duration": round(t, 3),
                 "scenes": total, "voice": voice},
    }


def render(script: dict, workdir: Path, mp4: Path, *,
           voice: str = "", logo: Path | None = None) -> dict:
    workdir = Path(os.path.expanduser(str(workdir)))
    spec = build_spec(script, workdir, voice=voice, logo=logo)
    spec_path = workdir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = TR.load_spec(spec_path, base=workdir)
    ev = TR.render(loaded, mp4)
    return {
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
