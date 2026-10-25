"""鲸鱼娘「阿斯特」占位立绘：程序化绘制 + 差分导出。

真图（deploy/presenter/ASSET-SPEC-whale-girl.md 里那套 GPT-image-2 差分）到位后，
把 PNG 覆盖到 ``~/CreatorStudio/state/characters/whale/{base,mouth,eyes,poses}/``
即可，渲染管线按文件名取图，不需要改代码。

用法：
    python3 character.py --out ~/CreatorStudio/state/characters/whale
    python3 character.py --sheet /tmp/sheet.png     # 出对照图，画崩了能立刻看见
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 2            # 超采样倍数
DES = 1024       # 成品边长
CANVAS = DES * S
CX = CANVAS // 2

SKIN = (255, 231, 217, 255)
HAIR_TOP = (63, 132, 226, 255)
HAIR_MID = (94, 168, 240, 255)
HAIR_BOT = (150, 214, 250, 255)
DRESS = (252, 253, 255, 255)
NAVY = (27, 58, 107, 255)
NAVY_SOFT = (52, 88, 148, 255)
OUTLINE = (23, 39, 68, 255)
TAIL = (127, 196, 245, 165)
BLUSH = (255, 168, 178, 120)
INK = (26, 45, 78, 255)
WHITE = (255, 255, 255, 255)

LW = 6 * S          # 主线宽
LW2 = 4 * S         # 细节线宽

HEAD_C = (CX, 372 * S)
HEAD_R = 200 * S
SHOULDER_Y = 612 * S
HIP_Y = 742 * S
GROUND = 930 * S


def _shape(d, pts, fill, outline=OUTLINE, width=LW):
    d.polygon(pts, fill=fill)
    if outline:
        d.line(list(pts) + [pts[0]], fill=outline, width=width, joint="curve")


def _curve(p0, p1, p2, steps=20):
    out = []
    for i in range(steps + 1):
        t = i / steps
        out.append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
                    (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]))
    return out


def _arc(cx, cy, r, a0, a1, steps=28):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
            for i in range(steps + 1)]


def draw_tail(img, sway=0.0):
    """身后半透明鲸尾，从右腰接出去，sway -1..1 摆尾。"""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0 = CX + 150 * S, HIP_Y + 40 * S
    ang = math.radians(8 + 12 * sway)
    pts, widths = [], []
    for i in range(12):
        t = i / 11
        r = t * 250 * S
        pts.append((x0 + math.cos(ang + t * 0.55) * r,
                    y0 + math.sin(ang + t * 0.55) * r))
        widths.append(int((52 - 26 * t) * S))
    for i in range(1, len(pts)):
        d.line([pts[i - 1], pts[i]], fill=TAIL, width=widths[i])
    tip = pts[-1]
    fluke = [(tip[0] - 26 * S, tip[1]), (tip[0] + 44 * S, tip[1] - 74 * S),
             (tip[0] + 96 * S, tip[1] - 30 * S), (tip[0] + 30 * S, tip[1] + 24 * S)]
    d.polygon(fluke, fill=TAIL)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(S)))


def _arm(d, side, angle_deg, length=172 * S):
    """side=-1 左臂 / +1 右臂；angle 0=垂下，90=平举。

    手臂短一截就只是肩膀上的两个球，抬起来也读不出手势：
    长度按「手能到下巴」定，袖口收在肘上，手掌画成连指手套状。
    """
    sx, sy = CX + side * 92 * S, SHOULDER_Y + 6 * S
    a = math.radians(angle_deg)
    ex = sx + side * math.sin(a) * length
    ey = sy + math.cos(a) * length
    d.line([(sx, sy), (ex, ey)], fill=OUTLINE, width=46 * S)
    d.line([(sx, sy), (ex, ey)], fill=SKIN, width=34 * S)
    # 短袖：盖到肘上，袖口压一道深色边，手臂才有起点
    mx, my = sx + (ex - sx) * 0.38, sy + (ey - sy) * 0.38
    d.line([(sx, sy), (mx, my)], fill=NAVY_SOFT, width=48 * S)
    nx, ny = sx + (ex - sx) * 0.38, sy + (ey - sy) * 0.38
    d.line([(nx, ny), (nx + (ex - nx) * 0.10, ny + (ey - ny) * 0.10)],
           fill=NAVY, width=48 * S)
    # 手掌：腕部收窄再鼓一圈，比一个孤零零的圆更像手
    r = 30 * S
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=SKIN)
    d.ellipse([ex - r, ey - r, ex + r, ey + r], outline=OUTLINE, width=LW2)


def draw_body(d, pose="idle"):
    # 脖子
    d.line([(CX, 528 * S), (CX, 618 * S)], fill=OUTLINE, width=62 * S)
    d.line([(CX, 528 * S), (CX, 618 * S)], fill=SKIN, width=50 * S)

    # 裙子：上窄下宽的梯形，裙摆是向下鼓的弧，绝不交叉
    hem_y, hem_w = 858 * S, 180 * S
    hem = []
    for i in range(21):
        t = i / 20
        hem.append((CX - hem_w + 2 * hem_w * t, hem_y - 26 * S * math.sin(math.pi * t)))
    _shape(d, [(CX - 96 * S, SHOULDER_Y + 18 * S), (CX + 96 * S, SHOULDER_Y + 18 * S)]
           + list(reversed(hem)), DRESS)
    d.line(hem, fill=NAVY_SOFT, width=13 * S, joint="curve")
    d.line([(CX - 84 * S, HIP_Y), (CX + 84 * S, HIP_Y)], fill=NAVY_SOFT, width=10 * S)

    # 水手领
    _shape(d, [(CX - 78 * S, 596 * S), (CX + 78 * S, 596 * S), (CX, 690 * S)], NAVY)
    d.line([(CX - 58 * S, 612 * S), (CX, 676 * S)], fill=WHITE, width=6 * S)
    d.line([(CX + 58 * S, 612 * S), (CX, 676 * S)], fill=WHITE, width=6 * S)

    # 胸前小鲸鱼玩偶
    bx, by = CX + 6 * S, 700 * S
    d.polygon([(bx - 46 * S, by - 6 * S), (bx - 82 * S, by - 30 * S),
               (bx - 74 * S, by + 18 * S)], fill=HAIR_MID)
    d.ellipse([bx - 44 * S, by - 34 * S, bx + 44 * S, by + 34 * S],
              fill=HAIR_TOP, outline=OUTLINE, width=LW2)
    d.ellipse([bx + 10 * S, by - 10 * S, bx + 22 * S, by + 2 * S], fill=WHITE)

    # 腿 + 深蓝袜
    for side in (-1, 1):
        lx = CX + side * 50 * S
        d.line([(lx, hem_y - 10 * S), (lx, GROUND - 34 * S)], fill=OUTLINE, width=46 * S)
        d.line([(lx, hem_y - 10 * S), (lx, GROUND - 34 * S)], fill=SKIN, width=36 * S)
        d.line([(lx, GROUND - 96 * S), (lx, GROUND - 34 * S)], fill=NAVY, width=40 * S)
        d.ellipse([lx - 34 * S, GROUND - 56 * S, lx + 34 * S, GROUND + 2 * S],
                  fill=NAVY, outline=OUTLINE, width=LW2)

    # (左臂角度, 右臂角度, 手臂长度系数)
    _ = (pose, d)  # 手臂单独一层画在头上面，见 draw_arms()


# 正角度=向外抬（左右对称），负角度=向身体内侧收（手能到下巴）
# 之前左臂用力方向反了：wave/shrug 抬的是内侧，手一直藏在头后面
POSES_ARMS = {"idle": (28, 28, 1.0), "wave": (134, 14, 1.06), "point": (96, 26, 1.10),
              "think": (-112, 10, 0.80), "happy": (100, 100, 1.10),
              "shrug": (78, 78, 0.96)}


def draw_arms(d, pose="idle"):
    """手臂画在头之后：抬起来的手不该被大脑袋和侧发吞掉。"""
    la, ra, scale = POSES_ARMS.get(pose, POSES_ARMS["idle"])
    _arm(d, -1, la, length=172 * S * scale)
    _arm(d, +1, ra, length=172 * S * scale)


def draw_head(d, pose="idle", mouth="closed", eyes="open"):
    hx, hy = HEAD_C
    # 后发
    d.ellipse([hx - HEAD_R - 24 * S, hy - HEAD_R - 26 * S,
               hx + HEAD_R + 24 * S, hy + HEAD_R + 34 * S],
              fill=HAIR_TOP, outline=OUTLINE, width=LW)

    # 侧发：从耳侧垂到胸口，末端收尖
    for side in (-1, 1):
        x = hx + side * (HEAD_R - 10 * S)
        pts = [(x, hy - 56 * S),
               (x + side * 44 * S, hy + 40 * S),
               (x + side * 54 * S, hy + 168 * S),
               (x + side * 18 * S, hy + 196 * S),
               (x - side * 16 * S, hy + 120 * S)]
        _shape(d, pts, HAIR_MID)

    # 脸
    d.ellipse([hx - HEAD_R, hy - HEAD_R + 18 * S, hx + HEAD_R, hy + HEAD_R + 6 * S],
              fill=SKIN, outline=OUTLINE, width=LW)

    # 刘海：外缘贴头形圆弧，内缘是起伏的碎发
    outer = _arc(hx, hy, HEAD_R + 10 * S, 196, 344, 30)
    fringe = []
    for i in range(21):
        t = i / 20
        x = (hx + HEAD_R + 4 * S) - (2 * HEAD_R + 8 * S) * t
        y = hy - 34 * S + 34 * S * math.sin(math.pi * 3 * t) - 26 * S * abs(0.5 - t)
        fringe.append((x, y))
    _shape(d, outer + fringe, HAIR_TOP)

    # 发丝高光
    for side in (-1, 1):
        d.line([(hx + side * 96 * S, hy - 138 * S), (hx + side * 58 * S, hy - 96 * S)],
               fill=HAIR_BOT, width=11 * S)

    # 鲸尾发饰
    for side in (-1, 1):
        ax = hx + side * (HEAD_R - 16 * S)
        _shape(d, [(ax, hy - 96 * S),
                   (ax + side * 86 * S, hy - 156 * S),
                   (ax + side * 104 * S, hy - 92 * S),
                   (ax + side * 26 * S, hy - 74 * S)], HAIR_BOT, width=LW2)

    # 眼睛
    ey = hy + 40 * S
    for side in (-1, 1):
        ex = hx + side * 82 * S
        if eyes == "closed":
            d.arc([ex - 38 * S, ey - 28 * S, ex + 38 * S, ey + 26 * S], 200, 340,
                  fill=INK, width=11 * S)
            continue
        rx, ry = 40 * S, (46 * S if eyes != "wide" else 54 * S)
        d.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=INK)
        d.ellipse([ex - rx + 8 * S, ey - ry + 10 * S, ex + rx - 10 * S, ey + ry - 16 * S],
                  fill=(43, 96, 168, 255))
        d.ellipse([ex - 18 * S, ey - 26 * S, ex + 4 * S, ey], fill=WHITE)
        d.ellipse([ex + 8 * S, ey + 12 * S, ex + 22 * S, ey + 26 * S],
                  fill=(255, 255, 255, 200))
        d.arc([ex - rx, ey - ry - 2 * S, ex + rx, ey + ry], 196, 344,
              fill=OUTLINE, width=9 * S)

    # 腮红
    for side in (-1, 1):
        bx = hx + side * 128 * S
        d.ellipse([bx - 32 * S, ey + 22 * S, bx + 32 * S, ey + 56 * S], fill=BLUSH)

    # 嘴
    my = ey + 74 * S
    if mouth == "closed":
        d.arc([CX - 24 * S, my - 22 * S, CX + 24 * S, my + 14 * S], 20, 160,
              fill=INK, width=7 * S)
    elif mouth == "half":
        d.ellipse([CX - 20 * S, my - 12 * S, CX + 20 * S, my + 16 * S],
                  fill=(126, 44, 64, 255))
    elif mouth == "open":
        d.ellipse([CX - 27 * S, my - 16 * S, CX + 27 * S, my + 34 * S],
                  fill=(126, 44, 64, 255))
        d.ellipse([CX - 17 * S, my + 10 * S, CX + 17 * S, my + 30 * S],
                  fill=(232, 122, 138, 255))
    elif mouth == "round":
        d.ellipse([CX - 14 * S, my - 12 * S, CX + 14 * S, my + 18 * S],
                  fill=(126, 44, 64, 255))


_TILT = {"idle": 0.0, "wave": -6.0, "point": 4.0, "think": -5.0,
         "happy": 6.0, "shrug": 1.5}

# 真立绘落地目录：有同名 PNG 就用真图，没有就地退回程序化占位图。
# 生成方的文件名约定见 deploy/presenter/ART-BRIEF-whale-girl.md
ASSET_ROOT = Path(os.environ.get("WHALE_ASSET_ROOT")
                   or "~/CreatorStudio/state/characters/whale").expanduser()
_ASSET_CACHE: dict[tuple, Image.Image] = {}
# 素材包里 pose_x_open.png 与 pose_x_closed.png 是同一张图（嘴都是张的），
# 直接按文件名当口型用，人物说话时嘴一动不动。这里记住哪些 pose 是「真差分」，
# 假差分就用 mouth_art 现场补出半开/闭/圆三种嘴。
_ART_MOUTH: dict[str, str] = {}


def _art_mouth_state(path: Path) -> str:
    """这张图真实的口型：文件名说 closed 但和 open 逐字节相同，就按 open 算。"""
    name = path.stem
    state = "open"
    for st in ("open", "closed", "half", "round"):
        if name.endswith("_" + st):
            state = st
            break
    cached = _ART_MOUTH.get(str(path))
    if cached is not None:
        return cached
    real = state
    if state in ("open", "closed"):
        sib_name = name[: -len(state) - 1] + ("_closed" if state == "open" else "_open") + ".png"
        sib = path.with_name(sib_name)
        try:
            if sib.is_file() and sib.stat().st_size == path.stat().st_size:
                if sib.read_bytes() == path.read_bytes():
                    # 两张一样：嘴只有张着那一份，closed 版不存在
                    real = "open"
        except Exception:  # noqa: BLE001
            real = state
    _ART_MOUTH[str(path)] = real
    return real


def _key_green(img: Image.Image) -> Image.Image:
    """绿幕转透明：抠掉 #00FF00，顺带压掉边缘溢出的绿。"""
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and g > 90 and g > r * 1.3 and g > b * 1.3:
                px[x, y] = (r, g, b, 0)
            elif a and g > max(r, b):
                # 半透明边缘：把绿压到其余通道的最大值，避免描边泛绿
                px[x, y] = (r, max(r, b), b, a)
    return img


