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


def _compose_card(scene: dict, out: Path, role: str) -> Path:
    """把标题排进卡面。

    ``render_previews`` 用的是剪映层的相对坐标，直接搬来会顶到画布外；
    mp4 这版按像素自己排，开场居中、要点贴左边强调条。
    """
    from PIL import Image, ImageDraw

    page = Image.open(scene["image"]).convert("RGB")
    text = " ".join(str(scene.get("text") or "").split())
    if text:
        draw = ImageDraw.Draw(page)
        if role == "hook":
            size, lines = _fit_title(text, factcard.W - 240, sizes=(72, 66, 60, 54))
            y = 640 - int(size * 1.25) * (len(lines) - 1) // 2
            for ln in lines:
                draw.text((factcard.W // 2, y), ln, font=factcard._font(size),
                          fill=(255, 255, 255, 240), anchor="ma")
                y += int(size * 1.28)
        else:
            size, lines = _fit_title(text, 1004 - 140 - 24)
            y = 512 if len(lines) > 1 else 542
            for ln in lines:
                draw.text((140, y), ln, font=factcard._font(size),
                          fill=(255, 255, 255, 226))
                y += int(size * 1.26)
    page.save(out, "PNG")
    return out


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
    )
    scenes = spec["scenes"]
    total = len(scenes)
    # 卡面文字自己排：预览版是给剪映层用的相对坐标，烧进 mp4 会溢出画面
    # 标题取原始文本：剪映层那份被压掉空格，"KV Cache" 会粘成 "KVCache"
    raw_titles = [" ".join(str(script.get("topic") or "").split())]
    for raw in points_raw:
        raw_titles.append(" ".join(str(raw).split("::")[0].split()))
    raw_titles.append("")
    cards = []
    for i, sc in enumerate(scenes):
        title = raw_titles[i] if i < len(raw_titles) else ""
        cards.append(_compose_card({**sc, "text": title}, workdir / f"card{i:02d}.png",
                                   "hook" if i == 0 else "cta" if i == total - 1 else "point"))
    char_cfg = script.get("character")
    if char_cfg is False:
        char_cfg = None
    elif char_cfg is None:
        char_cfg = {}

    video, overlay, audio = [], [], []
    t = 0.0
    for i, (scene, card) in enumerate(zip(scenes, cards)):
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
        if char_cfg is not None:
            frame = JE._character_frame(
                char_cfg, i, total, workdir, spec["width"], spec["height"],
                speaking=bool(say), scene=scene,
            )
            if frame is not None:
                # 立绘一断场就是贴图：整条轨都在，只靠淡入淡出换气
                fade = 0.0 if i in (0, total - 1) else FADE
                overlay.append({
                    "src": str(frame),
                    "start": round(max(0.0, t - 0.12), 3) if i else 0.0,
                    "end": round(t + dur + (0.12 if i < total - 1 else 0.0), 3),
                    "x": 0.0, "y": 0.0, "opacity": 1.0, "fade": fade,
                    "motion": {"type": "bob", "amplitude": 6.0, "period": 2.4},
                })
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
