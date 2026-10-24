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


def _arm(d, side, angle_deg, length=138 * S):
    """side=-1 左臂 / +1 右臂；angle 0=垂下，90=平举。"""
    sx, sy = CX + side * 84 * S, SHOULDER_Y + 8 * S
    a = math.radians(angle_deg)
    ex = sx + side * math.sin(a) * length
    ey = sy + math.cos(a) * length
    d.line([(sx, sy), (ex, ey)], fill=OUTLINE, width=42 * S)
    d.line([(sx, sy), (ex, ey)], fill=SKIN, width=32 * S)
    # 短袖：只覆盖靠近肩膀的一段
    mx, my = sx + (ex - sx) * 0.42, sy + (ey - sy) * 0.42
    d.line([(sx, sy), (mx, my)], fill=NAVY_SOFT, width=48 * S)
    r = 26 * S
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=SKIN)
    d.ellipse([ex - r, ey - r, ex + r, ey + r], outline=OUTLINE, width=LW2)


def draw_body(d, pose="idle"):
    # 脖子
    d.line([(CX, 528 * S), (CX, 618 * S)], fill=OUTLINE, width=62 * S)
    d.line([(CX, 528 * S), (CX, 618 * S)], fill=SKIN, width=50 * S)

    # 裙子：上窄下宽的梯形，裙摆是向下鼓的弧，绝不交叉
    hem_y, hem_w = 858 * S, 196 * S
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
    poses = {"idle": (14, 14, 1.0), "wave": (-146, 14, 1.08), "point": (96, 16, 1.10),
             "think": (-58, 10, 0.72), "happy": (-152, 152, 1.06),
             "shrug": (-108, 108, 0.92)}
    la, ra, scale = poses.get(pose, poses["idle"])
    _arm(d, -1, la, length=138 * S * scale)
    _arm(d, +1, ra, length=138 * S * scale)


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


def render(pose="idle", mouth="closed", eyes="open", sway=0.0, tilt=None) -> Image.Image:
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
    return img.resize((DES, DES), Image.LANCZOS)


POSES = {
    "idle": dict(pose="idle", mouth="closed", eyes="open"),
    "wave": dict(pose="wave", mouth="half", eyes="open"),
    "point": dict(pose="point", mouth="open", eyes="open"),
    "think": dict(pose="think", mouth="closed", eyes="open"),
    "happy": dict(pose="happy", mouth="open", eyes="closed"),
    "shrug": dict(pose="shrug", mouth="round", eyes="open"),
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
