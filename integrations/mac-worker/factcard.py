"""factcard.py — 「干货卡」竖屏版式：出图 + spec 组装（worker 与 CLI 共用一份实现）。

形态对标抖音「大模型面试题」类干货号：9:16、一问三答、逐屏大字卡、逐句字幕、
剪映音色配音；画面动效交给剪映原生资源（转场/画面特效/VIP 字体/文字动画）。

平台侧只要提交
    {"engine": "jianying", "template": "factcard",
     "topic": "…", "hook": "…", "points": ["标题::说明", …], "cta": "…", "voice": "…"}
worker 就会在这里出卡面并展开成完整 scenes，无需本地跑工具。
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

W, H = 1080, 1920
TOP_SAFE, BOTTOM_SAFE = 240, 430  # 顶部账号栏 / 底部字幕与按钮区

BODY_SIZE = 78        # 卡面正文字号（1080×1920 下的像素值）
HOOK_SIZE = 60        # 开场钩子字号：压在标题下面，不能和标题同大
FONT_PATH = os.environ.get("FACTCARD_FONT", "/System/Library/Fonts/STHeiti Medium.ttc")
DEFAULT_VOICE = "sami:BV405_streaming"
DEFAULT_KICKER = "大模型面试题"
DEFAULT_HOOK = "面试官问这句，八成在探你懂不懂推理优化"
DEFAULT_CTA = "记住这三条，面试能答到点上。下一题想看什么，评论区说"
TITLE_FONT = "SourceHanSansCN_Bold"       # 剪映会员字体，白名单内、可读性好
CAPTION_FONT = "SourceHanSansCN_Medium"

# 白名单里确认过的资源（见 jianying_engine._catalog），未知名字会被引擎丢掉
# 只用缩放/渐显：满屏图一滑动就露底边，卡面文字也跟着甩
IMAGE_ANIMS = ["轻微放大", "轻微缩小", "渐显"]
# 不要闪白：卡面是深底，白闪一下像故障。只用叠化/推拉/模糊这类低对比转场
TRANSITIONS = ["叠化", "推近", "拉远", "竖向模糊", "叠化"]
TEXT_ANIMS = ["居中打字", "打字机_I", "向右滑动", "渐显", "弹入"]
# 出场动画（/get_text_outro_types 97 项、/get_outro_animation_types 72 项里挑的稳重款）
TEXT_OUTS = ["向上溶解", "渐隐", "向下滑动", "向右擦除", "缩小消失"]
# 必须是 /get_video_scene_effect_types 里真实存在的名字（912 项）
SCENE_EFFECTS = ["哈苏胶片", "画质清晰", "法式暖调", "电影感", "秋日暖阳"]

PALETTES = [
    ((14, 18, 38), (36, 30, 78), (122, 143, 255)),    # 深蓝紫
    ((10, 26, 30), (22, 52, 58), (86, 226, 189)),     # 深青
    ((32, 14, 26), (60, 26, 52), (255, 138, 176)),    # 深玫
    ((30, 22, 10), (56, 40, 18), (255, 196, 87)),     # 深琥珀
    ((16, 16, 20), (34, 34, 42), (160, 170, 200)),    # 中性收尾
]

# 画布：抖音竖屏
W, H = 1080, 1920
TOP_SAFE, BOTTOM_SAFE = 240, 430  # 顶部账号栏 / 底部字幕与按钮区

FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"


# ---------------------------------------------------------------- 出图
def _font(size: int):
    from PIL import ImageFont

    return ImageFont.truetype(FONT_PATH, size)


def _vgrad(size, top, bottom):
    from PIL import Image

    w, h = size
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((w, h))


def _seed(slug: str) -> int:
    return abs(hash(slug)) % 9973



_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9%\.\-\+/]*|.", re.S)


_ORPHAN_PUNCT = set("，。、；：？！…）」』】》,.;:?!")


def _wrap_text(draw, text: str, font, max_w: float) -> list[str]:
    """中文逐字断行，西文按整词走（PDF / GPT-4 不被劈开）。"""
    out: list[str] = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        buf = ""
        for tok in _TOKEN_RE.findall(para):
            probe = buf + tok
            if draw.textlength(probe, font=font) > max_w and buf:
                # 优先在标点后断行："来自"被劈开成 "…句话来 / 自哪一秒" 最刺眼
                cut = -1
                for j in range(len(buf) - 1, 0, -1):
                    if buf[j] in _ORPHAN_PUNCT:
                        cut = j + 1
                        break
                # 断点够长就断在标点后：硬切会造出「长上 / 下文」这种半个词
                far_enough = cut > 0 and draw.textlength(buf[:cut], font=font) >= max_w * 0.45
                if far_enough:
                    out.append(buf[:cut])
                    buf = buf[cut:] + tok
                else:
                    out.append(buf)
                    buf = tok
            else:
                buf = probe
        if buf:
            out.append(buf)
    # 行首不该出现标点：把「，」。这种孤儿号并回上一行，否则读起来像断句错误
    for i in range(1, len(out)):
        while out[i] and out[i][0] in _ORPHAN_PUNCT:
            out[i - 1] += out[i][0]
            out[i] = out[i][1:]
    # 末行只剩一个字（"…真的读 / 过"）比换行错位更难看：从上一行借一个字回来
    if len(out) >= 2 and len(out[-1]) == 1 and len(out[-2]) > 2:
        out[-1] = out[-2][-1] + out[-1]
        out[-2] = out[-2][:-1]
    # 在标点后断行时行尾会留一个空格："Value " 挂在行末看着像少打了个字
    return [ln.rstrip() for ln in out]


def _draw_block(page, text: str, size: int, top: int, left: int, max_w: float,
                fill=(255, 255, 255, 240), spacing: float = 1.45,
                align: str = "left") -> int:
    """在出图阶段排版正文：剪映字号是相对量，精排只能自己算像素。返回结束 y。"""
    from PIL import ImageDraw

    if not text:
        return top
    draw = ImageDraw.Draw(page)
    font = _font(size)
    y = top
    for ln in _wrap_text(draw, text, font, max_w):
        if ln:
            x = left
            if align == "center":   # 标题居中，正文左对齐会读成两套排版
                x = left + (max_w - draw.textlength(ln, font=font)) / 2
            draw.text((x, y), ln, font=font, fill=fill)
        y += int(size * spacing)
    return y

# 两行排不下就往下试，直到 60：长句宁可小一号，也不要撑破卡片边框
_BODY_CANDIDATES = (96, 88, BODY_SIZE, 72, 66, 60)


def _body_size(text: str, measure=None, max_w: float = W - 264) -> tuple[int, int]:
    """PPT 式短句配 78px 会显得卡片空：在能排成两行以内的前提下取最大字号。

    只看字数会挑出把「按镜头切关键帧」拦腰断成 5+2 的字号——断句比字号更显眼。
    返回 (字号, 行数)。
    """
    if not text:
        return BODY_SIZE, 0
    def _clean(rows: list[str]) -> bool:
        # 末行只剩一两个字是「孤字」：比小一号更难看，宁可整句小一点排成一行
        if len(rows) == 1:
            return True
        if len(rows) > 2:
            return False
        last, first = len(rows[-1]), len(rows[0])
        return last >= 3 and last >= first * 0.45

    for size in _BODY_CANDIDATES:
        if measure is not None:
            lines = _wrap_text(measure, text, _font(size), max_w)
        else:
            lines = [text]
        if _clean(lines):
            return size, len(lines)
    # 最小字号还是超两行：返回真实行数，卡片高度要按它算，否则正文穿出卡底
    lines = _wrap_text(measure, text, _font(_BODY_CANDIDATES[-1]), max_w) if measure is not None else [text]
    return _BODY_CANDIDATES[-1], len(lines)


def render_card(path: Path, *, kicker: str, index: str, palette_idx: int,
                progress: int, total: int, slug: str, hook: bool = False,
                body: str = "", body_top: int = 640, plain: bool = False,
                logo: Path | None = None, draw_body: bool = True) -> Path:
    """卡面底图：渐变 + 网格点 + 圆角描边卡 + 编号幽灵字 + 进度点。文字交给剪映。"""
    from PIL import Image, ImageDraw, ImageFilter

    top, bottom, accent = PALETTES[palette_idx % len(PALETTES)]
    img = _vgrad((W, H), top, bottom).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    # 光斑：制造纵深，避免"纯色 PPT 味"
    rnd = _seed(slug)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow, "RGBA")
    for i in range(3):
        cx = int((0.2 + 0.6 * ((rnd + i * 37) % 100) / 100) * W)
        cy = int((0.15 + 0.5 * ((rnd + i * 61) % 100) / 100) * H)
        r = int(220 + 160 * ((rnd + i * 17) % 100) / 100)
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*accent, 30))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(120)))
    # 半透明元素必须画在独立图层再合成：直接在 RGBA 上 draw 会把 alpha 覆盖掉，
    # convert("RGB") 后就变成一坨纯白（卡片曾经整个糊成白板）。
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    # 网格点
    for y in range(TOP_SAFE, H - BOTTOM_SAFE, 60):
        for x in range(80, W - 80, 60):
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(255, 255, 255, 18))

    if plain:
        # 收尾卡不要空卡框：没有正文的圆角矩形只会显得空
        draw.polygon([(0, 1180), (W, 1010), (W, 1340), (0, 1510)], fill=(*accent, 30))
    measure = ImageDraw.Draw(overlay)
    body_size, body_nlines = _body_size(body, measure) if body else (BODY_SIZE, 0)
    if not hook and not plain:
        # 卡片高度跟着正文走，避免大片空白（"PPT 味"最主要的来源）
        body_lines = _wrap_text(measure, body, _font(body_size), W - 264) if body else []
        body_nlines = max(body_nlines, len(body_lines))
        # 短句放大后仍贴着卡顶会显得上重下轻，往下挪一点让正文落在版心
        if body_size > BODY_SIZE:
            body_top += 56 if body_size >= 110 else 40
        # 卡片高度跟着正文走；下限 1000 让正文区占满上三分之二
        # 一行正文还给 1000 的卡底，下半张卡就是空的：短句把卡收到 900 上下
        floor = 1000 if len(body_lines) > 1 else 900
        # 上限 1010：立绘头顶从 1014 起，卡片再长就把角色闷在卡里
        card_bottom = min(1010, max(floor,
                                   body_top + int(body_nlines * body_size * 1.42) + 96))
        draw.rounded_rectangle((76, 470, 1004, card_bottom), radius=56,
                               fill=(255, 255, 255, 14),
                               outline=(255, 255, 255, 70), width=2)
        # 左侧强调条只压标题区。拉到正文底下就是一条长杠，和两行大字的左缘
        # 只隔 12px，读起来像文字压线
        draw.rounded_rectangle((100, 530, 112, min(body_top - 34, card_bottom - 76)),
                               radius=6, fill=(*accent, 235))
        # 编号做成右上角小徽章：幽灵大字会和标题抢视线
        if index:
            bf = _font(46)
            bw = draw.textlength(index, font=bf)
            draw.rounded_rectangle((950 - bw - 34, 498, 966, 570), radius=36,
                                   fill=(*accent, 40), outline=(*accent, 150), width=2)
            draw.text((958 - bw - 17, 534), index, font=bf, fill=(*accent, 245), anchor="mm")
    elif hook:
        # 开场：斜切色块。收尾卡是素底，再画一条斜带就成了随机接缝
        draw.polygon([(0, 1200), (W, 980), (W, 1320), (0, 1540)], fill=(*accent, 26))
        # 开场卡不再单独画强调条：它和正文上方的分隔线只差一百多像素，会读成两道杠

    # 顶部 kicker
    draw.text((76, TOP_SAFE + 16), kicker, font=_font(40), fill=(255, 255, 255, 205))

    # 顶部进度条：一条线取代分页点，读起来是"进度"不是"翻页"
    bar_y, bar_h = 344, 8
    # 有品牌标的卡把进度条让开：白底圆角块压在条尾上像画错了
    bar_right = 1004 - (176 if logo else 0)
    draw.rounded_rectangle((76, bar_y, bar_right, bar_y + bar_h), radius=4,
                           fill=(255, 255, 255, 36))
    filled = 76 + int((bar_right - 76) * (progress + 1) / max(1, total))
    draw.rounded_rectangle((76, bar_y, filled, bar_y + bar_h), radius=4,
                           fill=(*accent, 240))

    # 正文要点：出图阶段精排（字号/行距/安全区都是像素级控制）
    if body and draw_body:
        if hook:
            _draw_block(overlay, body, HOOK_SIZE, body_top, 124, W - 248,
                        fill=(255, 255, 255, 214), spacing=1.42, align="center")
        else:
            _draw_block(overlay, body, body_size, body_top, 140, W - 264,
                        fill=(255, 255, 255, 236), spacing=1.42)
    if hook:
        # 开场卡没有边框，给钩子句一条细分隔线做锚
        draw.rounded_rectangle((W // 2 - 48, body_top - 44, W // 2 + 48, body_top - 38),
                               radius=3, fill=(*accent, 200))

    # 品牌标：只在开场和收尾露脸，中间每张都挂就成了水印
    if logo:
        try:
            from PIL import Image as _Image
            chip = 132
            x0, y0 = W - 76 - chip, TOP_SAFE - 8
            mark = _Image.open(logo).convert("RGB")
            # 图标四周留白很多，缩到 100px 会糊成一片白：先裁到内容框再居中
            box = mark.convert("L").point(lambda v: 255 if v < 200 else 0).getbbox()
            if box:
                mark = mark.crop(box)
            mark.thumbnail((chip - 34, chip - 34), _Image.LANCZOS)
            # 圆角白底和 logo 必须画在同一层：画在 overlay 上的底会盖掉贴在 img 上的 logo
            cell = _Image.new("RGBA", (chip, chip), (0, 0, 0, 0))
            ImageDraw.Draw(cell).rounded_rectangle(
                (0, 0, chip - 1, chip - 1), radius=30, fill=(255, 255, 255, 250))
            cell.paste(mark, ((chip - mark.width) // 2, (chip - mark.height) // 2))
            overlay.paste(cell, (x0, y0), cell)
        except Exception:
            pass  # 品牌标失败不该毁掉出片

    # 品牌角标
    draw.text((W - 76, H - 64), "Astrea Studio", font=_font(30),
              fill=(255, 255, 255, 120), anchor="ra")
    draw.text((76, H - 64), "AI 指挥台 · 自动生成", font=_font(30),
              fill=(255, 255, 255, 120))

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(img, overlay).convert("RGB").save(path, "PNG")
    return path



# ---------------------------------------------------------------- 预演
def _draw_centered(page, text, font, cx, cy, fill, max_w, spacing=1.3):
    """按剪映的字号换算规则在卡面上画字，用于出片前自查版式（不用打开剪映）。"""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(page)
    lines: list[str] = _wrap_text(draw, text, font, max_w)
    heights = [draw.textbbox((0, 0), ln or " ", font=font)[3] for ln in lines]
    total_h = sum(heights) + spacing * font.size * max(0, len(lines) - 1)
    y = cy - total_h / 2
    for ln, h in zip(lines, heights):
        if ln:
            draw.text((cx, y), ln, font=font, fill=fill, anchor="ma")
        y += h + spacing * font.size


def render_previews(spec: dict, out_dir: Path) -> list[Path]:
    from PIL import Image

    h, w = spec["height"], spec["width"]
    folder = out_dir / "preview"
    folder.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for i, scene in enumerate(spec["scenes"]):
        native = scene.get("native") or {}
        page = Image.open(scene["image"]).convert("RGB")
        # 字号换算是相对量：以「1080 宽画布 size=8 ≈ 44px 字幕」为锚点
        title_px = max(24, int(float(native.get("font_size") or 12) * w / 195))
        cy = h / 2 - float(native.get("transform_y", -0.45)) * h / 2
        cx = w / 2 + float(native.get("transform_x") or 0) * w / 2
        # 预览必须用和剪映同一个行距，否则自查看到的行距不是成片的行距
        title_spacing = 1 + float(native.get("line_spacing", 0.25))
        _draw_centered(page, scene.get("text") or "", _font(title_px), cx, cy,
                       (255, 255, 255), w * 0.72, spacing=title_spacing)
        cap = scene.get("caption") or ""
        if cap:
            cap_px = max(20, int(float(native.get("caption_size") or 9) * w / 195))
            cy2 = h / 2 - float(native.get("caption_y", -0.86)) * h / 2
            _draw_centered(page, cap, _font(cap_px), w / 2, cy2,
                           (255, 255, 255), w * 0.86, spacing=1.15)
        out = folder / f"p{i:02d}.png"
        page.save(out, "PNG")
        made.append(out)
    return made

# ---------------------------------------------------------------- spec

def _title_size(text: str) -> int:
    """标题字号随字数自适应，保证最多两行、且不压正文。"""
    n = len(re.sub(r"\s+", "", text))
    if n <= 8:
        return 18
    if n <= 12:
        return 16
    return 14

def _unwrap(text: str, limit: int = 17) -> str:
    """中文按字数硬折行，剪映不会自动折得好看。

    有逗号且两半都不长时优先在逗号后断：按字数硬切会把
    「给 AI 一个链接，它能」和「读到什么程度」排成悬殊的两行。
    """
    text = re.sub(r"\s+", "", text)
    if len(text) > limit:
        for j, ch in enumerate(text):
            if ch in "，,、；;：" and 4 <= j + 1 <= limit and len(text) - j - 1 <= limit:
                return text[:j + 1] + "\n" + text[j + 1:]
    return "\n".join(text[i:i + limit] for i in range(0, len(text), limit))


def _first_sentence(text: str, limit: int = 30) -> str:
    """字幕只留第一句，硬切到 limit 字；句读处断开，别把词砍成两半。"""
    text = re.sub(r"\s+", "", text or "")
    if not text:
        return ""
    cut = min(len(text), limit)
    # 落在西文词中间就往后退到词边界（"PDF"不该变成"PD"）
    while 0 < cut < len(text) and text[cut - 1].isascii() and text[cut - 1].isalnum() \
            and text[cut].isascii() and text[cut].isalnum():
        cut -= 1
    for idx in range(cut, max(6, cut - 8), -1):
        if text[idx - 1] in "。！？，；":
            return text[:idx]
    return text[:cut]


def _fallback_say(title: str, body: str) -> str:
    """没写口播稿时，短说明补个标题当主语，长说明直接念，别念成清单。"""
    text = str(body or "").strip()
    if len(text) >= 8:
        return text
    return f"{title}。{text}".strip("。")


def _parse_point(raw: object) -> tuple[str, str, str]:
    """要点支持「标题::说明」和「标题::说明::口播稿」两种写法。

    屏幕文案和嘴里说的话是两件事：卡面上要短，口播要顺口。
    不给口播稿时用说明直接念：说明本来就是你写的一句话，
    再叠一遍「标题。」就变成念要点清单了。
    """
    if isinstance(raw, dict):
        title = str(raw.get("title") or "").strip()
        body = str(raw.get("detail") or raw.get("body") or "").strip()
        say = str(raw.get("say") or raw.get("narration") or "").strip()
        if not body and not say:
            raise ValueError(f"要点缺说明：{raw}")
        return title, body, say or _fallback_say(title, body)
    parts = [p.strip() for p in str(raw).split("::")]
    title = parts[0] if parts else ""
    body = parts[1] if len(parts) > 1 else ""
    say = parts[2] if len(parts) > 2 else ""
    if not body and not say:
        raise ValueError(f"要点格式应为「标题::说明[::口播稿]」：{raw}")
    return title, body, say or _fallback_say(title, body)


def build_spec(*, topic: str, hook: str, points_raw: list, cta: str,
               voice: str, kicker: str, out_dir: Path,
               narration: dict | None = None, logo: Path | None = None,
               draw_text: bool = True) -> dict:
    """选题/要点 → 完整 scenes spec（含 5 张卡面 PNG）。"""
    scripts = [p for p in (points_raw or []) if str(p).strip()]
    points = [_parse_point(p) for p in scripts]
    narration = narration or {}
    if len(points) < 1:
        raise ValueError("factcard 至少需要一个要点（标题::说明）")
    total = 2 + len(points)  # 开场 + 要点 + 收尾
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5]+", "", topic)[:24] or "episode"
    scenes = []

    # 1) 开场钩子
    hook_img = render_card(out_dir / "s00.png", kicker=kicker, index="00",
                           palette_idx=0, progress=0, total=total, slug=slug, hook=True,
                           body=hook, body_top=800, logo=logo, draw_body=draw_text)
    scenes.append({
        "image": str(hook_img),
        "say": str(narration.get("intro") or f"{topic}。{hook}").strip(),
        "text": _unwrap(topic, 10),
        "voice": voice,
        "native": {
            "font": TITLE_FONT, "font_size": _title_size(topic),
            "line_spacing": 0.1,
            "transform_y": 0.458, "caption_size": 8, "caption_y": -0.72,
            "text_anim_in": "居中打字",
            "text_anim_out": "向上溶解",
            "transition_in": "叠化", "transition_duration": 0.7,
            # 开场卡不能是纯静态底图：轻微放大入场 + 一层暖调，先把质感立住
            "image_anim_in": "轻微放大", "image_anim_in_duration": 0.8,
            "effect": [SCENE_EFFECTS[3]],
            "kenburns": True,
        },
    })

    # 2) 要点逐屏
    for i, (title, body, say) in enumerate(points, start=1):
        img = render_card(out_dir / f"s{i:02d}.png", kicker=kicker, index=f"0{i}",
                          palette_idx=i, progress=i, total=total, slug=slug,
                          body=body, body_top=700, draw_body=draw_text)
        scenes.append({
            "image": str(img),
            "say": say,
            "text": _unwrap(title, 11),
            "body": body,
                "voice": voice,
            "native": {
                # 要点标签降级成小号、贴左的栏目名：正文放大后它不该再当标题
                "font": TITLE_FONT, "font_size": 12, "transform_y": 0.40,
                "transform_x": -0.62,
                "caption_size": 8, "caption_y": -0.78,
                "text_anim_in": TEXT_ANIMS[i % len(TEXT_ANIMS)],
                "text_anim_out": TEXT_OUTS[i % len(TEXT_OUTS)],
                # 卡面入场动效：光靠转场，切进来那一秒还是硬图
                "image_anim_in": IMAGE_ANIMS[i % len(IMAGE_ANIMS)],
                "image_anim_in_duration": 0.7,
                "transition_in": TRANSITIONS[i % len(TRANSITIONS)],
                "transition_duration": 0.6,
                # 氛围层：每屏都给一个轻特效，静态卡最怕"没质感"
                "effect": [SCENE_EFFECTS[(i - 1) % len(SCENE_EFFECTS)]],
                "kenburns": True,
            },
        })

    # 3) 收尾
    # 收尾卡也要有正文：空卡配一句飘在中间的字，最容易读成"没做完"
    cta_img = render_card(out_dir / f"s{total - 1:02d}.png", kicker=kicker, logo=logo,
                          index="", palette_idx=len(PALETTES) - 1, progress=total - 1,
                          total=total, slug=slug, hook=True,
                          body=re.sub(r"\s+", "", cta), body_top=700, draw_body=draw_text)
    scenes.append({
        "image": str(cta_img),
        "say": str(narration.get("cta") or cta).strip(),
        # 结论句已经画在卡面上，剪映再叠一层就是重影
        "text": "",
        "no_text": True,
        "voice": voice,
        "native": {
            "font": TITLE_FONT, "font_size": _title_size(cta),
            "transform_y": 0.10, "caption_size": 8, "caption_y": -0.78,
            "text_anim_in": "渐显",
            "text_anim_out": "渐隐",
            "transition_in": "叠化", "transition_duration": 0.7,
            "image_anim_out": "渐隐", "image_anim_out_duration": 0.6,
            "kenburns": True,
        },
    })

    return {
        "width": W, "height": H, "fps": 30,
        "voice": voice,
        "scenes": scenes,
        "meta": {"series": kicker, "topic": topic},
    }

def expand(spec: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """平台 spec（template=factcard）→ 完整 scenes spec，卡面写进 job_dir。"""
    topic = str(spec.get("topic") or spec.get("title") or "").strip()
    if not topic:
        raise ValueError("factcard 缺少 topic")
    full = build_spec(
        topic=topic,
        hook=str(spec.get("hook") or DEFAULT_HOOK),
        points_raw=spec.get("points") or [],
        cta=str(spec.get("cta") or DEFAULT_CTA),
        voice=str(spec.get("voice") or DEFAULT_VOICE),
        kicker=str(spec.get("kicker") or DEFAULT_KICKER),
        logo=Path(str(spec["logo"])).expanduser() if spec.get("logo") else None,
        out_dir=Path(job_dir),
        narration=spec.get("narration") if isinstance(spec.get("narration"), dict) else None,
    )
    full["engine"] = str(spec.get("engine") or "jianying")
    full["template"] = "factcard"
    if spec.get("fps"):
        full["fps"] = int(spec["fps"])
    full["meta"] = {**(full.get("meta") or {}), **(spec.get("meta") or {})}
    return full
