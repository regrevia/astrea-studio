"""Minimal HTTP client for the local Studio production API.

Only stdlib: the worker must survive on a bare macOS Python 3.11 without a
virtualenv, and every endpoint is loopback-only anyway.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"HTTP {status} on {path}: {body[:300]}")
        self.status = status
        self.body = body


class StudioClient:
    def __init__(self, base: str, worker_token: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.token = worker_token
        self.timeout = timeout

    def _call(self, method: str, path: str, payload: Any | None = None) -> Any:
        url = f"{self.base}{path}"
        data = None
        headers = {"x-worker-token": self.token, "accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.read().decode(errors="replace"), path) from None
        if not raw:
            return None
        body = json.loads(raw)

        # The server answers {data, code, message}. Without unwrapping, callers
        # read `job` off the envelope, always get None, and the worker idles
        # forever while claiming to be healthy — so unwrap here, once.
        if isinstance(body, dict) and "code" in body and "data" in body:
            if body.get("code") not in (0, None):
                raise ApiError(
                    int(body.get("code") or 0),
                    f"{body.get('message') or 'api error'}",
                    path,
                )
            return body["data"]
        return body

    # ------------------------------------------------------------------ jobs

    def claim(self, lane: str) -> Any:
        return self._call("POST", "/api/production/worker/claim", {"lane": lane})

    def heartbeat(self, job_id: str, lease_version: int, progress: int | None = None,
                  stage: str | None = None, message: str | None = None) -> Any:
        body: dict[str, Any] = {"leaseVersion": lease_version}
        if progress is not None:
            body["progress"] = progress
        if stage:
            body["stage"] = stage
        if message:
            body["message"] = message
        return self._call("POST", f"/api/production/worker/jobs/{job_id}/heartbeat", body)

    def event(self, job_id: str, kind: str, **kw: Any) -> Any:
        body = {"kind": kind, **{k: v for k, v in kw.items() if v is not None}}
        return self._call("POST", f"/api/production/worker/jobs/{job_id}/events", body)

    def complete(self, job_id: str, lease_version: int, result: dict[str, Any],
                 run_patch: dict[str, Any] | None = None) -> Any:
        return self._call(
            "POST",
            f"/api/production/worker/jobs/{job_id}/complete",
            {"leaseVersion": lease_version, "result": result, "runPatch": run_patch or {}},
        )

    def fail(self, job_id: str, lease_version: int, error: str, status: str = "failed") -> Any:
        return self._call(
            "POST",
            f"/api/production/worker/jobs/{job_id}/fail",
            {"leaseVersion": lease_version, "error": error, "status": status},
        )

    def attach_material(self, run_id: str, video_key: str, title: str = "",
                        desc: str = "", topics: list[str] | None = None,
                        duration: float | None = None) -> Any:
        """Turn a finished render into a draft in the owner's draft box.

        The server resolves the group, so the worker never sends a groupId and a
        retry lands on the same draft instead of creating a second one.
        """
        body: dict[str, Any] = {"videoUrl": video_key}
        if title:
            body["title"] = title
        if desc:
            body["desc"] = desc
        if topics:
            body["topics"] = topics
        if duration:
            body["duration"] = duration
        return self._call("POST", f"/api/production/worker/runs/{run_id}/material", body)

    def alive(self) -> bool:
        try:
            self._call("POST", "/api/production/worker/workers/heartbeat", {})
            return True
        except (ApiError, OSError):
            return False
