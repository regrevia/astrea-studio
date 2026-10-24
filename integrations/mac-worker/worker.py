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
import threading
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
from export import timeline_render as timeline
from export import vertical_short as vertical
from export import deliver as delivery

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"

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
        except (CaptureError, RenderError, PublishError, SlidesError, MotionError, TimelineError) as exc:
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

        # 竖屏 factcard 走同一条 timeline 通道：worker 自己把脚本展开成静帧卡 +
        # 立绘 + 配音的 spec，平台侧只发脚本文本，不用先算好时间轴
        if isinstance(spec_obj, dict) and spec_obj.get("mode") == "vertical_short":
            progress(4, "timeline:script")
            try:
                built = vertical.build_spec(
                    spec_obj, workdir,
                    voice=str(spec_obj.get("voice") or ""),
                    logo=Path(str(spec_obj["logo"])).expanduser() if spec_obj.get("logo") else None,
                )
            except Exception as exc:
                raise TimelineError(f"竖屏脚本展开失败: {exc}") from None
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

        # load_spec 只保留它认识的键，platforms/fit 得从原始 spec 里取
        src_spec = spec_obj if isinstance(spec_obj, dict) else {}
        platforms = (payload.get("platforms") or src_spec.get("platforms")
                     or spec.get("platforms") or [])
        fit = payload.get("fit") or src_spec.get("fit") or "blur"
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
