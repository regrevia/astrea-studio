#!/usr/bin/env python3
"""promo_envoy.py — Envoy 宣传片的卡面与时间线，一次生成。

为什么单起一份：`vertical_short` 的 factcard 版式是为「要点列表」排的，标题和
说明两段字各自定位，长句会把正文塞进标题行里（本次实测卡片叠字）；改它等于
改全部竖屏片的版式。宣传片要的是「一屏一句话 + 一个具体事实」，所以这里自己
排卡面，人物层仍然走平台那条 char_layer（口型/眨眼/手势都是现成的），成片
仍然由 timeline_render 出。

用法：
    python3 deploy/presenter/promo_envoy.py --cards-only     # 只排卡面，看构图
    python3 deploy/presenter/promo_envoy.py                  # 卡面 + 人物层 + 时间线
"""

from __future__ import annotations

import argparse
import json
import re
import math
import os
import subprocess
import sys
from pathlib import Path

WORKER = Path.home() / "CreatorStudio/runtime/integrations/mac-worker"
sys.path.insert(0, str(WORKER))
sys.path.insert(0, str(WORKER / "export"))

import char_layer as CL          # noqa: E402
import character as CH           # noqa: E402
import vertical_short as VS      # noqa: E402

W, H = 1080, 1920
FPS = 30
MARGIN = 84
TEXT_W = W - MARGIN * 2          # 912

BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
REG = "/System/Library/Fonts/Hiragino Sans GB.ttc"

INK = (246, 249, 255)
DIM = (158, 174, 204)
FAINT = (104, 118, 150)
CYAN = (126, 224, 255)
AMBER = (255, 206, 122)

# 人物机位与 factcard 那条链路一致：整片钉同一处，变化交给呼吸和手势
CHAR = dict(width=786, x=470, y=962)

LOGO = Path.home() / "CreatorStudio/materials/brand/envoy-bird.png"
AUD = Path("/tmp/envoy-promo-work")


def font(size: int, bold: bool = False):
    from PIL import ImageFont
    return ImageFont.truetype(BOLD if bold else REG, size, index=1 if bold else 0)


# ---------------------------------------------------------------- 排字
def _measure(draw, text: str, f, tracking: float = 0.0) -> float:
    if not tracking:
        return draw.textlength(text, font=f)
    return sum(draw.textlength(c, font=f) for c in text) + tracking * max(0, len(text) - 1)


def _tracked(draw, xy, text, f, fill, tracking=0.0):
    x, y = xy
    if not tracking:
        draw.text((x, y), text, font=f, fill=fill)
        return
    for c in text:
        draw.text((x, y), c, font=f, fill=fill)
        x += draw.textlength(c, font=f) + tracking


def _wrap(draw, text: str, f, max_w: float, tracking: float = 0.0) -> list[str]:
    """按字断行；中文没有词边界，把「、，。」这类标点从行首挤回上一行。"""
    lines, cur = [], ""
    for ch in text:
        probe = cur + ch
        if _measure(draw, probe, f, tracking) <= max_w or not cur:
            cur = probe
            continue
        # 标点不许顶行首：宁可让本行超一点点
        if ch in "，。、；：」』）!?！？·":
            cur = probe
            continue
        lines.append(cur)
        cur = ch
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, max_w, sizes, max_lines, tracking=0.0):
    for size in sizes:
        f = font(size, True)
        lines = _wrap(draw, text, f, max_w, tracking)
        if len(lines) <= max_lines:
            return size, f, lines
    f = font(sizes[-1], True)
    return sizes[-1], f, _wrap(draw, text, f, max_w, tracking)


