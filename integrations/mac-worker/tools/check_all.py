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
  5. 后端 production 单测    租约回收、发布状态机（vitest）
  6. Web 类型检查            界面与后端类型的接线（tsc，--with-web 才跑）
  7. Web 三套 lint           文案/中文排印/样式，按**不高于基线**判定（--with-web 才跑）

跑法：
    <venv-python> tools/check_all.py             全部（含真机探针，约 1 分钟）
    <venv-python> tools/check_all.py --offline   只跑离线（不排真机作业，约 5 s）
    <venv-python> tools/check_all.py --with-web  加上后端单测与 Web 类型检查（多约 1 分钟）
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
REPO = MW.parents[1]
BACKEND = REPO / "project/aitoearn-backend"
WEB = REPO / "project/aitoearn-web"

# kind: python=本目录工具；unittest=单元测试模块；lane=真机探针（压成一行结论）；version=版本依据；
# shell=外部工作区的命令（后端单测、Web 类型检查），只有 --with-web 才跑。
CHECKS: list[dict[str, object]] = [
    {"name": "发布段离线契约", "kind": "python", "cmd": ["check_publish.py"],
     "timeout": 180.0, "live": False, "group": "core"},
    {"name": "剪映段离线自检", "kind": "python", "cmd": ["check_jianying.py"],
     "timeout": 240.0, "live": False, "group": "core"},
    {"name": "worker 分发单元测试", "kind": "unittest",
     "cmd": ["tests.test_claim_dispatch", "tests.test_draft_listing",
            "tests.test_export_landing", "tests.test_input_tiers",
            "tests.test_materials", "tests.test_version_evidence"],
     "timeout": 120.0,
     "live": False, "group": "core"},
    {"name": "发布泳道真机探针", "kind": "lane",
     "cmd": ["--apply", "--all"], "timeout": 300.0, "live": True, "group": "core"},
    {"name": "剪映版本依据", "kind": "version", "cmd": ["--json"],
     "timeout": 60.0, "live": False, "group": "core"},
    {"name": "后端生产 spec", "kind": "shell",
     "cmd": ["npx", "vitest", "run",
             "apps/aitoearn-server/src/core/production/production.service.spec.ts"],
     "cwd": str(BACKEND), "timeout": 300.0, "live": False, "group": "web",
     "pattern": r"Tests\s+(\d+ passed[^\n]*)"},
    {"name": "Web 类型检查", "kind": "shell", "cmd": ["pnpm", "run", "type-check"],
     "cwd": str(WEB), "timeout": 600.0, "live": False, "group": "web",
     "pattern": r"(Tasks:\s+.*)", "okDetail": "tsc --noEmit 通过（0 错误）"},
    {"name": "Web 文案 lint", "kind": "lint", "lint": "ui-copy", "group": "web",
     "cmd": ["node", str(Path.home() / ".dsh/skills/ui-restraint/scripts/ui-copy-lint.mjs"),
             "src/app/[lng]/studio"],
     "cwd": str(WEB), "timeout": 240.0, "live": False, "baseline": 0},
    {"name": "Web 中文排印", "kind": "lint", "lint": "zh", "group": "web",
     "cmd": ["node", str(Path.home() / ".agents/skills/zh-ui-type-lint/scripts/zh-lint.mjs"),
             "src/app/[lng]/studio"],
     "cwd": str(WEB), "timeout": 240.0, "live": False, "baseline": 0},
    {"name": "Web 样式 lint", "kind": "lint", "lint": "style", "group": "web",
     "cmd": ["node", str(Path.home() / ".dsh/tools/style-lint.mjs"),
             "src/app/[lng]/studio"],
     "cwd": str(WEB), "timeout": 240.0, "live": False, "baseline": 1},
]

# 三套 lint 都是「改动不许把问题数推高」：基线集中写在上面的 baseline 里，超过才 FAIL，
# 并说清超了几条、是哪一类。文案这条现在是 0：studio 目录只剩界面文件，按渲染计划拼数据
# 的模块已挪到 src/utils/studio（它们不是文案，之前 64 条全是那两份文件的类型与注释）。
LINT_SUMMARY = {
    "ui-copy": r"ui-copy-lint:\s*(\d+) error\(s\),\s*(\d+) warning\(s\)",
    "zh": r"zh-lint:\s*(\d+) error\(s\),\s*(\d+) warning\(s\)",
}


