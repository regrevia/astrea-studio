#!/usr/bin/env python3
"""发布泳道的领取与取消——不发布任何东西的离线探针。

为什么需要：发布与回读都发生在成片 `succeeded` 之后。后端把「挂在已完成 run 上的
收尾作业」当成补跑渲染取消过一次（`POST_RUN_KINDS` 漏了 `publish_sau`），表现是作业
永远排队、最后租约过期、界面只说「排队中」。这类回归只在真机队列上看得出来。

四种探针只造 `payload: {}` 的 `publish_sau` 作业，不碰抖音、不起浏览器；第五种只验回读入口：

- `claim`：挂到一条已交付的 run 上，应当被 `publish` 泳道领走，然后在载荷校验处
  两秒内失败（`publish payload has no …`）——证明正常领取路径通。
- `cancelled-run`：挂到一条 `cancelled` 的 run 上，应当在领取阶段被取消并写明
  `run cancelled`——证明取消分支真的按作业号落库（`job.id` 曾经是空的，取消等于空操作）。
- `stale-revision`：run 是第 2 版、作业还是第 1 版，应当被取消并写明
  `superseded by revision 2`——证明旧稿不会被当成当前稿发布。
- `readback-retry`：造一条 `submission_unknown` 记录，`POST publications/:id/readback-job`
  应当排出一条 `publish_readback` 并把记录翻成 `reconciling`；再点一次被拒、无链接被拒。
  排上立刻取消（只验入口，不让 worker 去读一条不存在的链接）。

探针作业（必要时还有临时 run）跑完就删，台账不留痕。

用法：
    python3 tools/check_publish_lane.py                                   # 只打印计划
    python3 tools/check_publish_lane.py --apply --case claim              # 正常领取
    python3 tools/check_publish_lane.py --apply --case cancelled-run      # run 已取消
    python3 tools/check_publish_lane.py --apply --case stale-revision     # 旧稿作业
    python3 tools/check_publish_lane.py --apply --case readback-retry     # 回读入口
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

WORKER_ID = "6ad9ee47437a9be7260961a6"  # mac-1
# 必须等于 /tmp/auto_token.txt 里签发的用户 id：run / attempt / job 的 userId 是
# 归属过滤条件。用别的 id 造数据，接口只会回「Publication attempt not found」，
# 队列里的作业也永远领不到（claim 按 token 用户的 userId 过滤）。
USER_ID = "6ae99eae89951ef393625e28"
PROJECT_ID = "6ae237a72a5e71e140145c45"  # 端到端验收项目
WAIT_SECONDS = 60
API = "http://127.0.0.1:8080/api"
TOKEN_FILE = Path("/tmp/auto_token.txt")

CASES = ("claim", "cancelled-run", "stale-revision", "lease-expired", "readback-retry")
LEASE_WAIT_SECONDS = 150  # 回收器每分钟扫一次


CONTAINER = "creatorstudio-aitoearn-server-1"
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", str(Path.home() / "CreatorStudio")))
SERVER_CFG = STUDIO_HOME / "config" / "aitoearn-server.yaml"

# 这台机器上没有 mongosh（也不该为了一个门禁再装一套 shell），改成开一次
# node + mongodb 驱动（后台镜像里本来就有），用 JSON 传操作、JSON 回结果。
# 好处是查询条件、投影、排序都是结构化数据，不会像拼 JS 字符串那样被引号咬。
NODE_MONGO = r"""
import { MongoClient, ObjectId } from 'mongodb';
const raw = await new Promise((resolve) => {
  let buf = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => { buf += chunk; });
  process.stdin.on('end', () => resolve(buf || '{}'));
});
const req = JSON.parse(raw);
// 递归还原 Python 侧发来的 ObjectId / Date，别的原样传。
const revive = (value) => {
  if (Array.isArray(value)) return value.map(revive);
  if (value && typeof value === 'object') {
    if (typeof value.$oid === 'string') return new ObjectId(value.$oid);
    if (typeof value.$date === 'string') return new Date(value.$date);
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = revive(v);
    return out;
  }
  return value;
};
const op = req.op || {};
const coll = () => client.db(req.dbName).collection(op.collection);
const client = new MongoClient(req.uri);
let result;
try {
  await client.connect();
  const c = coll();
  switch (op.action) {
    case 'insertOne':
      result = { insertedId: String((await c.insertOne(revive(op.doc))).insertedId) };
      break;
    case 'findOne': {
      const opts = {};
      if (op.projection) opts.projection = op.projection;
      if (op.sort) opts.sort = op.sort;
      result = await c.findOne(revive(op.filter || {}), opts);
      break;
    }
    case 'find': {
      let cursor = c.find(revive(op.filter || {}), op.projection ? { projection: op.projection } : {});
      if (op.sort) cursor = cursor.sort(op.sort);
      if (op.limit) cursor = cursor.limit(op.limit);
      result = await cursor.toArray();
      break;
    }
    case 'countDocuments':
      result = await c.countDocuments(revive(op.filter || {}));
      break;
    case 'updateOne':
      result = await c.updateOne(revive(op.filter || {}), revive(op.update || {}));
      break;
    case 'updateMany':
      result = await c.updateMany(revive(op.filter || {}), revive(op.update || {}));
      break;
    case 'deleteOne':
      result = await c.deleteOne(revive(op.filter || {}));
      break;
    case 'deleteMany':
      result = await c.deleteMany(revive(op.filter || {}));
      break;
    default:
      throw new Error(`unsupported action ${op.action}`);
  }
  process.stdout.write(JSON.stringify({ ok: true, result }));
} catch (err) {
  process.stdout.write(JSON.stringify({ ok: false, error: String((err && err.message) || err) }));
  process.exitCode = 1;
} finally {
  await client.close();
}
"""


def _mongo_target() -> tuple[str, str]:
    """连接串直接取渲染后的后台配置：容器内主机名是 mongodb，只有它有驱动。"""
    if not SERVER_CFG.exists():
        raise SystemExit(f"缺少 {SERVER_CFG}，先跑 `make config`")
    text = SERVER_CFG.read_text()
    match = re.search(r'^"mongodb":\s*$.*?^\s+"uri":\s*"([^"]+)"', text, re.S | re.M)
    if not match:
        raise SystemExit(f"{SERVER_CFG} 里找不到 mongodb.uri")
    uri = match.group(1)
    db_match = re.search(r'^"mongodb":\s*$.*?^\s+"dbName":\s*"([^"]+)"', text, re.S | re.M)
    return uri, (db_match.group(1) if db_match else "aitoearn")


def _oid(hex_id: str) -> dict:
    return {"$oid": hex_id}


def _now(offset_seconds: float = 0) -> dict:
    stamp = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return {"$date": stamp.isoformat()}


def mongo_op(action: str, collection: str, **kwargs) -> Any:
    """在后台容器里执行一条结构化 Mongo 操作（不走 shell，参数不会被转义咬）。"""
    uri, db_name = _mongo_target()
    payload = json.dumps(
        {"uri": uri, "dbName": db_name, "op": {"action": action, "collection": collection, **kwargs}},
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [
            "docker", "exec", "-i", "-w", "/app", CONTAINER,
            "node", "--input-type=module", "-e", NODE_MONGO,
        ],
        input=payload,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"mongo {action} 失败: {(proc.stderr or proc.stdout).strip()[:300]}")
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    data = json.loads(line or "{}")
    if not data.get("ok"):
        raise SystemExit(f"mongo {action} 失败: {data.get('error')}")
    return data.get("result")


# 空库（新装环境）里没有任何 succeeded 的 run，探针不能就此罢工：自己造一个已交付
# 的 run 当靶子，收尾时连它一起删掉。这样这条泳道在干净机器上也能跑通。
_SEEDED_RUNS: list[str] = []


def pick_succeeded_run() -> str:
    doc = mongo_op(
        "findOne", "productionRun",
        filter={"status": "succeeded"},
        sort={"updatedAt": -1},
        projection={"_id": 1},
    )
    run_id = str((doc or {}).get("_id") or "")
    if not run_id:
        run_id = create_run("succeeded", 1)
        _SEEDED_RUNS.append(run_id)
        print(f"库里没有 succeeded 的 run，造一个当靶子：{run_id}", file=sys.stderr)
    return run_id


def create_run(status: str, revision: int) -> str:
    doc = {
        "userId": USER_ID,
        "projectId": PROJECT_ID,
        "revision": revision,
        "status": status,
        "currentStage": "done",
        "title": "probe",
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    return mongo_op("insertOne", "productionRun", doc=doc)["insertedId"]


def insert_probe(run_id: str, revision: int = 1) -> str:
    doc = {
        "runId": run_id,
        "revision": revision,
        "attempt": 1,
        "lane": "publish",
        "kind": "publish_sau",
        "status": "queued",
        "stage": "queued",
        "payload": {},
        "userId": USER_ID,
        "workerId": None,
        "leaseVersion": 0,
        "maxAttempts": 1,
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    return mongo_op("insertOne", "productionJob", doc=doc)["insertedId"]


def _attempt_doc(run_id: str, *, status: str, url: str, post_id: str, tag: str) -> dict:
    return {
        "userId": USER_ID,
        "runId": run_id,
        "projectId": PROJECT_ID,
        "platform": "douyin",
        "dispatcher": "sau",
        "accountRef": "yefen",
        "accountName": "yefen",
        "revision": 1,
        "idempotencyKey": f"{tag}-{int(time.time() * 1000)}",
        "plannedAt": _now(),
        "status": status,
        "content": {},
        "remotePostId": post_id,
        "remoteUrl": url,
        "evidence": [],
        "lastError": "",
        "createdAt": _now(),
        "updatedAt": _now(),
    }


def create_attempt(run_id: str) -> str:
    """造一条提交中途的记录：worker 已经点了发布、还没回写结论。"""
    doc = _attempt_doc(
        run_id, status="submitting", url="", post_id="", tag="round27-lease-probe"
    )
    return mongo_op("insertOne", "publicationAttempt", doc=doc)["insertedId"]


def create_readback_attempt(run_id: str, url: str = "https://www.douyin.com/video/7000000000000000001") -> str:
    """造一条「回执不明」的记录：它已经有链接（或故意没有），但没人替它核对过。"""
    post_id = "7000000000000000001" if url else ""
    doc = _attempt_doc(
        run_id, status="submission_unknown", url=url, post_id=post_id,
        tag="round31-readback-probe",
    )
    return mongo_op("insertOne", "publicationAttempt", doc=doc)["insertedId"]


def _rejected(status: int, body: dict) -> bool:
    """后台业务错误也是 HTTP 200，code 非 0 才算拒了。"""
    return status >= 400 or str(body.get("code")) not in ("0", "None")


def api_post(path: str) -> tuple[int, dict]:
    """调本机后台；4xx 也返回状态码，好让探针断言「第二次点会被拒」。"""
    return api_post_json(path, {})


def api_post_json(path: str, body: dict) -> tuple[int, dict]:
    token = TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 —— 本机后台
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except json.JSONDecodeError:
            return exc.code, {}


_PROBE_WORKER_ID = ""


def api_post_worker(path: str, body: dict, worker_token: str) -> tuple[int, dict]:
    """带 worker token 调领取接口：路由只在 worker 身份下存在。"""
    req = urllib.request.Request(
        f"{API}/{path}",
        data=json.dumps(body).encode(),
        headers={"x-worker-token": worker_token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 —— 本机后台
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except json.JSONDecodeError:
            return exc.code, {}


def ensure_probe_worker() -> tuple[str, str]:
    """注册一条临时 worker（能力覆盖 publish 泳道的两种活），用来验证领取契约。

    真机验收时 mac worker 就在跑；但 Docker 单机验收里没有常驻 worker，
    队列会被误判成「不领活」。这里自己扮演 worker：领到的是同一条队列、
    同一套租约和取消规则，验的是服务端契约，不是探针的假动作。
    """
    global _PROBE_WORKER_ID
    code, body = api_post_json(
        "production/workers/register",
        {"name": "publish-lane-probe", "capabilities": ["publish_sau", "publish_readback"], "version": "probe"},
    )
    data = body.get("data") or {}
    worker_id, token = str(data.get("workerId") or ""), str(data.get("token") or "")
    assert code == 200 and worker_id and token, f"注册探针 worker 失败: {code} {body}"
    _PROBE_WORKER_ID = worker_id
    return worker_id, token


def claim_once(worker_token: str) -> dict:
    """按 publish 泳道领一条活；没有可领的活返回空。"""
    code, body = api_post_worker("production/worker/claim", {"lane": "publish"}, worker_token)
    assert code == 200, f"领取失败: {code} {body}"
    return (body.get("data") or {}).get("job") or {}


def insert_expired_job(run_id: str, attempt_id: str) -> str:
    """running 作业 + 已经过期的租约：等价于 worker 被 kill -9 之后再没人续约。"""
    old = _now(-5 * 60)
    doc = {
        "runId": run_id,
        "revision": 1,
        "attempt": 1,
        "lane": "publish",
        "kind": "publish_sau",
        "status": "running",
        "stage": "submitting",
        "payload": {"attemptId": attempt_id},
        "userId": USER_ID,
        "workerId": WORKER_ID,
        "leaseOwner": WORKER_ID,
        "leaseVersion": 1,
        "maxAttempts": 1,
        "startedAt": old,
        "heartbeatAt": old,
        "leaseExpiresAt": old,
        "createdAt": old,
        "updatedAt": old,
    }
    return mongo_op("insertOne", "productionJob", doc=doc)["insertedId"]


def read_attempt(attempt_id: str) -> dict:
    doc = mongo_op(
        "findOne", "publicationAttempt",
        filter={"_id": _oid(attempt_id)},
        projection={"status": 1, "evidence": 1, "lastError": 1},
    )
    doc = doc or {}
    return {
        "status": doc.get("status"),
        "evidence": doc.get("evidence") or [],
        "lastError": doc.get("lastError") or "",
    }


def set_job_status(job_id: str, status: str) -> None:
    mongo_op(
        "updateOne", "productionJob",
        filter={"_id": _oid(job_id)},
        update={"$set": {"status": status}},
    )


def read_run_status(run_id: str) -> str:
    doc = mongo_op(
        "findOne", "productionRun",
        filter={"_id": _oid(run_id)},
        projection={"status": 1},
    )
    return str((doc or {}).get("status") or "")


def count_pending_jobs(run_id: str, exclude_job_id: str) -> int:
    return int(
        mongo_op(
            "countDocuments", "productionJob",
            filter={
                "runId": _oid(run_id),
                "status": {"$in": ["queued", "running"]},
                "_id": {"$ne": _oid(exclude_job_id)},
            },
        )
        or 0
    )


def read_state(job_id: str) -> dict:
    job = mongo_op(
        "findOne", "productionJob",
        filter={"_id": _oid(job_id)},
        projection={"status": 1, "message": 1, "lastError": 1, "leaseOwner": 1, "kind": 1, "payload": 1},
    )
    events = mongo_op(
        "find", "productionEvent",
        filter={"jobId": job_id},
        projection={"kind": 1, "message": 1, "jobId": 1},
    ) or []
    if job:
        job = {k: job.get(k) for k in ("status", "message", "lastError", "leaseOwner", "kind", "payload")}
    return {
        "job": job,
        "events": [
            {"kind": e.get("kind"), "message": e.get("message"), "jobId": str(e.get("jobId") or "")}
            for e in events
        ],
    }


def cleanup(job_id: str, run_ids: list[str], attempt_ids: tuple[str, ...] = ()) -> None:
    # 排队入口被拒时根本没有作业号：收尾要能跳过它，否则探针自己报错盖住真结论。
    if job_id:
        mongo_op("deleteMany", "productionEvent", filter={"jobId": job_id})
        mongo_op("deleteOne", "productionJob", filter={"_id": _oid(job_id)})
    for attempt_id in attempt_ids:
        mongo_op("deleteOne", "publicationAttempt", filter={"_id": _oid(attempt_id)})
    for run_id in run_ids:
        mongo_op("deleteMany", "productionEvent", filter={"runId": run_id})
        mongo_op("deleteOne", "productionRun", filter={"_id": _oid(run_id)})
    if _PROBE_WORKER_ID:
        mongo_op("deleteOne", "productionWorker", filter={"_id": _oid(_PROBE_WORKER_ID)})


def plan(case: str) -> dict:
    if case == "claim":
        return {
            "case": case,
            "run": "临时造一条已交付的 run（跑完即删）",
            "expect": [
                "临时 worker 从 publish 泳道领走：leaseOwner 是它，事件是 job.claimed 且带作业号",
                "作业进入 running，租约落在 future",
                "没有 job.cancelled、没有上传动作",
                "载荷校验属于 worker 泳道，需真 worker 在线才验得到，这一条不算通过",
            ],
        }
    if case == "readback-retry":
        return {
            "case": case,
            "run": "临时 succeeded run（跑完即删）",
            "expect": [
                "POST 回读作业被接受，台账推到 reconciling，排出一条 publish_readback",
                "同一记录再点一次被后端拒掉（不重复排、不重复读同一条链接）",
                "链接为空时拒绝排作业；结论由 worker 回写，探针不投真发布",
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
    if case == "lease-expired":
        return {
            "case": case,
            "run": "已交付 run + 一条租约过期的 running 作业 + 一条 submitting 提交",
            "expect": [
                "作业被判 lease expired（事件带作业号）",
                "那条提交不能停在 submitting（要落 submission_unknown 等回读）",
                "run 保持 succeeded，不给已交付 run 补渲染任务",
            ],
        }
    return {
        "case": case,
        "run": "临时造一条第 2 版的 run，作业挂第 1 版",
        "expect": ["旧稿作业被取消", "job.cancelled 带作业号", "原因写明 revision 1 superseded by 2"],
    }


def run_case(case: str, wait_seconds: float) -> dict:
    started = time.time()
    created_runs: list[str] = list(_SEEDED_RUNS)
    attempt_id = ""
    api_checks: dict[str, bool] = {}
    extra_attempts: list[str] = []
    if case == "readback-retry":
        run_id = create_run("succeeded", 1)
        created_runs.append(run_id)
        attempt_id = create_readback_attempt(run_id)
        first_code, first_body = api_post(f"production/publications/{attempt_id}/readback-job")
        first_data = first_body.get("data") or {}
        job_id = str(first_data.get("jobId") or "")
        # 排上就立刻快照并取消：worker 可能在一秒内领取，探针只验「排队入口」，
        # 不把真回读跑起来（那会去开浏览器读一条不存在的链接）。
        job = (read_state(job_id).get("job") or {}) if job_id else {}
        if job_id:
            set_job_status(job_id, "cancelled")
        second_code, second_body = api_post(f"production/publications/{attempt_id}/readback-job")
        # 没有链接的记录不该被排回读：读不到东西的作业只会白跑一趟。
        bare_id = create_readback_attempt(run_id, url="")
        bare_code, bare_body = api_post(f"production/publications/{bare_id}/readback-job")
        extra_attempts.append(bare_id)
        attempt = read_attempt(attempt_id)
        queued = job.get("kind") == "publish_readback"
        api_checks = {
            "enqueue_accepted": first_code in (200, 201) and str(first_body.get("code")) == "0",
            "attempt_reconciling": attempt.get("status") == "reconciling",
            "readback_job_queued": queued,
            "job_payload_bound": (job.get("payload") or {}).get("attemptId") == attempt_id,
            "second_click_rejected": _rejected(second_code, second_body),
            "job_created": bool(job_id),
            "no_duplicate_readback": (not job_id) or count_pending_jobs(run_id, job_id) == 0,
            "no_url_rejected": _rejected(bare_code, bare_body),
        }
        ok = all(api_checks.values())
        # 这条分支在 try 之前 return，走不到末尾的 finally：自己收尾，台账不留痕。
        cleanup(job_id, created_runs, tuple([attempt_id] + extra_attempts))
        print("probe cleaned", file=sys.stderr)
        return {
            "case": case, "jobId": job_id, "runId": run_id,
            "elapsedSeconds": round(time.time() - started, 1),
            "status": job.get("status"), "leaseOwner": job.get("leaseOwner"),
            "message": (second_body.get("message") or "")[:120],
            "attemptStatus": attempt.get("status", ""),
            "events": [], "eventDetails": [], "checks": api_checks,
            "verdict": "PASS" if ok else "FAIL",
        }
    if case == "claim":
        run_id = create_run("succeeded", 1)
        created_runs.append(run_id)
        job_revision = 1
    elif case == "lease-expired":
        run_id = pick_succeeded_run()
        job_revision = 1
        attempt_id = create_attempt(run_id)
        job_id = insert_expired_job(run_id, attempt_id)
    elif case == "cancelled-run":
        run_id = create_run("cancelled", 1)
        created_runs.append(run_id)
        job_revision = 1
    else:
        run_id = create_run("succeeded", 2)
        created_runs.append(run_id)
        job_revision = 1

    if case != "lease-expired":
        job_id = insert_probe(run_id, job_revision)
    try:
        if case in ("claim", "cancelled-run", "stale-revision"):
            # 领取即开工：revision 不符 / run 已取消都会在这一步被服务端取消，
            # 领到的那条会带 leaseOwner 和 job.claimed 事件。
            _, worker_token = ensure_probe_worker()
            claim_once(worker_token)
        # claim 只验领取契约：作业会停在 running（没有常驻 worker 去收尾），不用等终态。
        if case == "lease-expired":
            wait_for = LEASE_WAIT_SECONDS
        elif case == "claim":
            wait_for = 1
        else:
            wait_for = wait_seconds
        while time.time() - started < wait_for:
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

        if case == "lease-expired":
            attempt = read_attempt(attempt_id)
            checks = {
                "expired_failed": status == "failed" and "lease expired" in str(job.get("message") or ""),
                "failed_event_has_job_id": any(
                    e.get("kind") == "job.failed" and e.get("jobId") == job_id for e in events
                ),
                "attempt_not_stuck_submitting": attempt.get("status")
                in ("submission_unknown", "reconciling", "submitted_unverified", "published"),
                "attempt_marked": str(attempt.get("status") or "") != "submitting",
                "run_still_succeeded": read_run_status(run_id) == "succeeded",
                "no_requeue": count_pending_jobs(run_id, job_id) == 0,
            }
        elif case == "claim":
            # 服务端领取契约：租约、所有权、事件都写回同一条作业。
            # 「载荷不合格就失败」是 worker 侧泳道的逻辑，要真 worker 在线才验得到，
            # 不在这一条里假装验过。
            checks = {
                "claimed": bool(job.get("leaseOwner")) and "job.claimed" in kinds,
                "claimed_event_has_job_id": any(
                    e.get("kind") == "job.claimed" and e.get("jobId") == job_id for e in events
                ),
                "lease_owner_is_worker": job.get("leaseOwner") == _PROBE_WORKER_ID,
                "running": status == "running",
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
            "attemptStatus": (
            read_attempt(attempt_id).get("status", "")
            if case in ("lease-expired", "readback-retry") else ""
        ),
            "events": kinds,
            "eventDetails": events,
            "checks": checks,
            "verdict": "PASS" if ok else "FAIL",
        }
    finally:
        ids = tuple([attempt_id] + extra_attempts) if attempt_id else tuple(extra_attempts)
        cleanup(job_id, created_runs, ids)
        print("probe cleaned", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真插探针作业（默认只打印计划）")
    ap.add_argument("--case", default="claim", choices=CASES, help="跑哪一条探针")
    ap.add_argument("--all", action="store_true", help="五条依次跑")
    ap.add_argument("--wait", type=float, default=WAIT_SECONDS, help="等待秒数")
    args = ap.parse_args(argv)

    cases = CASES if args.all else (args.case,)
    if not args.apply:
        print(json.dumps([plan(c) for c in cases], ensure_ascii=False, indent=2))
        return 0

    results = []
    for case in cases:
        try:
            results.append(run_case(case, args.wait))
        except Exception as exc:  # noqa: BLE001 - 单条探针的失败要留在台账里，不能吞整轮
            results.append({
                "case": case,
                "verdict": "FAIL",
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            })
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["verdict"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
