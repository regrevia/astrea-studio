"""presenter_beat.py — 虚拟主播讲解风格的 B 版：句级节拍 + 缓动 + 逐条入场 + 音效。

和 A 版（presenter_style.py）的差别只在"怎么排"和"怎么动"，内容与音色完全一致：

1. 配音按句合成，句间留真实停顿（默认 0.28s），不再整段一口气；
2. 字幕按逗号级分句切换，跟配音同步，而不是一屏压一整段；
3. 要点条逐条入场（每条一个独立叠层 + out-back 缓动），卡片本身只留标题；
4. 卡片/人物/要点全部走缓动曲线，不再匀速平移；
5. 每次卡片入场配一条气声 whoosh，每条要点入场配一个轻 pop。

用法：
    python3 presenter_beat.py content.json --outdir /tmp/b --render /tmp/b.mp4
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import presenter_style as P
import timeline_render as T
import host_profile as H

try:  # 配音走剪映那条链
    import slides_render as S
except Exception:  # pragma: no cover - 无剪映时给个明确错误
    S = None

GAP = 0.55          # 页与页之间
SENT_PAUSE = 0.28   # 句与句之间
SENT_END = 0.35     # 页内最后一句之后

SENT_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?")
CLAUSE_RE = re.compile(r"[^，,、：:]+[，,、：:]?")


class BeatError(RuntimeError):
    """内容不满足 B 版版式要求。"""


# ------------------------------------------------------------------ 文本切分
def split_sentences(text: str) -> list:
    out = [m.group(0).strip() for m in SENT_RE.finditer(str(text))]
    return [s for s in out if s]


def split_clauses(sent: str) -> list:
    out = [m.group(0).strip() for m in CLAUSE_RE.finditer(str(sent))]
    return [s for s in out if s]


# ------------------------------------------------------------------ 素材
def _bullet_png(path: Path, text: str, width: int) -> Path:
    """一条要点：圆点 + 一行文字，透明底，单独叠层用。"""
    from PIL import Image, ImageDraw
    font = P._font(36)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, text, font, width - 60)[:2] or [""]
    lh = 56
    img = Image.new("RGBA", (width, lh * len(lines) + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 22, 16, 34], fill=P.ACCENT + (255,))
    for i, line in enumerate(lines):
        d.text((40, i * lh), line, font=font, fill=(214, 223, 244, 255))
    img.save(path)
    return path


def _whoosh(path: Path) -> Path:
    """一条 0.55s 气声，入场用。纯合成，无版权素材。"""
    graph = ("anoisesrc=d=0.55:c=pink:a=0.8,"
             "highpass=f=600,lowpass=f=4200,"
             "afade=t=in:d=0.12,afade=t=out:st=0.28:d=0.26,"
             "volume=0.55")
    subprocess.run([T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", graph,
                    "-c:a", "aac", "-b:a", "96k", str(path)], check=True)
    return path


def _pop(path: Path) -> Path:
    """一条 0.14s 的轻点击音。"""
    graph = ("aevalsrc='0.42*sin(2*PI*720*t)*exp(-26*t)':"
             "d=0.14:s=48000")
    subprocess.run([T.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", graph,
                    "-c:a", "aac", "-b:a", "96k", str(path)], check=True)
    return path


def _tts(text: str, out: Path, voice: str) -> float:
    if S is None:
        raise BeatError("找不到 slides_render，配音不可用")
    try:
        S._tts(text, out, voice)
    except Exception as exc:  # noqa: BLE001
        raise BeatError(f"配音失败（{voice}）: {exc}") from exc
    return float(S._dur(out))


# ------------------------------------------------------------------ 组装
def build_spec(content: dict, outdir: Path, *, host=None, maid_dir=None,
               voice=None, fps=30, work=None, tts=None) -> dict:
    title = str(content.get("title") or "").strip()
    pages = content.get("pages") or []
    if not title or not pages:
        raise BeatError("content 至少要有 title 和 1 个 page")

    W, H = 1920, 1080
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(work) if work else Path(tempfile.mkdtemp(prefix="beat-"))
    work.mkdir(parents=True, exist_ok=True)
    assets = outdir / "assets"
    assets.mkdir(exist_ok=True)

    profile = P._resolve_host(content, host=host, maid_dir=maid_dir)
    voice = voice or content.get("voice") or profile.voice
    series = str(content.get("series") or "ASTREA STUDIO")
    tts_fn = tts or _tts

    backdrop_png = P.make_backdrop(work / "backdrop.png", W, H)
    whoosh = _whoosh(assets / "sfx_whoosh.m4a")
    pop = _pop(assets / "sfx_pop.m4a")

    overlays, audios = [], []
    scenes = []
    t = 0.0
    for i, p in enumerate(pages):
        # 卡片只留标题，要点单独叠层，才能做逐条入场
        card_page = dict(p)
        card_page["bullets"] = []
        png = P.make_page(assets / f"page{i:02d}.png", card_page, i, len(pages),
                          series)

        sentences = split_sentences(str(p["narration"]))
        if not sentences:
            raise BeatError(f"page[{i}] narration 为空")

        # 1) 句级配音 + 精确起始时间
        sent_times = []
        cursor = t
        for j, sent in enumerate(sentences):
            mp3 = assets / f"voice{i:02d}_{j:02d}.mp3"
            dur = tts_fn(sent, mp3, voice)
            sent_times.append({"text": sent, "start": cursor, "end": cursor + dur})
            audios.append({"src": str(mp3), "start": round(cursor, 3),
                           "volume": 1.0, "role": "narration"})
            cursor += dur
            if j < len(sentences) - 1:
                cursor += SENT_PAUSE
        page_start = t
        page_end = cursor + SENT_END
        scenes.append({"png": png, "start": page_start, "end": page_end,
                       "maid": p.get("maid")})

        # 2) 卡片入场（缓动）
        overlays.append({
            "src": str(png), "start": round(page_start, 3),
            "end": round(page_end, 3),
            "x": P.PAGE["x"], "y": P.PAGE["y"], "width": P.PAGE["w"],
            "enter": {"from": "right", "duration": 0.5, "ease": "out-cubic"},
            "exit": {"to": "left", "duration": 0.32, "ease": "in-cubic"},
        })
        audios.append({"src": str(whoosh), "start": round(page_start, 3),
                       "volume": 0.18, "role": "sfx"})

        # 3) 要点逐条入场：每条间隔 0.42s，从右侧滑入并回弹
        bullets = p.get("bullets") or []
        by, gap_y = _bullets_layout(p, len(bullets))
        for k, bullet in enumerate(bullets):
            bp = _bullet_png(assets / f"bullet{i:02d}_{k:02d}.png",
                             str(bullet), P.PAGE["w"] - 140)
            bs = page_start + 0.85 + k * 0.42
            if bs > page_end - 0.6:
                bs = max(page_start + 0.35, page_end - 1.2)
            overlays.append({
                "src": str(bp), "start": round(bs, 3),
                "end": round(page_end - 0.25, 3),
                "x": P.PAGE["x"] + 100, "y": by + k * gap_y,
                "width": P.PAGE["w"] - 140,
                "enter": {"from": "right", "duration": 0.42, "ease": "out-back"},
                "fade": 0.12,
            })
            audios.append({"src": str(pop), "start": round(bs, 3),
                           "volume": 0.10, "role": "sfx"})

        # 4) 字幕：一句一条，跟着配音走（不做逗号级切分，避免半句话闪一下）
        for st in sent_times:
            cap = P.make_caption(assets / f"cap{i:02d}_{int(st['start'] * 1000)}.png",
                                 st["text"])
            overlays.append({
                "src": str(cap), "start": round(st["start"], 3),
                "end": round(st["end"], 3),
                "x": P.PAGE["x"], "y": P.CAPTION_Y, "fade": 0.10,
            })

        # 5) 主角
        overlays.extend(P._host_overlays(profile, scenes[-1], i, work, fps))
        t = page_end + GAP

    total = round(t - GAP, 3)
    backdrop = P.make_still_video(backdrop_png, assets / "backdrop.mp4", total, fps)

    bgm = content.get("bgm")
    if bgm is True or (bgm is None and content.get("music", True)):
        audios.append({"src": str(P.make_bgm(assets / "bgm.m4a", work)),
                       "start": 0, "volume": 0.10, "loop": True,
                       "fade": 1.2, "role": "bgm"})

    return {
        "canvas": {"width": W, "height": H, "fps": fps},
        "video": [{"src": str(backdrop), "in": 0, "out": total, "fit": "cover"}],
        "overlay": overlays,
        "text": [],
        "audio": audios,
        "font": T.find_font(),
        "meta": {"title": title, "series": series, "voice": voice,
                 "duration": total, "style": "presenter-beat",
                 "host": {"id": profile.id, "label": profile.label,
                          "kind": profile.kind, "alpha": profile.alpha}},
    }


def _bullets_layout(page: dict, count: int) -> tuple:
    """标题区之后第一条要点的 y 和行距：保证不压到页码，也不出卡片底边。"""
    from PIL import Image, ImageDraw
    font = P._font(70)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines = P._wrap(probe, str(page["title"]), font,
                    P.PAGE["w"] - 124 - 40)[:2] or [""]
    top = P.PAGE["y"] + 210 + 84 * len(lines) + 28
    if not count:
        return top, 74
    bottom = P.PAGE["y"] + P.PAGE["h"] - 96      # 页码上方留白
    gap = 74
    if top + gap * (count - 1) > bottom:
        gap = max(58, (bottom - top) // max(1, count - 1))
    if top + gap * (count - 1) > bottom:          # 还是挤就整体上提
        top = max(P.PAGE["y"] + 200, bottom - gap * (count - 1))
    return top, gap


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="虚拟主播讲解风格 B 版：句级节拍")
    ap.add_argument("content")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--spec-out")
    ap.add_argument("--render")
    ap.add_argument("--host")
    ap.add_argument("--voice")
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args(argv)

    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    outdir = Path(args.outdir)
    work = Path(tempfile.mkdtemp(prefix="beat-"))
    try:
        spec = build_spec(content, outdir, host=args.host or content.get("host"),
                          voice=args.voice, work=work)
        sp = Path(args.spec_out) if args.spec_out else outdir / "spec.json"
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                      encoding="utf-8")
        print(f"spec: {sp}  duration={spec['meta']['duration']}s "
              f"overlays={len(spec['overlay'])} audio={len(spec['audio'])}")
        if args.render:
            ev = T.render(T.load_spec(sp), args.render,
                          keep_workdir=args.keep_work)
            print(f"master: {ev['output']} {ev['bytes']}B {ev['duration']}s "
                  f"{ev['seconds']}s渲染")
        return 0
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