def run_shell(entry: dict) -> tuple[bool, str, str]:
    """外部命令（后端单测 / Web 类型检查）：成功看退出码，详情从输出里挑一行。"""
    import re
    started = time.time()
    try:
        proc = subprocess.run(
            list(entry["cmd"]), cwd=str(entry["cwd"]), capture_output=True,
            text=True, timeout=float(entry["timeout"]), check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f'{float(entry["timeout"]):.0f}s 超时', f"{time.time() - started:.1f}s"
    elapsed = f"{time.time() - started:.1f}s"
    output = f'{proc.stdout or ""}\n{proc.stderr or ""}'
    match = re.search(str(entry.get("pattern") or ""), output)
    if proc.returncode == 0 and entry.get("okDetail") and not match:
        detail = str(entry["okDetail"])
    elif match:
        detail = match.group(1).strip()[:120]
    else:
        tail = [line for line in output.splitlines() if line.strip()]
        detail = tail[-1].strip()[:120] if tail else "无输出"
    return proc.returncode == 0, detail, elapsed


def run_lint(entry: dict) -> tuple[bool, str, str]:
    """lint：解析问题条数并与基线比，不比退出码（这几套脚本有存量问题时本身非零）。"""
    import re
    started = time.time()
    try:
        proc = subprocess.run(list(entry["cmd"]), cwd=str(entry["cwd"]), capture_output=True,
                              text=True, timeout=float(entry["timeout"]), check=False)
    except subprocess.TimeoutExpired:
        return False, f'{float(entry["timeout"]):.0f}s 超时', f"{time.time() - started:.1f}s"
    elapsed = f"{time.time() - started:.1f}s"
    output = f'{proc.stdout or ""}\n{proc.stderr or ""}'
    baseline = int(entry.get("baseline") or 0)
    kind = str(entry.get("lint"))
    if kind in LINT_SUMMARY:
        match = re.search(LINT_SUMMARY[kind], output)
        if not match:
            return False, "解析不到问题条数（脚本可能改了输出格式）", elapsed
        errors, warnings = int(match.group(1)), int(match.group(2))
        ok = errors <= baseline
        detail = f"{errors} 条错误 / {warnings} 条提醒（基线 {baseline}）"
        return ok, detail, elapsed
    problems = len(re.findall(r"ERROR \[P\d\]", output))
    ok = problems <= baseline
    return ok, f"{problems} 条问题（基线 {baseline}）", elapsed


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


def run_unittest(modules: list[str], timeout: float) -> tuple[bool, str, str]:
    """在 mac-worker 根目录跑指定单元测试模块（标准库，不需要 worker 那套重依赖）。"""
    cmd = [sys.executable, "-m", "unittest", *modules]
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(MW), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"{timeout:.0f}s 超时", f"{time.time() - started:.1f}s"
    elapsed = f"{time.time() - started:.1f}s"
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()
    ran = next((line.strip() for line in reversed(tail) if line.startswith("Ran ")), "")
    detail = ran or (tail[-1][:160] if tail else "")
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
    elif support.get("status") == "accepted":
        adopted = f'行为实证 {info.get("evidence")} 条（这版剪映打开并回写过引擎草稿）'
    else:
        adopted = "沿用模板（无同主版本实证，不猜版本号）"
    stamps = "/".join(info.get("referenceApps") or []) or "无"
    return (f'{app.get("version")} build {app.get("build")} · {adopted} · '
            f'参考草稿 {info.get("references")} 份（版本戳 {stamps}）')


def main() -> int:
    ap = argparse.ArgumentParser(description="一体化宣发工作台全量自检")
    ap.add_argument("--offline", action="store_true", help="跳过真机排队探针")
    ap.add_argument("--with-web", action="store_true", help="加上后端单测与 Web 类型检查")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    rows: list[dict[str, object]] = []
    for entry in CHECKS:
        name = str(entry["name"])
        kind = str(entry["kind"])
        group = str(entry.get("group") or "core")
        if bool(entry.get("live")) and args.offline:
            rows.append({"name": name, "ok": True, "skipped": True,
                         "detail": "跳过（--offline）", "elapsed": "-"})
            continue
        if group == "web" and not args.with_web:
            rows.append({"name": name, "ok": True, "skipped": True,
                         "detail": "跳过（加 --with-web 才跑）", "elapsed": "-"})
            continue
        if kind == "version":
            # 版本依据本身不是「过/不过」，缺剪映才判失败；细节看 version_note()。
            proc = subprocess.run(
                [sys.executable, str(MW / "jianying_versions.py"), "--json"],
                cwd=str(MW), capture_output=True, text=True,
                timeout=float(entry["timeout"]), check=False,
            )
            rows.append({"name": name, "ok": proc.returncode == 0, "skipped": False,
                         "detail": version_note(), "elapsed": "-"})
            continue
        if kind == "lint":
            ok, detail, elapsed = run_lint(entry)
        elif kind == "lane":
            ok, detail, elapsed = run_lane(list(entry["cmd"]), float(entry["timeout"]))
        elif kind == "unittest":
            ok, detail, elapsed = run_unittest(list(entry["cmd"]), float(entry["timeout"]))
        elif kind == "shell":
            ok, detail, elapsed = run_shell(entry)
        else:
            ok, detail, elapsed = run_one(str(entry["cmd"][0]), list(entry["cmd"][1:]),
                                          float(entry["timeout"]))
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
