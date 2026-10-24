#!/usr/bin/env python3
"""把 creator-studio 的 HTTP MCP 端点桥接成 stdio MCP，供 Codex 等客户端接入。

为什么需要桥：后端把 MCP 暴露成 HTTP（`POST /api/production/mcp`，Bearer 鉴权），
而 Codex / Claude Desktop 这类客户端的 MCP 传输是 stdio。中间少一层转换，
所以这里做一个最小的透传桥：
  stdin 上逐行读 JSON-RPC → POST 到后端 → 把响应写回 stdout。

它刻意不做的事情：不改请求、不缓存、不重试、不解析业务语义。
一旦开始"智能处理"，桥就会和后端的 schema 漂移，且故障点翻倍。

用法（Codex config.toml）：
  [mcp_servers.creator_studio]
  command = "python3"
  args = ["$HOME/CreatorStudio/bin/studio_mcp_bridge.py"]  # 换成自己的路径
  startup_timeout_sec = 60

token 从 ~/CreatorStudio/config/user.token 读；也可以用环境变量
STUDIO_MCP_TOKEN 覆盖。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8080/api/production/mcp"
TOKEN_FILE = Path.home() / "CreatorStudio" / "config" / "user.token"


def _token() -> str:
    env = os.environ.get("STUDIO_MCP_TOKEN", "").strip()
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def _post(payload: dict, timeout: float = 120.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        os.environ.get("STUDIO_MCP_URL", DEFAULT_URL),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {_token()}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")

    # 后端可能回 SSE（`data: {...}`），也可能回纯 JSON。
    raw = raw.strip()
    if raw.startswith("event:") or raw.startswith("data:"):
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        raise ValueError(f"unparsable SSE from server: {raw[:200]}")
    return json.loads(raw)


def _err(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def main() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps(_err(None, -32700, "parse error")) + "\n")
            out.flush()
            continue

        # notification（无 id）不需要回响应
        is_notification = "id" not in msg

        try:
            result = _post(msg)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            result = _err(msg.get("id"), -32603, f"HTTP {exc.code}: {detail}")
        except Exception as exc:  # noqa: BLE001 - 桥必须永不崩
            result = _err(msg.get("id"), -32603, f"{type(exc).__name__}: {exc}")

        if not is_notification:
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