OUTFITS_ROOT = ASSET_ROOT / "outfits"
DEFAULT_OUTFIT = "01_sailor"
OUTFIT_LABELS = {
    "01_sailor": "水手裙",
    "02_maid": "经典女仆装",
    "03_casual": "鲸鱼卫衣休闲装",
    "04_winter": "冬日斗篷装",
    "05_princess": "深海公主礼服",
}


def list_outfits() -> list[str]:
    """平台上能直接换的服装：outfits/ 下每套，没分目录时退回根目录那套。"""
    names: list[str] = []
    if OUTFITS_ROOT.is_dir():
        names = sorted(p.name for p in OUTFITS_ROOT.iterdir() if p.is_dir())
    if DEFAULT_OUTFIT not in names:
        names.insert(0, DEFAULT_OUTFIT)
    return names


def outfit_root(outfit: str | None = None) -> Path:
    """某套服装的素材目录；没指定或那套没装，就用根目录（默认水手裙）。"""
    name = str(outfit or os.environ.get("WHALE_OUTFIT") or "").strip()
    if name:
        cand = OUTFITS_ROOT / name
        if cand.is_dir():
            return cand
    return ASSET_ROOT


def _asset_candidates(root: Path, pose: str, mouth: str, eyes: str,
                      allow_body: bool = False) -> list[Path]:
    """同一套服装内部按「动作说话版 → 动作闭口版 → 该套站姿」降级。

    只在本套里找：从女仆装降级到水手裙的图，人会突然换衣服。
    """
    states = [mouth]
    if mouth in ("half", "open", "round"):
        states.append("open")
    states.append("closed")
    uniq: list[str] = []
    for st in states:
        if st not in uniq:
            uniq.append(st)
    cands: list[Path] = []
    if allow_body:
        # 分离模式：body 只有身体，脸靠表情层叠上去。没有表情层就不用它，
        # 否则会出一张没脸的立绘。
        cands.append(root / f"body/pose_{pose or 'idle'}.png")
    if pose in ("", "idle", "neutral"):
        for st in uniq:
            if eyes == "closed":
                cands.append(root / ("base/03_base_blink_closed.png"
                                     if st == "closed" else "base/04_base_blink_open.png"))
            cands.append(root / f"base/01_base_{st}.png")
        cands.append(root / "base/01_base_closed.png")
        cands.append(root / "base/01_base.png")
    else:
        for st in uniq:
            cands.append(root / f"poses/pose_{pose}_{st}.png")
        for st in uniq:
            cands.append(root / f"poses/pose_{pose}_open.png")
        cands.append(root / f"poses/pose_{pose}.png")
        cands.append(root / "base/01_base_closed.png")
    return cands


