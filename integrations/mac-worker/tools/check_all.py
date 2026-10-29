#!/usr/bin/env python3
"""一体化宣发工作台：一条命令跑完当前所有**不碰 GUI、不真发**的自检。

为什么有它：链路里的检查分散在四五个脚本里，每轮验收要记顺序和参数，还容易漏。
这里按依赖顺序串起来，统一超时、统一汇总，任何一项失败退出码就是 1——上一轮
「四条队列探针都过」这类结论就不用靠人念。

覆盖：
  1. check_publish.py        发布段离线契约（参数、话题、账号、失败话术）
  2. check_jianying.py       剪映段离线自检（产出体检、产物绑定、时间线下发、sidecar、版本适配）
  3. check_publish_lane.py   发布泳道真机探针四条（claim / cancelled-run / stale-revision / lease-expired）
  4. jianying_versions.py    本机剪映版本与 schema 依据（同主版本实证才盖章）

跑法：
    <venv-python> tools/check_all.py            全部
    <venv-python> tools/check_all.py --offline  只跑离线（不排真机作业）
    <venv-python> tools/check_all.py --json /tmp/x.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

MW = Path(__file__).resolve().parents[1]
TOOLS = MW / "tools"

# (名字, 脚本, 参数, 超时秒, 是否联网/排队)
CHECKS: list[tuple[str, str, list[str], float, bool]] = [
    ("发布段离线契约", "check_publish.py", [], 180.0, False),
    ("剪映段离线自检", "check_jianying.py", [], 240.0, False),
    ("发布泳道真机探针", "check_publish_lane.py", ["--apply", "--all"], 300.0, True),
    ("剪映版本依据", "jianying_versions.py", ["--json"], 60.0, False),
]


def run_one(script: str, argv: list[str], timeout: float) -> tuple[bool, str, str]:
    cmd = [sys.executable, str(TOOLS / script), *argv] if script != "jianying_versions.py" \
        else [sys.executable, str(MW / script), *argv]
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(MW),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"{timeout:.0f}s 超时", f"{time.time() - started:.1f}s"
    elapsed = f"{time.time() - started:.1f}s"
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    detail = tail[-1][:160] if tail else ""
    return proc.returncode == 0, detail, elapsed


def run_lane(argv: list[str], timeout: float) -> tuple[bool, str, str]:
    """真机探针输出是 JSON 列表；这里把它压成一行结论，别把整段 JSON 当详情。"""
    import re
    cmd = [sys.executable, str(TOOLS / "check_publish_lane.py"), *argv]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(MW), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"{timeout:.0f}s 超时", f"{time.time() - started:.1f}s"
    elapsed = f"{time.time() - started:.1f}s"
    try:
        results = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001 - 探针自身崩了，用原始输出说明
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        return proc.returncode == 0, (tail[-1][:160] if tail else "无输出"), elapsed
    ok, detail = summarize_lane(results)
    return ok, detail, elapsed


def summarize_lane(results: list[dict]) -> tuple[bool, str]:
    """把探针 JSON 列表压成一行：全 PASS 给条数与耗时，有失败给首个失败项与失败断言。"""
    if not results:
        return False, "探针没有返回任何结果"
    names = "+".join(str(r.get("case")) for r in results)
    failed = [r for r in results if str(r.get("verdict")).upper() != "PASS"]
    if failed:
        bad = failed[0]
        reasons = [k for k, v in (bad.get("checks") or {}).items() if not v]
        return False, f'{bad.get("case")} FAIL：{", ".join(reasons)[:120] or "无断言输出"}'
    timings = "/".join(f'{float(r.get("elapsedSeconds") or 0):.1f}' for r in results)
    return True, f"{len(results)} 条全 PASS（{names}；{timings} s）"


def version_note() -> str:
    """版本依据的摘要：主版本、schema 来源、是否拿到同主版本实证。"""
    try:
        proc = subprocess.run(
            [sys.executable, str(MW / "jianying_versions.py"), "--json"],
            cwd=str(MW), capture_output=True, text=True, timeout=60, check=False,
        )
        info = json.loads(proc.stdout)
    except Exception as exc:  # noqa: BLE001
        return f"读不到版本信息：{type(exc).__name__}"
    app = info.get("app") or {}
    schema = info.get("schema") or {}
    support = info.get("support") or {}
    if not app.get("exists"):
        return "本机未安装剪映"
    if schema.get("source") == "reference-same-major":
        adopted = f'同主版本实证 schema {schema.get("version")}/{schema.get("new_version")}'
    else:
        adopted = "沿用模板（无同主版本实证，不猜版本号）"
    stamps = "/".join(info.get("referenceApps") or []) or "无"
    return (f'{app.get("version")} build {app.get("build")} · {adopted} · '
            f'参考草稿 {info.get("references")} 份（版本戳 {stamps}）')


def main() -> int:
    ap = argparse.ArgumentParser(description="一体化宣发工作台全量自检")
    ap.add_argument("--offline", action="store_true", help="跳过真机排队探针")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    rows: list[dict[str, object]] = []
    for name, script, argv, timeout, live in CHECKS:
        if live and args.offline:
            rows.append({"name": name, "ok": True, "skipped": True,
                         "detail": "跳过（--offline）", "elapsed": "-"})
            continue
        if script == "jianying_versions.py":
            # 版本依据本身不是「过/不过」，缺剪映才判失败；细节看 version_note()。
            proc = subprocess.run(
                [sys.executable, str(MW / script), "--json"],
                cwd=str(MW), capture_output=True, text=True, timeout=timeout, check=False,
            )
            ok = proc.returncode == 0
            rows.append({"name": name, "ok": ok, "skipped": False,
                         "detail": version_note(), "elapsed": "-"})
            continue
        if script == "check_publish_lane.py":
            ok, detail, elapsed = run_lane(argv, timeout)
        else:
            ok, detail, elapsed = run_one(script, argv, timeout)
        rows.append({"name": name, "ok": ok, "skipped": False,
                     "detail": detail, "elapsed": elapsed})

    width = max(len(str(row["name"])) for row in rows)
    print("一体化宣发工作台 · 自检汇总")
    for row in rows:
        mark = "跳过" if row["skipped"] else ("PASS" if row["ok"] else "FAIL")
        print(f'  {mark:4}  {str(row["name"]).ljust(width)}  {row["elapsed"]:>6}  {row["detail"]}')
    failed = [row for row in rows if not row["ok"]]
    print(f'\n{len(rows) - len(failed)}/{len(rows)} 通过' + (f'，失败：{failed[0]["name"]}' if failed else ""))
    if args.json:
        Path(args.json).write_text(
            json.dumps({"rows": rows, "passed": len(rows) - len(failed), "total": len(rows)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