# ---------------------------------------------------------------- 底色
def _background(accent, seed: int = 0):
    import numpy as np
    from PIL import Image, ImageFilter

    top = np.array([7, 11, 26], dtype=np.float64)
    bot = np.array([16, 22, 53], dtype=np.float64)
    t = np.linspace(0.0, 1.0, H)[:, None, None]
    arr = top * (1 - t) + bot * t
    arr = np.repeat(arr, W, axis=1)

    # 人物背后的水平柔光：只一盏，位置固定，不做动画（换屏时灯不能动）
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    d = np.sqrt(((xx - 830) / 520.0) ** 2 + ((yy - 1330) / 620.0) ** 2)
    glow = np.clip(1.0 - d, 0.0, 1.0) ** 2.2 * 46.0
    arr += glow[..., None] * (np.array(accent, dtype=np.float64) / 255.0)

    # 暗角：四角压下去，字才立得起来
    r = np.sqrt(((xx - W / 2) / (W * 0.78)) ** 2 + ((yy - H / 2) / (H * 0.62)) ** 2)
    vig = 1.0 - np.clip((r - 0.55) / 0.85, 0.0, 1.0) * 0.34
    arr *= vig[..., None]

    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    from PIL import ImageDraw
    dd = ImageDraw.Draw(dots)
    for y in range(250, 1760, 64):
        for x in range(MARGIN, W - MARGIN + 1, 64):
            dd.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(255, 255, 255, 17))
    img.alpha_composite(dots.filter(ImageFilter.GaussianBlur(0.4)))
    return img


def _brand_chip(path: Path, size: int = 112):
    from PIL import Image, ImageDraw, ImageFilter
    mark = Image.open(path).convert("RGB")
    box = mark.convert("L").point(lambda v: 255 if v < 236 else 0).getbbox()
    if box:
        mark = mark.crop(box)
    mark.thumbnail((size - 46, size - 46), Image.LANCZOS)
    cell = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(cell)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=30, fill=(255, 255, 255, 252))
    cell.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return cell


