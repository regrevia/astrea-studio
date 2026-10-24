#!/usr/bin/env python3
"""presenter_style.py — 「虚拟主播讲技术」风格的内容 → timeline spec 构建器。

不是一个新的渲染引擎：它只负责**把内容排成版**，产出仍然是
``timeline_render.py`` 吃的那种 spec（页面卡片/人物/字幕全走 overlay 与 text），
所以同一份 spec 既能本机渲母版，也能作为 advanced spec 交给平台 worker。

版式（1920x1080，可换）
-----------------------
* 背景：深色渐变 + 细网格，自己生成，不依赖外部素材。
* 页面卡片：左侧圆角卡片，章节胶囊 / 标题 / 要点 / 页码，翻页=左右滑入滑出。
* 人物：右下角带 alpha 的 webm（默认 dsh-pet，MIT），呼吸浮动 + 淡入淡出。
* 字幕：底部半透明字幕条，按旁白时间轴切分。
* 配音：剪映音色（MPT 的 sami_tts 子进程），时长决定每页停留。

用法::

    python3 presenter_style.py content.json --outdir /tmp/pv --spec-out spec.json
    python3 presenter_style.py content.json --outdir /tmp/pv --spec-out s.json --render m.mp4

content.json::

    {"title": "Astrea Studio：一句话到成片", "series": "ASTREA STUDIO",
     "pages": [{"chapter": "开场", "title": "全链路是什么",
                "bullets": ["一句话需求", "自动查平台", "母版+剪映草稿"],
                "narration": "……口播文案……",
                "maid": "女仆屈膝礼仪.webm"}]}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import host_profile as H  # noqa: E402
import timeline_render as T  # noqa: E402

# 主角模块：默认是 dsh-pet 的蓝毛小女仆（MIT）；也可以换成鲸鱼娘、真人或数字人
DEFAULT_MAID_DIR = H.DEFAULT_MAID_DIR
DEFAULT_VOICE = H.DEFAULT_VOICE

PAGE = {"x": 76, "y": 168, "w": 948, "h": 632}
MAID = dict(H.DEFAULT_PLACEMENT)   # 素材带透明留白，视觉实体约 45% 宽
CAPTION_Y = 822        # 字幕条顶边像素 y（卡片正下方）
GAP = 0.75           # 页与页之间留呼吸（也把总时长抬到 1 分钟以上）
SLIDE = 0.42         # 翻页时长

NAVY = (10, 15, 30)
INK = (234, 239, 252)
MUTED = (150, 161, 190)
ACCENT = (110, 168, 255)


class PresenterError(RuntimeError):
    """内容或素材不满足版式要求。"""


# ------------------------------------------------------------------ 素材生成
def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(T.find_font(), size)


def _wrap(draw, text: str, font, max_w: float) -> list:
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def make_backdrop(path: Path, width: int, height: int) -> Path:
    """深色渐变 + 网格 + 右上角柔光。输出 PNG（由 ffmpeg 转成静帧视频）。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(height):
        t = y / max(1, height - 1)
        d.line([(0, y), (width, y)],
               fill=(int(10 + 14 * t), int(15 + 18 * t), int(30 + 26 * t)))
    glow = Image.new("L", (width, height), 0)
    gd = ImageDraw.Draw(glow)
    cx, cy, r = int(width * 0.78), int(height * 0.22), int(height * 0.62)
    for i in range(28, 0, -1):
        rad = int(r * i / 28)
        gd.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                   fill=int(9 * (1 - i / 28)))
    img = Image.composite(Image.new("RGB", (width, height), (52, 92, 168)),
                          img, glow)
    d = ImageDraw.Draw(img)
    step = max(40, width // 48)
    for x in range(0, width, step):
        d.line([(x, 0), (x, height)], fill=(22, 30, 52))
    for y in range(0, height, step):
        d.line([(0, y), (width, y)], fill=(22, 30, 52))
    img.save(path)
    return path


def make_page(path: Path, page: dict, idx: int, total: int, series: str) -> Path:
    """一页卡片：章节胶囊 / 标题 / 要点 / 页码 / 强调色边。"""
    from PIL import Image, ImageDraw
    w, h = PAGE["w"], PAGE["h"]
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 6, w - 1, h - 1], radius=34, fill=(18, 26, 46, 246))
    d.rounded_rectangle([0, 6, w - 1, h - 1], radius=34,
                        outline=(48, 62, 96, 255), width=2)
    d.rounded_rectangle([0, 6, 10, h - 7], radius=5, fill=ACCENT + (255,))

    pad = 62
    f_series = _font(26)
    f_chip = _font(28)
    f_title = _font(70)
    f_body = _font(36)
    f_idx = _font(30)

    d.text((pad, 46), series.upper(), font=f_series, fill=MUTED + (255,))
    chip = str(page.get("chapter") or f"0{idx + 1}")
    cw = d.textlength(chip, font=f_chip) + 44
    d.rounded_rectangle([pad, 104, pad + cw, 104 + 50], radius=25,
                        fill=ACCENT + (36,), outline=ACCENT + (140,))
    d.text((pad + 22, 114), chip, font=f_chip, fill=ACCENT + (255,))

    y = 210
    for line in _wrap(d, str(page["title"]), f_title, w - pad * 2 - 40)[:2]:
        d.text((pad, y), line, font=f_title, fill=INK + (255,))
        y += 84
    y += 18

    for bullet in page.get("bullets") or []:
        d.ellipse([pad + 4, y + 17, pad + 16, y + 29], fill=ACCENT + (255,))
        for line in _wrap(d, str(bullet), f_body, w - pad * 2 - 60)[:2]:
            d.text((pad + 40, y), line, font=f_body, fill=(214, 223, 244, 255))
            y += 56
        y += 18

    note = str(page.get("note") or "").strip()
    if note:
        f_note = _font(25)
        nw = d.textlength(note, font=f_note)
        if nw <= w - pad * 2 - 240:
            d.text((w - pad - nw, 49), note, font=f_note, fill=ACCENT + (255,))
    idx_txt = f"{idx + 1:02d} / {total:02d}"
    d.text((w - pad - d.textlength(idx_txt, font=f_idx), h - 62), idx_txt,
           font=f_idx, fill=MUTED + (255,))
    img.save(path)
    return path


