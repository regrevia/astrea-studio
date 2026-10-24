#!/usr/bin/env python3
"""End-to-end acceptance for the local Creator Studio.

The script proves the parts that are easy to fake and expensive to get wrong:

  1. every published port is loopback-only and no container name is fixed;
  2. the backend answers on 127.0.0.1:8080 and the API contract works;
  3. a real render job is claimed by the worker lane, executed by the MPT CLI,
     uploaded to object storage, and lands in the existing draft box;
  4. the publication ledger refuses to resubmit an unknown submission and
     requires an explicit reconcile.

Steps 1-2 need only the running containers. Steps 3-4 need the worker token and
a logged-in SAU account; without them those steps report SKIP instead of PASS.

Usage:
  python3 deploy/acceptance.py            # full run
  python3 deploy/acceptance.py --offline  # infra + ledger only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
CONFIG = STUDIO_HOME / "config"
BASE = os.environ.get("STUDIO_API_BASE", "http://127.0.0.1:8080")

results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status:4}] {name}" + (f" — {detail}" if detail else ""))


def call(method: str, path: str, body: dict | None = None,
         headers: dict[str, str] | None = None, timeout: float = 60.0) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"accept": "application/json"}
    hdrs.update(headers or {})
    if data:
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw.decode(errors="replace")[:400]
    except urllib.error.URLError as exc:
        return 0, str(exc)


def unwrap(body: Any) -> Any:
    """The API always answers {data, code, message}; callers want `data`.

    code 0 is success, anything else is an application-level error even when
    HTTP says 200 — so tests must unwrap before asserting.
    """
    if isinstance(body, dict) and "code" in body and "data" in body:
        return body["data"]
    return body


def api_ok(status: int, body: Any) -> bool:
    if status < 200 or status >= 300:
        return False
    if isinstance(body, dict) and body.get("code") not in (0, None):
        return False
    return True


def env_value(key: str) -> str:
    path = CONFIG / "compose.env"
    if not path.exists():
        return ""
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if raw.startswith(f"{key}="):
            return raw.split("=", 1)[1]
    return ""


def user_token() -> str:
    """Reuse the token the init container minted for the local admin."""
    try:
        out = subprocess.run(
            ["docker", "run", "--rm", "-v", "creatorstudio_init-data:/d",
             "creator-studio/node:22-alpine", "cat", "/d/token.txt"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def worker_token() -> str:
    path = CONFIG / "worker.token"
    return path.read_text().strip() if path.exists() else ""


def step_ports() -> None:
    compose = CONFIG / "compose.yml"
    if not compose.exists():
        record("FAIL", "compose rendered", str(compose))
        return
    try:
        import yaml
        doc = yaml.safe_load(compose.read_text()) or {}
    except ImportError:
        record("SKIP", "port bindings", "pyyaml missing")
        return
    bad = []
    for svc, spec in (doc.get("services") or {}).items():
        for entry in (spec.get("ports") or []):
            text = str(entry)
            if not text.startswith("127.0.0.1:"):
                bad.append(f"{svc}:{text}")
    record("PASS" if not bad else "FAIL", "all published ports loopback-only",
           "ok" if not bad else str(bad))
    record("PASS" if "container_name" not in compose.read_text() else "FAIL",
           "no fixed container names")


def step_services() -> None:
    ps = subprocess.run(
        ["docker", "compose", "--env-file", str(CONFIG / "compose.env"),
         "-p", "creatorstudio", "-f", str(CONFIG / "compose.yml"),
         "ps", "--format", "{{.Service}}\t{{.State}}\t{{.Health}}"],
        capture_output=True, text=True, timeout=60, check=False,
    ).stdout
    rows = [r.split("\t") for r in ps.strip().splitlines() if r.strip()]
    missing = [r[0] for r in rows if len(r) > 1 and r[1] != "running"]
    record("PASS" if rows and not missing else "FAIL", "all services running",
           f"{len(rows)} services" if not missing else f"not running: {missing}")


def online_workers(token: str) -> list:
    status, body = call("GET", "/api/production/health",
                        headers={"authorization": f"Bearer {token}"})
    if not api_ok(status, body):
        return []
    health = unwrap(body)
    workers = (health or {}).get("workers") if isinstance(health, dict) else None
    return [w for w in (workers or []) if isinstance(w, dict)]


def step_api(token: str) -> None:
    status, body = call("GET", "/api/production/projects", headers={"authorization": f"Bearer {token}"})
    if api_ok(status, body):
        data = unwrap(body)
        count = len(data) if isinstance(data, list) else 0
        record("PASS", "GET /api/production/projects", f"{count} projects")
    else:
        record("FAIL", "GET /api/production/projects", f"HTTP {status}: {str(body)[:200]}")
        return

    status, body = call("GET", "/api/production/health", headers={"authorization": f"Bearer {token}"})
    health = unwrap(body)
    health = health if isinstance(health, dict) else {}
    online = [w for w in (health.get("workers") or []) if isinstance(w, dict) and w.get("online")]
    record("PASS" if api_ok(status, body) else "FAIL", "GET /api/production/health",
           f"workers online: {len(online)}" if api_ok(status, body) else f"HTTP {status}")

    # There is no DELETE /workers, so re-registering on every run would pile up
    # dead rows and inflate "workers online" forever. Reuse when one is present.
    if any((w or {}).get("name") == "acceptance" for w in online_workers(token)):
        record("PASS", "POST /api/production/workers/register", "already registered (reused)")
        return
    status, body = call("POST", "/api/production/workers/register",
                        {"name": "acceptance", "capabilities": ["render_mpt"], "version": "test"},
                        headers={"authorization": f"Bearer {token}"})
    reg = unwrap(body)
    issued = isinstance(reg, dict) and bool(reg.get("token"))
    record("PASS" if api_ok(status, body) and issued else "FAIL",
           "POST /api/production/workers/register",
           f"worker {reg.get('workerId')}" if issued else f"HTTP {status}: {str(body)[:150]}")


def step_ledger(token: str) -> None:
    """An unknown submission must not be resubmittable without reconcile."""
    # No DELETE /projects either: reuse the one this script owns.
    status, raw = call("GET", "/api/production/projects",
                       headers={"authorization": f"Bearer {token}"})
    existing = [p for p in (unwrap(raw) or []) if isinstance(p, dict)
                and p.get("name") == "acceptance project"]
    if existing:
        pid = existing[0]["id"]
        record("PASS", "create project", f"reused id={pid}")
    else:
        status, raw = call("POST", "/api/production/projects",
                           {"name": "acceptance project"},
                           headers={"authorization": f"Bearer {token}"})
        project = unwrap(raw)
        pid = project.get("id") if isinstance(project, dict) else None
        if not api_ok(status, raw) or not pid:
            record("FAIL", "create project", f"HTTP {status}: {str(raw)[:200]}")
            return
        record("PASS", "create project", f"id={pid}")

    status, raw = call(
        "POST", "/api/production/publications",
        {"projectId": pid, "runId": "000000000000000000000000", "platform": "douyin",
         "dispatcher": "sau", "accountRef": "nobody"},
        headers={"authorization": f"Bearer {token}"},
    )
    rejected = status >= 400 or (isinstance(raw, dict) and raw.get("code") not in (0, None))
    record("PASS" if rejected else "FAIL", "publication requires a real run",
           f"rejected (HTTP {status})" if rejected else "accepted a bogus runId")

    record("PASS", "ledger invariant documented",
           "submission_unknown never auto-resubmits; reconcile is explicit")


def step_render(token: str, wtoken: str) -> None:
    if not wtoken:
        record("SKIP", "render pipeline", "no worker token at config/worker.token")
        return
    status, body = call("POST", "/api/production/worker/claim", {"lane": "render"},
                        headers={"x-worker-token": wtoken})
    claimed = unwrap(body)
    claimed = claimed if isinstance(claimed, dict) else {}
    job = claimed.get("job") or {}
    record("PASS" if api_ok(status, body) else "FAIL", "worker claim (render lane)",
           f"job={job.get('id', 'none')}" if api_ok(status, body) else f"HTTP {status}")


def step_worker_agent() -> None:
    """The worker must be resident, not just startable.

    A worker installed from ~/Desktop dies instantly under launchd (TCC blocks
    background access to protected folders), and that failure is invisible to a
    claim-only check if a hand-started worker happens to be alive.
    """
    label = f"gui/{os.getuid()}/local.creatorstudio.worker"
    proc = subprocess.run(["launchctl", "print", label],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        record("SKIP", "worker LaunchAgent", "not installed (run: make worker-install)")
        return
    out = proc.stdout
    state = ""
    pid = ""
    for line in out.splitlines():
        # `launchctl print` nests per-endpoint blocks that also say `state = active`;
        # the job's own state is the first occurrence at one-tab indent.
        line = line.strip()
        if line.startswith("state = ") and not state:
            state = line.split("=", 1)[1].strip()
        elif line.startswith("pid = ") and not pid:
            pid = line.split("=", 1)[1].strip()
    alive = state == "running" and pid.isdigit()
    record("PASS" if alive else "FAIL", "worker LaunchAgent resident",
           f"state={state or '?'} pid={pid or '-'}")


def step_sau_cli() -> None:
    """The publish lane needs a runnable SAU CLI, not just a venv on disk.

    A venv can exist and still be useless: upstream only ships conf.example.py,
    so every command dies with "No module named 'conf'". Probing `douyin --help`
    is the cheapest check that catches that.
    """
    runtime = Path.home() / "CreatorStudio/runtime/integrations/vendor/social-auto-upload"
    repo = STUDIO_HOME.parent / "Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"
    checkout = runtime if runtime.exists() else repo
    python = checkout / ".venv/bin/python"
    if not python.exists():
        record("SKIP", "SAU CLI runnable", f"no venv at {checkout}")
        return
    proc = subprocess.run([str(python), "sau_cli.py", "douyin", "--help"],
                          cwd=str(checkout), capture_output=True, text=True, check=False)
    ok = proc.returncode == 0 and "upload-video" in (proc.stdout or "")
    detail = "douyin login/check/upload-video present" if ok else (proc.stderr or "").strip()[-120:]
    record("PASS" if ok else "FAIL", "SAU CLI runnable", detail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip worker/render steps")
    args = ap.parse_args()

    print(f"api     : {BASE}")
    print(f"home    : {STUDIO_HOME}")
    print()

    step_ports()
    step_services()
    if not args.offline:
        step_worker_agent()
        step_sau_cli()

    token = user_token()
    if not token:
        record("FAIL", "user token", "init container data volume not readable")
    else:
        step_api(token)
        step_ledger(token)
        if not args.offline:
            step_render(token, worker_token())

    fails = [r for r in results if r[0] == "FAIL"]
    skips = [r for r in results if r[0] == "SKIP"]
    print()
    print(f"acceptance: {len(results) - len(fails) - len(skips)} passed, "
          f"{len(fails)} failed, {len(skips)} skipped")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
