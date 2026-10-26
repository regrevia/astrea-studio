"""口型差分：素材包里 开/闭 两张立绘是同一张图，嘴其实不动。

手绘差分要重画九种姿态，成本比「让嘴真的动」高得多；这里在原图的嘴上做
局部重绘：抹掉原嘴、按目标口型画一个同风格的嘴。人物只占画面四分之一宽，
嘴在成片里不到 40 像素宽，肉眼看的是「张合」而不是唇形细节。

只依赖 PIL：worker 跑在系统 python3 上，没有 numpy。
"""
from __future__ import annotations

from pathlib import Path

import math
from PIL import Image, ImageDraw, ImageFilter

_CACHE: dict[tuple, Image.Image] = {}
# 老档名（closed/half/round/open）必须继续可用：paper 链路和旧 spec 里还有。
# 新档按宽度/高度/齿色区分，见 _draw_mouth。
STATES = ("closed", "half", "round", "open",
          "wide", "mid", "narrow", "pucker", "teeth")
_LEGACY = ("closed", "half", "round", "open")


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
    # 粉红的只是「嘴里」，扩到含唇线的整张嘴再返回
    box = _grow_box(img, box)
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


def _grow_box(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """把嘴内框扩到含唇线和唇影的整张嘴。

    `_mask_pink` 只框住嘴里的粉红，唇线那圈橙红、以及下唇下面那道阴影都在框
    外。抹嘴时按粉红框抹，就会在嘴下面留一条发灰的弧——成片里像「第二张嘴」。
    这里按行/列统计红色像素往外长，长到没红为止。
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    px = img.load()
    w, h = img.size

    def red(x: int, y: int) -> bool:
        if not (0 <= x < w and 0 <= y < h):
            return False
        r, g, b, a = px[x, y]
        return a > 200 and r > 140 and r - g >= 30 and r - b >= 25 and g > 55

    def row_red(y: int) -> int:
        n = 0
        for x in range(max(0, x0 - int(bw * 0.12)), min(w, x1 + int(bw * 0.12))):
            if red(x, y):
                n += 1
        return n

    def col_red(x: int) -> int:
        n = 0
        for y in range(max(0, y0 - int(bh * 0.2)), min(h, y1 + int(bh * 0.2))):
            if red(x, y):
                n += 1
        return n

    lim_y = int(bh * 0.95)
    ny0, ny1 = y0, y1
    for k in range(1, lim_y + 1):
        if row_red(y0 - k) >= 2:
            ny0 = y0 - k
        else:
            break
    for k in range(1, lim_y + 1):
        if row_red(y1 + k - 1) >= 2:
            ny1 = y1 + k
        else:
            break
    lim_x = int(bw * 0.25)
    nx0, nx1 = x0, x1
    for k in range(1, lim_x + 1):
        if col_red(x0 - k) >= 2:
            nx0 = x0 - k
        else:
            break
    for k in range(1, lim_x + 1):
        if col_red(x1 + k - 1) >= 2:
            nx1 = x1 + k
        else:
            break
    return (nx0, ny0, nx1, ny1)


def _skin(img: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """嘴唇旁边那圈皮肤色：贴着外框取一圈，跟嘴周围的光影一致。

    以前从外框上方脸颊取，那块比嘴周亮 2~4 个色阶，抹完嘴是一个发白的光晕。
    """
    w, h = img.size
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    ox, oy = max(2, int(bw * 0.22)), max(2, int(bh * 0.30))
    outer = (max(0, x0 - ox * 2), max(0, y0 - oy * 2),
             min(w, x1 + ox * 2), min(h, y1 + oy * 2))
    px = img.load()
    vals: list[tuple[int, int, int]] = []
    for y in range(outer[1], outer[3], 1):
        for x in range(outer[0], outer[2], 1):
            if x0 - ox <= x < x1 + ox and y0 - oy <= y < y1 + oy:
                continue  # 跳过嘴本身
            r, g, b, a = px[x, y]
            if a > 230 and r > 200 and g > 160 and b > 140 and r - g < 45:
                vals.append((r, g, b))
    return _median(vals)


def _erase(img: Image.Image, box, skin) -> Image.Image:
    """把原嘴抹成本地肤色。

    框内必须 100% 抹掉：椭圆蒙版会在框的四角留半透明残影，成片里就是嘴下面
    一条发灰的「第二张嘴」。所以蒙版用圆角矩形，整块不透明，只在框外羽化。
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    out = img.copy()
    px = max(2, int(bw * 0.16))
    # 下唇下面那道阴影比嘴内框低 0.7~0.9 倍嘴高：不抹掉就是嘴下方一条灰弧
    py_t = max(2, int(bh * 0.34))
    py_b = max(3, int(bh * 0.88))
    rect = (x0 - px, y0 - py_t, x1 + px, y1 + py_b)
    mask = Image.new("L", out.size, 0)
    r = max(2, int(min(bw, bh) * 0.45))
    ImageDraw.Draw(mask).rounded_rectangle(rect, radius=r, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1.6, bh * 0.20)))
    fill = Image.new("RGBA", out.size, skin + (255,))
    return Image.composite(fill, out, mask)


