#!/usr/bin/env python3
"""prove_chain.py — 把「虚拟主播讲解」内容整条链路走一遍（平台侧）。

和 `presenter_style.py` 的分工：那边只负责内容 → timeline spec；这边负责
**走平台**：POST /api/production/runs（advanced spec = 我们的 timeline），
等 mac worker 出母版、切平台规格、挂剪映草稿，然后按 run 的证据判定成败。

用法::

    python3 deploy/presenter/prove_chain.py --content deploy/presenter/astrea-intro.json \\
        --outdir ~/CreatorStudio/state/presenter/astrea-intro --platforms douyin,bilibili
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))                       # deploy/
sys.path.insert(0, str(ROOT / "integrations" / "mac-worker" / "export"))

import p2_timeline_e2e as e2e          # noqa: E402
import presenter_style as P            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="虚拟主播讲解：平台全链路验证")
    ap.add_argument("--content", default=str(HERE / "astrea-intro.json"))
    ap.add_argument("--outdir", default="~/CreatorStudio/state/presenter/astrea-intro")
    ap.add_argument("--platforms", default="douyin,bilibili")
    ap.add_argument("--title", default="虚拟主播技术讲解：Astrea 全链路")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--no-draft", action="store_true")
    ap.add_argument("--no-submit", action="store_true", help="只本地构建 spec，不发平台")
    ap.add_argument("--run-id", help="不发新 run，直接盯这个已存在的 runId")
    args = ap.parse_args()

    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    outdir = Path(args.outdir).expanduser()
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]

    print(f"building spec -> {outdir}")
    spec = P.build_spec(content, outdir)
    spec_path = outdir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    meta = spec["meta"]
    print(f"spec: {spec_path}  {meta['duration']}s  overlays={len(spec['overlay'])}  "
          f"voice={meta['voice']}")
    if args.no_submit:
        return 0

    payload = {"engine": "timeline", "platforms": platforms, "fit": "blur",
               "attachDraft": not args.no_draft, "timeline": spec}
    tok = e2e.user_token()
    if not tok:
        print("no user token")
        return 2
    auth = {"authorization": f"Bearer {tok}"}

    status, raw = e2e.call("GET", "/api/production/projects", headers=auth)
    projects = e2e.unwrap(raw) or []
    if not projects:
        print(f"no project; HTTP {status}: {str(raw)[:200]}")
        return 2
    project = next((p for p in projects if p.get("isDefault")), projects[0])
    print(f"project: {project['name']} ({project['id']})")

    if args.run_id:
        run_id = args.run_id
        print(f"run: {run_id} (attach)")
    else:
        status, raw = e2e.call("POST", "/api/production/runs", {
            "projectId": project["id"],
            "title": args.title,
            "brief": f"虚拟主播讲解风格 · {meta['duration']}s · {len(content['pages'])} 页",
            "spec": payload,
        }, headers=auth, timeout=120)
        run = e2e.unwrap(raw) or {}
        run_id = run.get("runId") or run.get("id")
        if not run_id:
            print(f"create run failed HTTP {status}: {str(raw)[:500]}")
            return 2
        print(f"run: {run_id} status={run.get('status')}")

    deadline = time.time() + args.timeout
    detail: dict = {}
    last = ""
    while time.time() < deadline:
        _, raw = e2e.call("GET", f"/api/production/runs/{run_id}", headers=auth)
        detail = e2e.unwrap(raw) or {}
        run_obj = detail.get("run") or {}
        jobs = detail.get("jobs") or []
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

    run_obj = detail.get("run") or {}
    jobs = detail.get("jobs") or []
    result = (jobs[0].get("result") or {}) if jobs else {}
    print("\n=== result ===")
    for key in ("output", "duration", "videoKey", "videoUrl", "draftError", "deliveryError"):
        if key in result:
            print(f"  {key:14s} {result[key]}")
    deliveries = result.get("deliveries") or []
    print(f"  deliveries     {len(deliveries)}")
    for d in deliveries:
        probe = d.get("probe") or {}
        print(f"    - {d.get('platform'):12s} ok={d.get('ok')} "
              f"{probe.get('width')}x{probe.get('height')} "
              f"{(d.get('bytes') or 0) // 1024} KiB key={d.get('key') or '-'}")
    material_id = run_obj.get("materialId")
    print(f"  materialId     {material_id or '-'}")

    problems = []
    if not result.get("videoKey"):
        problems.append("no master object key")
    if not args.no_draft and not material_id:
        problems.append("no draft material")
    if [d for d in deliveries if not d.get("ok")]:
        problems.append(f"{len([d for d in deliveries if not d.get('ok')])} platform(s) failed")
    if abs(float(result.get("duration") or 0) - float(meta["duration"])) > 0.5:
        problems.append(f"duration {result.get('duration')} != {meta['duration']}")
    if problems:
        print("\nFAIL: " + "; ".join(problems))
        print(json.dumps(detail, ensure_ascii=False)[:2000])
        return 1
    print("\nPASS: 母版 + 平台规格 + 剪映草稿 全部就位")
    print(f"  open: http://127.0.0.1:58686/studio")
    print(f"  runId: {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
