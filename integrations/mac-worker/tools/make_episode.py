#!/usr/bin/env python3
"""make_episode.py — 从命令行走平台出「干货卡」竖屏片（单条 / 批量）。

真正的版式实现已下沉到 `integrations/mac-worker/factcard.py`，worker 直接读
`spec.template == "factcard"` 出图并在本地展开，所以这个脚本只是薄薄一层 CLI：
组装 spec → 预览自查 → POST /api/production/runs → 轮询到草稿。

用法：
  # 单条
  python3 integrations/mac-worker/tools/make_episode.py \
    --topic "KV Cache 是什么？为什么只存 K 和 V" \
    --point "只存 K/V::Q 只参与一次相似度计算，算完即弃，缓存它纯属浪费显存" \
    --point "显存决定并发::batch 越大 KV Cache 越占"

  # 批量（JSON 数组，字段同 spec），一次排多条
  python3 integrations/mac-worker/tools/make_episode.py --batch topics.json

  # 只出图自查，不提交
  ... --dry-run
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

WORKER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKER_DIR))

import factcard  # noqa: E402

HOME = Path.home()
STUDIO_HOME = Path(os.environ.get("STUDIO_HOME", str(HOME / "CreatorStudio")))
API_BASE = os.environ.get("STUDIO_API", "http://127.0.0.1:8080/api")
TOKEN_PATH = Path(os.environ.get("STUDIO_TOKEN_FILE", str(STUDIO_HOME / "config/user.token")))
DEFAULT_PROJECT = os.environ.get("STUDIO_PROJECT", "6adc2161656a13996166bbfc")
TERMINAL = {"succeeded", "success", "completed", "failed", "error", "canceled"}


# ---------------------------------------------------------------- 平台
def _token() -> str:
    return TOKEN_PATH.read_text(encoding="utf-8").strip()


def _req(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API_BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {_token()}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def submit(project: str, title: str, brief: str, spec: dict) -> str | None:
    payload = {
        "projectId": project,
        "title": title,
        "brief": brief,
        "spec": {**spec, "engine": "jianying"},
    }
    data = _req("POST", "/production/runs", payload).get("data") or {}
    run = data.get("run") or (data if isinstance(data, dict) else {})
    run_id = (data.get("runId") or run.get("id") or run.get("_id")
              or (data.get("id") if isinstance(data, dict) else None))
    if not run_id:  # 有些版本只回 jobId，退一步按列表最新一条兜底
        latest = _req("GET", "/production/runs?pageNo=1&pageSize=1").get("data") or {}
        items = latest if isinstance(latest, list) else (
            latest.get("items") or latest.get("list") or [])
        run_id = items[0].get("id") if items else None
    return run_id


def wait_run(run_id: str, timeout: int = 900) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        body = _req("GET", f"/production/runs/{run_id}")
        data = body.get("data") or body
        run = data.get("run") or data
        status = str(run.get("status") or "")
        jobs = data.get("jobs") or []
        prog = max([int(j.get("progress") or 0) for j in jobs] or [0])
        line = f"{status} {prog}%"
        if line != last:
            print(f"[{time.strftime('%H:%M:%S')}] {run_id} → {line}", flush=True)
            last = line
        if status in TERMINAL:
            return data
        time.sleep(4)
    raise SystemExit(f"run {run_id} 超时未结束")


# ---------------------------------------------------------------- 单条
def one(spec_in: dict, args) -> dict:
    slug = factcard.re.sub(r"[^0-9a-zA-Z\u4e00-\u9fa5]+", "",
                           spec_in.get("topic") or "episode")[:24] or "episode"
    out_dir = Path(args.out) if args.out else STUDIO_HOME / "state/episodes" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = factcard.expand(spec_in, out_dir)
    (out_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"spec → {out_dir/'spec.json'}  scenes={len(spec['scenes'])}  画布 {factcard.W}x{factcard.H}")
    for shot in factcard.render_previews(spec, out_dir):
        print(f"preview → {shot}")
    if args.dry_run:
        return {"topic": spec_in.get("topic"), "previewDir": str(out_dir / "preview")}
    # 只把选题/要点发给平台，卡面由 worker 本地出图（template=factcard 触发展开）
    run_id = submit(args.project, spec_in.get("topic") or "干货卡", spec_in.get("hook") or "",
                    {**spec_in, "template": "factcard"})
    print(f"已提交 run {run_id}")
    final = wait_run(str(run_id))
    jobs = final.get("jobs") or []
    print(json.dumps({"run": run_id, "jobs": [{k: j.get(k) for k in ("status", "progress", "error")}
                                              for j in jobs]}, ensure_ascii=False)[:600])
    return {"topic": spec_in.get("topic"), "runId": run_id,
            "status": (final.get("run") or {}).get("status")}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="干货卡竖屏短片：出图 + spec + 平台提交")
    ap.add_argument("--topic")
    ap.add_argument("--kicker", default=factcard.DEFAULT_KICKER)
    ap.add_argument("--hook", default=factcard.DEFAULT_HOOK)
    ap.add_argument("--point", action="append", default=[])
    ap.add_argument("--cta", default=factcard.DEFAULT_CTA)
    ap.add_argument("--voice", default=factcard.DEFAULT_VOICE)
    ap.add_argument("--project", default=DEFAULT_PROJECT)
    ap.add_argument("--out", default="")
    ap.add_argument("--batch", default="", help="JSON 数组文件，逐条提交")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.batch:
        items = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        results = []
        for i, item in enumerate(items, 1):
            print(f"\n=== [{i}/{len(items)}] {item.get('topic')}")
            merged = {"kicker": args.kicker, "voice": args.voice, **item}
            if args.dry_run:
                merged["_previewOnly"] = True
            results.append(one(merged, args))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if not args.topic:
        ap.error("需要 --topic 或 --batch")
    spec_in = {"topic": args.topic, "kicker": args.kicker, "hook": args.hook,
               "points": args.point, "cta": args.cta, "voice": args.voice}
    one(spec_in, args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
