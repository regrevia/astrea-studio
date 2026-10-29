#!/usr/bin/env python3
"""发布泳道的领取与取消——不发布任何东西的离线探针。

为什么需要：发布与回读都发生在成片 `succeeded` 之后。后端把「挂在已完成 run 上的
收尾作业」当成补跑渲染取消过一次（`POST_RUN_KINDS` 漏了 `publish_sau`），表现是作业
永远排队、最后租约过期、界面只说「排队中」。这类回归只在真机队列上看得出来。

三种探针（都只造 `payload: {}` 的 `publish_sau` 作业，不碰抖音、不起浏览器）：

- `claim`：挂到一条已交付的 run 上，应当被 `publish` 泳道领走，然后在载荷校验处
  两秒内失败（`publish payload has no …`）——证明正常领取路径通。
- `cancelled-run`：挂到一条 `cancelled` 的 run 上，应当在领取阶段被取消并写明
  `run cancelled`——证明取消分支真的按作业号落库（`job.id` 曾经是空的，取消等于空操作）。
- `stale-revision`：run 是第 2 版、作业还是第 1 版，应当被取消并写明
  `superseded by revision 2`——证明旧稿不会被当成当前稿发布。

探针作业（必要时还有临时 run）跑完就删，台账不留痕。

用法：
    python3 tools/check_publish_lane.py                                   # 只打印计划
    python3 tools/check_publish_lane.py --apply --case claim              # 正常领取
    python3 tools/check_publish_lane.py --apply --case cancelled-run      # run 已取消
    python3 tools/check_publish_lane.py --apply --case stale-revision     # 旧稿作业
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time

MONGO_URI = (
    "mongodb://aitoearn:flClCq2wU6tzDpILsV1VdyE0kHOb@127.0.0.1:27017/aitoearn?authSource=admin"
)
MONGO_CONTAINER = "creatorstudio-mongodb-1"
WORKER_ID = "6ad9ee47437a9be7260961a6"  # mac-1
USER_ID = "6ad9e38c8d350ba17d570e2b"
PROJECT_ID = "6ae237a72a5e71e140145c45"  # 端到端验收项目
WAIT_SECONDS = 60

CASES = ("claim", "cancelled-run", "stale-revision")


def _mongosh_cmd(js: str) -> list[str]:
    """本机有 mongosh 就用本机，没有就借容器里的（这台机器上是后者）。"""
    local = shutil.which("mongosh")
    if local:
        return [local, MONGO_URI, "--quiet", "--eval", js]
    return ["docker", "exec", MONGO_CONTAINER, "mongosh", MONGO_URI, "--quiet", "--eval", js]


def mongo(js: str) -> str:
    proc = subprocess.run(
        _mongosh_cmd(js),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"mongosh failed: {proc.stderr.strip()[:300]}")
    return (proc.stdout or "").strip()


def pick_succeeded_run() -> str:
    raw = mongo(
        "const r=db.productionRun.find({status:'succeeded'}).sort({updatedAt:-1}).limit(1).toArray()[0];"
        "print(r?String(r._id):'')"
    )
    run_id = raw.splitlines()[-1].strip() if raw else ""
    if not run_id:
        raise SystemExit("没有 succeeded 的 run，先跑一版再验领取")
    return run_id


def create_run(status: str, revision: int) -> str:
    js = (
        "const now=new Date();"
        f"const r=db.productionRun.insertOne({{userId:'{USER_ID}',projectId:ObjectId('{PROJECT_ID}'),"
        f"revision:{revision},status:'{status}',currentStage:'done',title:'probe',"
        "createdAt:now,updatedAt:now});"
        "print(String(r.insertedId))"
    )
    return mongo(js).splitlines()[-1].strip()


def insert_probe(run_id: str, revision: int = 1) -> str:
    js = (
        "const now=new Date();"
        f"const r=db.productionJob.insertOne({{runId:ObjectId('{run_id}'),revision:{revision},attempt:1,"
        "lane:'publish',kind:'publish_sau',status:'queued',stage:'queued',payload:{},"
        f"userId:'{USER_ID}',workerId:null,leaseVersion:0,maxAttempts:1,"
        "createdAt:now,updatedAt:now});"
        "print(String(r.insertedId))"
    )
    return mongo(js).splitlines()[-1].strip()


def read_state(job_id: str) -> dict:
    js = (
        f"const j=db.productionJob.findOne({{_id:ObjectId('{job_id}')}},"
        "{status:1,message:1,lastError:1,leaseOwner:1});"
        f"const ev=db.productionEvent.find({{jobId:'{job_id}'}},"
        "{kind:1,message:1,jobId:1}).toArray();"
        "print(JSON.stringify({job:j&&{status:j.status,message:j.message,lastError:j.lastError,"
        "leaseOwner:j.leaseOwner},events:ev.map(e=>({kind:e.kind,message:e.message,"
        "jobId:e.jobId&&String(e.jobId)}))}))"
    )
    raw = mongo(js)
    line = raw.splitlines()[-1] if raw else "{}"
    return json.loads(line or "{}")


def cleanup(job_id: str, run_ids: list[str]) -> None:
    js = (
        f"db.productionEvent.deleteMany({{jobId:'{job_id}'}});"
        f"db.productionJob.deleteOne({{_id:ObjectId('{job_id}')}});"
    )
    for run_id in run_ids:
        js += (
            f"db.productionEvent.deleteMany({{runId:'{run_id}'}});"
            f"db.productionRun.deleteOne({{_id:ObjectId('{run_id}')}});"
        )
    mongo(js + "print('cleaned')")


def plan(case: str) -> dict:
    if case == "claim":
        return {
            "case": case,
            "run": f"{pick_succeeded_run()}（已交付）",
            "expect": [
                "被 publish 泳道领走（job.claimed 带作业号）",
                "失败原因是载荷校验（publish payload has no …），两秒内结束",
                "不查 cookie、不上传、没有 job.cancelled",
            ],
        }
    if case == "cancelled-run":
        return {
            "case": case,
            "run": "临时造一条 cancelled 的 run",
            "expect": [
                "领取阶段被取消",
                "job.cancelled 带作业号",
                "原因写明 run cancelled",
                "worker 不会收到这条活",
            ],
        }
    return {
        "case": case,
        "run": "临时造一条第 2 版的 run，作业挂第 1 版",
        "expect": ["旧稿作业被取消", "job.cancelled 带作业号", "原因写明 revision 1 superseded by 2"],
    }


def run_case(case: str, wait_seconds: float) -> dict:
    created_runs: list[str] = []
    if case == "claim":
        run_id = pick_succeeded_run()
        job_revision = 1
    elif case == "cancelled-run":
        run_id = create_run("cancelled", 1)
        created_runs.append(run_id)
        job_revision = 1
    else:
        run_id = create_run("succeeded", 2)
        created_runs.append(run_id)
        job_revision = 1

    job_id = insert_probe(run_id, job_revision)
    started = time.time()
    try:
        while time.time() - started < wait_seconds:
            state = read_state(job_id)
            if (state.get("job") or {}).get("status") in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(2)
        state = read_state(job_id)

        job = state.get("job") or {}
        events = state.get("events") or []
        kinds = [e.get("kind") for e in events]
        text = " ".join(
            [
                str(job.get("message") or ""),
                str(job.get("lastError") or ""),
                *(str(e.get("message") or "") for e in events),
            ]
        )
        cancelled_event = next((e for e in events if e.get("kind") == "job.cancelled"), {})
        status = job.get("status")

        if case == "claim":
            checks = {
                "claimed": bool(job.get("leaseOwner")) and "job.claimed" in kinds,
                "claimed_event_has_job_id": any(
                    e.get("kind") == "job.claimed" and e.get("jobId") == job_id for e in events
                ),
                "finished": status in ("succeeded", "failed"),
                "failed_on_payload": bool(re.search(r"publish payload has no \w+", text)),
                "not_cancelled": "job.cancelled" not in kinds,
                "no_upload": "publish:submitting" not in text
                and "publication.submitted" not in kinds,
            }
        elif case == "cancelled-run":
            checks = {
                "picked_up": bool(job.get("leaseOwner")),
                "cancelled": status == "cancelled",
                "cancelled_event_has_job_id": cancelled_event.get("jobId") == job_id,
                "reason_run_cancelled": "run cancelled" in str(cancelled_event.get("message") or ""),
                "worker_never_ran_it": "job.failed" not in kinds and "job.claimed" not in kinds,
            }
        else:
            checks = {
                "picked_up": bool(job.get("leaseOwner")),
                "cancelled": status == "cancelled",
                "cancelled_event_has_job_id": cancelled_event.get("jobId") == job_id,
                "reason_superseded": bool(
                    re.search(
                        r"revision 1 superseded by 2",
                        str(cancelled_event.get("message") or ""),
                    )
                ),
                "worker_never_ran_it": "job.failed" not in kinds and "job.claimed" not in kinds,
            }

        ok = all(checks.values())
        return {
            "case": case,
            "jobId": job_id,
            "runId": run_id,
            "elapsedSeconds": round(time.time() - started, 1),
            "status": status,
            "leaseOwner": job.get("leaseOwner"),
            "message": (job.get("message") or job.get("lastError") or "")[:120],
            "events": kinds,
            "eventDetails": events,
            "checks": checks,
            "verdict": "PASS" if ok else "FAIL",
        }
    finally:
        cleanup(job_id, created_runs)
        print("probe cleaned", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真插探针作业（默认只打印计划）")
    ap.add_argument("--case", default="claim", choices=CASES, help="跑哪一条探针")
    ap.add_argument("--all", action="store_true", help="三条依次跑")
    ap.add_argument("--wait", type=float, default=WAIT_SECONDS, help="等待秒数")
    args = ap.parse_args(argv)

    cases = CASES if args.all else (args.case,)
    if not args.apply:
        print(json.dumps([plan(c) for c in cases], ensure_ascii=False, indent=2))
        return 0

    results = [run_case(c, args.wait) for c in cases]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
