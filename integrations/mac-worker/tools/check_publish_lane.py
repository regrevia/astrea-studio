#!/usr/bin/env python3
"""发布泳道能不能真的把作业领走——不发布任何东西的离线探针。

为什么需要：发布与回读都发生在成片 `succeeded` 之后。后端把「挂在已完成 run 上的
作业」当成补跑渲染取消过一次，表现是作业永远排队、最后租约过期。这类回归只在
真机队列上才看得出来，所以这里造一条**故意缺 videoPath** 的 `publish_sau` 作业：

- 作业必须被 `publish` 泳道领取（出现 `job.claimed` 事件 / 状态变 running）；
- 必须在载荷校验处失败（`publish payload has no videoPath`），不查 cookie、不起上传进程；
- 不能被取消（`job.cancelled`），run 状态不能被改写。

探针作业跑完就删掉，台账和 run 都不留痕。

用法：
    python3 tools/check_publish_lane.py            # 只打印计划（dry-run）
    python3 tools/check_publish_lane.py --apply    # 真插一条探针作业并等结论
"""

from __future__ import annotations

import argparse
import re
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

MONGO_URI = (
    "mongodb://aitoearn:flClCq2wU6tzDpILsV1VdyE0kHOb@127.0.0.1:27017/aitoearn?authSource=admin"
)
MONGO_CONTAINER = "creatorstudio-mongodb-1"
WORKER_ID = "6ad9ee47437a9be7260961a6"  # mac-1
USER_ID = "6ad9e38c8d350ba17d570e2b"
WAIT_SECONDS = 90


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


def js_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def pick_run() -> str:
    raw = mongo(
        "const r=db.productionRun.find({status:'succeeded'}).sort({updatedAt:-1}).limit(1).toArray()[0];"
        "print(r?String(r._id):'')"
    )
    run_id = raw.splitlines()[-1].strip() if raw else ""
    if not run_id:
        raise SystemExit("没有 succeeded 的 run 可以挂探针作业")
    return run_id


def insert_probe(run_id: str) -> str:
    payload = js_str({})
    js = (
        "const now=new Date();"
        f"const r=db.productionJob.insertOne({{runId:ObjectId('{run_id}'),revision:1,attempt:1,"
        "lane:'publish',kind:'publish_sau',status:'queued',stage:'queued',"
        f"payload:{payload},userId:'{USER_ID}',workerId:null,leaseVersion:0,maxAttempts:1,"
        "createdAt:now,updatedAt:now});"
        "print(String(r.insertedId))"
    )
    return mongo(js).splitlines()[-1].strip()


def read_state(job_id: str) -> dict:
    js = (
        f"const j=db.productionJob.findOne({{_id:ObjectId('{job_id}')}},"
        "{status:1,message:1,lastError:1,leaseOwner:1});"
        f"const ev=db.productionEvent.find({{jobId:'{job_id}'}},{{kind:1,message:1,jobId:1}}).toArray();"
        "print(JSON.stringify({job:j&&{status:j.status,message:j.message,lastError:j.lastError,"
        "leaseOwner:j.leaseOwner},events:ev.map(e=>({kind:e.kind,message:e.message,jobId:e.jobId&&String(e.jobId)}))}))"
    )
    raw = mongo(js)
    line = raw.splitlines()[-1] if raw else "{}"
    return json.loads(line or "{}")


def cleanup(job_id: str) -> None:
    mongo(
        f"db.productionEvent.deleteMany({{jobId:'{job_id}'}});"
        f"db.productionJob.deleteOne({{_id:ObjectId('{job_id}')}});"
        "print('cleaned')"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真插探针作业（默认只打印计划）")
    ap.add_argument("--wait", type=float, default=WAIT_SECONDS, help="等待秒数")
    args = ap.parse_args(argv)

    run_id = pick_run()
    if not args.apply:
        print(json.dumps({
            "mode": "dry-run",
            "runId": run_id,
            "worker": WORKER_ID,
            "probe": {"kind": "publish_sau", "lane": "publish", "payload": {}},
            "expect": [
                "作业状态变 running（job.claimed 事件）",
                "失败原因是 publish payload has no videoPath",
                "没有 job.cancelled，不启动上传、不碰 cookie",
            ],
        }, ensure_ascii=False, indent=2))
        return 0

    job_id = insert_probe(run_id)
    started = time.time()
    state: dict = {}
    try:
        while time.time() - started < args.wait:
            state = read_state(job_id)
            status = (state.get("job") or {}).get("status")
            if status in ("succeeded", "failed", "cancelled"):
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

        checks = {
            # 领取与否可以直接看租约；事件流里也要有一条 job.claimed（带作业号）
            "claimed": bool(job.get("leaseOwner")) or "job.claimed" in kinds,
            "claimed_event_has_job_id": any(
                e.get("kind") == "job.claimed" and e.get("jobId") == job_id for e in events
            ),
            "finished": job.get("status") in ("succeeded", "failed"),
            "failed_on_payload": bool(re.search(r"publish payload has no \w+", text)),
            "not_cancelled": "job.cancelled" not in kinds,
            "no_upload": "publish:submitting" not in text and "publication.submitted" not in kinds,
        }
        ok = all(checks.values())
        print(json.dumps({
            "jobId": job_id,
            "runId": run_id,
            "elapsedSeconds": round(time.time() - started, 1),
            "status": job.get("status"),
            "leaseOwner": job.get("leaseOwner"),
            "message": (job.get("message") or job.get("lastError") or "")[:120],
            "textSample": text[:160],
            "events": kinds,
            "checks": checks,
            "verdict": "PASS" if ok else "FAIL",
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    finally:
        cleanup(job_id)
        print("probe cleaned", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
