#!/usr/bin/env python3
"""冒烟：按 MCP 客户端的真实时序跑一遍这个服务（不渲染）。

    python3 plugins/codex/mcp/smoke.py

初始化和列工具必须过；能力服务没起来的机器上，studio_ready 也算「有回话」，
只是 isError=true —— 那也通过，因为桥的责任是回话，不是替服务背锅。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FIXTURE = REPO / "tools/bridge/tests/production.json"


def main() -> int:
    proc = subprocess.Popen([sys.executable, str(HERE / "studio_mcp.py")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdin and proc.stdout

    def rpc(msg_id, method, params=None):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method,
                                     "params": params or {}}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            raise SystemExit(f"服务没回话（{method}），stderr: {proc.stderr.read()[-800:]}")
        return json.loads(line)

    failures = []

    init = rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "smoke", "version": "0"}})
    info = init.get("result", {}).get("serverInfo", {})
    print(f"initialize      -> {info.get('name')} {info.get('version')}")
    if info.get("name") != "creator-studio":
        failures.append("serverInfo.name 不是 creator-studio")

    tools = rpc(2, "tools/list").get("result", {}).get("tools", [])
    names = [t["name"] for t in tools]
    print(f"tools/list      -> {len(names)} 个: {', '.join(names)}")
    for required in ("studio_ready", "studio_submit", "studio_wait", "studio_shots"):
        if required not in names:
            failures.append(f"缺工具 {required}")
    if any(not t.get("inputSchema") for t in tools):
        failures.append("有工具没 inputSchema")
    if any("publish" in n or "upload" in n for n in names):
        failures.append("工具面出现了发布/上传口子")

    ready = rpc(3, "tools/call", {"name": "studio_ready", "arguments": {}}).get("result", {})
    body = ready.get("content", [{}])[0].get("text", "")
    print(f"studio_ready    -> isError={ready.get('isError')} 首行={body.splitlines()[0] if body else ''}")

    films = rpc(4, "tools/call", {"name": "studio_films", "arguments": {"limit": 3}})
    print("studio_films    ->", films.get("result", {}).get("content", [{}])[0].get("text", "")[:120].replace("\n", " "))

    if FIXTURE.is_file():
        out = Path("/tmp/studio-mcp-smoke")
        shots = rpc(5, "tools/call", {"name": "studio_shots",
                                      "arguments": {"input": str(FIXTURE), "out": str(out)}})
        res = shots.get("result", {})
        text = res.get("content", [{}])[0].get("text", "")
        ok = not res.get("isError") and (out / "spec.json").is_file()
        print(f"studio_shots    -> {'ok' if ok else 'FAILED'}  {out / 'spec.json'}")
        if not ok:
            failures.append("studio_shots 没产出 spec.json：" + text[:400])

    unknown = rpc(6, "tools/call", {"name": "nope", "arguments": {}})
    if "error" not in unknown:
        failures.append("未知工具没返回 error")
    print(f"未知工具        -> {unknown.get('error', {}).get('code')}")

    proc.stdin.close()
    proc.wait(timeout=20)

    if failures:
        print("\n不合格：\n- " + "\n- ".join(failures))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