_TEETH_WHITE = (246, 240, 230)  # 暖白：冷白 (255,255,255) 在暖肤色上像贴纸


def _teeth(inner: tuple[int, int, int]) -> tuple[int, int, int]:
    """齿色直接给暖白，只借一点唇色，别让牙跟着口红变粉。"""
    return tuple(int(c * 0.15 + w * 0.85) for c, w in zip(inner, _TEETH_WHITE))


def _draw_mouth(img: Image.Image, box, inner, rim, state: str) -> Image.Image:
    """在抹干净的嘴上画目标口型。

    成片里嘴只有 ~19x6 像素（素材 44x20，DES 1024，人物宽 696）。这个尺寸下
    能读出来的只有**横向宽度**和**露不露白**，所以：宽度是主区分维度，高度
    只在 0.55~1.0 倍之间小动（Lead 定的上限：wide ≤1.12、pucker 高 ≤0.95、
    teeth 高 ≤0.6 且宽 ≤0.6）。画得比素材大一圈，放大看会像球，成片里像换角色。
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out = img.copy()
    d = ImageDraw.Draw(out)
    line = tuple(max(0, int(c * 0.62)) for c in rim)
    if state == "closed":
        # 一条带笑的弧线：嘴闭上时只剩唇线。宽度贴着素材（0.70 倍），一根线，不叠第二道
        ww = bw * 0.70
        lw = max(2, int(round(bh * 0.14)))
        pts = []
        for k in range(13):
            u = k / 12.0
            xx = cx - ww / 2.0 + ww * u
            yy = cy + math.sin(math.pi * u) * bh * 0.13
            pts.append((xx, yy))
        d.line(pts, fill=line + (255,), width=lw, joint="curve")
        return out
    # 宽度序：pucker 0.38 < teeth 0.56 < round 0.62 < half 0.80 < narrow 0.86
    #        < mid 0.95 < open 1.00 < wide 1.12
    tooth = _teeth(inner)
    gap = tuple(max(0, int(c * 0.70)) for c in rim)
    if state == "half":
        ew, eh, fill_c, rim_c = bw * 0.80, bh * 0.62, (246, 152, 147), (209, 112, 110)
    elif state == "round":
        ew, eh, fill_c, rim_c = bw * 0.62, bh * 1.00, (246, 146, 142), (206, 104, 102)
    elif state == "mid":
        ew, eh, fill_c, rim_c = bw * 0.95, bh * 0.80, (244, 148, 143), (204, 106, 104)
    elif state == "narrow":
        ew, eh, fill_c, rim_c = bw * 0.86, bh * 0.50, (243, 146, 141), (203, 104, 102)
    elif state == "wide":
        ew, eh, fill_c, rim_c = bw * 1.12, bh * 0.80, (208, 108, 104), (178, 80, 78)
    elif state == "pucker":
        ew, eh, fill_c, rim_c = bw * 0.38, bh * 0.75, (238, 140, 136), (188, 88, 86)
    elif state == "teeth":
        ew, eh, fill_c, rim_c = bw * 0.56, bh * 0.60, (236, 142, 138), (192, 92, 90)
    else:  # open 走原素材，这里只给 open 被直呼时的退路
        ew, eh, fill_c, rim_c = bw * 1.00, bh * 1.00, (241, 140, 135), (198, 96, 94)
    box_e = (cx - ew / 2.0, cy - eh / 2.0, cx + ew / 2.0, cy + eh / 2.0)
    d.ellipse(box_e, fill=fill_c + (255,), outline=rim_c + (255,),
              width=max(2, int(round(eh * 0.17))))
    if state == "half":
        # 上唇阴影：素材的嘴上半圈本来就深一点，补上才不像平涂色块
        shade = tuple(max(0, int(c * 0.90)) for c in fill_c)
        d.ellipse((box_e[0], box_e[1], box_e[2], box_e[1] + eh * 0.42),
                  fill=shade + (255,))
        d.ellipse(box_e, outline=rim_c + (255,), width=max(2, int(round(eh * 0.17))))
    elif state == "wide":
        # 张口：口腔内侧比 open 再深一档（不然和闭唇高光糊成一条横带），
        # 上牙只留窄的一条，别把口腔割断
        d.ellipse((box_e[0], box_e[1] + eh * 0.28, box_e[2], box_e[3]),
                  fill=tuple(max(0, int(c * 0.82)) for c in fill_c) + (255,))
        # 不加牙条：口腔要读成「整个张开且更深」，任何浅色横条都会把它割成两半
        d.ellipse(box_e, outline=rim_c + (255,), width=max(2, int(round(eh * 0.17))))
    elif state == "teeth":
        # 咬合露齿：一整条横向齿面，顶部贴上唇内缘、高约嘴高一半、左右各收 8%，
        # 下唇留一条薄暗线；齿缝只在两侧各压 1px，不画中间那一竖
        tl = (box_e[0] + ew * 0.08, box_e[1] + eh * 0.10,
              box_e[2] - ew * 0.08, box_e[1] + eh * 0.58)
        d.rounded_rectangle(tl, radius=max(1, int(eh * 0.09)), fill=tooth + (255,))
        # 齿缝只做「隐约两道」，压到 70% 齿色：成片时嘴只有 19x7px，
        # 一深就成栅栏；中间那一竖坚决不画
        gcol = tuple(int(a * 0.72 + b * 0.28) for a, b in zip(tooth, rim))
        gw = max(1, int(round(ew * 0.012)))
        for fx in (-0.18, 0.18):
            gx = cx + ew * fx
            d.line(((gx, tl[1] + eh * 0.22), (gx, tl[3] - eh * 0.22)),
                   fill=gcol + (255,), width=gw)
        d.line(((box_e[0] + ew * 0.10, box_e[3] - eh * 0.17),
                (box_e[2] - ew * 0.10, box_e[3] - eh * 0.17)),
               fill=line + (255,), width=max(1, int(round(eh * 0.10))))
    elif state == "pucker":
        # 努嘴：外圈再收一道唇线，读成「嘴收紧了」，不加高光
        d.ellipse((box_e[0] - ew * 0.12, box_e[1] - eh * 0.10,
                   box_e[2] + ew * 0.12, box_e[3] + eh * 0.10),
                  outline=rim_c + (255,), width=max(2, int(round(eh * 0.24))))
    return out


def _sibling_closed(path) -> Path | None:
    """张口稿旁边那张闭口稿：``..._open.png`` → ``..._closed.png``。"""
    try:
        p = Path(str(path))
    except (TypeError, ValueError):
        return None
    name = p.name
    if not name.endswith("_open.png"):
        return None
    alt = p.with_name(name[: -len("_open.png")] + "_closed.png")
    return alt if alt.is_file() else None


def _key_green_small(im: Image.Image) -> Image.Image:
    """素材是绿背 RGB：底色抠成透明。只给小图块用（整张 1254² 太慢）。"""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and g > 150 and r < 130 and b < 130 and g - max(r, b) > 60:
                px[x, y] = (r, g, b, 0)
    return im


def _guard_fill(img: Image.Image, box) -> Image.Image:
    """把嘴框（含闭口稿留下的唇线/高光）用左右两侧的皮肤横向插值抹平。

    闭口稿贴过来的是**一张闭着的嘴**：唇线、上唇那道白光都在。只往上叠一个
    小椭圆，边缘就会露出闭唇 + 高光——成片里读成「闭着还有反光」。左右两侧
    同一行是腮红/皮肤，横向插值能保住竖向渐变，又不带任何唇色。
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    fx = max(2, int(bw * 0.30))
    ft = max(2, int(bh * 0.42))
    fb = max(3, int(bh * 0.78))
    r = (max(1, x0 - fx), max(1, y0 - ft),
         min(img.width - 2, x1 + fx), min(img.height - 2, y1 + fb))
    rw, rh = r[2] - r[0], r[3] - r[1]
    if rw < 4 or rh < 4:
        return img
    out = img.copy()
    px = out.load()
    src = img.load()
    fill = Image.new("RGB", (rw, rh))
    fp = fill.load()
    for yy in range(rh):
        y = r[1] + yy
        cl = src[r[0] - 1, y]
        cr = src[r[2], y]
        for xx in range(rw):
            u = xx / max(1, rw - 1)
            fp[xx, yy] = tuple(int(a + (b - a) * u) for a, b in zip(cl, cr))
    mask = Image.new("L", (rw, rh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, rw - 1, rh - 1),
                                           radius=max(2, int(min(rw, rh) * 0.30)),
                                           fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(max(1.2, bh * 0.12)))
    out.paste(fill, (r[0], r[1]), mask)
    return out