def make_caption(path: Path, text: str, width: int = PAGE["w"]) -> Path:
    """字幕条：和卡片同宽的半透明圆角条，按像素宽度折行，绝不出框。"""
    from PIL import Image, ImageDraw
    f = _font(30)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    pad_x, pad_y, lh = 32, 20, 44
    limit = width - pad_x * 2
    lines, cur = [], ""
    for ch in " ".join(str(text).split()):
        if cur and probe.textlength(cur + ch, font=f) > limit:
            if ch in PUNCT:
                cur += ch
                continue
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = lines or [""]
    h = pad_y * 2 + lh * len(lines)
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=18,
                        fill=(10, 15, 30, 220))
    d.rounded_rectangle([0, 0, 6, h - 1], radius=3, fill=ACCENT + (255,))
    for i, line in enumerate(lines):
        d.text((pad_x, pad_y + i * lh), line, font=f, fill=(242, 246, 255, 255))
    img.save(path)
    return path


def make_still_video(png: Path, out: Path, seconds: float, fps: float) -> Path:
    subprocess.run(
        [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-loop", "1", "-framerate", f"{fps:g}", "-i", str(png),
         "-t", f"{seconds:.3f}", "-r", f"{fps:g}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


BGM_CHORDS = (
    # A 小调 → F → G → Am-F 混合，慢板铺底，四段交叉淡入淡出
    (220.00, 261.63, 329.63),
    (174.61, 220.00, 261.63),
    (196.00, 246.94, 293.66),
    (164.81, 220.00, 261.63),
)


def make_bgm(out: Path, work: Path, seconds: float = 0.0) -> Path:
    """合成一段可商用的环境铺底（纯合成，无版权素材），循环交给渲染器。"""
    inputs, parts = [], []
    for i, chord in enumerate(BGM_CHORDS):
        expr = "+".join(f"{0.30 / (j + 1):.3f}*sin(2*PI*{f:.2f}*t)"
                        for j, f in enumerate(chord))
        inputs += ["-f", "lavfi", "-i", f"aevalsrc='{expr}':d=4:s=48000"]
        parts.append(f"[{i}:a]")
    graph = parts[0] + parts[1] + "acrossfade=d=1.4:c1=tri:c2=tri[b1]"
    for i in range(2, len(parts)):
        graph += f";[{'b' + str(i - 1)}]{parts[i]}acrossfade=d=1.4:c1=tri:c2=tri[b{i}]"
    graph += (f";[b{len(parts) - 1}]lowpass=f=1100,tremolo=f=0.22:d=0.25,"
              "volume=0.55,afade=t=in:d=1.0[aout]")
    cmd = [T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", graph, "-map", "[aout]",
           "-c:a", "aac", "-b:a", "128k", str(out)]
    subprocess.run(cmd, check=True)
    return out


def narrate(text: str, out: Path, voice: str) -> float:
    """用剪映音色配音，返回时长（秒）。"""
    import slides_render as S
    try:
        S._tts(text, out, voice)
    except Exception as exc:  # noqa: BLE001 - 转成风格层自己的错误
        raise PresenterError(f"配音失败（{voice}）: {exc}") from exc
    return S._dur(out)


# ------------------------------------------------------------------ 组装 spec
def build_spec(content: dict, outdir: Path, *, canvas=None, maid_dir=None,
               host=None, voice=None, fps=30, work=None, tts=None) -> dict:
    """内容 → timeline spec。

    `tts` 可注入替身 `(text, out_path, voice) -> 秒数`，测试时不必联网配音。
    """
    title = str(content.get("title") or "").strip()
    pages = content.get("pages") or []
    if not title or not pages:
        raise PresenterError("content 至少要有 title 和 1 个 page")
    for i, p in enumerate(pages):
        for key in ("title", "narration"):
            if not str(p.get(key) or "").strip():
                raise PresenterError(f"page[{i}] 缺 {key}")

    c = {"width": 1920, "height": 1080, "fps": fps}
    c.update(canvas or {})
    W, H, FPS = c["width"], c["height"], c["fps"]
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="presenter-"))
    work.mkdir(parents=True, exist_ok=True)
    assets = outdir / "assets"
    assets.mkdir(exist_ok=True)
    profile = _resolve_host(content, host=host, maid_dir=maid_dir)
    voice = voice or content.get("voice") or profile.voice
    series = str(content.get("series") or "ASTREA STUDIO")

    # 1) 背景 + 每页卡片 + 每页配音
    backdrop_png = make_backdrop(work / "backdrop.png", W, H)
    scenes, t = [], 0.0
    for i, p in enumerate(pages):
        png = make_page(assets / f"page{i:02d}.png", p, i, len(pages), series)
        mp3 = assets / f"voice{i:02d}.mp3"
        dur = (tts or narrate)(str(p["narration"]), mp3, voice)
        scenes.append({"png": png, "mp3": mp3, "start": t, "end": t + dur,
                       "maid": p.get("maid")})
        t += dur + GAP
    total = round(t - GAP, 3)

    # 2) 背景视频（静帧 loop 成整段，供 timeline_render 当底）
    backdrop = make_still_video(backdrop_png, assets / "backdrop.mp4", total, FPS)

    # 3) overlays：页面卡片翻页 + 右下角人物
    overlays = []
    for i, sc in enumerate(scenes):
        overlays.append({
            "src": str(sc["png"]), "start": round(sc["start"], 3),
            "end": round(sc["end"], 3),
            "x": PAGE["x"], "y": PAGE["y"], "width": PAGE["w"],
            "enter": {"from": "right", "duration": SLIDE},
            "exit": {"to": "left", "duration": SLIDE},
        })
        overlays.extend(_host_overlays(profile, sc, i, work, FPS))

    # 4) 字幕条（PNG overlay，像素级控制，交给 renderer 的文字层容易顶边）+ 配音
    texts, audios = [], []
    for i, sc in enumerate(scenes):
        cap = make_caption(assets / f"cap{i:02d}.png", str(pages[i]["narration"]))
        overlays.append({
            "src": str(cap), "start": round(sc["start"], 3),
            "end": round(sc["end"], 3),
            "x": PAGE["x"], "y": CAPTION_Y, "fade": 0.18,
        })
        audios.append({"src": str(sc["mp3"]), "start": round(sc["start"], 3),
                       "volume": 1.0, "role": "narration"})
    bgm = content.get("bgm")
    if bgm is True or (bgm is None and content.get("music", True)):
        audios.append({"src": str(make_bgm(assets / "bgm.m4a", work)),
                       "start": 0, "volume": 0.13, "loop": True,
                       "fade": 1.2, "role": "bgm"})
    elif bgm:
        p = Path(os.path.expanduser(str(bgm)))
        if not p.is_file():
            raise PresenterError(f"bgm 不存在: {p}")
        audios.append({"src": str(p), "start": 0, "volume": 0.16,
                       "loop": True, "fade": 1.2, "role": "bgm"})

    return {
        "canvas": c,
        "video": [{"src": str(backdrop), "in": 0, "out": total, "fit": "cover"}],
        "overlay": overlays,
        "text": texts,
        "audio": audios,
        "font": T.find_font(),
        "meta": {"title": title, "series": series, "voice": voice,
                 "duration": total, "style": "presenter",
                 "host": {"id": profile.id, "label": profile.label,
                          "kind": profile.kind, "alpha": profile.alpha}},
    }


