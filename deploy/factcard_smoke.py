#!/usr/bin/env python3
"""干货卡一键出片的端到端冒烟。

跑的是用户真实路径：HTTP 提交 → worker 渲染 → 草稿箱落地 → 校验 draft_content.json。
只依赖标准库，无头，不需要开窗口。

    python3 deploy/factcard_smoke.py            # 默认样例
    python3 deploy/factcard_smoke.py --no-wait  # 只提交，拿到 runId 就走

退出码非 0 表示这条链路坏了。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STUDIO_HOME = Path(os.environ.get("CREATORSTUDIO_HOME") or Path.home() / "CreatorStudio")
BASE = os.environ.get("STUDIO_API", "http://127.0.0.1:8080")
DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"

SAMPLE_SPEC = {
    "topic": "KV Cache 是什么？",
    "hook": "为什么只存 K 和 V",
    "points": [
        "省的是重复计算::每个 token 的 K/V 只算一次，后面直接查表",
        "为什么不存 Q::Q 只和当前 token 有关，下一个 token 就变了",
        "代价是显存::序列越长缓存越大，所以要做分页和量化",
    ],
    "cta": "记住这几条，面试能答到点上。下一题想看什么，评论区说",
    "voice": "sami:BV405_streaming",
}


def _token() -> str:
    path = STUDIO_HOME / "config/user.token"
    if not path.exists():
        sys.exit(f"找不到登录态: {path}")
    return path.read_text(encoding="utf-8").strip()


def _req(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path} → HTTP {exc.code}: {exc.read().decode('utf-8')[:400]}")


def _first_project() -> str:
    data = _req("GET", "/api/production/projects").get("data") or []
    if not data:
        sys.exit("没有可用项目，先在 /studio 建一个")
    return data[0]["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-wait", action="store_true", help="只提交，不等渲染")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--topic", default=SAMPLE_SPEC["topic"])
    args = ap.parse_args()

    project = _first_project()
    body = {
        "projectId": project,
        "title": f"冒烟·{args.topic}",
        "brief": "factcard smoke",
        "engine": "jianying",
        "template": "factcard",
        "spec": {**SAMPLE_SPEC, "topic": args.topic},
    }
    created = _req("POST", "/api/production/runs", body).get("data") or {}
    run_id = created.get("runId")
    if not run_id:
        sys.exit(f"提交没拿到 runId: {created}")
    print(f"run {run_id} 已提交 (project {project})")
    if args.no_wait:
        return 0

    deadline = time.time() + args.timeout
    job: dict = {}
    status = ""
    while time.time() < deadline:
        time.sleep(5)
        detail = _req("GET", f"/api/production/runs/{run_id}").get("data") or {}
        status = (detail.get("run") or {}).get("status") or ""
        jobs = detail.get("jobs") or []
        job = jobs[0] if jobs else {}
        print(f"  {status} {job.get('progress')}% {job.get('stage') or ''}".rstrip())
        if status in ("succeeded", "failed", "canceled"):
            break

    if status != "succeeded":
        last = str(job.get("lastError") or "").strip()
        sys.exit(f"失败: status={status} lastError={last[:600]}")

    result = job.get("result") or {}
    stats = result.get("stats") or {}
    draft = result.get("draftPath") or ""
    print(f"stats {json.dumps(stats, ensure_ascii=False)}")
    print(f"draft {draft}")
    if not draft:
        sys.exit("成功但没有 draftPath")
    content = Path(draft) / "draft_content.json"
    if not content.exists():
        sys.exit(f"草稿缺 draft_content.json: {content}")

    doc = json.loads(content.read_text(encoding="utf-8"))
    tracks = {}
    for track in (doc.get("tracks") or []):
        tracks.setdefault(track.get("type"), []).append(track)
    counts = {k: len(v) for k, v in tracks.items()}
    print(f"tracks {counts}")

    problems = []
    images = [s for t in tracks.get("video", []) for s in t.get("segments") or []]
    if len(images) < 3:
        problems.append(f"画面片段只有 {len(images)} 个")
    kf = sum(1 for s in images if (s.get("common_keyframes") or s.get("keyframe_list")))
    if kf == 0:
        problems.append("没有任何关键帧（推拉镜头没生效）")
    if not tracks.get("text"):
        problems.append("没有文字轨")
    if not tracks.get("audio"):
        problems.append("没有配音轨")
    if stats.get("skipped"):
        problems.append(f"有被剪映拒掉的资源: {stats['skipped']}")

    if problems:
        sys.exit("校验失败: " + "；".join(problems))
    print("OK 一键出片链路正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
