"""Creator Studio native macOS worker.

One process, two lanes:
  * render  — MPT CLI, CPU/GPU bound, one job at a time;
  * publish — browser automation, strictly one browser at a time.

The worker never decides that a publication happened. It returns evidence and
lets the production ledger advance the state, because a render that succeeds
and a publication that is unknown must stay separate facts.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from api import ApiError, StudioClient
from capture_web import CaptureError, capture_to_materials
from config import CONFIG_DIR, STUDIO_HOME, WorkerConfig, job_log_path
import capabilities
from publish_sau import PublishError, submit as publish_submit
from render_mpt import RenderError, render as render_mpt
from slides_render import SlidesError, render_from_payload as render_slides
from motion_render_engine import render_motion, MotionError
from jianying_engine import render_jianying, JianyingError

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"

log = logging.getLogger("studio.worker")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FORMAT,
        stream=sys.stdout,
    )
    for noisy in ("urllib3", "botocore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def load_s3_creds() -> dict[str, str]:
    """Read the S3 endpoint and keys from the private runtime env file."""
    env_path = CONFIG_DIR / "compose.env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    return {
        "endpoint": values.get("STUDIO_S3_ENDPOINT", "http://127.0.0.1:9001"),
        "access": values.get("STUDIO_S3_ACCESS_KEY", ""),
        "secret": values.get("STUDIO_S3_SECRET_KEY", ""),
    }


class Worker:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.client = StudioClient(cfg.api_base, cfg.worker_token)
        self.running = True
        self.backoff = 2.0

    # ------------------------------------------------------------- lifecycle

    def stop(self, *_args: Any) -> None:
        log.info("stop requested; finishing the current job first")
        self.running = False

    def run_forever(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        if not self.cfg.worker_token:
            log.error(
                "no worker token. Register one with:\n"
                "  curl -s -X POST %s/api/production/workers/register "
                "-H 'x-api-key: <user api key>' -H 'content-type: application/json' "
                "-d '{\"name\":\"mac-1\",\"capabilities\":[\"render_mpt\",\"publish_sau\"]}'\n"
                "then write the returned token to %s",
                self.cfg.api_base, CONFIG_DIR / "worker.token",
            )
            return 2

        log.info("worker up: api=%s capabilities=%s", self.cfg.api_base, ",".join(self.cfg.capabilities))
        while self.running:
            if not self.client.alive():
                log.warning("api unreachable; retrying in %.0fs", self.backoff)
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 1.6, 30.0)
                continue
            self.backoff = 2.0

            did_work = False
            for lane in (self.cfg.lane_render, self.cfg.lane_publish):
                if not self.running:
                    break
                try:
                    did_work |= self.poll_lane(lane)
                except ApiError as exc:
                    log.error("claim failed on %s: %s", lane, exc)
                    time.sleep(self.backoff)
            if not did_work:
                time.sleep(self.cfg.poll_interval_sec)
        log.info("worker stopped")
        return 0

    # ------------------------------------------------------------------ jobs

    def poll_lane(self, lane: str) -> bool:
        resp = self.client.claim(lane)
        job = (resp or {}).get("job")
        if not job:
            return False
        self.handle(job)
        return True

    def handle(self, job: dict[str, Any]) -> None:
        # claimNext reads with .lean(), so Mongo's `_id` alias (`id` virtual) is
        # absent here even though the rest of the API exposes it. Accept both.
        job = dict(job)
        if not job.get("id") and job.get("_id"):
            job["id"] = str(job["_id"])
        job_id = job.get("id")
        if not job_id:
            log.error("claimed job without an id: %s", job)
            return
        kind = job.get("kind")
        lease = int(job.get("leaseVersion") or 0)
        payload = job.get("payload") or {}
        log.info("claimed job %s kind=%s attempt=%s", job_id, kind, job.get("attempt"))

        def progress(pct: int, stage: str) -> None:
            try:
                self.client.heartbeat(job_id, lease, progress=pct, stage=stage)
            except ApiError as exc:
                log.debug("heartbeat rejected (lease may have moved): %s", exc)

        try:
            if kind == "render_mpt":
                result = self.do_render(job_id, payload, progress)
                # Draft first, then complete: if the draft box refuses the file,
                # the job must fail rather than report a success nobody can use.
                draft = self.do_attach_draft(job_id, payload, result, progress)
                result["materialId"] = draft.get("materialId")
                result["materialGroupId"] = draft.get("groupId")
                result["draftDeduped"] = bool(draft.get("deduped"))
                self.client.complete(job_id, lease, result)
                log.info("job %s succeeded: %s -> draft %s",
                         job_id, result.get("videoKey"), result.get("materialId"))
            elif kind == "slides_render":
                result = self.do_slides(job_id, payload, progress)
                draft = self.do_attach_draft(job_id, payload, result, progress)
                result["materialId"] = draft.get("materialId")
                result["materialGroupId"] = draft.get("groupId")
                result["draftDeduped"] = bool(draft.get("deduped"))
                self.client.complete(job_id, lease, result)
                log.info("job %s slides: %s scenes -> %s",
                         job_id, result.get("scenes"), result.get("videoKey"))
            elif kind == "motion_render":
                result = self.do_motion(job_id, payload, progress)
                self.client.complete(job_id, lease, result)
                log.info("job %s motion: %s frames -> %s",
                         job_id, result.get("frames"), result.get("videoKey"))
            elif kind == "jianying_draft":
                result = self.do_jianying(job_id, payload, progress)
                self.client.complete(job_id, lease, result)
                log.info("job %s jianying: draft -> %s",
                         job_id, result.get("draftPath"))
            elif kind == "publish_sau":
                result = self.do_publish(job_id, payload, progress)
                # Publication status is decided by the ledger, not by this process.
                status = result.pop("status", "submission_unknown")
                self.client.complete(
                    job_id, lease, result,
                    run_patch={"publicationHint": status},
                )
                log.info("job %s returned publication evidence: %s", job_id, status)
            elif kind == "capture_web":
                result = capture_to_materials(
                    payload, STUDIO_HOME / "captures" / job_id
                )
                self.client.complete(job_id, lease, result)
                log.info("job %s captured %s page(s)", job_id, result.get("count"))
            elif kind == "probe":
                self.client.complete(job_id, lease, {"probed": True, "at": time.time()})
            elif kind == "inventory":
                # 面板用的能力快照：平台登录态 / 配音音色 / 工具链版本。
                # 纯本地读取，不联网、不启动浏览器。
                result = capabilities.capabilities()
                self.client.complete(job_id, lease, result)
                log.info(
                    "job %s inventory: platforms ready %s/%s, voices %s",
                    job_id,
                    result.get("summary", {}).get("ready"),
                    result.get("summary", {}).get("total"),
                    len(result.get("voices", {}).get("jianying", [])) + len(result.get("voices", {}).get("edge", [])),
                )
            else:
                self.client.fail(job_id, lease, f"unsupported job kind: {kind}", "failed")
        except (CaptureError, RenderError, PublishError, SlidesError, MotionError) as exc:
            log.error("job %s failed: %s", job_id, exc)
            self.client.fail(job_id, lease, str(exc)[:1500], "failed")
        except ApiError as exc:
            log.error("job %s could not report back: %s", job_id, exc)
        except Exception as exc:  # noqa: BLE001 - a worker must never die on one job
            log.exception("job %s crashed", job_id)
            try:
                self.client.fail(job_id, lease, f"worker exception: {exc}"[:1500], "failed")
            except ApiError:
                pass

    def do_render(self, job_id: str, payload: dict[str, Any],
                  progress: Any) -> dict[str, Any]:
        run_id = payload.get("runId") or ""
        revision = int(payload.get("revision") or 1)
        result = render_mpt(job_id, run_id, revision, payload, self.cfg, progress)

        # Upload the finished file so the backend can turn it into a draft
        # without proxying hundreds of megabytes through the API process.
        s3 = load_s3_creds()
        if not (s3["access"] and s3["secret"]):
            raise RenderError("S3 credentials are missing from config/compose.env")
        from s3 import upload_file

        key = f"studio/runs/{run_id}/r{revision}/{Path(result['videoPath']).name}"
        progress(95, "render:uploading")
        object_key = upload_file(
            s3["endpoint"], s3["access"], s3["secret"], key, Path(result["videoPath"])
        )
        result["videoKey"] = object_key
        result["videoUrl"] = f"/oss/{object_key}"
        return result

    def do_attach_draft(self, job_id: str, payload: dict[str, Any],
                        result: dict[str, Any], progress: Any) -> dict[str, Any]:
        video_key = result.get("videoKey")
        if not video_key:
            raise RenderError("render produced no object key; cannot create a draft")

        run_id = payload.get("runId") or ""
        if not run_id:
            raise RenderError("job payload has no runId; cannot attach a draft")

        progress(97, "draft:attaching")
        try:
            draft = self.client.attach_material(
                run_id,
                video_key,
                title=(payload.get("title") or "").strip(),
                duration=result.get("duration"),
            )
        except ApiError as exc:
            raise RenderError(f"draft box rejected the render: {exc}") from None

        material_id = (draft or {}).get("materialId")
        if not material_id:
            raise RenderError(f"draft box returned no materialId: {draft}")
        log.info("run %s -> draft %s", run_id, material_id)
        return draft if isinstance(draft, dict) else {"materialId": material_id}

    def do_motion(self, job_id: str, payload: dict[str, Any],
                  progress: Any) -> dict[str, Any]:
        """动画引擎：GSAP 逐帧渲染 + SAMI 配音 + 剪映草稿。"""
        run_id = payload.get("runId") or ""
        if not run_id:
            raise MotionError("motion job has no runId; cannot attach a draft")
        revision = int(payload.get("revision") or 1)
        result = render_motion(payload, STUDIO_HOME / "motion" / job_id, progress)

        s3 = load_s3_creds()
        if not (s3["access"] and s3["secret"]):
            raise MotionError("S3 credentials are missing from config/compose.env")
        from s3 import upload_file

        key = f"studio/runs/{run_id}/r{revision}/{Path(result['videoPath']).name}"
        progress(95, "motion:uploading")
        object_key = upload_file(
            s3["endpoint"], s3["access"], s3["secret"], key, Path(result["videoPath"])
        )
        result["videoKey"] = object_key
        result["videoUrl"] = f"/oss/{object_key}"
        return result

    def do_jianying(self, job_id: str, payload: dict[str, Any],
                    progress: Any) -> dict[str, Any]:
        """剪映引擎：只出剪映草稿，不动剪映登录态、不自动导出。

        macOS 上剪映无法无头导出（实测 reload_desktop 返回
        unsupported_platform；自动导出仅 Windows + 剪映≤5.9），
        所以本方法的交付物是「草稿目录」而不是 mp4：
        平台侧把 draftPath 展示给用户，人工在剪映点导出后回填。
        """
        run_id = payload.get("runId") or ""
        result = render_jianying(payload, STUDIO_HOME / "jianying" / job_id, progress)
        if run_id:
            result["runId"] = run_id
        result["engine"] = "jianying"
        return result

    def do_slides(self, job_id: str, payload: dict[str, Any],
                  progress: Any) -> dict[str, Any]:
        run_id = payload.get("runId") or ""
        if not run_id:
            raise SlidesError("slides job has no runId; cannot attach a draft")
        revision = int(payload.get("revision") or 1)
        result = render_slides(payload, STUDIO_HOME / "slides" / job_id, progress)

        s3 = load_s3_creds()
        if not (s3["access"] and s3["secret"]):
            raise SlidesError("S3 credentials are missing from config/compose.env")
        from s3 import upload_file

        key = f"studio/runs/{run_id}/r{revision}/{Path(result['videoPath']).name}"
        progress(95, "slides:uploading")
        object_key = upload_file(
            s3["endpoint"], s3["access"], s3["secret"], key, Path(result["videoPath"])
        )
        result["videoKey"] = object_key
        result["videoUrl"] = f"/oss/{object_key}"
        return result

    def do_publish(self, job_id: str, payload: dict[str, Any],
                   progress: Any) -> dict[str, Any]:
        out = publish_submit(job_id, payload, self.cfg.timezone, progress)
        log_path = job_log_path(job_id)
        try:
            log_path.write_text(json.dumps(out.get("evidence", {}), ensure_ascii=False, indent=2))
        except OSError:
            pass
        return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Creator Studio macOS worker")
    ap.add_argument("--once", action="store_true", help="poll both lanes once then exit")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--print-config", action="store_true", help="show resolved config (no secrets)")
    args = ap.parse_args(argv)

    setup_logging(args.verbose)
    cfg = WorkerConfig.load()

    if args.print_config:
        print(json.dumps({
            "apiBase": cfg.api_base,
            "hasToken": bool(cfg.worker_token),
            "pollIntervalSec": cfg.poll_interval_sec,
            "heartbeatSec": cfg.heartbeat_sec,
            "ffmpeg": cfg.ffmpeg,
            "ffprobe": cfg.ffprobe,
            "timezone": cfg.timezone,
            "capabilities": cfg.capabilities,
        }, indent=2))
        return 0

    worker = Worker(cfg)
    if args.once:
        worker.running = False
        for lane in (cfg.lane_render, cfg.lane_publish):
            try:
                worker.poll_lane(lane)
            except ApiError as exc:
                log.error("claim failed on %s: %s", lane, exc)
        return 0
    return worker.run_forever()


if __name__ == "__main__":
    sys.exit(main())