def _expression_png(outfit: str | None, expression: str | None) -> Image.Image | None:
    """表情层是独立一张，和身体同尺寸贴上去，不重画整个身体。

    服装只提供身体，表情跨服装共用：换装时才不用把表情重画一遍。
    """
    name = str(expression or "").strip()
    if not name:
        return None
    root = outfit_root(outfit)
    if not root.is_dir():
        return None
    split = root.parent.name == "outfits"
    croot = root.parent.parent if split else root
    try:
        import character_assets as CA
        path = CA.expression_path(croot, root.name if split else None, name)
        if path is None:
            return None
        key = ("expr", str(path), path.stat().st_mtime)
        hit = _ASSET_CACHE.get(key)
        if hit is None:
            # 表情层本来就是透明的，不再抠绿：别把画里的绿色高光一起抠掉
            hit = Image.open(path).convert("RGBA").resize((DES, DES), Image.LANCZOS)
            _ASSET_CACHE[key] = hit
        return hit
    except Exception:  # noqa: BLE001 - 表情缺了就当没有，别拦着出片
        return None


def _asset_png(pose: str, mouth: str, eyes: str, outfit: str | None = None,
               expression: str | None = None) -> Image.Image | None:
    """按「服装 + 动作 + 口型」挑一张差分；眼睛闭只对站姿有专门的一张。"""
    root = outfit_root(outfit)
    if not root.is_dir():
        return None
    layer = _expression_png(outfit, expression)
    # 分离模式的 body 图优先于烘焙图，哪怕这次没叠表情：退回程序化占位图是换了个
    # 角色，远不如「这套衣服还没画脸」来得诚实。
    want = str(mouth or "closed")
    for path in _asset_candidates(root, pose, mouth, eyes, allow_body=bool(root.joinpath("body").is_dir())):
        if not path.is_file():
            continue
        if want != "open" and not path.stem.endswith("_open"):
            # 闭口稿是真的，但它的身体阴影/描边和张口稿差了一点点：一说话就
            # 在两张"同一姿势"之间来回切，脖子和头发会跟着闪。口型一律在张口
            # 那张上就地改，身体像素整片一帧不换。
            alt = path.with_name(path.stem[: -len(path.stem.split("_")[-1]) - 1] + "_open.png")
            if alt.is_file():
                path = alt
        try:
            stamp = path.stat().st_mtime
            key = (str(path), stamp, str(expression or ""), want)
            hit = _ASSET_CACHE.get(key)
            if hit is None:
                hit = _key_green(Image.open(path).convert("RGBA"))
                if want != "open":
                    try:
                        import mouth_art
                        hit = mouth_art.apply(hit, want, key=(str(path), stamp))
                    except Exception:  # noqa: BLE001 - 补嘴失败就退回原图
                        pass
                hit = hit.resize((DES, DES), Image.LANCZOS)
                if layer is not None:
                    hit = hit.copy()
                    # 表情图与身体同为 DES×DES 画布，锚点已画在图内，直接整层叠
                    hit.alpha_composite(layer)
                _ASSET_CACHE[key] = hit
            return hit.copy()
        except Exception:  # noqa: BLE001 - 真图坏了不该让整条片挂掉
            continue
    return None


