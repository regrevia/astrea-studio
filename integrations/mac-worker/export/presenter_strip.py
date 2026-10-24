"""presenter_strip.py — C 版：去卡片、去小标，真实界面截图当主视觉。

和 B 版的差别只有版式和文案密度：配音、字幕节奏、主角位置全部沿用 B 版成品
spec（presenter_beat.py 产出的 spec.json），因此三条样片可以逐帧对齐比较。

版式：
  · 左侧只有一行大标题（可带一行小副标），没有圆角卡片、没有章节 chip、没有页码；
  · 右侧是真实界面截图（GitHub 仓库页），竖向出血，入场慢推 + 极缓漂移；
  · 字幕去掉底框，只留带投影的白字。

用法：
  python3 presenter_strip.py --spec /tmp/ab/B2/spec.json --content /tmp/ab/envoy.json \
      --shots /tmp/ab/shots --plan plan.json --outdir /tmp/ab/C --render /tmp/ab/C-master.mp4
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import presenter_beat as B
import presenter_style as P
import timeline_render as T

SHOT_H = 780          # 截图区高度
SHOT_RIGHT = 88       # 距右边
SHOT_TOP = 150
HEAD_X = 104
HEAD_Y = 296
HEAD_W = 620
SUB_Y = 296 + 140
CAP_W = 1380
CAP_Y = 890


# ------------------------------------------------------------------ 工具
def _even(n: int) -> int:
    return int(n) if int(n) % 2 == 0 else int(n) - 1


def _size(path: Path) -> tuple:
    from PIL import Image as _I
    with _I.open(path) as im:
        return im.size


# ------------------------------------------------------------------ 文字
def _headline(path: Path, text: str, width: int = HEAD_W) -> Path:
    """一行主标题：透明底、白字、最多两行，不带任何容器。"""
    from PIL import Image, ImageDraw
    f = P._font(90)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, str(text), f, width)[:2] or [""]
    lh = int(90 * 1.26)
    img = Image.new("RGBA", (_even(width + 24), lh * len(lines) + 24),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((6, 6 + i * lh), line, font=f, fill=(244, 248, 255, 255))
    img.save(path)
    return path, len(lines), lh


def _subline(path: Path, text: str, width: int = HEAD_W) -> Path:
    """一行副标：小字号、灰蓝，最多两行。"""
    from PIL import Image, ImageDraw
    f = P._font(34)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, str(text), f, width)[:2] or [""]
    lh = 52
    img = Image.new("RGBA", (_even(width + 16), lh * len(lines) + 16),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((4, 4 + i * lh), line, font=f, fill=P.MUTED + (255,))
    img.save(path)
    return path


def _caption(path: Path, text: str, width: int = CAP_W) -> Path:
    """无底框字幕：白字 + 轻投影，最多两行，居中。"""
    from PIL import Image, ImageDraw
    f = P._font(38)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines, cur = [], ""
    for ch in " ".join(str(text).split()):
        if cur and probe.textlength(cur + ch, font=f) > width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = lines[:2] or [""]
    lh = 56
    img = Image.new("RGBA", (_even(width + 60), lh * len(lines) + 40),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        tw = probe.textlength(line, font=f)
        x = (img.width - tw) / 2
        y = 12 + i * lh
        for dx, dy in ((2, 2), (-2, 2), (2, -2), (-2, -2)):
            d.text((x + dx, y + dy), line, font=f, fill=(0, 0, 0, 190))
        d.text((x, y), line, font=f, fill=(248, 250, 255, 255))
    img.save(path)
    return path


# ------------------------------------------------------------------ 主流程
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

    # 1) 从 B 版 spec 里切出每页时间边界 / 主角层 / 配音层
    cards = sorted(
        [o for o in base["overlay"]
         if abs((o.get("width") or 0) - P.PAGE["w"]) < 1],
        key=lambda o: o["start"])
    hosts = [o for o in base["overlay"]
             if abs((o.get("width") or 0) - 820) < 1]
    narr = sorted([a for a in base["audio"] if a.get("role") == "narration"],
                  key=lambda a: a["start"])
    bgm = [a for a in base["audio"] if a.get("role") == "bgm"]

    # 2) 句子文本按页重新切分（与 B 版同一切分规则，顺次对齐）
    pages = content.get("pages") or []
    sentences = [B.split_sentences(str(p.get("narration") or "")) for p in pages]

    overlays = []
    for i, card in enumerate(cards):
        item = plan.get(str(i)) or {}
        st, en = card["start"], card["end"]
        sub_y = SUB_Y
        if item.get("headline"):
            hp, nlines, lh = _headline(assets / f"head{i:02d}.png",
                                       item["headline"])
            hw, _ = _size(hp)
            sub_y = HEAD_Y + nlines * lh + 26
            overlays.append({
                "src": str(hp), "start": st, "end": en,
                "x": HEAD_X, "y": HEAD_Y, "width": hw,
                "fade": 0.22,
                "enter": {"from": "bottom", "duration": 0.7, "ease": "out-cubic"},
                "exit": {"to": "left", "duration": 0.3, "ease": "in-cubic"},
            })
        if item.get("sub"):
            sp = _subline(assets / f"sub{i:02d}.png", item["sub"])
            sw, _ = _size(sp)
            overlays.append({
                "src": str(sp), "start": st + 0.25, "end": en,
                "x": HEAD_X, "y": sub_y, "width": sw,
                "fade": 0.3,
                "exit": {"to": "left", "duration": 0.3, "ease": "in-cubic"},
            })
        shot = item.get("shot")
        if shot:
            src = shots / f"{shot}.png"
            if not src.is_file():
                raise SystemExit(f"缺截图: {src}")
            iw, ih = _size(src)
            max_h = int(item.get("max_h") or SHOT_H)
            max_w = int(item.get("max_w") or 1000)
            k = min(max_w / iw, max_h / ih)      # 等比缩进框里，不裁切
            sw, sh = _even(iw * k), _even(ih * k)
            overlays.append({
                "src": str(src), "start": round(st + 0.1, 3), "end": en,
                "x": W - sw - SHOT_RIGHT, "y": SHOT_TOP + (max_h - sh) // 2,
                "width": sw,
                "fade": 0.35,
                "enter": {"from": "right", "duration": 1.5, "ease": "out-cubic"},
                "exit": {"to": "right", "duration": 0.45, "ease": "in-cubic"},
                "motion": {"type": "sway", "amplitude": 5.0, "period": 11.0},
            })

    # 3) 字幕：按页把句子顺次配给该页的配音段，去掉底框
    k = 0
    for i, card in enumerate(cards):
        st, en = card["start"], card["end"]
        mine = [a for a in narr if st - 0.2 <= a["start"] < en]
        texts = sentences[i] if i < len(sentences) else []
        for j, a in enumerate(mine):
            text = texts[j] if j < len(texts) else ""
            if not text:
                continue
            cap = _caption(assets / f"cap_{i:02d}_{j:02d}.png", text)
            cw, _ = _size(cap)
            overlays.append({
                "src": str(cap), "start": a["start"],
                "end": round(_dur(a["src"]) + a["start"], 3),
                "x": (W - cw) // 2, "y": CAP_Y, "width": cw, "fade": 0.08,
            })
            k += 1

    # 4) 主角沿用 B 版位置与动画
    overlays.extend(hosts)

    return {
        "canvas": base["canvas"],
        "video": base["video"],
        "overlay": overlays,
        "text": [],
        "audio": narr + bgm,
        "font": base["font"],
        "meta": {**base["meta"], "style": "presenter-strip",
                 "duration": total},
    }


def _dur(path) -> float:
    import slides_render as S
    return float(S._dur(Path(path)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C 版：真实界面 + 去卡片")
    ap.add_argument("--spec", required=True, help="B 版 spec.json")
    ap.add_argument("--content", required=True, help="同一份内容 JSON")
    ap.add_argument("--shots", required=True)
    ap.add_argument("--plan", required=True, help="页面文案计划 JSON")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--spec-out")
    ap.add_argument("--render")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    try:
        spec = build(Path(args.spec), Path(args.content), Path(args.shots),
                     plan, Path(args.outdir))
        sp = Path(args.spec_out) if args.spec_out else Path(args.outdir) / "spec.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"spec: {sp} overlays={len(spec['overlay'])} "
              f"audio={len(spec['audio'])}")
        if args.render:
            ev = T.render(T.load_spec(sp), args.render,
                          keep_workdir=args.keep_work)
            print(f"master: {ev['output']} {ev['bytes']}B {ev['duration']}s")
        return 0
    finally:
        pass


if __name__ == "__main__":
    sys.exit(main())
