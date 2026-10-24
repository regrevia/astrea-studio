#!/usr/bin/env python3
"""End-to-end check for the C route: platform run -> worker -> master -> platforms.

This drives the public API exactly like the UI does, with `spec.engine='timeline'`,
then waits for the mac worker to render the master with our own timeline spec,
cut every requested platform variant, upload both to object storage, and attach
the draft.

It asserts on the *run's* materialId and on the per-platform objects, because a
job that reports "done" without a publishable artifact is not a success.

Usage:
  python3 deploy/p2_timeline_e2e.py --assets /tmp/demo_c/asset
  python3 deploy/p2_timeline_e2e.py --keep      # keep polling a printed run
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
CANVAS = {"width": 1080, "height": 1920, "fps": 30}


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


SUBS = ["这是自有时间轴渲染的母版", "不启动剪映，也不抢占前台", "一条 spec 出全部平台规格"]


def clips_under(assets: Path) -> list[str]:
    clips = [p for p in ("a.mp4", "b.mp4", "c.mp4") if (assets / p).is_file()]
    if not clips:
        raise SystemExit(f"no clips under {assets} (expected a.mp4/b.mp4/c.mp4)")
    return clips


def build_friendly_spec(assets: Path, platforms: list[str]) -> dict[str, Any]:
    """友好输入：只给文案/素材/画幅，具体时间轴由服务端展开。

    这条路径是「用户不手写 JSON」的验收口径 —— 前端表单发的就是它。
    """
    spec: dict[str, Any] = {
        "engine": "timeline",
        "mode": "script",
        "assetBase": str(assets),
        "materials": clips_under(assets),
        "script": SUBS,
        "canvas": "portrait",
        "clipSeconds": 3,
        "platforms": platforms,
        "fit": "blur",
        "attachDraft": True,
    }
    if (assets / "bgm.m4a").is_file():
        spec["bgm"] = {"src": "bgm.m4a", "volume": 0.35}
    return spec


def build_spec(assets: Path, platforms: list[str]) -> dict[str, Any]:
    clips = clips_under(assets)
    video = [{"src": str(assets / name), "in": 0, "out": 3} for name in clips]
    subs = SUBS
    texts = [{
        "text": subs[i % len(subs)],
        "start": 3.0 * i + 0.2,
        "end": 3.0 * i + min(2.8, 3.0 * len(clips) - 3.0 * i),
        "size": 0.052,
        "y": 0.86,
        "color": "#FFFFFF",
        "stroke": 4,
        "box": "#00000066",
    } for i in range(len(clips))]
    audio = []
    bgm = assets / "bgm.m4a"
    if bgm.is_file():
        audio = [{"src": str(bgm), "start": 0, "volume": 0.35, "loop": True}]
    return {
        "engine": "timeline",
        "assetBase": str(assets),
        "platforms": platforms,
        "fit": "blur",
        "attachDraft": True,
        "timeline": {
            "canvas": CANVAS,
            "video": video,
            "text": texts,
            "audio": audio,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default="/tmp/demo_c/asset")
    ap.add_argument("--platforms", default="douyin,bilibili")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--no-draft", action="store_true", help="skip draft attach")
    ap.add_argument("--mode", choices=("advanced", "friendly"), default="advanced",
                    help="advanced=自己写完整时间轴；friendly=只给文案/素材/画幅，服务端展开")
    args = ap.parse_args()

    assets = Path(args.assets).expanduser().resolve()
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if args.mode == "friendly":
        spec = build_friendly_spec(assets, platforms)
        shape = f"{len(spec['materials'])} clips -> 服务端展开"
    else:
        spec = build_spec(assets, platforms)
        shape = f"{len(spec['timeline']['video'])} clips"
    if args.no_draft:
        spec["attachDraft"] = False
    print(f"mode: {args.mode}  assets: {assets}  platforms: {platforms}  {shape}")

    tok = user_token()
    if not tok:
        print("no user token")
        return 2
    auth = {"authorization": f"Bearer {tok}"}

    status, raw = call("GET", "/api/production/projects", headers=auth)
    projects = unwrap(raw) or []
    if not projects:
        print(f"no project; GET returned HTTP {status}: {str(raw)[:200]}")
        return 2
    project = next((p for p in projects if p.get("isDefault")), projects[0])
    print(f"project: {project['name']} ({project['id']})")

    status, raw = call("POST", "/api/production/runs", {
        "projectId": project["id"],
        "title": "C 路线端到端验证",
        "brief": "自有 timeline spec 渲染母版 + 多平台交付",
        "spec": spec,
    }, headers=auth)
    run = unwrap(raw)
    run_id = (run or {}).get("id") or (run or {}).get("runId") if isinstance(run, dict) else None
    if not run_id:
        print(f"create run failed HTTP {status}: {str(raw)[:400]}")
        return 2
    print(f"run: {run_id} status={run.get('status')}")

    deadline = time.time() + args.timeout
    last = ""
    detail: dict[str, Any] = {}
    job: dict[str, Any] = {}
    while time.time() < deadline:
        status, raw = call("GET", f"/api/production/runs/{run_id}", headers=auth)
        detail = unwrap(raw) or {}
        run_obj = (detail.get("run") or {}) if isinstance(detail, dict) else {}
        jobs = (detail.get("jobs") or []) if isinstance(detail, dict) else []
        job = jobs[0] if jobs else {}
        line = (f"run={run_obj.get('status')} stage={run_obj.get('currentStage')} "
                f"job={job.get('status')} {job.get('progress')}% {job.get('stage') or ''} "
                f"material={run_obj.get('materialId') or '-'}")
        if line != last:
            print(f"  {line}")
            last = line
        if job.get("status") in ("failed", "succeeded", "done") or run_obj.get("status") in (
                "failed", "succeeded", "done", "ready"):
            if run_obj.get("materialId") or job.get("status") == "failed":
                break
        time.sleep(3)

    result = (job.get("result") or {})
    print("\n=== job result ===")
    for key in ("output", "duration", "videoKey", "videoUrl", "draftError", "deliveryError"):
        if key in result:
            print(f"  {key:14s} {result[key]}")
    deliveries = result.get("deliveries") or []
    print(f"  deliveries     {len(deliveries)}")
    for d in deliveries:
        probe = d.get("probe") or {}
        print(f"    - {d.get('platform'):12s} ok={d.get('ok')} "
              f"{probe.get('width')}x{probe.get('height')} "
              f"{(d.get('bytes') or 0) // 1024} KiB key={d.get('key') or '-'} "
              f"err={d.get('uploadError') or '-'}")

    run_obj = (detail.get("run") or {}) if isinstance(detail, dict) else {}
    material_id = run_obj.get("materialId")
    print(f"  materialId     {material_id or '-'}")

    problems = []
    if not result.get("videoKey"):
        problems.append("no master object key")
    if not material_id:
        problems.append("no draft material")
    bad = [d for d in deliveries if not d.get("ok")]
    if bad:
        problems.append(f"{len(bad)} platform(s) failed")
    if result.get("deliveryError"):
        problems.append(f"deliveryError: {result['deliveryError']}")
    run_spec = (run_obj.get("spec") or {}) if isinstance(run_obj, dict) else {}
    if args.mode == "friendly":
        built = run_spec.get("timeline") or {}
        if not (built.get("video") and built.get("text")):
            problems.append("friendly spec 没有被服务端展开成 timeline")
        expected = float(len(spec["materials"]) * spec["clipSeconds"])
        if abs(float(result.get("duration") or 0) - expected) > 0.2:
            problems.append(f"duration {result.get('duration')} != {expected}")

    if problems:
        print("\nFAIL: " + "; ".join(problems))
        print(json.dumps(detail, ensure_ascii=False)[:1500])
        return 1
    print("\nPASS: master + platforms uploaded, draft attached")
    print(f"  open: {BASE}/production/runs/{run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
