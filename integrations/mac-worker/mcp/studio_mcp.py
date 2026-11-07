#!/usr/bin/env python3
"""creator-studio 的 stdio MCP 服务（Codex 插件的动作面）。

对照 CapCut 官方 Codex 插件的做法：客户端里塞一行 `[mcp_servers]` 只是接上了传输，
真正的接法是「插件清单 + skill 说明 + 一层模型能直接调的动作」。所以这里只暴露动作，
不暴露路由：模型不用知道 8899、不用拼 JSON、不用自己轮询。

八个工具：
  studio_ready / studio_films / studio_job / studio_jobs / studio_submit
  / studio_wait / studio_shots / studio_verify

刻意没有「发布到平台」：上传、预签名、真实发布必须人工点，工具面不出现这个口子。

协议：stdio 上逐行 JSON-RPC 2.0，零依赖（只用标准库），因此不会被 venv 版本绊住。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_NAME = "creator-studio"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

REPO = Path(__file__).resolve().parents[3]
BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8899").rstrip("/")
HOME = Path(os.environ.get("STUDIO_HOME", str(Path.home() / "CreatorStudio")))
WORK = Path(os.environ.get("STUDIO_MCP_WORK", str(HOME / "work" / "mcp")))
TERMINAL = {"succeeded", "failed", "cancelled", "canceled", "error"}


# ------------------------------------------------------------------ HTTP
def _call(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _studio_py() -> str:
    env = os.environ.get("STUDIO_PY", "").strip()
    if env:
        return env
    venv = HOME / "runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python"
    return str(venv) if venv.is_file() else sys.executable


# ------------------------------------------------------------------ 工具
def _text(payload) -> dict:
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], "isError": False}


def _fail(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def tool_ready(_: dict) -> dict:
    out: dict = {"service": BASE,
                  "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
    try:
        out["healthz"] = _call("GET", "/healthz", timeout=5)
        out["可提交"] = True
    except Exception as exc:  # noqa: BLE001 - 自检要报原因，不要抛
        return _fail(f"能力服务没起来（{BASE}）：{type(exc).__name__}: {exc}\n"
                     f"先在仓库里跑 ./bin/studio ready 看是哪一项。")
    try:
        jobs = _call("GET", "/jobs", timeout=10)
        rows = jobs if isinstance(jobs, list) else jobs.get("jobs", [])
        out["最近任务"] = rows[-3:]
    except Exception as exc:  # noqa: BLE001
        out["最近任务"] = f"读不到：{type(exc).__name__}: {exc}"
    out["交付目录"] = {str(d): d.is_dir() for d in _deliver_dirs()}
    return _text(out)


def _deliver_dirs() -> list[Path]:
    return [Path("/Volumes/US202/CreatorStudio/deliver"), HOME / "deliver"]


def tool_films(args: dict) -> dict:
    limit = int(args.get("limit") or 15)
    rows = []
    for d in _deliver_dirs():
        if not d.is_dir():
            continue
        for p in d.glob("*.mp4"):
            try:
                st = p.stat()
            except OSError:
                continue
            rows.append({"file": str(p), "bytes": st.st_size,
                         "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return _text(rows[:limit] or [{"提示": "交付目录里还没有 mp4；先 studio_submit 一条。"}])


def tool_jobs(_: dict) -> dict:
    data = _call("GET", "/jobs", timeout=10)
    rows = data if isinstance(data, list) else data.get("jobs", [])
    slim = [{k: r.get(k) for k in ("id", "kind", "status", "slug", "createdAt", "error")}
            for r in rows]
    return _text(slim[-20:])


def tool_job(args: dict) -> dict:
    job_id = str(args.get("jobId") or "").strip()
    if not job_id:
        return _fail("缺 jobId。先用 studio_jobs 列最近的。")
    return _text(_call("GET", f"/jobs/{job_id}", timeout=15))


def tool_wait(args: dict) -> dict:
    job_id = str(args.get("jobId") or "").strip()
    timeout = float(args.get("timeoutSec") or 600)
    if not job_id:
        return _fail("缺 jobId。")
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = _call("GET", f"/jobs/{job_id}", timeout=15)
        status = str(last.get("status") or "").lower()
        if status in TERMINAL:
            return _text(last)
        time.sleep(1.5)
    return _fail(f"等了 {timeout:.0f}s 还没结束，最后一次状态：\n"
                 + json.dumps(last, ensure_ascii=False, indent=2)[:1500])


def tool_submit(args: dict) -> dict:
    kind = str(args.get("kind") or "").strip()
    if not kind:
        return _fail("缺 kind（short / timeline / paper / slides / motion / jianying / materials）。")
    payload = args.get("payload") or {}
    spec = args.get("spec")
    if spec:
        payload = {**payload, "spec": spec}
    slug = str(args.get("slug") or "").strip()
    if slug:
        payload = {**payload, "slug": slug}
    try:
        res = _call("POST", "/jobs", {"kind": kind, "payload": payload}, timeout=30)
    except urllib.error.HTTPError as exc:
        return _fail(f"提交被拒：HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:400]}")
    job_id = res.get("id") or res.get("job", {}).get("id")
    if args.get("wait") and job_id:
        return tool_wait({"jobId": job_id, "timeoutSec": args.get("timeoutSec") or 900})
    return _text(res)


def tool_shots(args: dict) -> dict:
    """分镜表 → 可编辑时间轴（不渲染就只是 spec，等于 CapCut 的「草稿」）。"""
    src = str(args.get("input") or "").strip()
    WORK.mkdir(parents=True, exist_ok=True)
    out_dir = Path(str(args.get("out") or WORK / f"shots-{int(time.time())}")).expanduser()
    if src:
        source = Path(src).expanduser()
        if not source.is_file():
            return _fail(f"读不到分镜表：{source}")
    else:
        shots = args.get("shots")
        if not isinstance(shots, list) or not shots:
            return _fail("给 input（production.json/state.json）或者 shots 数组。")
        source = out_dir / "production.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        source.write_text(json.dumps({
            "id": "mcp-draft",
            "title": args.get("title") or "草稿",
            "coverText": args.get("coverText") or "",
            "shots": shots,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [_studio_py(), str(REPO / "tools/bridge/from_media_desk.py"), str(source),
           "--out", str(out_dir)]
    if args.get("noCover"):
        cmd.append("--no-cover")
    if args.get("render"):
        cmd.append("--render")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        return _fail("桥接失败：\n" + "\n".join(tail))
    spec = out_dir / "spec.json"
    plan = out_dir / "plan.md"
    return _text({
        "spec": str(spec) if spec.is_file() else None,
        "plan": str(plan) if plan.is_file() else None,
        "preview": str(out_dir / "preview.mp4") if (out_dir / "preview.mp4").is_file() else None,
        "outDir": str(out_dir),
        "改动方式": "spec.json 是明文时间轴，改完用 studio_submit kind=timeline specPath=... 重渲",
    })


def tool_verify(args: dict) -> dict:
    target = Path(str(args.get("path") or "")).expanduser()
    if not target.is_file():
        return _fail(f"没有这个文件：{target}")
    proc = subprocess.run([sys.executable, str(REPO / "tools/verify_delivery.py"), str(target)],
                          capture_output=True, text=True, timeout=1800)
    body = (proc.stdout or "") + (proc.stderr or "")
    return _text(body.strip()[-3000:] or f"退出码 {proc.returncode}")


TOOLS = {
    "studio_ready": (tool_ready, {
        "type": "object", "properties": {},
        "description": "自检：能力服务在不在、交付目录挂没挂。第一次用、或者任务失败时先跑它。",
    }),
    "studio_films": (tool_films, {
        "type": "object",
        "properties": {"limit": {"type": "integer", "description": "最多几条，默认 15"}},
        "description": "列出已经交付的成片（路径、大小、时间），按时间倒序。",
    }),
    "studio_jobs": (tool_jobs, {
        "type": "object", "properties": {},
        "description": "最近 20 个任务的 id/kind/status，找 jobId 用。",
    }),
    "studio_job": (tool_job, {
        "type": "object", "properties": {"jobId": {"type": "string"}},
        "required": ["jobId"],
        "description": "一个任务的完整状态；失败时 error 字段就是原因。",
    }),
    "studio_wait": (tool_wait, {
        "type": "object",
        "properties": {"jobId": {"type": "string"},
                       "timeoutSec": {"type": "number", "description": "最多等多少秒，默认 600"}},
        "required": ["jobId"],
        "description": "阻塞到任务结束再返回结果（成片、时长、落点）。",
    }),
    "studio_submit": (tool_submit, {
        "type": "object",
        "properties": {
            "kind": {"type": "string",
                     "enum": ["short", "timeline", "paper", "slides", "motion", "jianying", "materials"]},
            "payload": {"type": "object", "description": "kind 自己的参数（topic/hook/points… 或 specPath）"},
            "spec": {"type": "object", "description": "timeline 可以直接贴 spec 对象，省一次落盘"},
            "slug": {"type": "string"},
            "wait": {"type": "boolean", "description": "true 就直接等出结果"},
            "timeoutSec": {"type": "number"},
        },
        "required": ["kind"],
        "description": "提交一条出片任务。short=给文案出竖屏；timeline=给时间轴渲一遍。",
    }),
    "studio_shots": (tool_shots, {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "分镜表文件（media-desk 的 production/state.json）"},
            "shots": {"type": "array", "items": {"type": "object"},
                      "description": "也可以直接给分镜数组：voice/seconds/shot/src"},
            "title": {"type": "string"}, "coverText": {"type": "string"},
            "out": {"type": "string", "description": "产物目录，默认 ~/CreatorStudio/work/mcp/shots-<时间>"},
            "noCover": {"type": "boolean"}, "render": {"type": "boolean"},
        },
        "description": "分镜表 → 可编辑时间轴 spec（选 render 就直接出一条预览片）。",
    }),
    "studio_verify": (tool_verify, {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "description": "USB 交付盘上的成片必须用这个校验（大块读会串数据，shasum 不可信）。",
    }),
}


# ------------------------------------------------------------------ 协议
def _result(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle(msg: dict) -> dict | None:
    if "id" not in msg:
        return None
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return _result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [
            {"name": name, "description": spec["description"], "inputSchema": spec}
            for name, (_, spec) in TOOLS.items()
        ]})
    if method == "tools/call":
        name = str(params.get("name") or "")
        if name not in TOOLS:
            return _error(msg_id, -32602, f"没有这个工具：{name}")
        fn, _ = TOOLS[name]
        try:
            return _result(msg_id, fn(params.get("arguments") or {}))
        except urllib.error.URLError as exc:
            return _result(msg_id, _fail(f"连不上能力服务（{BASE}）：{exc}"))
        except Exception as exc:  # noqa: BLE001 - 工具失败要回话，不要让客户端看到断线
            return _result(msg_id, _fail(f"{type(exc).__name__}: {exc}"))
    return _error(msg_id, -32601, f"不支持的方法：{method}")


def main() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps(_error(None, -32700, "parse error")) + "\n")
            out.flush()
            continue
        if isinstance(msg, list):  # 批量请求（很少见，兼容一下）
            replies = [r for r in (handle(m) for m in msg) if r]
            if replies:
                out.write(json.dumps(replies, ensure_ascii=False) + "\n")
                out.flush()
            continue
        reply = handle(msg)
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
