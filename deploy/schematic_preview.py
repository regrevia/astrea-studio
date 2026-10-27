#!/usr/bin/env python3
"""schematic 预览：拿一份 spec + 真实截图，出一条深色仪表盘风格的竖屏片。

    python3 deploy/schematic_preview.py deploy/specs/envoy_intro.json \
        --shots ~/CreatorStudio/materials/shots/github-repo.png \
                ~/CreatorStudio/materials/shots/studio-panel.png \
        --out ~/CreatorStudio/deliver/envoy-schematic-a.mp4

不给 --shots 就是纯 SVG 背景版；截图按屏序贴，第 i 张配 --caps 的第 i 条角标。

截图既可以给绝对路径，也可以写素材包引用 ``pack:<slug>/<name>``（见
integrations/mac-worker/tools/material_packs.py），版位后缀照旧跟在后面：
    --shots pack:envoy-demo/repo-top#396#contain#396
spec 顶层写了 ``"shots": [...]`` 时，--shots 为空就用 spec 里的。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations/mac-worker"))
sys.path.insert(0, str(ROOT / "integrations/mac-worker/export"))
sys.path.insert(0, str(ROOT / "integrations/mac-worker/tools"))

import paper_short  # noqa: E402
from material_packs import PackError, resolve_ref  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", type=Path)
    ap.add_argument("--shots", nargs="*", default=[])
    ap.add_argument("--caps", nargs="*", default=[])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workdir", type=Path)
    ap.add_argument("--style", default="schematic", choices=["schematic", "paper"],
                    help="schematic=深色仪表盘（默认）；paper=暖色印刷纸面 + 纤维纸纹")
    a = ap.parse_args()

    body = json.loads(a.spec.read_text(encoding="utf-8"))
    spec = body.get("spec") or body
    # spec 里也能写 shots；命令行给了就优先命令行（临时替换几张图不用改 spec）
    refs = list(a.shots) or list(spec.get("shots") or [])
    caps = list(a.caps) or [Path(p).stem for p in refs]
    try:
        shots = [(p if p.startswith("sketch:") else resolve_ref(p), caps[i] if i < len(caps) else "")
                 for i, p in enumerate(refs)]
    except PackError as e:
        raise SystemExit(f"素材引用有问题：{e}")
    for p, _ in shots:
        if p.startswith("sketch:"):
            continue
        if not Path(p.split("#")[0]).exists():
            raise SystemExit(f"截图不存在: {p}")

    work = a.workdir or Path(tempfile.mkdtemp(prefix="schematic-"))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(m, flush=True)  # noqa: E731
    info = paper_short.render(spec, work, a.out, style=a.style,
                              shots=shots, log=log)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
