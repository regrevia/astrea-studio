"""presenter_card.py — D 版：README + 图标当主视觉，逐页换版式。

C 版反馈：截图位置乱、边缘硬、图文不符；一版到底又太死。三条原则：

  · 主视觉只有「仓库图标 + README 对应段落」这一种素材，不掺文件列表、代码页；
  · 每页换一次构图（右侧卡 / 上标题下通栏 / 满幅），位置尺寸都在网格上，不飘；
  · 圆角 26px、细描边、柔投影；段落尾部空白自动裁掉，短段落不空一大片。

用法：
  python3 presenter_card.py --spec /tmp/ab/B2/spec.json --content /tmp/ab/envoy-card.json \\
      --shots /tmp/ab/readme --plan /tmp/ab/card-plan.json --outdir /tmp/ab/D \\
      --render /tmp/ab/D-master.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import presenter_beat as B
import presenter_style as P
import timeline_render as T

CANVAS_W = 1920
CARD_ZONE = (76, 1340)      # 卡片可用横向区间，右侧留给主角
# 主角素材已裁到人物包围盒（216x270），这里 width 直接是人物宽度
HOST = dict(width=480, x=1364, y=296, motion={"type": "bob", "amplitude": 6, "period": 3.2})
PAD = 34
HEADER_H = 100
GAP = 18
BOTTOM_LIMIT = 872          # 卡片最低不能压到字幕
ICON = 64

# 主角片段默认读仓库内 assets/hostcrop（已裁到人物包围盒 216x270 的 640x360 透明
# webm，包围盒 x 213-427 / y 64-332），可用 CREATORSTUDIO_HOST_CROP_DIR 覆盖。
# 旧版写死 /tmp/ab/hostcrop，/tmp 一被清理渲染就直接 FileNotFoundError。
HOST_CROP_DIR = Path(
    os.environ.get("CREATORSTUDIO_HOST_CROP_DIR")
    or Path(__file__).resolve().parent / "assets" / "hostcrop"
)
CAP_W = 1224
CAP_Y = 916

# 每页可选三种构图
LAYOUTS = {
    "wide":  dict(head_x=76, head_w=1256, head_font=64, head_y=92,
                  card_x=None, card_top=None, card_w=1256, body_max=540, radius=28),
    "top":   dict(head_x=76, head_w=1256, head_font=58, head_y=132,
                  card_x=None, card_top=None, card_w=1176, body_max=580, radius=26),
    "side":  dict(head_x=1368, head_w=500, head_font=46, head_y=100,   # 标题放在主角正上方
                  card_x=None, card_top=None, card_w=1256, body_max=520, radius=26),

}


def _even(n) -> int:
    n = int(round(n))
    return n if n % 2 == 0 else n - 1


def _size(path: Path) -> tuple:
    from PIL import Image as _I
    with _I.open(path) as im:
        return im.size


# ---------------------------------------------------------------- 文字层
def _headline(path: Path, text: str, width: int, font_size: int) -> tuple:
    from PIL import Image, ImageDraw
    f = P._font(font_size)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, str(text), f, width)[:2] or [""]
    lh = int(font_size * 1.26)
    img = Image.new("RGBA", (_even(width + 18), lh * len(lines) + 18), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((4, 4 + i * lh), line, font=f, fill=(246, 249, 255, 255))
    img.save(path)
    return path, len(lines), lh


def _subline(path: Path, text: str, width: int) -> Path:
    from PIL import Image, ImageDraw
    f = P._font(28)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, str(text), f, width)[:2] or [""]
    lh = 42
    img = Image.new("RGBA", (_even(width + 12), lh * len(lines) + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((2, 2 + i * lh), line, font=f, fill=P.MUTED + (255,))
    img.save(path)
    return path


def _tokens(text: str):
    out, cur = [], ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in ".-_/+:'\"()[]<>#@&%$!?=,;:·"):
            cur += ch
        else:
            if cur:
                out.append(cur)
                cur = ""
            out.append(ch)
    if cur:
        out.append(cur)
    return out


def _caption(path: Path, text: str) -> Path:
    from PIL import Image, ImageDraw
    f = P._font(36)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, cur = [], ""
    for tok in _tokens(" ".join(str(text).split())):
        cand = cur + tok
        if cur and probe.textlength(cand, font=f) > CAP_W:
            lines.append(cur.rstrip())
            cur = "" if tok == " " else tok
        else:
            cur = cand
    if cur.strip():
        lines.append(cur.rstrip())
    lines = lines[:2] or [""]
    lh = 54
    img = Image.new("RGBA", (_even(CAP_W + 60), lh * len(lines) + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        tw = probe.textlength(line, font=f)
        x = (img.width - tw) / 2
        y = 12 + i * lh
        for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
            d.text((x + dx, y + dy), line, font=f, fill=(0, 0, 0, 200))
        d.text((x, y), line, font=f, fill=(248, 250, 255, 255))
    img.save(path)
    return path


# ---------------------------------------------------------------- 圆角卡片
def _rounded_mask(size, radius: int):
    from PIL import Image, ImageDraw
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1],
                                        radius=radius, fill=255)
    return m


def _card(path: Path, shot: Path, icon: Path | None, label: str, meta: str,
          crop: dict | None, card_w: int, body_max: int, radius: int) -> dict:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    body_w = card_w - PAD * 2
    ins = Image.open(shot).convert("RGB")
    iw, ih = ins.size
    cx = int((crop or {}).get("x", 0))
    cy = int((crop or {}).get("y", 0))
    cw = int((crop or {}).get("w", iw))
    ch = int((crop or {}).get("h", ih))
    cw, ch = min(cw, iw - cx), min(ch, ih - cy)
    body = ins.crop((cx, cy, cx + cw, cy + ch))

    # 裁掉尾部空白行（短段落不留大白边）
    bg = Image.new("RGB", body.size, (255, 255, 255))
    diff = ImageChops.difference(body, bg).convert("L")
    bbox = diff.point(lambda v: 255 if v > 12 else 0).getbbox()
    if bbox:
        body = body.crop((0, 0, body.width, min(body.height, bbox[3] + 16)))

    k = body_w / body.width
    bh = min(int(body.height * k), body_max)
    bh = max(bh, min(260, body_max))
    body = body.resize((body_w, bh), Image.LANCZOS)

    card_h = PAD + HEADER_H + GAP + bh + PAD
    pad_big = 60
    canvas = Image.new("RGBA", (card_w + pad_big * 2, card_h + pad_big * 2), (0, 0, 0, 0))
    inner = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(inner)
    d.rounded_rectangle([0, 0, card_w - 1, card_h - 1], radius=radius,
                        fill=(252, 253, 255, 255), outline=(226, 230, 240, 255), width=2)

    hy = PAD
    if icon and Path(icon).is_file():
        av = Image.open(icon).convert("RGBA").resize((ICON, ICON), Image.LANCZOS)
        av.putalpha(_rounded_mask((ICON, ICON), ICON // 2))
        inner.alpha_composite(av, (PAD, hy + (HEADER_H - ICON) // 2))
        tx = PAD + ICON + 20
    else:
        tx = PAD
    d.text((tx, hy + 12), label, font=P._font(32), fill=(22, 26, 36, 255))
    d.text((tx, hy + 56), meta, font=P._font(21), fill=(120, 130, 152, 255))
    inner.alpha_composite(body.convert("RGBA"), (PAD, PAD + HEADER_H + GAP))

    sm = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(sm).rounded_rectangle(
        [pad_big, pad_big + 16, pad_big + card_w - 1, pad_big + card_h - 1 + 16],
        radius=radius, fill=110)
    sm = sm.filter(ImageFilter.GaussianBlur(24))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(sm)
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.alpha_composite(inner, (pad_big, pad_big))
    out.save(path)
    return {"path": path, "w": out.width, "h": out.height, "pad": pad_big,
            "card_h": card_h}


# ---------------------------------------------------------------- 主流程
def build(spec_path: Path, content_path: Path, shots: Path, plan: dict,
          outdir: Path) -> dict:
    base = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    content = json.loads(Path(content_path).read_text(encoding="utf-8"))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    assets = outdir / "assets"
    assets.mkdir(exist_ok=True)
    shots = Path(shots)

    W = base["canvas"]["width"]
    total = base["meta"]["duration"]

    pages_cards = sorted([o for o in base["overlay"]
                          if abs((o.get("width") or 0) - 948) < 1],
                         key=lambda o: o["start"])
    hosts = [o for o in base["overlay"] if abs((o.get("width") or 0) - 872) < 1]
    narr = sorted([a for a in base["audio"] if a.get("role") == "narration"],
                  key=lambda a: a["start"])
    mixed = [a for a in base["audio"] if a.get("role") != "narration"]

    pages = content.get("pages") or []
    sentences = [B.split_sentences(str(p.get("narration") or "")) for p in pages]

    overlays = []
    for i, card in enumerate(pages_cards):
        item = plan.get(str(i)) or {}
        lay = LAYOUTS.get(str(item.get("layout") or "wide"), LAYOUTS["wide"])
        st, en = card["start"], card["end"]
        card_w = int(item.get("card_w") or lay["card_w"])
        radius = int(lay["radius"])
        exit_anim = {"to": "left", "duration": 0.3, "ease": "in-cubic"}
        motion = item.get("motion")
        head, sub = item.get("headline"), item.get("sub")
        shot = item.get("shot")
        centered_head = lay["head_y"] is None

        def mkcard(top, bmax):
            src = shots / f"{shot}.png"
            if not src.is_file():
                raise SystemExit(f"缺截图: {src}")
            return _card(assets / f"card{i:02d}.png", src, shots / "icon.png",
                         str(item.get("label") or "regrevia / Envoy"),
                         str(item.get("meta") or "Public · Apache-2.0"),
                         item.get("crop"), card_w, bmax, radius)

        info = None
        if centered_head:
            card_top = int(item.get("card_top") or lay["card_top"])
            body_max = min(lay["body_max"],
                           BOTTOM_LIMIT - card_top - (PAD + HEADER_H + GAP + PAD))
            if shot:
                info = mkcard(card_top, body_max)

        hp = sp = None
        nlines = lh = 0
        if head:
            hp, nlines, lh = _headline(assets / f"head{i:02d}.png", head,
                                       lay["head_w"], lay["head_font"])
        if sub:
            sp = _subline(assets / f"sub{i:02d}.png", sub, lay["head_w"])
        block_h = (nlines * lh if hp else 0) + (52 if sp else 0)

        if centered_head:
            card_h = info["card_h"] if info else (PAD + HEADER_H + GAP + PAD)
            head_pos = (lay["head_x"], card_top + max(0, (card_h - block_h) // 2))
        else:
            head_pos = (lay["head_x"], lay["head_y"])
            card_top = lay["head_y"] + (nlines * lh if hp else 0) + (44 if head else 0)
            body_max = min(lay["body_max"],
                           BOTTOM_LIMIT - card_top - (PAD + HEADER_H + GAP + PAD))

        if hp:
            hw, _ = _size(hp)
            ov = {"src": str(hp), "start": st, "end": en,
                  "x": head_pos[0], "y": head_pos[1], "width": hw, "fade": 0.22,
                  "enter": {"from": "bottom", "duration": 0.55, "ease": "out-cubic"},
                  "exit": exit_anim}
            if motion and centered_head:
                ov["motion"] = motion
            overlays.append(ov)
        if sp:
            sw, _ = _size(sp)
            overlays.append({
                "src": str(sp), "start": st + 0.18, "end": en,
                "x": head_pos[0], "y": head_pos[1] + (nlines * lh if hp else 0) + 8,
                "width": sw, "fade": 0.3, "exit": exit_anim,
            })

        if not shot:
            continue
        if info is None:
            info = mkcard(card_top, body_max)
        card_x = lay["card_x"] if lay["card_x"] is not None else \
            (CARD_ZONE[0] + CARD_ZONE[1] - card_w) // 2
        ov = {"src": str(info["path"]), "start": round(st + 0.04, 3), "end": en,
              "x": card_x - info["pad"], "y": card_top - info["pad"],
              "width": info["w"], "fade": 0.35, "exit": exit_anim}
        if motion:
            ov["motion"] = motion
        overlays.append(ov)

    # ---- 字幕
    for i, card in enumerate(pages_cards):
        st, en = card["start"], card["end"]
        mine = [a for a in narr if st - 0.2 <= a["start"] < en]
        texts = sentences[i] if i < len(sentences) else []
        for j, a in enumerate(mine):
            text = texts[j] if j < len(texts) else ""
            if not text:
                continue
            cap = _caption(assets / f"cap_{i:02d}_{j:02d}.png", text)
            cw, _ = _size(cap)
            overlays.append({"src": str(cap), "start": a["start"],
                             "end": round(_dur(a["src"]) + a["start"], 3),
                             "x": (CARD_ZONE[0] + CARD_ZONE[1] - cw) // 2, "y": CAP_Y, "width": cw, "fade": 0.08})

    # 主角：固定右侧留白区，缩小成角落讲解员，绝不压卡片正文
    for i, h in enumerate(hosts):
        o = dict(h)
        o.update(HOST)
        crop = HOST_CROP_DIR / f"h{i}.webm"
        if crop.exists():
            o["src"] = str(crop)
        overlays.append(o)

    return {
        "canvas": base["canvas"],
        "video": base["video"],
        "overlay": overlays,
        "text": [],
        "audio": narr + mixed,
        "font": base["font"],
        "meta": {**base["meta"], "style": "presenter-card", "duration": total},
    }


def _dur(path) -> float:
    import slides_render as S
    return float(S._dur(Path(path)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D 版：README 卡片 + 逐页换版式")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--shots", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--spec-out")
    ap.add_argument("--render")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    spec = build(Path(args.spec), Path(args.content), Path(args.shots),
                 plan, Path(args.outdir))
    sp = Path(args.spec_out) if args.spec_out else Path(args.outdir) / "spec.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"spec: {sp} overlays={len(spec['overlay'])} audio={len(spec['audio'])}")
    if args.render:
        ev = T.render(T.load_spec(sp), args.render, keep_workdir=args.keep_work)
        print(f"master: {ev['output']} {ev['bytes']}B {ev['duration']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