PUNCT = "，。、；：！？）」』"


def _caption_lines(text: str, limit: int = 26) -> list:
    """按字数硬折，但把行首标点并回上一行，别让逗号顶格。"""
    text = " ".join(str(text).split())
    if not text:
        return [""]
    lines, cur = [], ""
    for ch in text:
        cur += ch
        if len(cur) >= limit and ch not in PUNCT:
            lines.append(cur)
            cur = ""
    if cur:
        if lines and (len(cur) <= 2 or cur[0] in PUNCT):
            lines[-1] += cur
        else:
            lines.append(cur)
    return lines


def _resolve_host(content: dict, *, host=None, maid_dir=None) -> H.HostProfile:
    """主角优先级：显式 host > 显式 maid_dir > content.host > 内置默认。"""
    if host is not None:
        try:
            return H.load_host(host)
        except H.HostError as exc:
            raise PresenterError(str(exc)) from exc
    if maid_dir is not None:
        folder = Path(os.path.expanduser(str(maid_dir)))
        clips = []
        if folder.is_dir():
            order = ["优雅女仆舞.webm", "东张西望.webm", "女仆屈膝礼仪.webm",
                     "凭空生花.webm", "三球抛接.webm"]
            clips = [folder / n for n in order if (folder / n).is_file()]
        return H.HostProfile(id="custom", label="自定义人物", kind="avatar",
                             voice=DEFAULT_VOICE, dir=folder, clips=clips)
    try:
        return H.load_host(content.get("host") or None)
    except H.HostError:
        # 没装 dsh-pet 也要能出片：主角位置留空，卡片/字幕/配音照常
        return H.HostProfile(id="none", label="无主角", kind="avatar",
                             voice=DEFAULT_VOICE, clips=[])


