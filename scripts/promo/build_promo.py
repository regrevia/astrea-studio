#!/usr/bin/env python3
"""把一条真实的产品宣传片做成可提交的 timeline 规格。

输入全是本机真实素材：两段产品界面录屏 + 一张界面截图 + 产品 logo；
旁白用 edge-tts 现场合成（带 WordBoundary，字幕严格跟着字走），
BGM 用 MoneyPrinterTurbo 自带的曲库。产出：

  $STUDIO_HOME/state/promo/narration.wav        旁白音轨（绝对时间对齐）
  $STUDIO_HOME/state/promo/words.json           逐字时间表（字幕来源）
  $STUDIO_HOME/state/promo/product_promo.json   timeline 规格（可直接 submit）

规格里的路径都是绝对路径，所以它落在运行时目录、不进仓库；仓库里是这份脚本
（脚本 + 素材 = 可复现的规格），避免把本机路径写进要开源的提交。

用法：
  python3 scripts/promo/build_promo.py                # 只生成规格
  python3 scripts/promo/build_promo.py --submit ...   # 生成并提交本机队列
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

STUDIO_HOME = Path(os.environ.get("STUDIO_HOME") or (Path.home() / "CreatorStudio")).expanduser()
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = STUDIO_HOME / "state" / "promo"
SHOTS = STUDIO_HOME / "materials" / "shots" / "promo2"
# 窗口卡：frame.png 是 1000x669 的「边框 + 标题栏」透明遮罩，中间挖空 625px 高给录屏，
# 于是录屏的直角四角被圆角边框盖掉，视觉上就是一台真窗口。
CARD = {"x": 40, "y": 604, "w": 1000, "h": 625}
FRAME_Y = CARD["y"] - 44
SONGS = REPO_ROOT / "integrations" / "vendor" / "MoneyPrinterTurbo" / "resource" / "songs"
LOGO = REPO_ROOT / "project" / "aitoearn-web" / "public" / "logo.png"
FFMPEG = os.environ.get("FFMPEG") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE") or "/opt/homebrew/bin/ffprobe"

VOICE = os.environ.get("PROMO_VOICE") or "zh-CN-XiaoxiaoNeural"
RATE = os.environ.get("PROMO_RATE") or "+8%"

# 一句话一行：每行即一条字幕的语义单元（切分只在行内按词边界做）
LINES = [
    "一句话说清目标，剩下的交给工作台",
    "找素材、写脚本、配旁白，自动排进时间轴",
    "不满意就直接说改哪一段，改动看得见",
    "要精修就导出剪映工程，本机打开接着改",
    "出片、质检、进草稿箱，一条流程走完",
    "渲染和发布都在本机跑，数据不出这台机器",
    "从想法到可发布的成片，就在同一个工作区",
]

GAP = 0.30          # 句间停顿
LEAD = 0.90         # 开场留白
TAIL = 1.30         # 收尾留白
CUE_GAP = 0.10      # 字幕之间的缝，避免两行字幕贴着闪
MAX_CUE = 13        # 单条字幕最多几个字


def sh(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失败: {r.stderr.strip()[-400:]}")
    return r.stdout


def probe_duration(path: Path) -> float:
    out = sh([FFPROBE, "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", str(path)])
    return float(out.strip())


def synth_line(idx: int, text: str) -> tuple[Path, float, list[dict]]:
    """合成一行。返回 (mp3, 时长, [{text, offset}]，offset 相对本行起点)。"""
    import edge_tts

    mp3 = OUT_DIR / f"line{idx:02d}.mp3"

    async def run() -> list[dict]:
        comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
        words: list[dict] = []
        with open(mp3, "wb") as fh:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    fh.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    words.append({
                        "text": chunk["text"],
                        "offset": chunk["offset"] / 1e7,      # 100ns → s
                        "duration": chunk["duration"] / 1e7,
                    })
        return words

    words = asyncio.run(run())
    if not mp3.is_file() or mp3.stat().st_size == 0:
        raise RuntimeError(f"第 {idx} 行没有音频产出")
    if not words:
        raise RuntimeError(f"第 {idx} 行没有 WordBoundary（字幕无法对字）")
    return mp3, probe_duration(mp3), words


def cues_for_line(text: str, words: list[dict], line_start: float, line_dur: float) -> list[dict]:
    """把一行的逐字表按字数上限切成若干条字幕，时间只落在真实字边界上。"""
    if len(text) <= MAX_CUE:
        return [{"text": text, "start": round(line_start, 3),
                 "end": round(line_start + line_dur, 3)}]
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cur_len = 0
    for w in words:
        n = len(w["text"])
        if cur and cur_len + n > MAX_CUE:
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(w)
        cur_len += n
    if cur:
        groups.append(cur)
    cues = []
    for gi, g in enumerate(groups):
        start = line_start + g[0]["offset"]
        if gi + 1 < len(groups):
            end = line_start + groups[gi + 1][0]["offset"] - CUE_GAP
        else:
            end = line_start + line_dur
        end = max(end, start + 0.45)
        cues.append({"text": "".join(w["text"] for w in g),
                     "start": round(start, 3), "end": round(end, 3)})
    return cues


def build_narration(parts: list[tuple[Path, float]]) -> tuple[Path, float]:
    """按行起点把各行拼成一条连续旁白（adelay 对齐 + amix 保电平）。"""
    wav = OUT_DIR / "narration.wav"
    cmd = [FFMPEG, "-v", "error", "-y"]
    for p, _ in parts:
        cmd += ["-i", str(p)]
    total = LEAD + sum(d for _, d in parts) + GAP * (len(parts) - 1) + TAIL
    filt = []
    for i, (_, dur) in enumerate(parts):
        ms = int(round((LEAD + sum(d + GAP for _, d in parts[:i])) * 1000))
        filt.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
                    f"adelay={ms}:all=1[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(parts)))
    filt.append(f"{mix}amix=inputs={len(parts)}:duration=longest:normalize=0,"
                f"apad=whole_dur={total:.3f},atrim=0:{total:.3f}[aout]")
    cmd += ["-filter_complex", ";".join(filt), "-map", "[aout]",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(wav)]
    sh(cmd)
    return wav, total


def ensure_segment(src: Path, name: str, start: float, end: float, tempo: float) -> Path:
    """把录屏里的有效段剪出来并加速（加速是为了压掉等待键鼠的空白时间）。"""
    dst = SHOTS / name
    stamp = dst.with_suffix(".key")
    key = f"{src.name}|{start}|{end}|{tempo}"
    if dst.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == key:
        return dst
    span = round(end - start, 3)
    sh([FFMPEG, "-v", "error", "-y", "-ss", str(start), "-t", str(span), "-i", str(src),
        "-vf", f"setpts=PTS/{tempo},scale=1600:1000:flags=lanczos,fps=30,format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", str(dst)])
    stamp.write_text(key, encoding="utf-8")
    return dst


def txt(text: str, start: float, end: float, y: float, size: float,
        color: str = "#ffffff", x: float = 0.5, align: str = "center",
        stroke: float = 0.0, fade: float = 0.22) -> dict:
    """size / x / y 都是画布比例：size 是画布高的比例，y 是 0..1 的中心线。"""
    return {"text": text, "start": round(start, 3), "end": round(max(start + 0.2, end), 3),
            "x": x, "y": y, "size": size, "color": color, "align": align,
            "stroke": stroke, "stroke_color": "#000000", "fade": fade}


def screen_plan(total: float) -> dict:
    """三段真实界面录屏铺在「窗口卡」里，最后留一段纯排版收尾。"""
    studio = SHOTS / "studio.webm"
    for p in (studio, SHOTS / "bg.png", SHOTS / "frame.png"):
        if not p.is_file():
            raise RuntimeError(f"缺素材: {p}（先跑 tools/capture/capture_ui2.cjs 与 make_promo_assets.cjs）")

    # 三段同一次录屏：指挥台 → 本机队列列表 → 手动出片面板
    raw = [("seg-a.mp4", studio, 2.0, 10.0, 1.12),
           ("seg-b.mp4", studio, 10.2, 22.4, 1.12),
           ("seg-c.mp4", studio, 25.6, 33.4, 1.18)]
    segs = []
    for name, src, a, b, tempo in raw:
        p = ensure_segment(src, name, a, b, tempo)
        segs.append((p, round(probe_duration(p), 3)))

    end_card = 4.8
    screen_end = round(total - end_card, 3)
    cross = 0.28                                   # 相邻两段交叉溶解的重叠
    span = sum(d for _, d in segs) - cross * (len(segs) - 1)
    intro = round(screen_end - span - 0.40, 3)      # 开头空窗时间：窗口先出现，内容再进来
    if intro < 0.20:
        raise RuntimeError(f"素材太长：三段共 {span:.2f}s，屏幕段只有 {screen_end:.2f}s")

    videos = [{"src": str(SHOTS / "bg.png"), "in": 0.0, "out": total,
               "fit": "cover", "zoom": [1.0, 1.05]}]
    overlays: list[dict] = []
    scenes: list[dict] = []
    t = intro
    for i, (p, d) in enumerate(segs):
        start, end = round(t, 3), round(t + d, 3)
        scenes.append({"start": start, "end": end})
        overlays.append({"src": str(p), "start": start, "end": end,
                         "x": CARD["x"], "y": CARD["y"], "width": CARD["w"],
                         "opacity": 1.0, "fade": 0.45 if i == 0 else cross})
        t = end - cross
    last_end = scenes[-1]["end"]
    # 遮罩必须最后画：overlay 的顺序就是层级，后出现的盖在前面之上
    overlays.append({"src": str(SHOTS / "frame.png"), "start": round(max(0.0, intro - 0.55), 3),
                     "end": round(last_end + 0.20, 3), "x": CARD["x"], "y": FRAME_Y,
                     "width": CARD["w"], "opacity": 1.0, "fade": 0.40})

    texts = [txt("AiToEarn · Creator Studio", 0.5, total - 0.4, 0.042, 0.021,
                 color="#8f99a8", x=0.06, align="left")]
    heads = [("一句话，出一支片", "脚本 · 分镜 · 旁白，自动排进时间轴"),
             ("素材与成片，一个项目", "任务、修订版、成片都在同一条时间线上"),
             ("剪映工程自动落地", "要精修就打开本机剪映接着改")]
    for i, (head, sub) in enumerate(heads):
        s0 = scenes[i]["start"] - 0.10
        s1 = scenes[i]["end"] + (0.12 if i < len(heads) - 1 else 0.25)
        texts.append(txt(head, s0, s1, 0.155, 0.046))
        if sub:
            texts.append(txt(sub, s0 + 0.15, s1, 0.198, 0.026, color="#a8b2c1"))

    # 整段“窗框 + 录屏”作为一个整体轻轻浮动：给静态网页录屏一点呼吸，
    # 又不至于像假动画。phase 用 start-intro 抵消，让每段都落在同一条正弦上，
    # 段与段之间的切点不会错位。
    for ov in overlays:
        ov["motion"] = {"type": "bob", "amplitude": 10.0, "period": 6.2,
                        "phase": round(ov["start"] - intro, 3)}

    end_at = round(last_end + 0.25, 3)
    texts += [
        txt("Creator Studio", end_at, total - 0.06, 0.385, 0.058),
        txt("把宣发工作台本身做到极致", end_at + 0.15, total - 0.06, 0.437, 0.034, color="#f5b544"),
        txt("真实界面 · 真实渲染 · 真实剪映草稿", end_at + 0.35, total - 0.06, 0.535, 0.028, color="#c8d0dc"),
        txt("一稿多版，改哪段都看得见", end_at + 0.50, total - 0.06, 0.575, 0.028, color="#c8d0dc"),
        txt("渲染与发布都在本机", end_at + 0.65, total - 0.06, 0.615, 0.028, color="#c8d0dc"),
    ]
    return {"videos": videos, "overlays": overlays, "texts": texts,
            "scenes": scenes, "screenEnd": screen_end, "endAt": end_at}


def build_spec() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parts = []
    all_words: list[dict] = []
    line_starts: list[float] = []
    for i, text in enumerate(LINES):
        mp3, dur, words = synth_line(i, text)
        start = LEAD + sum(d for _, d, _ in parts) + GAP * len(parts)
        line_starts.append(start)
        parts.append((mp3, dur, words))
    narration, total = build_narration([(p, d) for p, d, _ in parts])

    cues: list[dict] = []
    for i, text in enumerate(LINES):
        mp3, dur, words = parts[i]
        cues += cues_for_line(text, words, line_starts[i], dur)
        for w in words:
            all_words.append({"text": w["text"],
                              "start": round(line_starts[i] + w["offset"], 3),
                              "end": round(line_starts[i] + w["offset"] + w["duration"], 3)})
    cues.sort(key=lambda c: c["start"])
    for i in range(len(cues) - 1):
        cues[i]["end"] = round(min(cues[i]["end"], cues[i + 1]["start"] - 0.05), 3)

    plan = screen_plan(total)
    videos, overlays = plan["videos"], plan["overlays"]
    bgm = SONGS / "output013.mp3"
    if not bgm.is_file():
        cand = sorted(SONGS.glob("*.mp3"))
        if not cand:
            raise RuntimeError("曲库是空的")
        bgm = cand[0]

    spec = {
        "canvas": {"width": 1080, "height": 1920, "fps": 30},
        "duration": total,
        "video": videos,
        "text": plan["texts"] + [
            {"text": c["text"], "start": c["start"], "end": c["end"],
             "x": 0.5, "y": 0.905, "size": 0.038,
             "color": "#ffffff", "stroke": 3.4, "stroke_color": "#000000",
             "box": "#00000000", "align": "center", "fade": 0.18}
            for c in cues],
        "overlay": overlays + [
            {"src": str(LOGO), "start": 0.4, "end": round(total - 0.3, 3),
             "x": 48, "y": 64, "width": 176, "opacity": 0.95, "fade": 0.5}],
        "audio": [
            {"src": str(narration), "start": 0.0, "volume": 1.0, "role": "narration"},
            # audio[].out 是「源内的出点」，不是音量包络；BGM 要给整段就必须留空，
            # 淡化靠 audio[].fade（引擎在首尾各放一次 afade）。
            {"src": str(bgm), "start": 0.0, "volume": 0.13, "loop": True,
             "fade": 1.2, "role": "bgm"},
        ],
    }

    (OUT_DIR / "words.json").write_text(
        json.dumps(all_words, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    spec_path = OUT_DIR / "product_promo.json"
    envelope = {
        "projectId": "promo",
        "title": "Creator Studio 产品宣传片",
        "brief": "真实界面录屏 + 中文旁白 + 逐字字幕 + BGM，1080x1920@30 一条时间轴渲染",
        "engine": "timeline",
        "spec": spec,
    }
    spec_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return {"specPath": str(spec_path), "duration": total,
            "clips": len(videos), "overlays": len(spec["overlay"]),
            "texts": len(spec["text"]), "scenes": plan["scenes"],
            "cues": len(cues), "words": len(all_words), "narration": str(narration),
            "narrationSeconds": probe_duration(narration)}


def submit(spec_path: str) -> dict:
    token = Path(os.environ.get("STUDIO_TOKEN_FILE")
                 or (STUDIO_HOME / "ref" / "e2e" / "tok.txt")).expanduser()
    base = os.environ.get("STUDIO_API") or "http://127.0.0.1:8080"
    headers = {"Content-Type": "application/json"}
    if token.is_file():
        headers["Authorization"] = "Bearer " + token.read_text().strip()
    body = json.dumps({"kind": "timeline",
                       "payload": {"specPath": spec_path, "slug": "product-promo"}},
                      ensure_ascii=False).encode()
    req = urllib.request.Request(base + "/api/jobs", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="产品宣传片规格生成（真实素材 + 真实旁白）")
    ap.add_argument("--submit", action="store_true", help="生成后提交到本机队列")
    args = ap.parse_args(argv)
    t0 = time.time()
    info = build_spec()
    print(json.dumps({**info, "seconds": round(time.time() - t0, 1)},
                     ensure_ascii=False, indent=2))
    if args.submit:
        print(json.dumps(submit(info["specPath"]), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
