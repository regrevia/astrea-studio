#!/usr/bin/env python3
"""motion_render_engine.py — 工作台的动画渲染引擎

把 cut_render.py（GSAP 逐帧渲染 + SAMI 配音）包装成 worker 可调用接口，
并在出片后自动生成剪映草稿，让人工可以在剪映里继续精修。

与 slides_render 的区别：
  slides  = 静态图文定格（快、能打字幕，但 PPT 感）
  motion  = GSAP 时间轴 + 镜头硬切（慢，但有真正的动效语言）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

class MotionError(RuntimeError):
    pass


MOTION_HOME = Path.home() / "CreatorStudio" / "motion"
MOTION_PY = Path.home() / "CreatorStudio" / ".motionvenv" / "bin" / "python"
JY_PY = MOTION_HOME / ".jyvenv" / "bin" / "python"
JY_BUILD = MOTION_HOME / "jy_build.py"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 1800) -> str:
    p = subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise MotionError((p.stderr or p.stdout or "")[-2000:])
    return p.stdout


def render_motion(payload: dict[str, Any], job_dir: Path,
                  progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    """payload['spec'] 为分镜 JSON；返回 videoPath / frames / jianyingDraft。"""
    say = progress or (lambda p, m: None)
    spec = payload.get("spec") or {}
    if not spec.get("scenes"):
        raise MotionError("motion spec has no scenes")

    job_dir.mkdir(parents=True, exist_ok=True)
    spec_path = job_dir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    if not MOTION_PY.exists():
        raise MotionError(f"motion venv missing: {MOTION_PY}")

    say(5, "motion:rendering")
    # cut_render.py 自己会把产物写到 <job_dir>/<spec-stem>/
    out = _run([str(MOTION_PY), str(MOTION_HOME / "cut_render.py"), str(spec_path)],
               cwd=MOTION_HOME, timeout=3600)
    result: dict[str, Any] = {}
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    video = result.get("videoPath")
    if not video or not Path(video).exists():
        raise MotionError(f"motion render produced no video: {out[-800:]}")
    say(90, "motion:rendered")
    result["videoPath"] = video

    # 自动生成剪映草稿（失败不阻塞出片）
    plan_file = Path(video).parent / "jy_plan.json"
    if JY_PY.exists() and JY_BUILD.exists() and plan_file.exists():
        try:
            say(92, "motion:jianying-draft")
            name = f"studio_{job_dir.name[:20]}"
            jy_out = _run([str(JY_PY), str(JY_BUILD), str(plan_file), "--name", name],
                          cwd=MOTION_HOME, timeout=600)
            for line in reversed(jy_out.strip().splitlines()):
                if line.strip().startswith("{"):
                    result["jianyingDraft"] = json.loads(line).get("draft")
                    break
        except Exception as exc:  # noqa: BLE001 - 草稿失败不影响成片
            result["jianyingError"] = str(exc)[:400]

    return result