def _mouth_base(img: Image.Image, box, key) -> Image.Image:
    """把嘴那一块换成同姿势闭口稿的皮肤，作为重画的底。

    平涂肤色（_erase）和嘴周的腮红/阴影对不齐，抹完总有一圈浅色光晕；闭口稿
    和这张是同一张画，只差嘴，所以直接贴它嘴那一块，底色就是原画的底色。
    抹掉的范围要含下唇下面那道阴影（比嘴内框低约一个嘴高）。
    """
    alt_path = _sibling_closed(key)
    if alt_path is not None:
        try:
            with Image.open(alt_path) as fh:
                alt = fh.convert("RGBA")
            if alt.size == img.size:
                x0, y0, x1, y1 = box
                bw, bh = x1 - x0, y1 - y0
                pad_x = max(2, int(bw * 0.30))
                pad_t = max(2, int(bh * 0.45))
                pad_b = max(3, int(bh * 1.05))
                rect = (max(0, x0 - pad_x), max(0, y0 - pad_t),
                        min(img.width, x1 + pad_x), min(img.height, y1 + pad_b))
                patch = _key_green_small(alt.crop(rect))
                mask = Image.new("L", patch.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, patch.width - 1, patch.height - 1),
                    radius=max(2, int(min(patch.width, patch.height) * 0.35)),
                    fill=255)
                mask = mask.filter(ImageFilter.GaussianBlur(max(1.4, bh * 0.18)))
                out = img.copy()
                out.paste(patch, rect[:2], mask)
                return _guard_fill(out, box)
        except Exception:  # noqa: BLE001 - 贴不上就退回平涂
            pass
    return _erase(img, box, _skin(img, box))


def apply(img: Image.Image, state: str, key=None) -> Image.Image:
    """把立绘的嘴换成 state 口型；识别不到嘴就原样返回（宁可不动也不能画歪）。"""
    if state not in STATES:
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
    if state == "open":
        # 张口直接用素材自己的嘴，不抹不画
        out = img
    else:
        base = _mouth_base(img, box, key[0])
        if state == "closed":
            # 底已经是原画的闭口唇线：不再画第二道
            out = _draw_mouth(base, box, inner, rim, state) if base is None else base
        else:
            out = _draw_mouth(base, box, inner, rim, state)
    if len(_CACHE) > 96:
        _CACHE.clear()
    _CACHE[key] = out
    return out.copy()

