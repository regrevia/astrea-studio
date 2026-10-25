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
import zlib
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
    ((26, 27, 40), (54, 56, 82), (198, 208, 236)),    # 收尾：压太黑会把立绘吞进去
]

# 语气词 → 色温：同一套版式，整条片子是同一个颜色才像模板。
# 指出缺口用深玫（警示），落到结论用深琥珀（落槌），讲方法用深青（冷静）。
def _mix(c1, c2, t: float) -> tuple:
    """按比例把 c1 往 c2 混：每屏换整块底色，切点就像换了套模板。"""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


_BG_ANCHOR = 0.74  # 底色统一压向第一套深蓝，只留两成半的色温差


_TONE_HINTS = (
    (2, ("不", "没", "别", "缺", "错", "假", "糊")),
    (3, ("结论", "回查", "核对", "出处", "所以", "因此", "最终")),
    (1, ("先", "再", "然后", "拆", "步骤", "流程", "怎么")),
)


def _tone_palette(*texts: str, fallback: int = 1, avoid: tuple = ()) -> int:
    """按句子里的语气词挑色温；挑不出来就顺着上一屏轮换，避免相邻两屏撞色。"""
    joined = "".join(str(t or "") for t in texts)
    for idx, words in _TONE_HINTS:
        if any(w in joined for w in words):
            if idx not in avoid:
                return idx
    for step in range(1, len(PALETTES)):
        cand = 1 + (fallback + step - 1) % (len(PALETTES) - 2)
        if cand not in avoid:
            return cand
    return fallback


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
    # 用 crc32 而不是内置 hash：hash 每个进程随机加盐，worker 重启后同一份稿子会位移
    return zlib.crc32(slug.encode("utf-8")) % 9973



_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9%\.\-\+/]*|.", re.S)


_ORPHAN_PUNCT = set("，。、；：？！…）」』】》,.;:?!")
# 换行优先断在这些字后面：它们是词边界，断在词中间就成了「最舍 / 不得删」
_NATURAL_BREAK = _ORPHAN_PUNCT | set("里的是在把就才也都让给和与这那我你它过到要后前点")
# 断在引号里会把一句话劈成两半："…一句「挺有意 / 思」"，宁可提前一行收
_OPEN_BRACKETS = "「『（《【“‘"
_CLOSE_BRACKETS = "」』）》】”’"


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
                    brk = len(buf)
                    depth = 0
                    for j in range(len(buf) - 1, -1, -1):
                        ch = buf[j]
                        if ch in _CLOSE_BRACKETS:
                            depth += 1
                        elif ch in _OPEN_BRACKETS:
                            if depth:
                                depth -= 1
                                continue
                            # 断点落在引号里就退回引号前，前提是退完这行还不算短
                            if j > 0 and draw.textlength(buf[:j], font=font) >= max_w * 0.45:
                                brk = j
                            break
                    out.append(buf[:brk])
                    buf = buf[brk:] + tok
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
    # 两行长短悬殊（"你收藏夹里最舍不得删的是 / 哪条"）读起来像断错了：改在中间断，
    # 让两行接近等宽。贪心断行只保证不超宽，不管好看。
    if len(out) == 2 and len(out[0]) > 4 and (
            len(out[-1]) <= 2 or len(out[-1]) < len(out[0]) * 0.45):
        flat = out[0] + out[1]
        best, best_gap = None, None
        for cut in range(2, len(flat) - 1):
            left_w = draw.textlength(flat[:cut], font=font)
            right_w = draw.textlength(flat[cut:], font=font)
            if left_w > max_w or right_w > max_w:
                continue
            # 断在虚词/标点后读着才顺；断在词中间（"最舍 / 不得删"）肉眼就是断错
            natural = flat[cut - 1] in _NATURAL_BREAK
            gap = abs(left_w - right_w) + (0 if natural else 900)
            if best_gap is None or gap < best_gap:
                best, best_gap = cut, gap
        if best is not None:
            out = [flat[:best], flat[best:]]
    # 在标点后断行时行尾会留一个空格："Value " 挂在行末看着像少打了个字
    out = [ln.rstrip() for ln in out]
    # 行尾挂一个顿号/逗号像话没说完被打断，但这行往往是强调句的落点：
    # 真正的解法是正文别写成「前半句，后半句」，硬搬标点会把「，」甩到下一行行首，
    # 跟强调色一起读成排版错误。这里只修句末空格，断点仍交给排版。
    return [ln for ln in out if ln] or out


