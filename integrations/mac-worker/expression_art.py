"""表情层素材：画在透明画布上的情绪符号，和身体分开存。

服装只有身体，表情符号跨服装共用：换衣服不用重画表情，加一套新表情也不用
碰任何一张立绘。坐标按 1024 画布对齐（脸中心约 x=505、眼睛 y≈400、脸颊
y≈428），改画布尺寸要一起改这里。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

DES = 1024
FACE_X = 505
EYE_Y = 400
CHEEK_Y = 428
CHEEK_X_L = 443
CHEEK_X_R = 567
BLUSH = (243, 152, 172)
SWEAT = (126, 210, 255)
SPARK = (255, 240, 176)
ANGER = (240, 118, 92)
TEAR = (150, 205, 255)
LINE = (58, 62, 96)


def _blank() -> Image.Image:
    return Image.new("RGBA", (DES, DES), (0, 0, 0, 0))


def blush() -> Image.Image:
    """害羞/被夸：两团柔和腮红压在脸颊上，不加线条，免得看成脏点。"""
    img = _blank()
    d = ImageDraw.Draw(img)
    for cx in (CHEEK_X_L, CHEEK_X_R):
        d.ellipse((cx - 40, CHEEK_Y - 18, cx + 40, CHEEK_Y + 18), fill=BLUSH + (120,))
        d.ellipse((cx - 26, CHEEK_Y - 11, cx + 26, CHEEK_Y + 11), fill=BLUSH + (70,))
    return img


def sweat() -> Image.Image:
    """紧张/惊讶：头右侧外沿一颗汗，落在头发轮廓外，不被刘海吞掉。"""
    img = _blank()
    d = ImageDraw.Draw(img)
    cx, cy, r = 716, 236, 26
    d.polygon([(cx, cy - r * 2.0), (cx + r, cy + r * 0.55), (cx, cy + r * 1.6),
               (cx - r, cy + r * 0.55)], fill=SWEAT + (225,))
    d.ellipse((cx - r * 0.40, cy - r * 0.05, cx + r * 0.10, cy + r * 0.6),
              fill=(255, 255, 255, 200))
    return img


def sparkle() -> Image.Image:
    """开心/推荐成立：头两侧三颗四角星，最大的在左上。"""
    img = _blank()
    d = ImageDraw.Draw(img)
    for cx, cy, s in ((286, 196, 36), (648, 150, 26), (250, 320, 18)):
        pts = []
        for i in range(8):
            ang = math.pi / 4 * i - math.pi / 2
            rr = s if i % 2 == 0 else s * 0.32
            pts.append((cx + math.cos(ang) * rr, cy + math.sin(ang) * rr))
        d.polygon(pts, fill=SPARK + (240,))
        d.ellipse((cx - s * 0.12, cy - s * 0.12, cx + s * 0.12, cy + s * 0.12),
                  fill=(255, 255, 255, 220))
    return img


def anger() -> Image.Image:
    """不服/被冒犯：右上角一颗青筋符号，画在头发外面。"""
    img = _blank()
    d = ImageDraw.Draw(img)
    cx, cy = 702, 194
    for dx in (-13, 0, 13):
        d.line((cx + dx - 7, cy - 15, cx + dx + 7, cy + 15), fill=ANGER + (235,), width=6)
    d.line((cx - 16, cy - 3, cx + 16, cy - 3), fill=ANGER + (235,), width=6)
    return img


def tears() -> Image.Image:
    """替人着急：眼角往下挂两滴泪，落在可见的颊面上。"""
    img = _blank()
    d = ImageDraw.Draw(img)
    for cx in (CHEEK_X_L - 6, CHEEK_X_R + 4):
        d.polygon([(cx, EYE_Y + 42), (cx + 13, EYE_Y + 80), (cx, EYE_Y + 94),
                   (cx - 13, EYE_Y + 80)], fill=TEAR + (215,))
        d.ellipse((cx - 4, EYE_Y + 58, cx + 3, EYE_Y + 70), fill=(255, 255, 255, 210))
    return img


LAYERS = {"blush": blush, "sweat": sweat, "sparkle": sparkle,
          "anger": anger, "tears": tears}


def write_all(out: Path) -> dict:
    out = Path(out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    done = {}
    for name, fn in LAYERS.items():
        img = fn()
        path = out / f"{name}.png"
        img.save(path)
        done[name] = {"path": str(path), "bytes": path.stat().st_size}
    return done


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="~/CreatorStudio/state/characters/whale/expressions")
    ap.add_argument("--sheet", default="")
    a = ap.parse_args()
    info = write_all(Path(a.out))
    print(json.dumps(info, ensure_ascii=False, indent=2))
