"""草稿体检：把人工看片的那几条硬指标做成可跑的检查，批量出片时当闸门用。

用法：
  python3 tools/qa_draft.py --draft "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/studio_xxx"
  python3 tools/qa_draft.py            # 自动挑最新草稿
  python3 tools/qa_draft.py --json     # 给流水线读

检查项：字幕分段/断句/连播、主文案与屏数、卡面数量、立绘覆盖与姿态、品牌标、总时长。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys

from PIL import Image

DRAFT_ROOT = pathlib.Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
JOB_ROOT = pathlib.Path.home() / "CreatorStudio/jianying"
MAX_SUB_CHARS = 18
MIN_SUB_CHARS = 2
GAP_TOLERANCE = 0.02
DURATION_RANGE = (10.0, 90.0)  # 三点稿压紧到 11~12 秒是正常的，下限只用来抓「配音全丢」
ORPHAN_PUNCT = "，。、；：？！…）」』】》,.;:?!"


def latest_draft() -> pathlib.Path:
    cands = [pathlib.Path(p) for p in glob.glob(str(DRAFT_ROOT / "studio_*")) if (pathlib.Path(p) / "draft_content.json").exists()]
    if not cands:
        raise SystemExit("没有找到草稿")
    return max(cands, key=lambda p: (p / "draft_content.json").stat().st_mtime)


def find_job_dir(draft: pathlib.Path) -> pathlib.Path | None:
    """草稿目录名是 runId 前 24 位，出片目录名带同一个前缀，靠前缀认，别靠时间猜。"""
    prefix = draft.name.split("_", 1)[-1]
    hits = [pathlib.Path(p).parent for p in glob.glob(str(JOB_ROOT / f"{prefix}*" / "s00.png"))]
    if hits:
        return max(hits, key=lambda p: (p / "s00.png").stat().st_mtime)
    stamp = (draft / "draft_content.json").stat().st_mtime
    best, best_gap = None, 1e9
    for p in glob.glob(str(JOB_ROOT / "*" / "s00.png")):
        gap = abs(pathlib.Path(p).stat().st_mtime - stamp)
        if gap < best_gap:
            best, best_gap = pathlib.Path(p).parent, gap
    return best if best_gap < 900 else None


def track(d: dict, name: str) -> list[dict]:
    for t in d.get("tracks") or []:
        if t.get("name") == name:
            return t.get("segments") or []
    return []


def chip_present(card: pathlib.Path) -> bool:
    """品牌标是白色圆角块 + 深色图形：右上角要有成片的亮像素。"""
    img = Image.open(card).convert("RGB")
    w, h = img.size
    # 徽标落点跟着安全区走（y 约 190-360），取右上角一块干净区域数近白像素
    px = list(img.crop((w - 260, 100, w - 60, 500)).resize((100, 200)).getdata())
    bright = sum(1 for r, g, b in px if r > 235 and g > 235 and b > 235)
    return bright >= 1500


def check(draft: pathlib.Path) -> dict:
    d = json.loads((draft / "draft_content.json").read_text())
    materials = d.get("materials") or {}
    texts = {t["id"]: t for t in materials.get("texts") or []}
    videos = {v["id"]: v for v in materials.get("videos") or []}
    reports: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        reports.append({"check": name, "ok": bool(ok), "detail": detail})

    subs = []
    for seg in track(d, "text_sub"):
        body = json.loads(texts.get(seg["material_id"], {}).get("content") or "{}")
        start = seg["target_timerange"]["start"] / 1e6
        dur = seg["target_timerange"]["duration"] / 1e6
        subs.append((start, dur, str(body.get("text") or "")))
    subs.sort()
    add("字幕存在", bool(subs), f"{len(subs)} 段")

    longest = max((len(s[2]) for s in subs), default=0)
    add("单段不超过 %d 字" % MAX_SUB_CHARS, longest <= MAX_SUB_CHARS, f"最长 {longest} 字")
    short = min((len(s[2]) for s in subs), default=0)
    add("没有一闪而过的碎片", short >= MIN_SUB_CHARS, f"最短 {short} 字")

    orphan = [s[2] for s in subs if s[2][:1] in ORPHAN_PUNCT]
    add("行首没有孤儿标点", not orphan, "、".join(orphan) or "无")

    gaps, prev = [], 0.0
    for start, dur, _ in subs:
        if abs(start - prev) > GAP_TOLERANCE:
            gaps.append(round(start - prev, 2))
        prev = start + dur
    add("字幕连播无空档", not gaps, f"空档 {gaps}" if gaps else "接得严")

    total = max((s[0] + s[1] for s in subs), default=0.0)
    add("总时长合理", DURATION_RANGE[0] <= total <= DURATION_RANGE[1], f"{total:.2f}s")

    mains = track(d, "text_main")
    add("主文案分段", bool(mains), f"{len(mains)} 段")

    cards = [videos[s["material_id"]]["path"] for s in track(d, "image_main") if s["material_id"] in videos]
    add("每屏一张卡", len(cards) == len(mains) + 1, f"{len(cards)} 张卡 / {len(mains)} 段主文案")

    chars = track(d, "character")
    add("立绘逐屏出现", len(chars) >= len(cards) * 4, f"{len(chars)} 段立绘 / {len(cards)} 屏")

    if cards:
        first, last = pathlib.Path(cards[0]), pathlib.Path(cards[-1])
        ok = chip_present(first) and chip_present(last)
        add("首尾卡带品牌标", ok, f"开场 {chip_present(first)} / 收尾 {chip_present(last)}")

    trans_ids = {t["id"] for t in materials.get("transitions") or []}
    seen = 0
    for seg in track(d, "image_main"):
        if trans_ids & set(seg.get("extra_material_refs") or []):
            seen += 1
    add("屏间有转场", seen >= max(1, len(cards) - 1), f"{seen} 处转场 / {max(0, len(cards) - 1)} 个衔接")
    tail = track(d, "image_main")
    last_has = bool(tail) and bool(trans_ids & set(tail[-1].get("extra_material_refs") or []))
    add("片尾不加转场", not last_has, "末屏有转场" if last_has else "末屏干净")

    audio = [s for s in track(d, "audio_main") if s.get("material_id")]
    audio_end = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                     for s in audio), default=0) / 1e6
    video_end = max((s["target_timerange"]["start"] + s["target_timerange"]["duration"]
                     for s in track(d, "image_main")), default=0) / 1e6
    add("音画齐平", audio and abs(audio_end - video_end) < 0.35,
        f"音频 {audio_end:.2f}s / 画面 {video_end:.2f}s")

    # 立绘一断场就是贴图感。口型帧本身很短，查的是「整条轨道不断、盖满全片」
    spans = sorted((s["target_timerange"]["start"] / 1e6,
                    (s["target_timerange"]["start"] + s["target_timerange"]["duration"]) / 1e6)
                   for s in chars)
    holes = [round(b[0] - a[1], 2) for a, b in zip(spans, spans[1:]) if b[0] - a[1] > 0.2]
    char_end = spans[-1][1] if spans else 0.0
    add("立绘全程在场", not holes and char_end >= video_end - 0.4,
        f"断场 {holes}" if holes else f"盖到 {char_end:.2f}s / 画面 {video_end:.2f}s")

    job = find_job_dir(draft)
    if job:
        poses = sorted({p.stem.split("_")[2] for p in job.glob("char_*.png") if len(p.stem.split("_")) > 2})
        add("姿态有变化", len(poses) >= 3, "、".join(poses) or "无")
        add("卡面文件齐全", len(list(job.glob("s[0-9][0-9].png"))) == len(cards),
            f"{len(list(job.glob('s[0-9][0-9].png')))} 个卡面文件")

    return {"draft": str(draft), "duration": round(total, 2), "checks": reports}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    draft = pathlib.Path(os.path.expanduser(args.draft)) if args.draft else latest_draft()
    result = check(draft)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"草稿 {result['draft']}")
        print(f"时长 {result['duration']}s\n")
        for row in result["checks"]:
            print(f"{'✓' if row['ok'] else '✗'} {row['check']}：{row['detail']}")
    failed = [r for r in result["checks"] if not r["ok"]]
    if not args.json:
        print("\n通过" if not failed else f"\n{len(failed)} 项没过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