def render(pose="idle", mouth="closed", eyes="open", sway=0.0, tilt=None,
           outfit: str | None = None, expression: str | None = None) -> Image.Image:
    real = _asset_png(pose, mouth, eyes, outfit, expression)
    if real is not None:
        return real
    img = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw_tail(img, sway)
    d = ImageDraw.Draw(img)
    draw_body(d, pose)
    head = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw_head(ImageDraw.Draw(head), pose, mouth, eyes)
    ang = _TILT.get(pose, 0.0) if tilt is None else tilt
    if ang:
        head = head.rotate(ang, resample=Image.BICUBIC, center=HEAD_C)
    img.alpha_composite(head)
    draw_arms(d, pose)
    return img.resize((DES, DES), Image.LANCZOS)


POSES = {
    "idle": dict(pose="idle", mouth="closed", eyes="open"),
    "wave": dict(pose="wave", mouth="half", eyes="open"),
    "point": dict(pose="point", mouth="open", eyes="open"),
    "think": dict(pose="think", mouth="closed", eyes="open"),
    "happy": dict(pose="happy", mouth="open", eyes="closed"),
    "shrug": dict(pose="shrug", mouth="round", eyes="open"),
    "surprise": dict(pose="surprise", mouth="open", eyes="open"),
    "pout": dict(pose="pout", mouth="closed", eyes="open"),
    "shy": dict(pose="shy", mouth="half", eyes="open"),
    "approve": dict(pose="approve", mouth="open", eyes="open"),
}


