"""Render a run with the pinned MoneyPrinterTurbo CLI.

Hard rules from the handoff:
  * argument array, never a shell string;
  * explicit cwd = the pinned MPT checkout;
  * explicit MPT_HOME with an absolute path;
  * one MPT task per job, never --batch-file together with --task-id;
  * success is decided by process exit + final JSON + the file actually
    existing and probing as a real video — 100% progress proves nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from config import MPT_CHECKOUT, MPT_HOME, WorkerConfig, job_log_path, project_dir

MPT_PYTHON = MPT_CHECKOUT / ".venv/bin/python"


class RenderError(RuntimeError):
    pass


@dataclass
class RenderSpec:
    """Everything the renderer needs, derived from a run's spec/facts."""

    script: str
    title: str
    materials: list[Path]
    voice: str = "zh-CN-XiaoxiaoNeural-Female"
    aspect: str = "9:16"
    language: str = "zh-CN"
    n_threads: int = 2
    bgm_volume: float = 0.0
    subtitle: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any], cfg: WorkerConfig) -> RenderSpec:
        spec = payload.get("spec") or {}
        brief = (payload.get("brief") or "").strip()
        script = (spec.get("script") or "").strip() or brief
        if not script:
            raise RenderError("run has neither spec.script nor brief; nothing to render")

        materials = [Path(m).expanduser() for m in (spec.get("materials") or [])]
        materials = [m for m in materials if m.is_file()]
        if not materials:
            raise RenderError(
                "no usable local material: set spec.materials to absolute paths of existing files"
            )

        return cls(
            script=script,
            title=(payload.get("title") or spec.get("title") or "studio").strip(),
            materials=materials,
            voice=spec.get("voice") or cfg.voice,
            aspect=spec.get("aspect") or "9:16",
            language=spec.get("language") or "zh-CN",
            n_threads=int(spec.get("nThreads") or cfg.n_threads),
            bgm_volume=float(spec.get("bgmVolume") or 0.0),
            subtitle=bool(spec.get("subtitle", True)),
        )


def _unique_materials(materials: list[Path], target: Path) -> list[Path]:
    """MPT splits --video-materials on commas, so any file whose name contains a
    comma must be linked under an unambiguous name first."""
    stage = target / "materials"
    stage.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for idx, src in enumerate(materials, start=1):
        if "," in src.name:
            link = stage / f"scene-{idx:02d}{src.suffix}"
            if not link.exists():
                try:
                    link.symlink_to(src)
                except OSError:
                    shutil.copy2(src, link)
            out.append(link)
        else:
            out.append(src)
    return out


def build_argv(spec: RenderSpec, task_id: str) -> list[str]:
    argv = [
        str(MPT_PYTHON),
        "cli.py",
        "--task-id", task_id,
        "--video-script", spec.script,
        "--video-source", "local",
        "--video-materials", ",".join(str(m) for m in spec.materials),
        "--video-concat-mode", "sequential",
        "--video-fit-mode", "contain",
        "--video-aspect", spec.aspect,
        "--video-language", spec.language,
        "--voice-name", spec.voice,
        "--n-threads", str(max(1, spec.n_threads)),
        "--bgm-volume", str(spec.bgm_volume),
        "--video-count", "1",
    ]
    if not spec.subtitle:
        argv.append("--no-subtitle-enabled")
    return argv


def render(job_id: str, run_id: str, revision: int, payload: dict[str, Any],
           cfg: WorkerConfig, on_progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    if not MPT_PYTHON.exists():
        raise RenderError(f"MPT venv missing at {MPT_PYTHON}; run deploy/bootstrap_mpt.sh")

    spec = RenderSpec.from_payload(payload, cfg)
    workdir = project_dir(run_id, revision)
    spec.materials = _unique_materials(spec.materials, workdir)

    task_id = payload.get("mptTaskId") or str(uuid.uuid4())
    argv = build_argv(spec, task_id)

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "TZ": cfg.timezone,
        "MPT_HOME": str(MPT_HOME),
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8",
    }

    log_path = job_log_path(job_id)
    if on_progress:
        on_progress(5, "render:starting")

    with log_path.open("ab") as log:
        # start_new_session so a worker restart can kill the whole MPT tree.
        proc = subprocess.Popen(
            argv,
            cwd=str(MPT_CHECKOUT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if on_progress:
            on_progress(10, "render:running")
        stdout, stderr = proc.communicate()
        log.write(b"--- argv ---\n" + json.dumps(argv, ensure_ascii=False).encode() + b"\n")
        log.write(b"--- stdout ---\n" + stdout + b"--- stderr ---\n" + stderr + b"\n")

    if proc.returncode != 0:
        tail = stderr.decode(errors="replace").strip().splitlines()[-12:]
        raise RenderError(
            f"MPT exited {proc.returncode}: " + " | ".join(tail[-4:] or ["no stderr"])
        )

    # Machine-readable result is the last JSON line on stdout.
    result: dict[str, Any] = {}
    for line in reversed(stdout.decode(errors="replace").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    videos = (result.get("result") or {}).get("videos") or []
    if not videos:
        raise RenderError(f"MPT reported success but produced no video (task {task_id})")

    video = Path(videos[0])
    if not video.is_file() or video.stat().st_size < 1024:
        raise RenderError(f"MPT output missing or suspiciously small: {video}")

    probe = subprocess.run(
        [cfg.ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height",
         "-of", "json", str(video)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if probe.returncode != 0:
        raise RenderError(f"ffprobe rejected the output: {probe.stderr[:200]}")
    meta = json.loads(probe.stdout or "{}")
    streams = meta.get("streams") or []
    duration = float((meta.get("format") or {}).get("duration") or 0)
    if not streams or duration <= 0:
        raise RenderError("rendered file has no decodable stream")
    if not any(s.get("codec_name") for s in streams):
        raise RenderError("rendered file has no video codec")

    if on_progress:
        on_progress(90, "render:verifying")

    return {
        "taskId": task_id,
        "videoPath": str(video),
        "duration": duration,
        "width": streams[0].get("width"),
        "height": streams[0].get("height"),
        "codec": streams[0].get("codec_name"),
        "sizeBytes": video.stat().st_size,
        "subtitlePath": (result.get("result") or {}).get("subtitle_path") or "",
        "audioPath": (result.get("result") or {}).get("audio_file") or "",
        "script": (result.get("result") or {}).get("script") or spec.script,
        "logPath": str(log_path),
    }
