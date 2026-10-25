"""口型差分：素材包里 开/闭 两张立绘是同一张图，嘴其实不动。

手绘差分要重画九种姿态，成本比「让嘴真的动」高得多；这里在原图的嘴上做
局部重绘：抹掉原嘴、按目标口型画一个同风格的嘴。人物只占画面四分之一宽，
嘴在成片里不到 40 像素宽，肉眼看的是「张合」而不是唇形细节。

只依赖 PIL：worker 跑在系统 python3 上，没有 numpy。
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

_CACHE: dict[tuple, Image.Image] = {}


def _px_ok(r: int, g: int, b: int, a: int) -> bool:
    return a > 200


def _mask_pink(img: Image.Image, rg: int = 80) -> Image.Image:
    """嘴内粉红：比皮肤更红、蓝绿都压下去。

    下巴/脖子那圈阴影也有 r-g≈66，阈值放到 80 才只留下嘴内（r-g≥100）。
    """
    from PIL import ImageChops

    r, g, b, a = img.split()
    m = r.point(lambda v: 255 if v >= 200 else 0)
    m = ImageChops.multiply(m, g.point(lambda v: 255 if 80 <= v <= 215 else 0))
    m = ImageChops.multiply(m, b.point(lambda v: 255 if 80 <= v <= 220 else 0))
    rgm = ImageChops.subtract(r, g).point(lambda v: 255 if v >= rg else 0)
    gbm = ImageChops.subtract(g, b).point(lambda v: 255 if v <= 30 else 0)
    m = ImageChops.multiply(m, ImageChops.multiply(rgm, gbm))
    return ImageChops.multiply(m, a.point(lambda v: 255 if v > 200 else 0))


def _components(mask: Image.Image, limit: int = 4000) -> list[tuple[int, int, int, int]]:
    """mask 上的连通块外框（BFS，区域小，够快）。"""
    w, h = mask.size
    data = mask.load()
    seen = bytearray(w * h)
    boxes: list[tuple[int, int, int, int]] = []
    for y in range(h):
        for x in range(w):
            i = y * w + x
            if seen[i] or not data[x, y]:
                continue
            stack = [(x, y)]
            seen[i] = 1
            x0 = x1 = x
            y0 = y1 = y
            n = 0
            while stack:
                cx, cy = stack.pop()
                n += 1
                if n > limit:
                    break
                if cx < x0:
                    x0 = cx
                if cx > x1:
                    x1 = cx
                if cy < y0:
                    y0 = cy
                if cy > y1:
                    y1 = cy
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        j = ny * w + nx
                        if not seen[j] and data[nx, ny]:
                            seen[j] = 1
                            stack.append((nx, ny))
            if n >= 12:
                boxes.append((x0, y0, x1 + 1, y1 + 1))
    return boxes


def _median(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    if not pixels:
        return (250, 226, 214)
    rs = sorted(p[0] for p in pixels)
    gs = sorted(p[1] for p in pixels)
    bs = sorted(p[2] for p in pixels)
    k = len(pixels) // 2
    return (rs[k], gs[k], bs[k])


def _locate(img: Image.Image) -> tuple[tuple[int, int, int, int], tuple, tuple] | None:
    """找到嘴：返回 (外框, 唇内色, 唇线色)。"""
    w, h = img.size
    # 脸在画面上部中间：窗口按画布比例固定，不跟着姿势跑
    # 窗口卡在脸中央：往下多给一点就会先选中下巴那圈阴影
    win = (int(w * 0.40), int(h * 0.385), int(w * 0.585), int(h * 0.470))
    sub = img.crop(win)
    scale = 4
    small = sub.resize((max(1, sub.width // scale), max(1, sub.height // scale)), Image.BILINEAR)
    boxes = _components(_mask_pink(small))
    if not boxes:
        # 有的表情嘴被手挡掉一半，阈值退一档再找；位置仍锁在同一个窗口
        boxes = _components(_mask_pink(small, 65))
    if not boxes:
        # 实在找不到就按素材包的固定脸位放（九个姿势共用同一张脸）
        k = w / 1254.0
        box = (int(590 * k), int(550 * k), int(628 * k), int(576 * k))
        pink = [img.getpixel((x, y))[:3]
                for y in range(box[1], box[3], 2)
                for x in range(box[0], box[2], 2)
                if img.getpixel((x, y))[0] - img.getpixel((x, y))[1] >= 70]
        return box, _median(pink), (212, 116, 112)
    # 嘴是窗口里最像「圆/椭圆」的那块：取最宽的，排除零碎高光
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    x0, y0, x1, y1 = boxes[0]
    box = (win[0] + x0 * scale, win[1] + y0 * scale,
           win[0] + x1 * scale, win[1] + y1 * scale)
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw < 12 or bh < 8 or bw > sub.width * 0.5:
        return None
    # 唇内色取外框内的偏粉像素；唇线色取外框内圈（就是那圈略深的橙红）
    inner: list[tuple[int, int, int]] = []
    rim: list[tuple[int, int, int]] = []
    px = img.load()
    bw = box[2] - box[0]
    bh = box[3] - box[1]
    pad = max(2, int(min(bw, bh) * 0.18))
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            r, g, b, a = px[x, y]
            if a <= 200 or r < 140:
                continue
            edge = (x < box[0] + pad or x >= box[2] - pad
                    or y < box[1] + pad or y >= box[3] - pad)
            if edge:
                if r - g >= 25 and g - b <= 30 and (r + g + b) < 640:
                    rim.append((r, g, b))
            elif r - g >= 35 and g - b <= 30:
                inner.append((r, g, b))
    # 唇线直接由唇内色压暗压红得到：素材里那圈橙红就是唇内色乘 0.78，
    # 比在边缘像素里挑暗色稳（边缘会混进皮肤）
    mid = _median(inner)
    line = (int(mid[0] * 0.78), int(mid[1] * 0.56), int(mid[2] * 0.55))
    return box, mid, line


def _skin(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """嘴唇周围的皮肤色：从外框上方的脸颊取中位色。"""
    w, h = img.size
    x0, y0, x1, y1 = box
    bw = x1 - x0
    patch = img.crop((max(0, x0 - bw), max(0, y0 - int(bw * 1.2)),
                      min(w, x1 + bw), max(1, y0 - int(bw * 0.4))))
    px = patch.load()
    vals: list[tuple[int, int, int]] = []
    for y in range(0, patch.height, 2):
        for x in range(0, patch.width, 2):
            r, g, b, a = px[x, y]
            if a > 230 and r > 200 and g > 165 and b > 145 and r - g < 45:
                vals.append((r, g, b))
    return _median(vals)


def _erase(img: Image.Image, box, skin) -> Image.Image:
    """把原嘴抹成皮肤：椭圆羽化填充，边缘不出现一圈硬边。"""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    out = img.copy()
    pad = int(max(bw, bh) * 0.40)
    mask = Image.new("L", out.size, 0)
    ImageDraw.Draw(mask).ellipse((x0 - pad, y0 - int(pad * 0.8),
                                  x1 + pad, y1 + int(pad * 0.8)), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(3.0, bh * 0.40)))
    fill = Image.new("RGBA", out.size, skin + (255,))
    return Image.composite(fill, out, mask)


def _draw_mouth(img: Image.Image, box, inner, rim, state: str) -> Image.Image:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out = img.copy()
    d = ImageDraw.Draw(out)
    line = tuple(max(0, int(c * 0.62)) for c in rim)
    if state == "closed":
        # 一条带笑的弧线：嘴闭上时只剩唇线
        ww = bw * 0.46
        lw = max(3, int(bh * 0.16))
        pts = []
        for k in range(11):
            u = k / 10.0
            xx = cx - ww / 2.0 + ww * u
            yy = cy + math.sin(math.pi * u) * bh * 0.10
            pts.append((xx, yy))
        d.line(pts, fill=line + (255,), width=lw, joint="curve")
        return out
    # 成片里嘴只有 ~24x11 像素，四档要一眼分得开：闭口一条线、半张是
    # 压扁的宽椭圆、张口是正椭圆、圆口收窄拉高。尺寸都在 1.0 倍上下，
    # 放大到 1.7 倍会变成贴在脸上的球。
    if state == "half":
        # 半张要能看出「张开了一条缝」：原来 0.42 高度在 1080 宽下只有十来个像素，
        # 远看和闭着的微笑线一模一样，说话时半条片子都在这个档上，就显成嘴不动。
        ew, eh, fill_c, rim_c = bw * 0.86, bh * 0.74, (246, 152, 147), (209, 112, 110)
    elif state == "round":
        ew, eh, fill_c, rim_c = bw * 0.62, bh * 1.16, (246, 146, 142), (206, 104, 102)
    else:  # open
        ew, eh, fill_c, rim_c = bw * 1.02, bh * 1.10, (241, 140, 135), (198, 96, 94)
    box_e = (cx - ew / 2.0, cy - eh / 2.0, cx + ew / 2.0, cy + eh / 2.0)
    d.ellipse(box_e, fill=fill_c + (255,), outline=rim_c + (255,),
              width=max(2, int(eh * 0.20)))
    # 上唇阴影：素材的嘴上半圈本来就深一点，补上才不像平涂色块
    if state in ("half", "round"):
        shade = tuple(max(0, int(c * 0.90)) for c in fill_c)
        d.ellipse((box_e[0], box_e[1], box_e[2], box_e[1] + eh * 0.42),
                  fill=shade + (255,))
        d.ellipse(box_e, outline=rim_c + (255,), width=max(2, int(eh * 0.20)))
    return out


def apply(img: Image.Image, state: str, key=None) -> Image.Image:
    """把立绘的嘴换成 state 口型；识别不到嘴就原样返回（宁可不动也不能画歪）。"""
    if state not in ("closed", "half", "round", "open"):
        return img
    key = (key or id(img), state)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit.copy()
    found = _locate(img)
    if found is None:
        _CACHE[key] = img
        return img.copy()
    box, inner, rim = found
    skin = _skin(img, box)
    base = _erase(img, box, skin)
    # open 直接用素材自己的嘴（已经不抹了），其余口型在抹掉的原嘴上重画
    out = img if state == "open" else _draw_mouth(base, box, inner, rim, state)
    if len(_CACHE) > 96:
        _CACHE.clear()
    _CACHE[key] = out
    return out.copy()


import math  # noqa: E402  (放在末尾只给 _draw_mouth 用，避免顶部 import 顺序噪声)