def export_all(out: Path) -> dict:
    out = Path(out).expanduser()
    for sub in ("base", "mouth", "eyes", "poses", "shots"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    base = render()
    base.save(out / "base/01_base.png")
    base.save(out / "mouth/mouth_closed.png")
    base.save(out / "eyes/eyes_open.png")
    render(eyes="closed").save(out / "eyes/eyes_closed.png")
    for name in ("half", "open", "round"):
        render(mouth=name).save(out / f"mouth/mouth_{name}.png")
    for name, kw in POSES.items():
        render(**kw).save(out / f"poses/pose_{name}.png")
    render(pose="point", mouth="open").save(out / "poses/pose_intro_open.png")
    render(pose="wave", mouth="open").save(out / "poses/pose_wave_open.png")
    render(pose="think", mouth="half").save(out / "poses/pose_think_open.png")
    shot = render(pose="wave", mouth="open").crop((80, 120, 440, 480))
    shot.resize((720, 720), Image.LANCZOS).save(out / "shots/shot_closeup.png")
    return {"root": str(out), "count": 14}


def contact_sheet(path: Path) -> Path:
    cell = 512
    sheet = Image.new("RGBA", (cell * 3, cell * 2), (18, 22, 42, 255))
    for i, (name, kw) in enumerate(POSES.items()):
        im = render(**kw).resize((cell, cell), Image.LANCZOS)
        sheet.alpha_composite(im, ((i % 3) * cell, (i // 3) * cell))
    path = Path(path)
    sheet.convert("RGB").save(path)
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="~/CreatorStudio/state/characters/whale")
    ap.add_argument("--sheet", default="")
    a = ap.parse_args()
    print("sheet " + str(contact_sheet(a.sheet)) if a.sheet else export_all(Path(a.out)))
