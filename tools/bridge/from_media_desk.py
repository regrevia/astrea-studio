#!/usr/bin/env python3
"""from_media_desk.py — 把 dsh-media-desk 的成稿（标题 + 口播 + 分镜表）接成我们自己能渲的 timeline spec。

为什么是「桥」而不是插件
------------------------
dsh-media-desk 是浏览器里的写作台：它出脚本、标题、封面、配图、分镜，**不配音、不渲染**；
我们是出片平台：有 TTS、有 timeline_render、有响度和交付。两边各自完整，接缝只在一种数据上——
它镜头表里的 ``{no, shot, visual, voice, seconds}``（原文见其 ``src/produce.js`` 的
``normalizePlan``）。所以这里不 import 它的任何代码、不装插件，只认这份契约：

    production = {
      "title": "…", "coverText": "…", "article": "…",
      "images": [{"no":1, "caption":"…", "src":"media-desk/files/x.jpg"}],
      "shots":  [{"no":1, "shot":"近景", "visual":"…", "voice":"…", "seconds":6,
                  "src": "media-desk/files/x.jpg"}]
    }

输入可以是它的 ``$DSH_HOME/media-desk/state.json``（用 ``--id``/``--title`` 挑一条），
也可以是任何手写/导出的同形状 JSON。输出：

    <WORK>/audio/aNN.mp3      每镜一条配音（edge-tts，句内不再切拍）
    <WORK>/cards/NN.jpg       没有分镜图时用卡面兜底（复用 promo_envoy 的版式）
    <WORK>/spec.json          timeline spec（canvas/video/audio/text 全用绝对路径）
    <WORK>/plan.md            镜头表（和它的「分镜脚本.md」对得上，方便人核）
    <WORK>/raw.mp4            --render 才有
    <WORK>/preview.mp4        --render + 响度（--af 控制），再看要不要走交付

用法
----
    python3 tools/bridge/from_media_desk.py prod.json --out /tmp/desk
    python3 tools/bridge/from_media_desk.py ~/.dsh/media-desk/state.json --id 3f2a --render
    ./tools/bridge/from_media_desk.py prod.json --out /tmp/desk --submit   # 走能力服务排队

它只读不写 media-desk 的状态：反过来的「我们的稿子给它改」不在这一层，别在这里长成双向同步。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = REPO / "integrations" / "mac-worker" / "export"
PRESENTER = REPO / "deploy" / "presenter"
sys.path.insert(0, str(PRESENTER))
sys.path.insert(0, str(REPO / "tools" / "promo"))

W, H, FPS = 1080, 1920, 30
VOICE = os.environ.get("BRIDGE_VOICE", os.environ.get("PROMO_VOICE", "zh-CN-XiaoxiaoNeural"))
RATE = os.environ.get("BRIDGE_RATE", os.environ.get("PROMO_RATE", "+14%"))
DEFAULT_AF = os.environ.get(
    "BRIDGE_AF", "volume=1.5dB,alimiter=limit=0.80:level=false:attack=3:release=60")
SHOT_SLACK = float(os.environ.get("BRIDGE_SHOT_SLACK", "0.35"))  # 配音后至少留的气口
XFADE = float(os.environ.get("BRIDGE_XFADE", "0.18"))
SUB_SIZE = 0.033        # 字幕字号（× 画布高）；0.042 时一行放不下 17 字
SUB_WRAP = 13           # 每行字数（timeline_render 的 text 只认 \n，不自动折行）
CARD_HEAD_MAX = 14      # 卡面大字最多几个字：一句话的主张，不重抄字幕
COVER_SECONDS = float(os.environ.get("BRIDGE_COVER_SECONDS", "2.6"))


# ---------------------------------------------------------------- 读 production
def _find_shots(prod: dict) -> list[dict]:
    for key in ("shots", "storyboard", "scenes"):
        if isinstance(prod.get(key), list) and prod[key]:
            return [s for s in prod[key] if isinstance(s, dict)]
    return []


def _normalize_shot(s: dict, idx: int) -> dict:
    """只取我们渲得动的字段；其余（prompt 之类）留在 plan.md 里供人看。"""
    voice = str(s.get("voice") or s.get("say") or s.get("narration") or "").strip()
    visual = str(s.get("visual") or s.get("frame") or s.get("desc") or "").strip()
    shot = str(s.get("shot") or s.get("size") or "").strip()
    seconds = s.get("seconds", s.get("duration"))
    try:
        seconds = float(seconds) if seconds not in (None, "") else None
    except (TypeError, ValueError):
        seconds = None
    return {
        "no": int(s.get("no") or idx + 1),
        "shot": shot, "visual": visual, "voice": voice,
        "seconds": seconds, "src": str(s.get("src") or s.get("image") or s.get("frame_src") or ""),
        "prompt": str(s.get("prompt") or ""),
    }


def _pick_production(state: dict, ident: str | None, title: str | None) -> tuple[dict, str]:
    """state.json 里 productions 可能叫各种名字；逐个容器找带 shots 的对象。"""
    if _find_shots(state):
        return state, str(state.get("id") or state.get("title") or "production")
    for key in ("productions", "items", "content", "drafts", "list"):
        seq = state.get(key)
        if isinstance(seq, dict):
            seq = list(seq.values())
        if not isinstance(seq, list):
            continue
        cands = [x for x in seq if isinstance(x, dict) and _find_shots(x)]
        if not cands:
            continue
        if ident:
            for x in cands:
                if str(x.get("id")) == ident or str(x.get("id", "")).startswith(ident):
                    return x, str(x.get("id"))
            raise SystemExit(f"没有 id 匹配 {ident!r} 的成稿，现有: "
                             f"{[str(x.get('id')) for x in cands][:10]}")
        if title:
            for x in cands:
                if title in str(x.get("title") or ""):
                    return x, str(x.get("id"))
            raise SystemExit(f"没有标题包含 {title!r} 的成稿，现有: "
                             f"{[x.get('title') for x in cands][:10]}")
        return cands[0], str(cands[0].get("id"))
    raise SystemExit("输入里没找到带 shots 的成稿（支持 state.json 或单个 production）")


def _files_dir(src: Path) -> Path:
    """它的分镜图落在 <state 同级>/files/，src 常写成 media-desk/files/x.jpg。"""
    for cand in (src.parent / "files", src.parent):
        if cand.is_dir():
            return cand
    return src.parent


def _resolve_image(raw: str, files: Path) -> Path | None:
    if not raw:
        return None
    p = Path(os.path.expanduser(raw))
    if p.is_file():
        return p
    name = Path(raw).name
    for cand in (files / name, files / "files" / name):
        if cand.is_file():
            return cand
    return None


# ---------------------------------------------------------------- 配音
def _dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


async def _synth(text: str, mp3: Path) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="SentenceBoundary")
    with open(mp3, "wb") as fh:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                fh.write(ch["data"])


def make_voice(text: str, aud: Path, key: str) -> Path:
    """同一句配音只合成一次：缓存键是文本+音色+语速，换镜头顺序不会重配。"""
    h = hashlib.sha1(f"{VOICE}|{RATE}|{text}".encode()).hexdigest()[:10]
    mp3 = aud / f"{key}.mp3"
    stamp = aud / f"{key}.txt"
    if mp3.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == h:
        return mp3
    asyncio.run(_synth(text, mp3))
    stamp.write_text(h, encoding="utf-8")
    return mp3


# ---------------------------------------------------------------- 画面
def _wrap_sub(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    lines = [text[i:i + SUB_WRAP] for i in range(0, len(text), SUB_WRAP)]
    return "\n".join(lines[:3])  # 三行以上字幕就不是字幕了，说明该拆镜


_LABEL = re.compile(r"^\s*(标题卡|卡面|画面|镜头|口播|字幕|正文|旁白)\s*[:：]\s*")


def card_head(shot: dict) -> str:
    """卡面大字 = 这镜的主张，不拿分镜描述凑。

    `visual` 是写给人看的分镜说明（「近景，人物半身，背景虚化」），上屏就露馅；
    所以优先 `title`，否则取口播的前几个分句——主张上屏、整句留给字幕和耳朵。
    """
    labelled = _LABEL
    title = labelled.sub("", str(shot.get("title") or "")).strip()
    if title:
        return title[:24]
    voice = labelled.sub("", str(shot.get("voice") or "")).strip()
    if not voice:
        return f"第 {shot['no']} 镜"
    parts = [p.strip() for p in re.split(r"[，,。？！；!?;]", voice) if p.strip()]
    head = parts[0][:CARD_HEAD_MAX]
    # 第一分句太短（「然后」「接着」）才并下一句，否则卡面和字幕会念同一句
    for part in parts[1:]:
        if len(head) >= 6 or len(head) + 1 + len(part) > CARD_HEAD_MAX:
            break
        head += "，" + part
    return head


def card_for(shot: dict, idx: int, total: int, path: Path) -> Path:
    """没有分镜图时的兜底卡面：复用宣传片那套版式，不另立视觉。"""
    from promo_envoy import CYAN, card
    head = card_head(shot)
    scene = {
        "head": head[:40],
        "kicker": f"镜 {shot['no']:02d}" + (f" · {shot['shot']}" if shot["shot"] else ""),
        "num": f"{idx + 1:02d}/{total:02d}",
        "accent": CYAN,
        "head_size": 68,
        "max_lines": 3,
        "head_y": 470,
        "sizes": [68, 62, 56, 50],
    }
    card(scene, idx, total, path)
    return path


# ---------------------------------------------------------------- spec
def build_spec(prod: dict, shots: list[dict], work: Path, files: Path,
               cover: bool) -> tuple[dict, list[dict]]:
    aud = work / "audio"
    cards = work / "cards"
    aud.mkdir(parents=True, exist_ok=True)
    cards.mkdir(parents=True, exist_ok=True)

    video: list[dict] = []
    audio: list[dict] = []
    text: list[dict] = []
    plan: list[dict] = []
    t = 0.0

    # 片头：标题卡（它的「封面」是静态图，不是画幅；这里只借标题和封面大字）
    if cover:
        title_s = str(prod.get("title") or "").strip()
        cover_text = str(prod.get("coverText") or "").strip()
        if title_s or cover_text:
            scene = {
                "head": (cover_text or title_s)[:24],
                "kicker": "分镜预览",
                "pill": title_s[:26] if title_s else None,
                "num": "",
                "accent": (0, 224, 255),
                "head_size": 92, "head_y": 470, "sizes": [92, 84, 76],
            }
            from promo_envoy import CYAN, card
            scene["accent"] = CYAN
            p = cards / "cover.jpg"
            card(scene, 0, len(shots) + 1, p)
            video.append({"src": str(p), "in": 0.0, "out": COVER_SECONDS,
                          "zoom": (1.0, 1.02), "pulse": False,
                          "fade_in": 0.2, "fade_out": 0.0,
                          "xfade_in": 0.0 if not video else XFADE})
            t += COVER_SECONDS
            plan.append({"no": 0, "shot": "片头", "voice": "", "seconds": COVER_SECONDS,
                         "frame": str(p)})

    for idx, shot in enumerate(shots):
        key = f"a{idx + 1:02d}"
        voice_mp3 = make_voice(shot["voice"], aud, key) if shot["voice"] else None
        v_dur = _dur(voice_mp3) if voice_mp3 else 0.0
        seg = max(shot["seconds"] or 0.0, v_dur + SHOT_SLACK)
        if seg < 1.2:
            raise SystemExit(f"第 {shot['no']} 镜时长只有 {seg:.2f}s：没有口播就必须给 seconds")
        img = _resolve_image(shot["src"], files)
        if img is None:
            img = card_for(shot, idx, len(shots), cards / f"{key}.jpg")
        video.append({"src": str(img), "in": 0.0, "out": round(seg, 3),
                      "fit": "cover", "zoom": (1.0, 1.045), "pulse": False,
                      "fade_in": 0.0, "fade_out": 0.0,
                      "xfade_in": XFADE})
        if voice_mp3:
            audio.append({"src": str(voice_mp3), "start": round(t, 3),
                          "volume": 1.0, "role": "voice"})
            text.append({"text": _wrap_sub(shot["voice"]),
                         "start": round(t + 0.12, 3), "end": round(t + seg - 0.10, 3),
                         "x": 0.5, "y": 0.845, "size": SUB_SIZE,
                         "color": "#ffffff", "stroke": 3.0, "stroke_color": "#000000",
                         "box": "#00000088", "radius": 0.22, "align": "center"})
        plan.append({"no": shot["no"], "shot": shot["shot"], "voice": shot["voice"],
                     "seconds": round(seg, 3), "frame": str(img),
                     "visual": shot["visual"], "prompt": shot["prompt"]})
        t += seg

    spec = {
        "canvas": {"width": W, "height": H, "fps": FPS},
        "video": video,
        "audio": audio,
        "text": text,
    }
    return spec, plan


def write_plan(plan: list[dict], path: Path, prod: dict, total: float) -> None:
    lines = [f"# {prod.get('title') or '未命名'}：镜头表", "",
             f"总时长 {total:.2f}s · {len(plan)} 镜 · 配音 {VOICE} {RATE}", "",
             "| 镜号 | 景别 | 时长 | 口播 | 画面 | 素材 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for p in plan:
        cell = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {p['no']} | {cell(p.get('shot'))} | {p['seconds']}s | "
                     f"{cell(p.get('voice'))} | {cell(p.get('visual'))} | "
                     f"`{Path(p['frame']).name}` |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 出片
def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, cwd=str(cwd) if cwd else None)


def render(work: Path, spec: Path, af: str | None) -> Path:
    raw = work / "raw.mp4"
    run([sys.executable, ENGINE / "timeline_render.py", "--spec", spec, "--out", raw])
    if not af:
        return raw
    out = work / "preview.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-i", raw,
         "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    return out


def submit(spec: Path, slug: str, out: Path | None) -> None:
    cmd = [REPO / "bin" / "studio", "jobs", "submit", "timeline", spec,
           "--slug", slug, "--wait"]
    if out:
        cmd += ["--out", out]
    run(cmd)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dsh-media-desk 成稿 → timeline spec → mp4")
    ap.add_argument("input", help="production.json 或 media-desk 的 state.json")
    ap.add_argument("--id", default=None, help="state.json 里挑哪条成稿（前缀即可）")
    ap.add_argument("--title", default=None, help="按标题子串挑成稿")
    ap.add_argument("--out", default=None, help="工作目录（默认 ~/CreatorStudio/work/desk-bridge）")
    ap.add_argument("--no-cover", action="store_true", help="不要片头标题卡")
    ap.add_argument("--render", action="store_true", help="直接渲一遍（不排队）")
    ap.add_argument("--af", default=DEFAULT_AF, help=f"响度滤镜，'none' 跳过（默认 {DEFAULT_AF}）")
    ap.add_argument("--submit", action="store_true", help="交给能力服务排队（bin/studio jobs submit）")
    ap.add_argument("--slug", default="desk-preview", help="--submit 用的 slug")
    args = ap.parse_args(argv)

    src = Path(os.path.expanduser(args.input)).resolve()
    state = json.loads(src.read_text(encoding="utf-8"))
    prod, ident = _pick_production(state, args.id, args.title)
    shots = [_normalize_shot(s, i) for i, s in enumerate(_find_shots(prod))]
    if not shots:
        raise SystemExit("成稿里 shots 是空的：它还没跑完「标题与画面规划」")
    missing = [s["no"] for s in shots if not s["voice"]]
    if missing:
        raise SystemExit(f"第 {missing} 镜没有口播（voice）：没有旁白的镜头不出片，先回写作台补")

    work = Path(os.path.expanduser(args.out or str(Path.home() / "CreatorStudio/work/desk-bridge")))
    work.mkdir(parents=True, exist_ok=True)
    spec, plan = build_spec(prod, shots, work, _files_dir(src), not args.no_cover)
    total = sum(v["out"] for v in spec["video"])

    spec_path = work / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    write_plan(plan, work / "plan.md", prod, total)

    said = sum(len(s["voice"]) for s in shots)
    print(f"成稿 {ident} · {len(shots)} 镜 · {total:.2f}s · 口播 {said} 字 "
          f"({said / total if total else 0:.2f} 字/秒)")
    for p in plan:
        print(f"  {str(p['no']):>2} {p['seconds']:>6.2f}s  {p.get('shot') or '':<4} "
              f"{(p.get('voice') or '')[:34]}")
    print(f"\nspec → {spec_path}\n镜头表 → {work / 'plan.md'}")

    if args.render:
        af = None if (args.af or "").lower() in ("", "none", "off") else args.af
        out = render(work, spec_path, af)
        print(f"\n成片 → {out}")
        run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration,size:stream=codec_type,width,height,r_frame_rate",
             "-of", "default=nw=1", out])
    if args.submit:
        submit(spec_path, args.slug, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
