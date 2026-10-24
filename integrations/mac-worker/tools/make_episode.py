#!/usr/bin/env python3
"""make_episode.py — 「干货卡」竖屏短片一条龙：卡片出图 → spec 组装 → 平台提交 → 等草稿。

对标抖音这类"大模型面试题"干货号：竖屏 9:16、一问三答、逐屏大字、逐句字幕、剪映音色配音，
画面交给剪映原生的转场/画面特效/VIP 字体/文字动画（会员价值），我们只出卡面底图。

用法：
  python3 integrations/mac-worker/tools/make_episode.py \
    --topic "KV Cache 是什么？为什么只存 K 和 V" \
    --hook "面试官问这句，八成在探你懂不懂推理优化" \
    --point "只存 K/V::Q 只参与一次相似度计算，算完即弃，缓存它纯属浪费显存" \
    --point "省的是计算不是显存::KV Cache 让你不用每次重算历史 token 的 K/V，速度换显存" \
    --point "显存决定并发::batch 越大 KV Cache 越占，所以长上下文贵、并发上不去"

  # 只出图 + spec，不提交平台
  ... --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", str(HOME / "CreatorStudio")))
API_BASE = os.environ.get("STUDIO_API", "http://127.0.0.1:8080/api")
TOKEN_PATH = Path(os.environ.get("STUDIO_TOKEN_FILE", str(STUDIO_HOME / "config/user.token")))

# 画布：抖音竖屏
W, H = 1080, 1920
TOP_SAFE, BOTTOM_SAFE = 240, 430  # 顶部账号栏 / 底部字幕与按钮区

FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
DEFAULT_VOICE = "sami:BV405_streaming"
TITLE_FONT = "SourceHanSansCN_Bold"       # 剪映会员字体，白名单内、可读性好
CAPTION_FONT = "SourceHanSansCN_Medium"

# 白名单里确认过的资源（见 jianying_engine._catalog），未知名字会被引擎丢掉
TRANSITIONS = ["分割", "云朵", "倒影", "冰雪结晶", "画笔擦除"]
TEXT_ANIMS = ["居中打字", "打字机_I", "向右滑动", "渐显", "弹入"]

PALETTES = [
    ((14, 18, 38), (36, 30, 78), (122, 143, 255)),    # 深蓝紫
    ((10, 26, 30), (22, 52, 58), (86, 226, 189)),     # 深青
    ((32, 14, 26), (60, 26, 52), (255, 138, 176)),    # 深玫
    ((30, 22, 10), (56, 40, 18), (255, 196, 87)),     # 深琥珀
    ((16, 16, 20), (34, 34, 42), (160, 170, 200)),    # 中性收尾
]


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



def _wrap_text(draw, text: str, font, max_w: float) -> list[str]:
    """中文逐字断行；
 保留为硬换行。"""
    out: list[str] = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        buf = ""
        for ch in para:
            probe = buf + ch
            if draw.textlength(probe, font=font) > max_w and buf:
                out.append(buf)
                buf = ch
            else:
                buf = probe
        if buf:
            out.append(buf)
    return out


def _draw_block(page, text: str, size: int, top: int, left: int, max_w: float,
                fill=(255, 255, 255, 240), spacing: float = 1.45) -> int:
    """在出图阶段排版正文：剪映字号是相对量，精排只能自己算像素。返回结束 y。"""
    from PIL import ImageDraw

    if not text:
        return top
    draw = ImageDraw.Draw(page)
    font = _font(size)
    y = top
    for ln in _wrap_text(draw, text, font, max_w):
        if ln:
            draw.text((left, y), ln, font=font, fill=fill)
        y += int(size * spacing)
    return y

def render_card(path: Path, *, kicker: str, index: str, palette_idx: int,
                progress: int, total: int, slug: str, hook: bool = False,
                body: str = "", body_top: int = 640) -> Path:
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

    if not hook:
        # 卡片高度跟着正文走，避免大片空白（"PPT 味"最主要的来源）
        measure = ImageDraw.Draw(overlay)
        body_lines = _wrap_text(measure, body, _font(46), W - 248) if body else []
        card_bottom = max(1080, body_top + int(len(body_lines) * 46 * 1.45) + 110)
        draw.rounded_rectangle((76, 500, 1004, card_bottom), radius=56,
                               fill=(255, 255, 255, 14),
                               outline=(255, 255, 255, 70), width=2)
        # 左侧强调条：标题到正文结束
        draw.rounded_rectangle((100, 560, 112, card_bottom - 90), radius=6,
                               fill=(*accent, 235))
        # 编号做成右上角小徽章：幽灵大字会和标题抢视线
        if index:
            bf = _font(46)
            bw = draw.textlength(index, font=bf)
            draw.rounded_rectangle((950 - bw - 34, 528, 966, 600), radius=36,
                                   fill=(*accent, 40), outline=(*accent, 150), width=2)
            draw.text((958 - bw - 17, 564), index, font=bf, fill=(*accent, 245), anchor="mm")
    else:
        # 开场：大引号 + 斜切色块
        draw.polygon([(0, 1200), (W, 980), (W, 1320), (0, 1540)], fill=(*accent, 26))
        draw.rounded_rectangle((76, 500, 180, 512), radius=6, fill=(*accent, 240))

    # 顶部 kicker
    draw.text((76, TOP_SAFE + 44), kicker, font=_font(40), fill=(255, 255, 255, 205))

    # 进度点
    dot_y = H - 470
    gap, r = 26, 6
    start = W // 2 - int((total - 1) * gap / 2)
    for i in range(total):
        cx = start + i * gap
        draw.ellipse((cx - r, dot_y - r, cx + r, dot_y + r),
                     fill=(*accent, 255) if i == progress else (255, 255, 255, 60))

    # 正文要点：出图阶段精排（字号/行距/安全区都是像素级控制）
    if body:
        _draw_block(overlay, body, 46 if not hook else 44, body_top, 124, W - 248,
                    fill=(255, 255, 255, 236))
    if hook:
        # 开场卡没有边框，给钩子句一条细分隔线做锚
        draw.rounded_rectangle((124, body_top - 44, 220, body_top - 38), radius=3,
                               fill=(*accent, 200))

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
    lines: list[str] = []
    for para in text.split("\n"):
        if not para:
            lines.append("")
            continue
        buf = ""
        for ch in para:
            probe = buf + ch
            if draw.textlength(probe, font=font) > max_w and buf:
                lines.append(buf)
                buf = ch
            else:
                buf = probe
        if buf:
            lines.append(buf)
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
        _draw_centered(page, scene.get("text") or "", _font(title_px), w / 2, cy,
                       (255, 255, 255), w * 0.72)
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
    """中文按字数硬折行，剪映不会自动折得好看。"""
    text = re.sub(r"\s+", "", text)
    return "\n".join(text[i:i + limit] for i in range(0, len(text), limit))


def build_spec(args, out_dir: Path) -> dict:
    points = [p.strip() for p in args.point if p.strip()]
    if len(points) < 1:
        raise SystemExit("至少给一个 --point '标题::说明'")
    total = 2 + len(points)  # 开场 + 要点 + 收尾
    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5]+", "", args.topic)[:24] or "episode"
    scenes = []

    # 1) 开场钩子
    hook_img = render_card(out_dir / "s00.png", kicker=args.kicker, index="00",
                           palette_idx=0, progress=0, total=total, slug=slug, hook=True,
                           body=args.hook, body_top=880)
    scenes.append({
        "image": str(hook_img),
        "say": f"{args.topic}。{args.hook}",
        "text": _unwrap(args.topic, 10),
        "caption": f"{args.topic}？{args.hook}"[:30],
        "voice": args.voice,
        "native": {
            "font": TITLE_FONT, "font_size": _title_size(args.topic),
            "transform_y": 0.354, "caption_size": 8, "caption_y": -0.78,
            "text_anim_in": "居中打字",
            "transition_in": TRANSITIONS[0], "transition_duration": 0.7,
        },
    })

    # 2) 要点逐屏
    for i, raw in enumerate(points, start=1):
        title, _, body = raw.partition("::")
        title, body = title.strip(), body.strip()
        img = render_card(out_dir / f"s{i:02d}.png", kicker=args.kicker, index=f"0{i}",
                          palette_idx=i, progress=i, total=total, slug=slug,
                          body=body, body_top=760)
        scenes.append({
            "image": str(img),
            "say": f"{title}。{body}",
            "text": _unwrap(title, 11),
            "caption": (body or title)[:30],
            "voice": args.voice,
            "native": {
                "font": TITLE_FONT, "font_size": _title_size(title),
                "transform_y": 0.375, "caption_size": 8, "caption_y": -0.78,
                "text_anim_in": TEXT_ANIMS[i % len(TEXT_ANIMS)],
                "transition_in": TRANSITIONS[i % len(TRANSITIONS)],
                "transition_duration": 0.6,
                "effect": ["哈苏胶片"] if i == 1 else None,
            },
        })

    # 3) 收尾
    cta_img = render_card(out_dir / f"s{total - 1:02d}.png", kicker=args.kicker,
                          index="", palette_idx=len(PALETTES) - 1, progress=total - 1,
                          total=total, slug=slug)
    scenes.append({
        "image": str(cta_img),
        "say": args.cta,
        "text": _unwrap(args.cta, 11),
        "caption": args.cta[:30],
        "voice": args.voice,
        "native": {
            "font": TITLE_FONT, "font_size": _title_size(args.cta),
            "transform_y": 0.10, "caption_size": 8, "caption_y": -0.78,
            "text_anim_in": "渐显",
            "transition_in": "倒影", "transition_duration": 0.7,
        },
    })

    return {
        "width": W, "height": H, "fps": 30,
        "voice": args.voice,
        "scenes": scenes,
        "meta": {"series": args.kicker, "topic": args.topic},
    }


# ---------------------------------------------------------------- 平台
def _token() -> str:
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def _req(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {_token()}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def submit(args, spec: dict) -> dict:
    payload = {
        "projectId": args.project,
        "title": args.topic,
        "brief": args.hook,
        "spec": {**spec, "engine": "jianying"},
    }
    return _req("POST", "/production/runs", payload)


def wait_run(run_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        body = _req("GET", f"/production/runs/{run_id}")
        data = body.get("data") or body
        run = data.get("run") or data
        status = str(run.get("status") or "")
        jobs = data.get("jobs") or []
        prog = max([int(j.get("progress") or 0) for j in jobs] or [0])
        line = f"{status} {prog}%"
        if line != last:
            print(f"[{time.strftime('%H:%M:%S')}] run {run_id} → {line}", flush=True)
            last = line
        if status in {"succeeded", "success", "completed", "failed", "error", "canceled"}:
            return data
        time.sleep(4)
    raise SystemExit(f"run {run_id} 超时未结束")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="干货卡竖屏短片：出图 + spec + 平台提交")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--kicker", default="大模型面试题")
    ap.add_argument("--hook", default="面试官问这句，八成在探你懂不懂推理优化")
    ap.add_argument("--point", action="append", default=[])
    ap.add_argument("--cta", default="记住这三条，面试能答到点上。下一题想看什么，评论区说")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--project", default=os.environ.get("STUDIO_PROJECT", "6adc2161656a13996166bbfc"))
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    slug = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5]+", "", args.topic)[:24] or "episode"
    out_dir = Path(args.out) if args.out else STUDIO_HOME / "state/episodes" / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = build_spec(args, out_dir)
    spec_path = out_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"spec → {spec_path}  scenes={len(spec['scenes'])}  画布 {W}x{H}")
    for shot in render_previews(spec, out_dir):
        print(f"preview → {shot}")

    if args.dry_run:
        return 0

    try:
        created = submit(args, spec)
    except urllib.error.HTTPError as exc:
        print(f"提交失败 {exc.code}: {exc.read().decode('utf-8', 'ignore')[:400]}")
        return 2
    data = created.get("data") or created
    run = data.get("run") or (data if isinstance(data, dict) else {})
    run_id = run.get("id") or run.get("_id") or (data.get("id") if isinstance(data, dict) else None)
    if not run_id:  # 有些版本只回 jobId，退一步按列表最新一条兜底
        latest = _req("GET", "/production/runs?pageNo=1&pageSize=1").get("data") or {}
        if isinstance(latest, list):
            items = latest
        else:
            items = latest.get("items") or latest.get("list") or []
        run_id = items[0].get("id") if items else None
    print(f"已提交 run {run_id}")
    final = wait_run(str(run_id))
    print(json.dumps(final, ensure_ascii=False)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
