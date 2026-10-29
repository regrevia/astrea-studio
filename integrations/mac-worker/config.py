"""Configuration and paths for the Creator Studio macOS worker.

Everything is read from the rendered runtime tree (~/CreatorStudio), never from
the worker's own working directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
CONFIG_DIR = STUDIO_HOME / "config"
MPT_HOME = Path(os.environ.get("MPT_HOME", STUDIO_HOME / "data/mpt")).expanduser()
MPT_CHECKOUT = REPO_ROOT / "integrations/vendor/MoneyPrinterTurbo"
SAU_CHECKOUT = REPO_ROOT / "integrations/vendor/social-auto-upload"
PROJECTS_DIR = STUDIO_HOME / "projects"
LOGS_DIR = STUDIO_HOME / "logs"
SAU_COOKIES = SAU_CHECKOUT / "cookies"


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


@dataclass
class WorkerConfig:
    api_base: str = "http://127.0.0.1:8080"
    worker_token: str = ""
    lane_render: str = "render"
    lane_publish: str = "publish"
    poll_interval_sec: float = 3.0
    heartbeat_sec: float = 10.0
    capabilities: list[str] = field(
        default_factory=lambda: ["render_mpt", "render_ffmpeg", "slides_render", "motion_render", "render_timeline", "jianying_draft", "jianying_export", "jianying_open", "probe", "publish_sau", "publish_readback", "capture_web", "inventory"]
    )
    ffmpeg: str = "/opt/homebrew/bin/ffmpeg"
    ffprobe: str = "/opt/homebrew/bin/ffprobe"
    n_threads: int = 2
    voice: str = "zh-CN-XiaoxiaoNeural-Female"
    timezone: str = "Asia/Shanghai"
    version: str = "0.1.0"

    @classmethod
    def load(cls) -> WorkerConfig:
        cfg = cls()
        studio_json = CONFIG_DIR / "studio.json"
        if studio_json.exists():
            data = json.loads(studio_json.read_text())
            cfg.api_base = data.get("workbenchUrl", cfg.api_base).replace("8080", "8080")
            render = data.get("render", {})
            cfg.n_threads = render.get("nThreads", cfg.n_threads)
            publish = data.get("publish", {})
            cfg.timezone = publish.get("timezone", cfg.timezone)
            worker = data.get("worker", {})
            cfg.poll_interval_sec = worker.get("pollIntervalSec", cfg.poll_interval_sec)
            cfg.heartbeat_sec = worker.get("heartbeatSec", cfg.heartbeat_sec)

        token_file = CONFIG_DIR / "worker.token"
        if token_file.exists():
            cfg.worker_token = token_file.read_text().strip()
        if not cfg.worker_token:
            cfg.worker_token = os.environ.get("STUDIO_WORKER_TOKEN", "")

        if os.environ.get("STUDIO_API_BASE"):
            cfg.api_base = os.environ["STUDIO_API_BASE"]

        import shutil

        cfg.ffmpeg = os.environ.get("STUDIO_FFMPEG") or shutil.which("ffmpeg") or cfg.ffmpeg
        cfg.ffprobe = os.environ.get("STUDIO_FFPROBE") or shutil.which("ffprobe") or cfg.ffprobe
        return cfg


def project_dir(run_id: str, revision: int) -> Path:
    path = PROJECTS_DIR / run_id / f"r{revision}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_log_path(job_id: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{job_id}.log"