# ---------------------------------------------------------------- 卡面
def card(scene: dict, idx: int, total: int, path: Path):
    from PIL import Image, ImageDraw, ImageFilter

    accent = scene.get("accent") or CYAN
    img = _background(accent)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 顶栏：品牌 + 页码
    _tracked(d, (MARGIN, 158), "REGREVIA / ENVOY", font(30), (163, 179, 209, 226), tracking=3.2)
    num = scene.get("num") or ""
    if num:
        f = font(30, True)
        d.text((W - MARGIN - d.textlength(num, font=f), 158), num, font=f,
               fill=(163, 179, 209, 226))

    # 顶栏下的细线 + 进度：就是"这条片子走到哪"的读数，不是翻页点
    y = 232
    d.rounded_rectangle((MARGIN, y, W - MARGIN, y + 3), radius=2, fill=(255, 255, 255, 40))
    if idx is not None:
        p = (idx + 1) / max(1, total)
        d.rounded_rectangle((MARGIN, y, MARGIN + (W - MARGIN * 2) * p, y + 3), radius=2,
                            fill=(*accent, 235))

    # 眉标：这一屏讲的是哪一层（格式 / 可核对 / 诚实 / 安装）
    kick = scene.get("kicker")
    if kick:
        _tracked(d, (MARGIN, scene.get("head_y", 402) - 76), kick,
                 font(26, False), (*accent, 200), tracking=7.0)

    # 主标题
    head = scene["head"]
    if "\n" in head:
        # 显式换行：每段各自收进画幅，不交给自动断行（宣传片的断句是设计的一部分）
        nl = [x for x in head.split("\n") if x]
        size, f = scene.get("sizes", [104, 98, 92])[-1], None
        for cand in scene.get("sizes", [104, 98, 92, 86, 80, 74, 68]):
            cf = font(cand, True)
            if all(_measure(d, x, cf) <= scene.get("head_w", TEXT_W) for x in nl):
                size, f = cand, cf
                break
        if f is None:
            f = font(size, True)
        lines = nl
    else:
        hs = scene.get("head_size")
        size, f, lines = _fit(d, head, scene.get("head_w", TEXT_W),
                             [hs] if hs else scene.get("sizes", [104, 98, 92, 86, 80, 74, 68]),
                             scene.get("max_lines", 1 if hs else 2))
    lh = int(size * 1.24)
    hy = scene.get("head_y", 402)
    for n, line in enumerate(lines):
        d.text((MARGIN, hy + n * lh), line, font=f, fill=(*INK, 252))
    bottom = hy + len(lines) * lh

    # 一句具体事实：药丸 + 左侧小亮点。不描边不重投影，只靠底色分层
    pill = scene.get("pill")
    if pill:
        pf = font(scene.get("pill_size", 42), False)
        pw = _measure(d, pill, pf) + 86
        ph = scene.get("pill_size", 42) + 52
        py = bottom + scene.get("pill_gap", 62)
        px = MARGIN
        box = (px, py, px + pw, py + ph)
        d.rounded_rectangle(box, radius=ph // 2, fill=(*accent, 30))
        d.rounded_rectangle(box, radius=ph // 2, outline=(*accent, 96), width=2)
        d.ellipse((px + 34, py + ph // 2 - 6, px + 46, py + ph // 2 + 6), fill=(*accent, 240))
        d.text((px + 62, py + (ph - scene.get("pill_size", 42)) / 2 - 6), pill, font=pf,
               fill=(*accent, 246))
        bottom = py + ph

    # 附注
    sub = scene.get("sub")
    if sub:
        sf = font(scene.get("sub_size", 34), False)
        sl = _wrap(d, sub, sf, scene.get("sub_w", TEXT_W))[:2]
        sy = bottom + 46
        for n, line in enumerate(sl):
            d.text((MARGIN, sy + n * int(scene.get("sub_size", 34) * 1.5)), line, font=sf,
                   fill=(*DIM, 235))
        bottom = sy + len(sl) * int(scene.get("sub_size", 34) * 1.5)

    # 实测读数：宣传片最容易空的地方就是「只有一句口号」，这三屏各挂两条具体数字
    facts = scene.get("facts") or []
    if facts:
        fs_ = scene.get("fact_size", 33)
        ff = font(fs_, False)
        step = int(fs_ * 1.46)
        rows: list[str] = []
        for line in facts:
            rows.extend(_wrap(d, line, ff, scene.get("fact_w", TEXT_W - 8)))
        rows = rows[:4]
        fy0 = bottom + 44
        d.rounded_rectangle((MARGIN, fy0 + 6, MARGIN + 3, fy0 + len(rows) * step - 12),
                            radius=2, fill=(*accent, 120))
        for n, row in enumerate(rows):
            d.text((MARGIN + 26, fy0 + n * step), row, font=ff, fill=(*DIM, 226))
        bottom = fy0 + len(rows) * step

    # 品牌标：开场和收尾才露脸，中间挂满就成了水印
    if scene.get("logo"):
        chip = _brand_chip(LOGO)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (W - MARGIN - 112, 96, W - MARGIN, 96 + 112), radius=30, fill=(0, 0, 0, 130))
        layer.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
        layer.alpha_composite(chip, (W - MARGIN - 112, 96))

    # 数据出处：屏幕上出现的每个数字都要能追到评测原文
    note = scene.get("note")
    if note:
        d.text((MARGIN, 1720), note, font=font(23, False), fill=(112, 128, 160, 190))

    # 底栏：署名 + 出品，整片同一位置
    fy = font(30, False)
    _tracked(d, (MARGIN, 1782), "大肥鱼 · 解说", fy, (112, 128, 160, 210), tracking=1.4)
    right = "Astrea Studio"
    d.text((W - MARGIN - d.textlength(right, font=fy), 1782), right, font=fy,
           fill=(112, 128, 160, 210))

    img.alpha_composite(layer)
    img.convert("RGB").save(path, quality=95)
    return path


def _dur(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- 分镜
SCENES = [
    dict(kind="cover", audio="a00.mp3", poses=["wave", "wave", "point"],
         head="给 AI 一个链接，\n它只读到标题",
         sub="剩下的，全靠它自己脑补。", logo=True, num="",
         accent=CYAN, sizes=[92, 86, 80], head_y=396, sub_size=40, sub_w=820,
         say="给 AI 一个链接，它只读到标题，剩下的全靠脑补。"),

    dict(kind="point", audio="a01.mp3", poses=["point", "point", "approve", "point"],
         head="一个链接，什么都能读", kicker="格式",
         pill="网页 · 公众号 · PDF · 小红书 · B 站 · 视频",
         facts=["自测 123 条链接：80.5% 能拿到可用材料",
                "能连上的链接里 92.5% 读得到正文"],
         note="数据：123 条真实链接兼容性实测",
         num="01 / 04", accent=CYAN, head_size=88, head_y=470, pill_size=36,
         say="网页、公众号、PDF、小红书图文、B 站视频、本地视频，一个链接就够。"),

    dict(kind="point", audio="a02.mp3", poses=["think", "point"], pose_every=1,
         exprs={1: "sparkle"},
         head="每条结论，都带时间戳", kicker="可核对",
         pill="[04:18] 点开就核对", num="02 / 04", accent=AMBER,
         head_size=88, head_y=470,
         facts=["参考事实覆盖率 9.1% → 38.2%", "第一次拿到证据 88 秒 → 48 秒"],
         note="数据：60 轮 A/B 自测评测，每臂 60 轮",
         say="切片段的时候顺手标好时间点；结论后面挂一个方括号，点开就能核对。"),

    dict(kind="point", audio="a03.mp3", poses=["shrug", "shrug", "point", "approve"],
         head="没读到，就说没读到", kicker="诚实",
         pill="缺口单列「待补」，不猜", num="03 / 04", accent=CYAN,
         head_size=88, head_y=470,
         facts=["空回答 31 处 → 0 处", "跑到超时没结论 68% → 0%"],
         note="数据：60 轮 A/B 自测评测，每臂 60 轮",
         say="要登录、被人机校验拦住，它会直说，让 Agent 换条路，不编内容。"),

    dict(kind="point", audio="a04.mp3", poses=["point", "point", "approve"],
         head="三个宿主，一行命令", kicker="安装",
         pill="Claude Code · Codex · DSH", num="04 / 04", accent=CYAN,
         head_size=88, head_y=470,
         facts=["默认完整档：runtime 178 MB · 浏览器 683 MB",
                "转写权重 140 MB · 装完 envoy doctor --json"],
         say="Claude Code、Codex、DeepSeek Harness，三个宿主各一行命令，装完自检两条。"),

    dict(kind="cta", audio="a05.mp3", poses=["wave", "wave", "happy", "happy"],
         exprs={2: "sparkle"},
         head="装上 Envoy，\n链接就是材料",
         pill="github.com/regrevia/Envoy", logo=True, num="",
         sub="本期视频全程由大肥鱼制作完成，没有一个 token 受伤。",
         accent=CYAN, sizes=[96, 90, 84], head_y=396, pill_size=38, sub_size=31,
         say="装上 Envoy，链接就是材料。本期视频全程由大肥鱼制作完成，没有一个 token 受伤。"),
]


def _beats(mp3: Path, min_gap: float = 0.10) -> list[tuple[float, float]]:
    """按配音里的换气口把一句话切成一拍一拍。

    整屏只给一个人物分段时，动作五秒不动，观感就是贴纸；切成句子级的小段，
    每一拍可以换手势/表情，人物才是「在讲」而不是「被摆着」。
    """
    dur = _dur(mp3)
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(mp3), "-af",
         "silencedetect=noise=-32dB:d=0.14", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    marks = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", out)]
    cuts = [0.0]
    for m in marks:
        if m - cuts[-1] > min_gap:
            cuts.append(m)
    cuts.append(dur)
    beats = [(round(a, 3), round(b, 3)) for a, b in zip(cuts, cuts[1:]) if b - a > 0.05]
    return beats or [(0.0, round(dur, 3))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="/tmp/envoy-promo-work2")
    ap.add_argument("--spec", default="/tmp/envoy-promo-timeline2.json")
    ap.add_argument("--cards-only", action="store_true")
    args = ap.parse_args(argv)

    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)

    # 卡面
    cards = []
    for i, sc in enumerate(SCENES):
        audio = AUD / sc["audio"]
        if not audio.exists():
            raise SystemExit(f"缺配音 {audio}（先跑 vertical_short 的 TTS 那一遍）")
        dur = _dur(audio)
        png = card(sc, None if sc["kind"] in ("cover", "cta") else i - 1,
                   4, work / f"card{i:02d}.png")
        cards.append(dict(scene=sc, png=png, audio=audio, dur=dur))
        flat = " ".join(sc["head"].split())
        print("card%02d %s %.2fs" % (i, flat, dur), flush=True)

    # 时间线：一屏 = 一段配音 + 收尾留白，屏与屏之间 0.4s 交叠
    XF = 0.42
    GAP = 0.34
    t = 0.0
    video, audio_track, char_segs = [], [], []
    for i, c in enumerate(cards):
        seg = c["dur"] + GAP
        start = max(0.0, t - (XF if i else 0.0))
        out = round(start + seg, 3) if i else round(seg, 3)
        start = round(start, 3)
        video.append({"src": str(c["png"]), "out": round(out - start, 3),
                      "zoom": [1.0, 1.05], "pulse": False,
                      "fade_in": 0.0, "fade_out": 0.0,
                      "xfade_in": XF if i else 0.0})
        audio_track.append({"src": str(c["audio"]), "start": round(start + 0.12, 3), "volume": 1.0})
        sc = c["scene"]
        end = out
        beats = _beats(c["audio"])
        a0 = round(start + 0.12, 3)
        poses = sc["poses"]
        every = int(sc.get("pose_every", 2))
        exprs = sc.get("exprs") or {}
        for bi, (bs, be) in enumerate(beats):
            pose = poses[min(len(poses) - 1, bi // every)]
            expr = exprs.get(bi)
            if expr is None and hasattr(VS, "_pose_expression"):
                expr = VS._pose_expression(pose)
            b_start = round(start + bs + 0.12 if bi else start, 3)
            b_end = round(end if bi == len(beats) - 1 else start + be + 0.12, 3)
            char_segs.append({
                "start": b_start, "end": b_end,
                "width": CHAR["width"], "x": CHAR["x"], "y": CHAR["y"],
                "pose": pose, "mouth": "closed", "eyes": "open",
                "expression": expr, "outfit": None,
                "anim": VS._char_anim_name(pose, True) if hasattr(VS, "_char_anim_name") else None,
                "anim_mode": "once" if pose == "wave" else "loop",
                "anim_t0": round(b_start - (0.30 if pose == "wave" else 0.0), 3),
                "fade_in": 0.0, "fade_out": 0.0, "speaking": True,
                # 口型吃整屏那条配音 + 整屏起点：逐拍切段只换动作，声音对齐不跟着抖
                "lip_audio": str(c["audio"]), "lip_text": sc["say"], "lip_t0": a0,
                "enter": {"from": "bottom", "duration": 0.55, "ease": "out-cubic"} if (i == 0 and bi == 0) else None,
                "_outfit_root": str(CH.outfit_root(None)),
            })
        t = out

    duration = round(t, 3)
    print(f"timeline {duration}s / {len(video)} scenes", flush=True)

    char_mov = None
    if not args.cards_only:
        char_mov = CL.build(
            char_segs, width=W, height=H, fps=FPS, workdir=work, duration=duration,
            render=CH.render, key_green=CH._key_green, seed=envoy_seed(),
            log=lambda m: print(f"[char] {m}", flush=True))
        if char_mov is None:
            raise SystemExit("人物层没出来")
        char_mov, cx, cy = char_mov
        print(f"char layer {char_mov}", flush=True)
    else:
        cx = cy = 0

    overlay = []
    if char_mov is not None:
        overlay.append({"src": str(char_mov), "start": 0.0, "end": duration,
                        "x": float(cx), "y": float(cy), "width": 0,
                        "opacity": 1.0, "fade": 0.0,
                        "motion": {"type": "none", "amplitude": 0.0, "period": 1.0}})

    spec = {"canvas": {"width": W, "height": H, "fps": FPS},
            "video": video, "overlay": overlay, "audio": audio_track,
            "assetBase": str(work),
            "meta": {"engine": "promo_envoy", "duration": duration}}
    Path(args.spec).write_text(json.dumps(spec, ensure_ascii=False, indent=1))
    print(f"spec {args.spec}", flush=True)


def envoy_seed() -> int:
    """整片一个种子：卡面配色/人物摆位要能复现，不受 job run_id 影响。"""
    return int(os.environ.get("ENVOY_PROMO_SEED", "20261104"))


if __name__ == "__main__":
    main()
