#!/usr/bin/env python3
"""把 envoy_intro 这类简报直接渲染成一条纸面版预览片（不经过卡片渲染）。

用法：
  $HOME/CreatorStudio/runtime/integrations/mac-worker/.python3 deploy/render_paper_preview.py                  # 用最近一次成片的时间轴
  $HOME/CreatorStudio/runtime/integrations/mac-worker/.python3 deploy/render_paper_preview.py --out ~/Desktop/x.mp4
配音直接复用同一条片子的音轨，所以能一眼比出「画面层」换了引擎之后差多少。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "mac-worker"))
import paper_render_engine as PAPER  # noqa: E402

XFADE = 0.40


def newest_timeline() -> Path:
    base = Path.home() / "CreatorStudio" / "timeline"
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if (d / "master.mp4").exists() and (d / "spec.json").exists():
            return d
    raise SystemExit("没有找到带 spec.json 的时间轴目录")


def scene_bounds(spec: dict) -> list[tuple[float, float]]:
    scenes = spec.get("video") or []
    out, t = [], 0.0
    for s in scenes:
        dur = float(s.get("dur") or s.get("out") or s.get("length") or 0)
        start = t if not out else t - XFADE
        out.append((start, start + dur))
        t = start + dur
    return out


def split_brief(brief_path: Path) -> list[dict]:
    d = json.loads(brief_path.read_text())
    s = d["spec"]
    nar = s.get("narration") or {}
    rows: list[dict] = []
    rows.append({
        "kicker": "ENVOY / 简报",
        "headline": s.get("hook", ""),
        "body": nar.get("intro", ""),
        "foot": s.get("topic", ""),
    })
    for i, p in enumerate(s.get("points") or []):
        parts = [x.strip() for x in str(p).split("::")]
        title = parts[0] if parts else ""
        data = parts[1] if len(parts) > 1 else ""
        line = nar.get("points", [""] * 9)[i] if i < len(nar.get("points", [])) else ""
        rows.append({
            "kicker": f"{i + 1:02d} / {len(s.get('points') or []):02d}",
            "headline": title,
            "body": line,
            "foot": data,
        })
    rows.append({
        "kicker": "收尾",
        "headline": s.get("cta", ""),
        "body": re.sub(r"^装上 Envoy，链接就是材料。", "", nar.get("outro", "")),
        "foot": s.get("kicker", ""),
    })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", default=str(ROOT / "deploy/specs/envoy_intro.json"))
    ap.add_argument("--out", default=str(Path.home() / "Desktop/Aster-paper-preview.mp4"))
    ap.add_argument("--timeline", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    tl = Path(a.timeline) if a.timeline else newest_timeline()
    spec = json.loads((tl / "spec.json").read_text())
    bounds = scene_bounds(spec)
    rows = split_brief(Path(a.brief))
    if len(rows) != len(bounds):
        print(f"场景数不一致 brief={len(rows)} timeline={len(bounds)}，按 timeline 截断/补齐")
    plan = []
    for i, (st, en) in enumerate(bounds[: len(rows)]):
        r = dict(rows[i])
        r.update({"start": round(st, 3), "end": round(en, 3)})
        plan.append(r)
    audio = tl / "master.mp4"
    out = Path(a.out).expanduser()
    work = tl / "paper_preview"
    print(f"timeline {tl.name} scenes={len(plan)} total={plan[-1]['end']:.2f}s")
    PAPER.render(plan, out, audio=str(audio), workdir=work if a.keep else None)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