def _tail_cut(text: str, lo: int = 3, hi: int = 12) -> int:
    """找出这句话的落点：最后一个逗号之后的短句。返回它在原文里的起点，找不到返回 -1。

    整块正文一个颜色，读者得自己找重点；一句话的落点染上强调色，
    眼睛先落地，再回头读前提。
    """
    body = str(text or "").rstrip("。！!")
    for sep in ("，", ",", "；", ";"):
        i = body.rfind(sep)
        if i <= 0:
            continue
        tail = body[i + 1:].strip()
        if lo <= len(tail) <= hi:
            return i + 1
        return -1
    return -1


def _draw_block(page, text: str, size: int, top: int, left: int, max_w: float,
                fill=(255, 255, 255, 240), spacing: float = 1.45,
                align: str = "left", tail_fill=None) -> int:
    """在出图阶段排版正文：剪映字号是相对量，精排只能自己算像素。返回结束 y。"""
    from PIL import ImageDraw

    if not text:
        return top
    draw = ImageDraw.Draw(page)
    font = _font(size)
    y = top
    rows = _wrap_text(draw, text, font, max_w)
    cut = _tail_cut(text) if tail_fill else -1
    pos = 0     # 已消费的原文下标，用来把落点映射到某一行中间
    for ln in rows:
        if ln:
            x = left
            if align == "center":   # 标题居中，正文左对齐会读成两套排版
                x = left + (max_w - draw.textlength(ln, font=font)) / 2
            if cut < 0:
                draw.text((x, y), ln, font=font, fill=fill)
            else:
                # 行内切两段：落点之前用正文色，之后用强调色
                stripped = ln.lstrip()
                lead = len(ln) - len(stripped)
                head_len = max(0, min(len(stripped), cut - pos))
                head, tail = stripped[:head_len], stripped[head_len:]
                if head:
                    draw.text((x, y), head, font=font, fill=fill)
                    x += draw.textlength(head, font=font)
                if tail:
                    draw.text((x, y), tail, font=font, fill=tail_fill)
                pos += len(stripped) + lead
                while pos < cut and pos < len(text) and text[pos].isspace():
                    pos += 1
        y += int(size * spacing)
    return y

# 两行排不下就往下试，直到 60：长句宁可小一号，也不要撑破卡片边框
# 正文比标题还大，一眼就看不出谁是要点：候选从 72 起，让层级正过来
_BODY_CANDIDATES = (72, 66, 60, 54, 48)


BODY_PAD = 72      # 卡底到正文末行的留白：比顶部留白还大，卡内看着就往下坠
CARD_CAP = 1010    # 卡底上限：立绘头顶 1014，再长就把角色闷进卡片