def _host_overlays(profile: H.HostProfile, scene: dict, idx: int, work: Path,
                   fps: float) -> list:
    """一页的主角叠层：alpha 素材直接叠；真人/数字人先垫圆角相框。"""
    clip = scene.get("maid")
    if clip:
        base = profile.dir or Path(".")
        path = Path(str(clip))
        path = path if path.is_absolute() else (base / path)
        if not path.is_file():
            raise PresenterError(f"人物素材不存在: {path}")
        src = path
    else:
        try:
            src = H.ensure_clip(profile, work, idx, scene["end"] - scene["start"], fps)
        except H.HostError as exc:
            raise PresenterError(str(exc)) from exc
    if src is None:
        return []

    place = dict(profile.placement)
    out = []
    if not profile.alpha:
        try:
            vw, vh = H.probe_size(src)
        except H.HostError:
            vw, vh = 16, 9
        box_w = int(place["width"])
        box_h = max(2, int(round(box_w * vh / max(1, vw))))
        box_h -= box_h % 2
        place = {"x": int(place["x"]), "y": max(0, 1080 - box_h - 90),
                 "width": box_w}
        if profile.outline:
            frame = H.make_frame(work / f"hostframe{idx:02d}.png", box_w, box_h)
            out.append({
                "src": str(frame), "start": round(scene["start"], 3),
                "end": round(scene["end"], 3),
                "x": place["x"] - H.FRAME_PAD, "y": place["y"] - H.FRAME_PAD,
                "width": box_w + H.FRAME_PAD * 2, "fade": 0.3,
            })
    out.append({
        "src": str(src), "start": round(scene["start"], 3),
        "end": round(scene["end"], 3),
        "x": place["x"], "y": place["y"], "width": place["width"],
        "fade": 0.3, "motion": dict(profile.motion),
    })
    return out


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="虚拟主播技术讲解风格：内容 → timeline spec")
    ap.add_argument("content", nargs="?", help="content.json 路径")
    ap.add_argument("--outdir", help="素材与产物目录")
    ap.add_argument("--spec-out", help="spec 写到哪（默认 outdir/spec.json）")
    ap.add_argument("--render", help="顺手渲母版到这个路径")
    ap.add_argument("--host", help="主角 id 或角色目录（默认 dsh-pet 女仆；见 --list-hosts）")
    ap.add_argument("--maid-dir", help="兼容旧参数：人物 webm 目录")
    ap.add_argument("--list-hosts", action="store_true", help="列出可用主角")
    ap.add_argument("--voice", help=f"配音音色（默认 {DEFAULT_VOICE}）")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    if args.list_hosts:
        for h in H.list_hosts():
            print(f"{h.id:16s} {h.kind:13s} {h.label}  voice={h.voice}")
        return 0

    if not args.content or not args.outdir:
        ap.error("除 --list-hosts 外都要给 content 和 --outdir")
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    work = Path(tempfile.mkdtemp(prefix="presenter-"))
    try:
        spec = build_spec(content, outdir, host=args.host or content.get("host"),
                          maid_dir=args.maid_dir, voice=args.voice, work=work)
        sp = Path(args.spec_out) if args.spec_out else outdir / "spec.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"spec: {sp}  duration={spec['meta']['duration']}s "
              f"overlays={len(spec['overlay'])}")
        if args.render:
            loaded = T.load_spec(sp)
            ev = T.render(loaded, args.render, keep_workdir=args.keep_work)
            print(f"master: {ev['output']} {ev['bytes']}B "
                  f"{ev['duration']}s {ev['seconds']}s渲染")
        return 0
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
