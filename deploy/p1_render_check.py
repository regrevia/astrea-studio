#!/usr/bin/env python3
"""Create a real production run that the mac worker can render end to end.

This is the P1 harness: it drives the same API the UI drives, then waits for the
worker to claim the job, render with MPT, upload to S3, and attach the draft.

It deliberately asserts on the *draft*, not just on a job status, because a
render that produces a file nobody can publish is not a success.

Usage:
  python3 deploy/p1_render_check.py --fixture ~/CreatorStudio/projects/_fixtures/fixture-02.mp4
  python3 deploy/p1_render_check.py --watch      # follow an existing run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

STUDIO_HOME = Path.home() / "CreatorStudio"
BASE = "http://127.0.0.1:8080"
CONFIG = STUDIO_HOME / "config"


def call(method: str, path: str, body: dict | None = None,
         headers: dict[str, str] | None = None, timeout: float = 60.0) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"accept": "application/json"}
    hdrs.update(headers or {})
    if data:
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw.decode(errors="replace")[:400]


def unwrap(body: Any) -> Any:
    if isinstance(body, dict) and "code" in body and "data" in body:
        return body["data"]
    return body


def user_token() -> str:
    out = subprocess.run(
        ["docker", "run", "--rm", "-v", "creatorstudio_init-data:/d",
         "creator-studio/node:22-alpine", "cat", "/d/token.txt"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True, help="absolute path to a local video file")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    fixture = Path(args.fixture).expanduser().resolve()
    if not fixture.is_file():
        print(f"fixture missing: {fixture}")
        return 2
    print(f"fixture: {fixture} ({fixture.stat().st_size // 1024} KiB)")

    tok = user_token()
    if not tok:
        print("no user token")
        return 2
    auth = {"authorization": f"Bearer {tok}"}

    # 1. project (reuse the seeded default)
    status, raw = call("GET", "/api/production/projects", headers=auth)
    projects = unwrap(raw) or []
    if not projects:
        print(f"no project; GET returned HTTP {status}: {str(raw)[:200]}")
        return 2
    project = next((p for p in projects if p.get("isDefault")), projects[0])
    print(f"project: {project['name']} ({project['id']})")

    # 2. run with a script + the real fixture as material
    spec = {
        "script": "本地渲染链路验证。这是一段由 MoneyPrinterTurbo 合成的测试视频。",
        "materials": [str(fixture)],
        "aspect": "9:16",
        "language": "zh-CN",
        "subtitle": True,
        "nThreads": 2,
    }
    status, raw = call("POST", "/api/production/runs", {
        "projectId": project["id"],
        "title": "P1 渲染验证",
        "brief": "验证 MPT 出片并进入草稿箱",
        "spec": spec,
    }, headers=auth)
    run = unwrap(raw)
    # POST /runs answers {runId, status, revision, statusUrl} — not a full run
    # object, so `id` is absent even though every read returns one.
    run_id = None
    if isinstance(run, dict):
        run_id = run.get("id") or run.get("runId")
    if not run_id:
        print(f"create run failed HTTP {status}: {str(raw)[:400]}")
        return 2
    print(f"run: {run_id} status={run.get('status')}")
    print("waiting for the worker (render -> upload -> draft)...")

    deadline = time.time() + args.timeout
    last = ""
    detail: dict[str, Any] = {}
    while time.time() < deadline:
        status, raw = call("GET", f"/api/production/runs/{run_id}", headers=auth)
        detail = unwrap(raw) or {}
        r = (detail.get("run") or {}) if isinstance(detail, dict) else {}
        jobs = (detail.get("jobs") or []) if isinstance(detail, dict) else []
        job = jobs[0] if jobs else {}
        line = (f"run={r.get('status')} stage={r.get('currentStage')} "
                f"job={job.get('status')} {job.get('progress')}% {job.get('stage') or ''} "
                f"material={r.get('materialId') or '-'}")
        if line != last:
            print(f"  {line}")
            last = line

        if r.get("materialId"):
            print("\nPASS: render produced a draft")
            print(f"  materialId : {r['materialId']}")
            print(f"  videoKey   : {(job.get('result') or {}).get('videoKey')}")
            print(f"  duration   : {(job.get('result') or {}).get('duration')}")
            print(f"  sizeBytes  : {(job.get('result') or {}).get('sizeBytes')}")
            return 0
        if r.get("status") in ("failed", "cancelled") or job.get("status") == "failed":
            print("\nFAIL: run did not produce a draft")
            print(f"  run status : {r.get('status')}")
            print(f"  job status : {job.get('status')}")
            print(f"  error      : {r.get('lastError') or job.get('message') or '-'}")
            events = (detail.get("events") or [])[-6:]
            for e in events:
                print(f"    - {e.get('kind')}: {str(e.get('message'))[:160]}")
            return 1
        time.sleep(3)

    print("\nTIMEOUT: no draft within the deadline")
    print(f"  last state: {last}")
    print(f"  worker log: {STUDIO_HOME}/logs/<jobId>.log")
    return 1


if __name__ == "__main__":
    sys.exit(main())