def body_layout(text: str, measure=None, max_w: float = W - 264,
                top: int = 700) -> tuple[int, list[str], int, int]:
    """正文几何一次算清：字号、断行、起排 y、卡底。

    卡面底图和文字贴片各算各的，行高一差分不清是「留白不够」还是「字太大」。
    这里同时受两个约束：断行不能留孤字，正文末行到卡底要留够 BODY_PAD 且不压立绘。
    """
    if not text:
        # 只有标题的卡：卡框收到标题下沿，否则下半张卡是空的（一眼看出没排完）
        return BODY_SIZE, [], int(top), int(top) + 210

    def _clean(rows: list[str]) -> bool:
        if len(rows) <= 1:
            return True
        if len(rows) > 2:
            return False
        last, first = len(rows[-1]), len(rows[0])
        return last >= 3 and last >= first * 0.45

    def _geom(size: int, rows: list[str]) -> tuple[int, int]:
        start = int(top) + (56 if size >= 110 else 40 if size > BODY_SIZE else 0)
        return start, start + int(len(rows) * size * 1.42) + BODY_PAD

    chosen: tuple[int, list[str], int] | None = None
    for size in _BODY_CANDIDATES:
        rows = _wrap_text(measure, text, _font(size), max_w) if measure is not None else [text]
        start, need = _geom(size, rows)
        if need <= CARD_CAP and _clean(rows):
            chosen = (size, rows, start)
            break
    if chosen is None:
        size = _BODY_CANDIDATES[-1]
        rows = _wrap_text(measure, text, _font(size), max_w) if measure is not None else [text]
        chosen = (size, rows, _geom(size, rows)[0])
    size, rows, start = chosen
    _, need = _geom(size, rows)
    # 卡底下限：两行正文也压到 968，和立绘头顶（1014）留出 46 的清白
    floor = 968 if len(rows) > 1 else 880
    return size, rows, start, min(CARD_CAP, max(floor, need))


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
                logo: Path | None = None, draw_body: bool = True,
                hud: bool = True, card_bottom: int | None = None,
                card_top: int = 470) -> Path:
    """卡面底图：渐变 + 网格点 + 圆角描边卡 + 编号幽灵字 + 进度点。文字交给剪映。

    ``card_bottom`` / ``card_top`` 只在没有正文时有用：这种卡（封面/收尾）的字由
    调用方自己排，面板高度就无从得知，不传就按一整屏铺，收尾屏下半截会空一大片。
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    top, bottom, accent = PALETTES[palette_idx % len(PALETTES)]
    # 语气只该改强调色，不该换整块底色：切点从深蓝跳到深琥珀，观众看到的是
    # 「换了一套模板」，不是「换了一个意思」。底色统一压低到同一条色轴。
    top = _mix(top, PALETTES[0][0], _BG_ANCHOR)
    bottom = _mix(bottom, PALETTES[0][1], _BG_ANCHOR)
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

    # 网格点：素底开场没有大块图形，推近时浮点太淡就等于画面定格
    dot_alpha = 46 if plain else 18
    for y in range(TOP_SAFE, H - BOTTOM_SAFE, 60):
        for x in range(80, W - 80, 60):
            draw.ellipse((x - 1, y - 1, x + 1, y + 1),
                         fill=(255, 255, 255, dot_alpha))

    if plain:
        # 开场是素底：原来那条斜切色块正压在立绘身上，像一块没对齐的板。
        # 换成钩子文字后面一团柔光，字有底、人物也不被切。
        glow = Image.radial_gradient("L").resize((1080, 1080))
        glow = glow.point(lambda v: max(0, int(52 * (1 - v / 255))))
        patch = Image.new("RGBA", (1080, 1080), (*accent, 0))
        patch.putalpha(glow)
        overlay.alpha_composite(patch, (0, 240))
        # 以前这里画过两圈 3px 的细环，想让推近时画面有动的东西。实际成片里
        # 它们横穿人脸和立绘，读成「过曝的光圈」而不是纵深——删掉，动态交给
        # 人物呼吸和卡面 7% 推近。
    measure = ImageDraw.Draw(overlay)
    body_size, body_lines, body_top, panel_bottom = (
        body_layout(body, measure, top=body_top) if body else (BODY_SIZE, [], body_top, 1000))
    if card_bottom is not None:
        # 没有正文的卡（封面/收尾）：字由调用方排，面板底只能由调用方给，
        # 不然默认 1000 会在收尾屏留一截空白，边缘还可能横切在人物头上
        panel_bottom = max(body_top + 120, min(panel_bottom, int(card_bottom)))
    if not hook and not plain:
        # 卡片高度跟着正文走，避免大片空白（"PPT 味"最主要的来源）；
        # 几何跟贴片共用 body_layout，卡底和字底不会再差出一档留白
        # 硬描边的圆角框 + 右上角编号是「幻灯片」的两大特征：观众一眼看到 03
        # 就知道这是第几页。改成无描边的玻璃面板，只留一道顶缘高光给出边界。
        # 面板边缘做羽化：硬边一出，观众看到的就是"一张贴在画面上的卡片"。
        # 羽化 22px 后边界融进背景，只在文字底下留一层薄薄的提亮。
        _box = (76, card_top, 1004, panel_bottom)
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle(_box, radius=56,
                                                fill=(255, 255, 255, 11))
        _mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(_mask).rounded_rectangle(_box, radius=56, fill=255)
        panel.putalpha(ImageChops.multiply(panel.getchannel("A"),
                                           _mask.filter(ImageFilter.GaussianBlur(60))))
        overlay.alpha_composite(panel)
        # 顶缘高光跟着羽化一起收窄，否则它自己会变成新的硬边
        draw.rounded_rectangle((150, card_top + 4, 930, card_top + 5), radius=1,
                               fill=(255, 255, 255, 30))
        # 左侧强调条只压标题区。拉到正文底下就是一条长杠，和两行大字的左缘
        # 只隔 12px，读起来像文字压线
        draw.rounded_rectangle((100, 530, 112, min(body_top - 34, panel_bottom - 76)),
                               radius=6, fill=(*accent, 235))
        # 编号不再上屏：页序号是幻灯片的东西，成片里只会提醒观众「这是一页」。
        # index 参数保留，剪映工程里还要靠它排序。
    elif hook:
        # 开场：斜切色块。收尾卡是素底，再画一条斜带就成了随机接缝
        draw.polygon([(0, 1200), (W, 980), (W, 1320), (0, 1540)], fill=(*accent, 26))
        # 开场卡不再单独画强调条：它和正文上方的分隔线只差一百多像素，会读成两道杠

    # 顶部 kicker 与进度条是「界面」，不是「画面」：出片时贴在独立的静态层上，
    # 卡面推近/叠化都不会带着它一起缩放（缩放中的 HUD 在转场里会重影成两张）
    if hud:
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
                        fill=(255, 255, 255, 236), spacing=1.42,
                        tail_fill=(*accent, 245))
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
    if hud:
        draw.text((W - 76, H - 64), "Astrea Studio", font=_font(30),
                  fill=(255, 255, 255, 120), anchor="ra")
        draw.text((76, H - 64), "AI 指挥台 · 自动生成", font=_font(30),
                  fill=(255, 255, 255, 120))

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(img, overlay).convert("RGB").save(path, "PNG")
    return path



def render_glow(path: Path, *, palette_idx: int, alpha: int = 54) -> Path:
    """一块透明柔光，用来让素底在推近之外还有东西在动（静止 2 秒就是 PPT）。"""
    import numpy as np
    from PIL import Image

    _top, _bottom, accent = PALETTES[palette_idx % len(PALETTES)]
    # 直接用强调色当光，蓝底色上就是一枚看得见的蓝灯泡。光要读成"照亮"，
    # 就得往白里提、往底色里退：提亮 45% 后只剩一层色偏，不会有人盯着它看。
    accent = _mix(accent, (255, 255, 255), 0.45)
    size = (1360, 800)
    # 衰减自己算：PIL 的 radial_gradient 把四角当最远点，边缘留着半透明，
    # 贴上屏幕就是一块看得见直边的方斑。这里横宽纵扁压成椭圆——正圆贴在
    # 人物背后是一轮看得见的圆盘，椭圆才像从画外漫进来的环境光。
    # 三次方衰减：中心一点亮，边缘彻底化开，不跟卡面的圆角抢边。
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - (w - 1) / 2), (yy - (h - 1) / 2) / 0.58) / (min(w, h) / 2)
    # 衰减两端都要平：先在 512 网格上算再放大，放大后首尾各留一条亮边，贴上暗底
    # 就是一串同心圆环（8 bit 量化把 1/255 的台阶画成线）。改成原尺寸算、
    # smoothstep 收边，再叠一层 ±1 的抖动噪声，台阶被打散，读作一层雾。
    tt = np.clip(1.0 - r / 1.02, 0.0, 1.0)
    fall = (tt * tt * (3.0 - 2.0 * tt)) ** 1.45
    rng = np.random.default_rng(7)
    alpha_map = fall * alpha + rng.uniform(-1.0, 1.0, fall.shape)
    mask = Image.fromarray(np.clip(alpha_map, 0, 255).astype(np.uint8), "L")
    img = Image.new("RGBA", size, (*accent, 0))
    img.putalpha(mask)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return path


def render_hud(path: Path, *, kicker: str, progress: int, total: int,
               palette_idx: int, logo: Path | None = None) -> Path:
    """界面层：kicker + 进度条 + 角标，透明底，整条片只贴一次不跟着缩放。

    和 render_card 用同一套坐标，挪动一处就得两处一起改。
    """
    from PIL import Image, ImageDraw

    _top, _bottom, accent = PALETTES[palette_idx % len(PALETTES)]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    draw.text((76, TOP_SAFE + 16), kicker, font=_font(40), fill=(255, 255, 255, 205))
    bar_y, bar_h = 344, 8
    bar_right = 1004 - (176 if logo else 0)
    draw.rounded_rectangle((76, bar_y, bar_right, bar_y + bar_h), radius=4,
                           fill=(255, 255, 255, 36))
    filled = 76 + int((bar_right - 76) * (progress + 1) / max(1, total))
    draw.rounded_rectangle((76, bar_y, filled, bar_y + bar_h), radius=4,
                           fill=(*accent, 240))
    draw.text((W - 76, H - 64), "Astrea Studio", font=_font(30),
              fill=(255, 255, 255, 120), anchor="ra")
    draw.text((76, H - 64), "AI 指挥台 · 自动生成", font=_font(30),
              fill=(255, 255, 255, 120))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
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
               draw_text: bool = True, hud: bool = True) -> dict:
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
                           body=hook, body_top=800, logo=logo, draw_body=draw_text,
                           hud=hud)
    scenes.append({
        "image": str(hook_img),
        "palette": 0,
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
    palette_used: list[int] = [0]
    for i, (title, body, say) in enumerate(points, start=1):
        # 色温跟着这句话的语气走，不是跟着第几屏走
        pal = _tone_palette(title, body, fallback=i, avoid=tuple(palette_used[-1:]))
        palette_used.append(pal)
        img = render_card(out_dir / f"s{i:02d}.png", kicker=kicker, index=f"0{i}",
                          palette_idx=pal, progress=i, total=total, slug=slug, hud=hud,
                          body=body, body_top=700, draw_body=draw_text)
        scenes.append({
            "image": str(img),
            "palette": pal,
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
    # 卡面写什么、配音就念什么。以前卡面只写 cta、配音念了更长的 outro，
    # 观众看到「装上 Envoy，链接就是材料」，耳朵却多听到半句，像两条音轨没对齐。
    # 稿子里 cta 和 outro 常常都在：cta 是写在卡上的短语，outro 才是配音念的整句。
    # 优先 outro，卡面才会跟耳朵听到的是同一句。
    close_say = str(narration.get("outro") or narration.get("cta") or cta).strip()
    cta_img = render_card(out_dir / f"s{total - 1:02d}.png", kicker=kicker, logo=logo,
                          index="", palette_idx=len(PALETTES) - 1, progress=total - 1,
                          total=total, slug=slug, hook=True, hud=hud,
                          body=re.sub(r"\s+", "", close_say), body_top=700,
                          draw_body=draw_text)
    scenes.append({
        "image": str(cta_img),
        "palette": len(PALETTES) - 1,
        # 稿子可能写成 outro 而不是 cta：只认 cta 会把收尾那句丢掉，配音改成
        # 卡面正文念一遍，情绪也退回默认的笑
        "say": close_say,
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
