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
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from api import ApiError, StudioClient
from capture_web import CaptureError, capture_to_materials
from config import CONFIG_DIR, STUDIO_HOME, WorkerConfig, job_log_path
import pylocator
import capabilities
from publish_sau import PublishError, submit as publish_submit
from publish_sau import run_readback_job
from render_mpt import RenderError, render as render_mpt
from slides_render import SlidesError, render_from_payload as render_slides
from motion_render_engine import render_motion, MotionError
from jianying_engine import render_jianying, JianyingError
import claim_dispatch as claim
import export_landing as landing
import video_qa
from export import timeline_render as timeline
from export import vertical_short as vertical
from export import deliver as delivery

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"

# 失败原因码：后端 /fail 只有一个 error 文本字段（见 api.StudioClient.fail），
# 所以原因码写成文本前缀 reasonCode=<code>，平台侧按前缀解析、人看后半句。
# 没有原因码的失败等于没有归因，重试策略和用户提示都无从下手。
REASON_BY_EXC = {
    "CaptureError": "capture_failed",
    "RenderError": "render_failed",
    "PublishError": "publish_failed",
    "SlidesError": "slides_failed",
    "MotionError": "motion_failed",
    "TimelineError": "timeline_failed",
    "JianyingError": "jianying_draft_failed",
}
REASON_CODES = (
    "unsupported_job_kind",
    "worker_exception",
    "jianying_export_failed",
    "export_needs_manual_gui",
    "export_user_busy",
    "export_output_exists",
    "export_ambiguous_output",
    "export_timeout",
    "export_invalid_profile",
    "export_aspect_mismatch",
    "export_param_not_applied",
    "export_panel_unavailable",
    "export_draft_not_open",
    "export_probe_failed",
    "draft_not_found",
    "draft_media_missing",
    "draft_spec_mismatch",
    "jianying_app_missing",
    "jianying_open_failed",
)


def _check_payload(stdout: str) -> dict:
    """从 `open_draft.py --check` 的 JSON 行里取体检结论，取不到就当没有。"""
    import json as _json
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = _json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _check_blockers(stdout: str) -> list[str]:
    blockers = _check_payload(stdout).get("blockers")
    return [b for b in blockers if isinstance(b, str)] if isinstance(blockers, list) else []


def _check_field(stdout: str, key: str):
    return _check_payload(stdout).get(key)


def reason_text(code: str, message: str) -> str:
    """把原因码拼到错误文本最前面（后端只收文本，这是唯一能带归因的位置）。"""
    return "reasonCode=%s %s" % (code or "unknown", message or "")


def reason_of(exc: BaseException) -> str:
    for klass, code in REASON_BY_EXC.items():
        if type(exc).__name__ == klass:
            return code
    return REASON_BY_EXC.get(type(exc).__name__, "job_failed")

log = logging.getLogger("studio.worker")


class TimelineError(RuntimeError):
    """C 路线（自有 spec 渲染 + 平台交付）失败。独立成类，便于出可读的失败原因。"""


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


# 任务种类：定义在 worker 侧，jobs.py 只负责把字符串发过来。
# jianying_draft 出草稿（中间态），jianying_export 收导出产物，两步分开记。
JOB_JIANYING_DRAFT = "jianying_draft"
JOB_JIANYING_EXPORT = "jianying_export"
# 只把草稿在剪映里打开给人改，不导出、不点任何面板。
JOB_JIANYING_OPEN = "jianying_open"


def _wait_idle(payload: dict) -> float:
    """waitIdle 允许显式 0（=现在就动手），不能因为 0 是假值就退回 30。"""
    v = payload.get("waitIdle")
    return 30.0 if v is None else max(0.0, float(v))


def _land_export(video: str, payload: dict, job_id: str, revision: int,
                 outdir: str) -> dict | None:
    """成片入库：只认库里的落点，不留在 out/<jobId> 里当散件。

    收口细节（体检证据、字段兜底、失败留痕不丢文件）在 export_landing.py，
    这里只负责告诉它去哪找证据、以及把告警接到 worker 日志上。
    """
    roots = []
    try:
        import config as _cfg  # noqa: WPS433
        roots.append(Path(_cfg.REPO_ROOT))
    except Exception:
        pass
    return landing.land(video, payload, job_id, revision, outdir, roots=roots,
                        warn=log.warning)


