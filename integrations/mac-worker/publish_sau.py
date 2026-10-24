"""Douyin publication through the pinned social-auto-upload CLI.

Publication is the only step that can produce irreversible side effects, so this
module never decides "published". It returns whatever evidence the platform page
gave back, and the backend records it as `submitted_unverified` unless a real
work ID/URL was read back. Any ambiguity becomes `submission_unknown`, which the
ledger refuses to resubmit automatically.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import SAU_CHECKOUT

SAU_PYTHON = SAU_CHECKOUT / ".venv/bin/python"
WORK_URL_RE = re.compile(r"https://(?:www\.)?douyin\.com/(?:video|note)/(\d{6,})")


class PublishError(RuntimeError):
    pass


@dataclass
class PublishSpec:
    """What to publish. Either a video (one file) or a note (1..N images).

    Douyin has two separate entry points — upload-video and upload-note — and
    they take different argv, so the kind has to be carried explicitly instead
    of inferred at call time.
    """

    kind: str = "video"          # "video" | "note"
    video: Path | None = None
    images: list[Path] = field(default_factory=list)
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    account: str = ""
    schedule: str | None = None
    thumbnail: Path | None = None
    bgm: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PublishSpec:
        content = payload.get("content") or {}
        account = str(payload.get("accountRef") or "").strip()
        if not account:
            raise PublishError("publish payload has no accountRef (SAU cookie account name)")

        title = str(content.get("title") or payload.get("title") or "").strip()
        if not title:
            raise PublishError("publish payload has no title")

        # content.type is the explicit switch; fall back to sniffing the payload
        # so existing video callers keep working untouched.
        raw_kind = str(content.get("type") or payload.get("type") or "").strip().lower()
        image_raw = content.get("imagePaths") or content.get("images")

        if raw_kind in ("note", "image", "images", "photo") or (not raw_kind and image_raw):
            kind = "note"
        elif raw_kind in ("video", "reel", ""):
            kind = "video"
        else:
            raise PublishError(f"unsupported publication type: {raw_kind!r} (use 'video' or 'note')")

        tags = [str(t).lstrip("#") for t in (content.get("tags") or []) if str(t).strip()]
        schedule = payload.get("schedule") or content.get("schedule")
        desc = str(content.get("desc") or content.get("note") or "")

        if kind == "note":
            images = [Path(str(p)).expanduser() for p in (image_raw or [])]
            if not images:
                raise PublishError(
                    "note publication needs content.imagePaths (list of local image files)"
                )
            missing = [str(p) for p in images if not p.is_file()]
            if missing:
                raise PublishError(f"note image(s) do not exist: {', '.join(missing)}")
            return cls(
                kind="note", images=images, title=title, description=desc,
                tags=tags, account=account, schedule=schedule,
                bgm=str(content.get("bgm") or ""),
            )

        video_raw = payload.get("videoPath") or content.get("videoPath")
        if not video_raw:
            raise PublishError("publish payload has no videoPath")
        video = Path(str(video_raw)).expanduser()
        if not video.is_file():
            raise PublishError(f"video for publication does not exist: {video}")

        thumb = content.get("thumbnailPath")
        return cls(
            kind="video",
            video=video,
            title=title,
            description=desc,
            tags=tags,
            account=account,
            schedule=schedule,
            thumbnail=Path(thumb).expanduser() if thumb else None,
        )


def check_cookie(account: str, cfg_tz: str) -> bool:
    """`sau douyin check` exits 0 only for a live cookie file."""
    proc = subprocess.run(
        [str(SAU_PYTHON), "sau_cli.py", "douyin", "check", "--account", account],
        cwd=str(SAU_CHECKOUT),
        capture_output=True, text=True, timeout=180, check=False,
        env={**os.environ, "TZ": cfg_tz, "PYTHONUNBUFFERED": "1"},
    )
    return proc.returncode == 0 and "invalid" not in (proc.stdout or "")


def build_argv(spec: PublishSpec) -> list[str]:
    if spec.kind == "note":
        # upload-note takes --images (nargs +), --note / --notef, --bgm instead
        # of --file / --desc / --thumbnail. Passing video flags here would make
        # argparse reject the whole call.
        argv = [
            str(SAU_PYTHON), "sau_cli.py", "douyin", "upload-note",
            "--account", spec.account,
            "--images", *[str(p) for p in spec.images],
            "--title", spec.title,
        ]
        if spec.description:
            argv += ["--note", spec.description]
        if spec.tags:
            argv += ["--tags", ",".join(spec.tags)]
        if spec.bgm:
            argv += ["--bgm", spec.bgm]
        if spec.schedule:
            argv += ["--schedule", spec.schedule]
        return argv

    if spec.video is None:
        raise PublishError("video publication has no file")
    argv = [
        str(SAU_PYTHON), "sau_cli.py", "douyin", "upload-video",
        "--account", spec.account,
        "--file", str(spec.video),
        "--title", spec.title,
        "--desc", spec.description,
        "--tags", ",".join(spec.tags),
    ]
    if spec.schedule:
        argv += ["--schedule", spec.schedule]
    if spec.thumbnail and spec.thumbnail.is_file():
        argv += ["--thumbnail", str(spec.thumbnail)]
    return argv


def submit(job_id: str, payload: dict[str, Any], timezone: str,
           on_progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    if not SAU_PYTHON.exists():
        raise PublishError(f"SAU venv missing at {SAU_PYTHON}; run deploy/bootstrap_sau.sh")

    spec = PublishSpec.from_payload(payload)

    if on_progress:
        on_progress(10, "publish:checking-cookie")
    if not check_cookie(spec.account, timezone):
        raise PublishError(
            f"Douyin cookie for account '{spec.account}' is missing or expired; "
            f"run: sau douyin login --account {spec.account}"
        )

    argv = build_argv(spec)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "TZ": timezone,
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8",
    }
    if on_progress:
        on_progress(20, "publish:submitting")

    proc = subprocess.run(
        argv, cwd=str(SAU_CHECKOUT), env=env,
        capture_output=True, text=True, timeout=1800, check=False,
        start_new_session=True,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    if proc.returncode != 0:
        raise PublishError(f"sau exited {proc.returncode}: {out.strip()[-500:]}")

    if "submitted" not in out.lower():
        # A zero exit without the CLI's success line is not evidence of success.
        return {
            "status": "submission_unknown",
            "evidence": {"stdout": out[-2000:], "returncode": proc.returncode},
            "reason": "sau exited 0 but printed no submission confirmation",
        }

    found = WORK_URL_RE.search(out)
    remote_id = found.group(1) if found else ""
    return {
        "status": "submitted_unverified",
        "remotePostId": remote_id,
        "remoteUrl": found.group(0) if found else "",
        "evidence": {"stdout": out[-2000:], "returncode": proc.returncode},
        "reason": "" if remote_id else "page confirmed submission but no work ID was readable",
    }