def _land_timeline(video: str, payload: dict, job_id: str, revision: int, *,
                   platform: str = "", duration: float | None = None,
                   meta: dict | None = None) -> dict | None:
    """timeline 通道的成片也入库：母版与各平台规格走同一套命名与台账。

    与剪映导出不同，这里成片还在 deliver/ 里等 S3 上传，所以只拷贝不搬走。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import library as lib  # noqa: WPS433
        project = str(payload.get("libraryProjectName") or payload.get("libraryProject")
                      or payload.get("project") or "studio")
        project_id = str(payload.get("libraryProject") or "")
        title = str(payload.get("libraryTitle") or payload.get("title")
                    or payload.get("runId") or job_id)
        return lib.land(video, project=project, title=title, project_id=project_id,
                        platform=platform or str(payload.get("platform") or "douyin"),
                        revision=revision, run_id=str(payload.get("runId") or ""),
                        job_id=job_id, duration=duration,
                        meta=dict(meta or {}), move=False)
    except Exception as exc:  # 入库失败不能吞掉成片，但也必须留痕
        log.warning("timeline 成片入库失败（%s）：%s 仍留在 %s", job_id, exc, video)
        return None


def _foreground_pid() -> int:
    """当前前台 App 的 pid；拿不到就 0（导出结束后不还台，但也不算失败）。"""
    try:
        exe = Path(__file__).with_name("bin") / "ax_tool"
        if not exe.exists():
            exe = Path.home() / "CreatorStudio" / "bin" / "ax_tool"
        out = subprocess.run([str(exe), "front"], capture_output=True, text=True, timeout=5)
        first = (out.stdout or "").split()
        return int(first[0]) if first and first[0].isdigit() else 0
    except Exception:
        return 0


def screen_locked() -> bool:
    """屏锁着的时候别去点剪映：锁屏下 GUI 自动化拿不到窗口，只会空转。

    这里用 CoreGraphics 的会话字典判断，比读 assertions 稳；拿不到字典就当没锁。
    """
    try:
        import ctypes
        import ctypes.util

        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        session = cg.CGSessionCopyCurrentDictionary()
        if not session:
            return False
        key = cf.CFStringCreateWithCString(None, b"CGSSessionScreenIsLocked", 0x08000100)
        value = cf.CFDictionaryGetValue(session, key)
        return bool(value and cf.CFBooleanGetValue(value))
    except Exception:
        return False


class Worker:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg
        self.client = StudioClient(cfg.api_base, cfg.worker_token)
        self.running = True
        self.backoff = 2.0
        self._caps_synced_at = 0.0

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
        # 解释器放在启动日志第二行：先看它，再看任务是否 exit 69
        log.info("%s", pylocator.describe())
        try:
            self.sync_capabilities()
        except Exception as exc:  # noqa: BLE001 - 启动时后端没起来不该让 worker 退出
            log.warning("initial capability sync skipped: %s", exc)
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
                # 空闲时定期重对齐：服务端换版本、或本机配置加了新能力，
                # 都不该等到人工去改数据库才生效。
                if time.time() - self._caps_synced_at > 600:
                    try:
                        self.sync_capabilities()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("capability sync skipped: %s", exc)
                time.sleep(self.cfg.poll_interval_sec)
        log.info("worker stopped")
        return 0

    # ------------------------------------------------------------------ jobs

    def sync_capabilities(self) -> None:
        """把配置里的 capabilities 推给服务端；失败只降级成告警。"""
        self._caps_synced_at = time.time()
        try:
            resp = self.client.refresh_capabilities(self.cfg.capabilities)
        except ApiError as exc:
            if exc.status in (404, 405):
                log.warning(
                    "server 尚无 capabilities 自刷新端点（旧镜像）；"
                    "claim 过滤仍按注册时的能力列表，新增 kind 需要手改库",
                )
            else:
                log.warning("capability refresh failed: %s", exc)
            return
        got = (resp or {}).get("capabilities") if isinstance(resp, dict) else None
        log.info("capabilities synced: %s", ",".join(got or self.cfg.capabilities))

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
        # job log 首行固定是解释器：渲染失败先看这一行排掉 exit 69 类问题
        pylocator.stamp_job_log(job_log_path(job_id))

        def progress(pct: int, stage: str) -> None:
            try:
                self.client.heartbeat(job_id, lease, progress=pct, stage=stage)
            except ApiError as exc:
                log.debug("heartbeat rejected (lease may have moved): %s", exc)

        # 租约只有 60s，一次竖屏渲染要两三分钟、中间可能一个 progress 都没有。
        # 不单独续约，服务端会在渲染途中判租约过期、补一条重复 job 再渲一遍。
        stop_renew = threading.Event()

        def renew_lease() -> None:
            interval = max(5.0, float(self.cfg.heartbeat_sec or 30) / 2.0)
            while not stop_renew.wait(interval):
                try:
                    self.client.heartbeat(job_id, lease)
                except ApiError as exc:
                    log.debug("lease renew failed: %s", exc)

        threading.Thread(target=renew_lease, daemon=True).start()

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
            elif kind == "render_timeline":
                result = self.do_timeline(job_id, payload, progress)
                # 母版已经能直接发平台；draft 只是把它登记进素材库。
                # 登记失败不能把渲染判死，否则「片已经渲好了」这个事实会丢。
                # 平台侧 attachDraft 写在 spec 里，本地试跑可以写在 payload 上
                _spec = payload.get("spec")
                _want_draft = payload.get("attachDraft")
                if _want_draft is None and isinstance(_spec, dict):
                    _want_draft = _spec.get("attachDraft")
                if _want_draft is None:
                    _want_draft = True
                if _want_draft:
                    try:
                        draft = self.do_attach_draft(job_id, payload, result, progress)
                        result["materialId"] = draft.get("materialId")
                        result["materialGroupId"] = draft.get("groupId")
                        result["draftDeduped"] = bool(draft.get("deduped"))
                    except RenderError as exc:
                        log.warning("job %s timeline rendered but draft attach failed: %s", job_id, exc)
                        result["draftError"] = str(exc)[:500]
                self.client.complete(job_id, lease, result)
                log.info("job %s timeline: %s'd -> %s (+%s platform file(s))",
                         job_id, result.get("duration"), result.get("videoKey"),
                         len(result.get("deliveries") or []))
            elif kind == "jianying_draft":
                result = self.do_jianying(job_id, payload, progress)
                self.client.complete(job_id, lease, result)
                draft_path = result.get("draftPath")
                log.info("job %s jianying: draft -> %s", job_id, draft_path)
                if draft_path:
                    # 后端 completeJob 的白名单没有 draftPath，草稿路径只能留在日志里；
                    # 统一一行 DRAFT_PATH= 方便 grep，和首行的解释器指纹同一个位置。
                    try:
                        with open(job_log_path(job_id), "a", encoding="utf-8") as fh:
                            fh.write(f"DRAFT_PATH={draft_path}\n")
                    except OSError as exc:
                        log.warning("job %s could not record draft path: %s", job_id, exc)
            elif kind == JOB_JIANYING_OPEN:
                result = self.do_jianying_open(job_id, payload, progress)
                self.client.complete(job_id, lease, result)
                log.info("job %s jianying open: stage=%s", job_id, result.get("stage"))
            elif kind == JOB_JIANYING_EXPORT:
                # 剪映在 macOS 上没有无头导出：这一步要么等人工点完导出后归位产物，
                # 要么在 payload 显式 allowGui=true 时驱动导出面板（仍要求用户空闲）。
                result = self.do_jianying_export(job_id, payload, progress)
                self.client.complete(job_id, lease, result)
                log.info("job %s jianying export: stage=%s path=%s",
                         job_id, result.get("stage"), (result.get("output") or {}).get("videoPath"))
            elif kind == "publish_sau":
                result = self.do_publish(job_id, payload, progress)
                # Publication status is decided by the ledger, not by this process.
                status = result.pop("status", "submission_unknown")
                self.client.complete(
                    job_id, lease, result,
                    run_patch={"publicationHint": status},
                )
                log.info("job %s returned publication evidence: %s", job_id, status)
            elif kind == "publish_readback":
                result = run_readback_job(payload, self.client)
                self.client.complete(job_id, lease, result)
                log.info("job %s readback: %s applied=%s", job_id, result.get("state"), result.get("applied"))
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
                self.client.fail(
                    job_id, lease,
                    reason_text("unsupported_job_kind", f"unsupported job kind: {kind}"),
                    "failed",
                )
        except (CaptureError, RenderError, PublishError, SlidesError, MotionError,
                TimelineError, JianyingError) as exc:
            log.error("job %s failed: %s", job_id, exc)
            self.client.fail(job_id, lease, reason_text(reason_of(exc), str(exc)[:1400]), "failed")
        except ApiError as exc:
            log.error("job %s could not report back: %s", job_id, exc)
        except Exception as exc:  # noqa: BLE001 - a worker must never die on one job
            log.exception("job %s crashed", job_id)
            try:
                self.client.fail(
                    job_id, lease,
                    reason_text("worker_exception", f"worker exception: {exc}"[:1400]),
                    "failed",
                )
            except ApiError:
                pass
        finally:
            stop_renew.set()

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

        这里是**中间态**：draft ready ≠ 成片。所以返回值里 videoPath 恒为 None、
        stage 恒为 draft_ready，出片由 jianying_export 那一步接。
        原生效果没能写进草稿时，report 里的 unsupported/missing 原样带上并标 degraded，
        不能把「草稿建成了但特效丢了」包成一次干净的成功。
        """
        run_id = payload.get("runId") or ""
        revision = int(payload.get("revision") or 1)
        spec_payload = dict(payload)
        spec_payload["runId"] = run_id
        spec_payload["revision"] = revision
        result = render_jianying(spec_payload, STUDIO_HOME / "jianying" / job_id, progress)
        result["runId"] = run_id or result.get("runId")
        result["revision"] = revision
        result["jobId"] = job_id
        result["engine"] = "jianying"
        result["stage"] = "draft_ready"
        result.setdefault("output", {"videoPath": None, "requiresManualExport": True})
        caps = result.get("capabilities") or {}
        if caps.get("unsupported") or caps.get("missing"):
            result["degraded"] = True
            result["degradedReason"] = "部分原生效果未写入草稿，明细见 report"
        return result

    def do_jianying_open(self, job_id: str, payload: dict[str, Any],
                         progress: Any) -> dict[str, Any]:
        """在剪映里打开一条草稿，交给人继续改。

        只做「打开」：不点导出、不动时间线、不保存。剪映必须前台才能操作，
        所以这一步会把它带到前台——只在人明确点「在剪映中打开」时才走。
        """
        draft_path = str(payload.get("draftPath") or payload.get("draft_path") or "")
        if not draft_path:
            raise JianyingError("jianying_open 需要 draftPath（草稿目录），不猜最新草稿")
        draft = Path(draft_path)
        if not draft.is_dir():
            raise JianyingError(reason_text("draft_not_found",
                                            f"草稿目录不存在：{draft_path}"))
        base = {"jobId": job_id, "draftPath": draft_path,
                "draftName": draft.name,
                "runId": payload.get("runId") or "",
                "revision": int(payload.get("revision") or 1)}
        if payload.get("check"):
            # 只体检：不点鼠标、不抢前台，人正在用机器时也能跑。
            sha = Path(__file__).with_name("export") / "open_draft.py"
            try:
                probed = subprocess.run(
                    [sys.executable, str(sha), "--name", draft.name, "--check",
                     "--draft-path", draft_path],
                    capture_output=True, text=True, timeout=120,
                )
            except subprocess.TimeoutExpired:
                raise JianyingError(reason_text("jianying_open_failed",
                                                f"体检 120s 没结束（{draft.name}）"))
            if probed.returncode != 0:
                tail = ((probed.stdout or "") + (probed.stderr or "")).strip()[-400:]
                blockers = _check_blockers(probed.stdout)
                code = {"media_missing": "draft_media_missing",
                        "spec_mismatch": "draft_spec_mismatch",
                        "app_missing": "jianying_app_missing"}.get(
                            blockers[0] if blockers else "", "draft_not_found")
                label = {"draft_spec_mismatch": "草稿和这次任务对不上",
                         "draft_media_missing": "草稿引用的素材不全",
                         "jianying_app_missing": "这台机器没装剪映",
                         "draft_not_found": "草稿目录不在"}[code]
                detail = tail or "无输出"
                if code == "draft_spec_mismatch":
                    diffs = _check_field(probed.stdout, "mismatches")
                    if isinstance(diffs, list) and diffs:
                        detail = "；".join(str(x) for x in diffs)
                raise JianyingError(reason_text(code, f"{label}：{detail}"))
            import json as _json
            try:
                info = _json.loads((probed.stdout or "").strip().splitlines()[-1])
            except (ValueError, IndexError):
                raise JianyingError(reason_text("jianying_open_failed",
                                                "体检输出不是 JSON"))
            return dict(base, **{
                "stage": "draft_checked",
                "output": dict(info, opened=False),
                "note": "只体检：草稿目录和剪映都在，没动剪映也没抢前台。",
            })
        if screen_locked():
            return dict(base, **{
                "stage": "blocked",
                "reasonCode": "screen_locked",
                "output": {"opened": False},
                "note": "屏幕锁着，这轮没动剪映；解锁后再点一次。",
            })
        progress(20, "jianying:open_draft")
        opener = Path(__file__).with_name("export") / "open_draft.py"
        try:
            opened = subprocess.run(
                [sys.executable, str(opener), "--name", draft.name,
                 "--wait-idle", str(float(_wait_idle(payload)))],
                capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise JianyingError(reason_text("jianying_open_failed",
                                            f"打开草稿 600s 没结束（{draft.name}）"))
        tail = ((opened.stdout or "") + (opened.stderr or "")).strip()[-600:]
        if opened.returncode != 0:
            reason = {9: "export_user_busy", 2: "export_draft_not_open"}.get(
                opened.returncode, "jianying_open_failed")
            message = {
                9: "你正在用这台 Mac，这轮没抢鼠标；空下来再点一次。",
                2: "剪映首页没找到这条草稿；先刷新草稿箱再试。",
            }.get(opened.returncode, "打开草稿失败（rc=%d）" % opened.returncode)
            if opened.returncode in (2, 9):
                return dict(base, **{
                    "stage": "blocked",
                    "reasonCode": reason,
                    "output": {"opened": False},
                    "note": message,
                    "log": tail,
                })
            raise JianyingError(reason_text(reason, "%s：%s" % (message, tail or "无输出")))
        return dict(base, **{
            "stage": "draft_opened",
            "output": {"opened": True},
            "note": "草稿已在剪映里打开，改完自己点导出，再回平台认领成片。",
            "log": tail,
        })

    def do_jianying_export(self, job_id: str, payload: dict[str, Any],
                           progress: Any) -> dict[str, Any]:
        """剪映出片：把「人点完导出」之后的产物绑到本任务并体检。

        默认不动 GUI——剪映的导出面板必须真人点（Qt 控件对 AXPress 无效），
        没有 allowGui=true 时这里只回报「排队等人工导出」，不假装自己导出了。
        带 allowGui=true 才驱动 export/jianying_export.py，且导出端本身要求
        用户空闲（等不到就退 9），绝不为了跑任务把用户正在打字的鼠标挪走。
        """
        run_id = payload.get("runId") or ""
        revision = int(payload.get("revision") or 1)
        draft_path = str(payload.get("draftPath") or payload.get("draft_path") or "")
        platform = str(payload.get("platform") or "douyin")
        outdir = str(payload.get("outdir") or (STUDIO_HOME / "out"))
        base = {
            "jobId": job_id, "runId": run_id, "revision": revision,
            "platform": platform, "draftPath": draft_path or None,
        }
        claim_path = str(payload.get("claimPath") or "")
        if claim_path:
            # 人工已经在剪映里点过导出，这里只认领产物：不开面板、不动鼠标，
            # 体检（时长/画布/帧率）与自动导出同一套，过了才入库。
            if not draft_path:
                raise JianyingError("认领成片需要 draftPath：没有草稿就无从核对时长与画布")
            progress(20, "jianying:claim")
            script = Path(__file__).with_name("export") / "jianying_export.py"
            argv = claim.build_argv(
                sys.executable, script, claim_path=claim_path, job_id=job_id,
                revision=revision, platform=platform, outdir=outdir,
                draft_path=draft_path, canvas=str(payload.get("canvas") or ""),
            )
            try:
                proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                raise JianyingError(f"认领进程 600s 没结束（{script}）")
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-1200:]
            if proc.returncode != 0:
                code, message = claim.failure(proc.returncode, tail)
                raise JianyingError(reason_text(code, message))
            final_dir = Path(outdir) / job_id / f"r{revision}"
            video = claim.find_video(final_dir)
            if not video:
                raise JianyingError(f"认领进程返回 0 但 {final_dir} 里没有产物")
            landed = _land_export(video, payload, job_id, revision, outdir)
            output, video = claim.finish(video, final_dir, landed, claimed=True)
            return dict(base, **{
                "stage": "exported",
                "output": output,
                "videoPath": video,
                "log": tail,
            })

        if not payload.get("allowGui"):
            # 明确不替你动鼠标：把活留在队列里，等人工在剪映点导出。
            return dict(base, **{
                "stage": "awaiting_manual_export",
                "reasonCode": "export_needs_manual_gui",
                "output": {"videoPath": None, "requiresManualExport": True},
                "expectedOutputDir": str(Path(outdir) / job_id / f"r{revision}"),
                "note": ("macOS 上剪映只能人工点导出。导出后带 allowGui=true 再跑一次本任务，"
                         "产物会绑到 out/<jobId>/r<rev>/ 并做体检。"),
            })

        if screen_locked():
            # 锁屏时 GUI 自动化必然空转。不烧 1800 秒超时，直接交回人工。
            return dict(base, **{
                "stage": "awaiting_manual_export",
                "reasonCode": "screen_locked",
                "output": {"videoPath": None, "requiresManualExport": True},
                "expectedOutputDir": str(Path(outdir) / job_id / f"r{revision}"),
                "note": "屏幕锁着，这轮不开剪映；解锁后重新发起导出。",
            })

        if not draft_path:
            raise JianyingError("jianying_export 需要 draftPath（草稿目录），不猜最新草稿")

        # 记下「导出前用户正在用的 App」，点完导出立刻把前台还回去
        fore_pid = _foreground_pid()

        progress(15, "jianying:open_draft")
        # 导出驱动只认「导出面板已经能开出来」，而面板只存在于已打开的草稿里；
        # 剪映没有「按路径打开草稿」的入口，草稿必须从首页草稿箱点进去。这一步以前靠人，
        # 于是 allowGui 也总在 open_panel 处 rc=2。这里先把它点开，失败就明确报原因。
        opener = Path(__file__).with_name("export") / "open_draft.py"
        draft_name = Path(draft_path).name
        opened = subprocess.run(
            [sys.executable, str(opener), "--name", draft_name,
             "--wait-idle", str(float(_wait_idle(payload)))],
            capture_output=True, text=True, timeout=600,
        )
        open_tail = ((opened.stdout or "") + (opened.stderr or "")).strip()[-600:]
        if opened.returncode != 0:
            reason = {9: "export_user_busy", 2: "export_draft_not_open"}.get(
                opened.returncode, "export_draft_not_open")
            raise JianyingError(reason_text(
                reason, "草稿没能打开（rc=%d）：%s" % (opened.returncode, open_tail or "无输出")))

        progress(20, "jianying:export")
        script = Path(__file__).with_name("export") / "jianying_export.py"
        argv = [
            sys.executable, str(script),
            "--job-id", job_id,
            "--revision", str(revision),
            "--platform", platform,
            "--outdir", outdir,
            "--draft-path", draft_path,
            "--wait-idle", str(float(_wait_idle(payload))),
            "--return-pid", str(int(fore_pid or 0)),
            "--timeout", str(int(payload.get("exportTimeout") or 900)),
        ]
        if payload.get("canvas"):
            argv += ["--canvas", str(payload["canvas"])]
        if payload.get("allowAspectMismatch"):
            argv.append("--allow-aspect-mismatch")
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise JianyingError(f"导出进程 1800s 没结束（{script}）")
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-1200:]
        code = proc.returncode
        if code == 0:
            final_dir = Path(outdir) / job_id / f"r{revision}"
            video = claim.find_video(final_dir)
            if not video:
                raise JianyingError(f"导出进程返回 0 但 {final_dir} 里没有产物")
            landed = _land_export(video, payload, job_id, revision, outdir)
            output, video = claim.finish(video, final_dir, landed)
            return dict(base, **{
                "stage": "exported",
                "output": output,
                "videoPath": video,
                "log": tail,
            })
        if code == 9:
            # 用户一直在用机器：排队，不是失败
            return dict(base, **{
                "stage": "awaiting_manual_export",
                "reasonCode": "export_user_busy",
                "output": {"videoPath": None, "requiresManualExport": True},
                "note": "用户在忙，这轮没动鼠标；等下一轮或人工点导出。",
                "log": tail,
            })
        codes = {2: "export_invalid_profile", 3: "export_param_not_applied",
                 4: "export_panel_unavailable", 5: "export_timeout",
                 6: "export_probe_failed", 7: "export_aspect_mismatch",
                 8: "export_output_exists", 10: "export_ambiguous_output",
                 11: "export_draft_revision_mismatch", 12: "draft_media_changed"}
        raise JianyingError(reason_text(codes.get(code, "jianying_export_failed"),
                                        f"导出失败 rc={code}: {tail}"))

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

    def _render_paper(self, script: dict[str, Any], workdir: Path,
                      progress: Any) -> dict[str, Any]:
        """纸面母版：逐帧画一张纸，再叠人物层和音轨。

        版式在 paper_short 里，这里只管把它接进 timeline 那条通道：参数、
        进度、出货字段都跟 factcard 一致，平台侧不用知道换了个引擎。
        """
        from export import paper_short as paper
        out = workdir / "master.mp4"
        progress(8, "timeline:paper")
        started = time.time()
        try:
            info = paper.render(script, workdir, out,
                               voice=str(script.get("voice") or ""),
                               log=lambda msg: log.info("paper: %s", msg))
        except Exception as exc:
            raise TimelineError(f"纸面渲染失败: {exc}") from None
        built = json.loads(Path(info["specPath"]).read_text(encoding="utf-8"))
        ev = {
            "output": info["videoPath"],
            "duration": info["duration"],
            "canvas": {"width": 1080, "height": 1920, "fps": 30},
            "clips": info["scenes"],
            "texts": 0,
            "audios": len(built.get("audio") or []),
            "seconds": round(time.time() - started, 1),
        }
        return {"ev": ev, "specPath": info["specPath"]}

    def do_timeline(self, job_id: str, payload: dict[str, Any],
                    progress: Any) -> dict[str, Any]:
        """C 路线主入口：自有 timeline spec → 母版 mp4（可选各平台规格）。

        全程纯 ffmpeg/Pillow，不起剪映、不抢前台、不动鼠标——这是它相对
        「剪映导出面板」的唯一重要差别：可以在后台排队跑，也能整段搬进容器。
        spec 由 payload.spec 直接给（前端产出），也可用 specPath 指本机文件。
        """
        run_id = payload.get("runId") or ""
        revision = int(payload.get("revision") or 1)
        spec_obj = payload.get("spec")
        # worker 的 PATH 里可能没有 homebrew；用配置里的绝对路径覆盖模块常量
        for ff in (self.cfg.ffmpeg, self.cfg.ffprobe):
            if Path(ff).exists():
                timeline.FFMPEG = delivery.FFMPEG = self.cfg.ffmpeg
                timeline.FFPROBE = delivery.FFPROBE = self.cfg.ffprobe

        # 外层 spec（可能带引擎外壳）与内层 timeline 本体都可能是 assetBase 的来源
        src_spec: dict[str, Any] = spec_obj if isinstance(spec_obj, dict) else {}
        spec_path_hint = payload.get("specPath")
        workdir = STUDIO_HOME / "timeline" / job_id
        workdir.mkdir(parents=True, exist_ok=True)

        # 平台侧的 run.spec 是「引擎 + 参数」的外壳，timeline 本体可能在 timeline 键下
        if isinstance(spec_obj, dict) and not spec_obj.get("canvas") \
                and isinstance(spec_obj.get("timeline"), dict):
            inner = dict(spec_obj["timeline"])
            for k in ("platforms", "fit", "assetBase", "attachDraft"):
                if k in spec_obj and k not in inner:
                    inner[k] = spec_obj[k]
            spec_obj = inner
            src_spec = inner

        # 竖屏脚本分支会把 spec_obj 整个换成 build 出来的 timeline spec，
        # 平台名与 fit 只在「引擎外壳」上有，换掉之前先留一份，否则交付阶段永远取不到。
        platforms_hint = spec_obj.get("platforms") if isinstance(spec_obj, dict) else None
        fit_hint = spec_obj.get("fit") if isinstance(spec_obj, dict) else None

        # 竖屏 factcard 走同一条 timeline 通道：worker 自己把脚本展开成静帧卡 +
        # 立绘 + 配音的 spec，平台侧只发脚本文本，不用先算好时间轴
        copy_report = None
        paper_ev: dict[str, Any] | None = None
        if isinstance(spec_obj, dict) and spec_obj.get("mode") == "vertical_short" \
                and str(spec_obj.get("template") or "") == "paper":
            # 纸面版：同一份脚本换画面层。卡面不生成，整条片是一张往上走的
            # 纸，所以时间点还是先用竖屏那套算（配音、人物层都在里面）。
            progress(4, "timeline:paper")
            copy_report = vertical._copy_report(spec_obj)
            got = self._render_paper(spec_obj, workdir, progress)
            paper_ev = got["ev"]
            spec_path = Path(got["specPath"])
        elif isinstance(spec_obj, dict) and spec_obj.get("mode") == "vertical_short":
            progress(4, "timeline:script")
            # 批量排 N 条时同一份稿子入队 N 次：把 runId 当表演种子，
            # 出来的几条手势不一样，才有得挑；不传种子时渲染结果仍是原样。
            if run_id and not spec_obj.get("_variant_seed"):
                spec_obj["_variant_seed"] = run_id
            log.info("variant seed=%r run=%s", spec_obj.get("_variant_seed"), run_id)
            # 文案体检跟着母版一起回给平台：写稿的人在 run 详情里就能看到
            copy_report = vertical._copy_report(spec_obj)
            try:
                built = vertical.build_spec(
                    spec_obj, workdir,
                    voice=str(spec_obj.get("voice") or ""),
                    logo=Path(str(spec_obj["logo"])).expanduser() if spec_obj.get("logo") else None,
                )
            except Exception as exc:
                # 平台把 lastError 原样显示给写稿的人：「脚本展开失败」是内部说法，
                # 留在日志里就够了，回给界面的应该是要改什么。
                log.error("vertical script expand failed: %s", exc)
                raise TimelineError(str(exc)) from None
            spec_path = workdir / "spec.json"
            spec_path.write_text(json.dumps(built, ensure_ascii=False, indent=2), encoding="utf-8")
            src_spec = built
            spec_obj = built
        elif isinstance(spec_obj, dict) and spec_obj.get("canvas"):
            spec_path = workdir / "spec.json"
            spec_path.write_text(json.dumps(spec_obj, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        elif spec_path_hint and Path(spec_path_hint).exists():
            spec_path = Path(spec_path_hint)
        else:
            raise TimelineError("timeline job 需要 payload.spec（含 canvas）或存在的 specPath")

        # 相对 src 的基准：显式 assetBase 优先，其次 spec 所在目录
        # 平台侧 job payload 只有 {runId, revision, spec}，所以 assetBase 也可能写在 spec 里
        asset_base = payload.get("assetBase") or src_spec.get("assetBase")
        base = Path(asset_base).expanduser() if asset_base else spec_path.parent
        if not base.exists():
            raise TimelineError(f"assetBase 不存在: {base}")

        progress(5, "timeline:spec")
        try:
            spec = timeline.load_spec(spec_path, base=base)
        except Exception as exc:  # SpecError / OSError 都算「spec 不合法」
            raise TimelineError(f"timeline spec 不合法: {exc}") from None

        progress(10, "timeline:rendering")
        if paper_ev is not None:
            ev = paper_ev
        else:
            try:
                ev = timeline.render(spec, workdir / "master.mp4")
            except RuntimeError as exc:
                raise TimelineError(f"母版渲染失败: {exc}") from None

        result: dict[str, Any] = {
            "engine": "timeline",
            "videoPath": ev["output"],
            "duration": ev["duration"],
            "canvas": ev["canvas"],
            "clips": ev["clips"],
            "texts": ev["texts"],
            "audios": ev["audios"],
            "renderSeconds": ev["seconds"],
            "deliveries": [],
        }
        if copy_report is not None:
            result["copyCheck"] = copy_report
        try:
            result["videoQa"] = video_qa.check(Path(ev["output"]), Path(spec_path))
        except Exception as exc:  # 体检失败不该让成片白跑，记一条就够
            log.warning("job %s video qa failed: %s", job_id, exc)

        # load_spec 只保留它认识的键，platforms/fit 得从原始 spec 里取
        src_spec = spec_obj if isinstance(spec_obj, dict) else {}
        platforms = (payload.get("platforms") or platforms_hint
                     or src_spec.get("platforms") or spec.get("platforms") or [])
        fit = payload.get("fit") or fit_hint or src_spec.get("fit") or "blur"
        if platforms:
            progress(60, "timeline:delivering")
            try:
                dev = delivery.deliver(
                    ev["output"], list(platforms), workdir / "platforms",
                    fit=fit, quiet=True,
                )
            except Exception as exc:  # 交付阶段失败要连母版一起交还，别整单判死
                log.warning("job %s timeline delivered master but delivery failed: %s", job_id, exc)
                result["deliveryError"] = str(exc)[:800]
            else:
                result["deliveries"] = []
                for row in dev.get("platforms") or []:
                    item = {k: row.get(k) for k in
                            ("platform", "label", "aspect", "target", "status",
                             "output", "bytes", "probe", "fit", "notes",
                             "errors", "warnings")}
                    item["ok"] = row.get("status") == "ok"
                    result["deliveries"].append(item)
                result["deliveryFailed"] = dev.get("failed", 0)
                if not any(item.get("ok") for item in result["deliveries"]):
                    # 请求了平台却一个都没出：不能静默 null，把原因带回 run 详情
                    notes = []
                    for row in dev.get("platforms") or []:
                        for msg in (row.get("errors") or []) + (row.get("notes") or []):
                            if msg:
                                notes.append(f"{row.get('platform')}: {msg}")
                    result["deliveryError"] = (
                        "所有平台交付失败：" + "；".join(notes)[:700]
                        if notes else "所有平台交付失败，未产出任何平台规格"
                    )

        # 平台规格同样入库：一条 run 出来的每个尺寸都能在库里按 platform 找到
        if run_id or payload.get("library"):
            for item in result["deliveries"]:
                if not item.get("ok") or not item.get("output"):
                    continue
                prow = _land_timeline(item["output"], payload, job_id, revision,
                                      platform=str(item.get("platform") or ""),
                                      duration=(item.get("probe") or {}).get("duration"))
                if not prow:
                    continue
                item["libraryPath"] = prow["path"]
                item["libraryAssetId"] = prow["assetId"]
                result.setdefault("libraryEntries", []).append(
                    {"kind": "platform", "platform": item.get("platform"),
                     "path": prow["path"]})

        # 成片入库：run 任务一律入库，本地试跑只在显式要求时入，免得试跑把库搅乱
        if run_id or payload.get("library"):
            landed = _land_timeline(ev["output"], payload, job_id, revision,
                                    duration=ev["duration"],
                                    meta={"engine": "timeline", "clips": ev["clips"]})
            if landed:
                result["libraryPath"] = landed["path"]
                result["libraryAssetId"] = landed["assetId"]
                result["md5"] = landed["md5"]
                result["libraryEntries"] = [{"kind": "master", "path": landed["path"]}]

        if not run_id:
            # 没有 runId 就是本地试跑：不传 S3，路径直接交回调用方
            return result

        s3 = load_s3_creds()
        if not (s3["access"] and s3["secret"]):
            raise TimelineError("S3 credentials are missing from config/compose.env")
        from s3 import upload_file

        progress(90, "timeline:uploading")
        prefix = f"studio/runs/{run_id}/r{revision}"
        key = upload_file(s3["endpoint"], s3["access"], s3["secret"],
                          f"{prefix}/{Path(ev['output']).name}", Path(ev["output"]))
        result["videoKey"] = key
        result["videoUrl"] = f"/oss/{key}"
        for item in result["deliveries"]:
            if not item.get("ok") or not item.get("output"):
                continue
            try:
                pkey = upload_file(
                    s3["endpoint"], s3["access"], s3["secret"],
                    f"{prefix}/platforms/{item['platform']}/{Path(item['output']).name}",
                    Path(item["output"]),
                )
                item["key"] = pkey
                item["url"] = f"/oss/{pkey}"
            except Exception as exc:  # 单个平台传不上去，不影响其它平台
                item["uploadError"] = str(exc)[:300]
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
