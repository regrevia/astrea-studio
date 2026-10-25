"""能力快照 HTTP 服务（只读，loopback）。

为什么单独起一个进程而不是塞进后端容器：
后端 aitoearn-server 跑在容器里、只挂了 config.yaml，读不到宿主上的
cookies 目录和 ffmpeg/vhs 二进制。而这份「哪些平台能发、有哪些音色」
本来就是宿主事实，在容器里看反而失真。

所以：宿主起一个零依赖的 stdlib HTTP 服务，只做 GET /inventory 和
GET /characters，面板跨域直读。全部只读、不联网、不启动浏览器、不碰发布。

用法:
    python3 capabilities_server.py --port 8899
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import capabilities

sys.path.insert(0, str(Path(__file__).resolve().parent / "export"))

CACHE_TTL_SEC = 60          # cookie 变化不频繁，缓存一分钟足够
_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def characters() -> list:
    """可用主角（虚拟形象/真人/数字人）。读不到就返回空表，别让面板 500。"""
    try:
        import host_profile as H
        return [
            {"id": h.id, "label": h.label, "kind": h.kind, "voice": h.voice,
             "alpha": h.alpha, "clips": len(h.clips),
             "source": "builtin" if h.source == "builtin" else "custom"}
            for h in H.list_hosts()
        ]
    except Exception:  # noqa: BLE001 - 主角是可选能力
        return []


def outfits() -> list:
    """主角能换的服装：动作齐全的排前面，只有主图的照样能选。"""
    try:
        import character as C
        rows = []
        for oid in C.list_outfits():
            root = C.outfit_root(oid)
            poses = root / "poses"
            full = poses.is_dir() and sum(1 for _ in poses.glob("pose_*_open.png")) >= 5
            rows.append({"id": oid,
                         "label": C.OUTFIT_LABELS.get(oid, oid),
                         "complete": bool(full)})
        return rows
    except Exception:  # noqa: BLE001 - 换装是可选能力
        return []


def snapshot(force: bool = False) -> dict:
    with _lock:
        now = time.time()
        if force or _cache["data"] is None or now - _cache["at"] > CACHE_TTL_SEC:
            data = capabilities.capabilities()
            data["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            data["characters"] = characters()
            data["outfits"] = outfits()
            _cache.update(at=now, data=data)
        return _cache["data"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path in ("/inventory", "/capabilities"):
                body = json.dumps(snapshot(), ensure_ascii=False).encode()
                self._send(200, body)
            elif path == "/outfits":
                body = json.dumps({"outfits": outfits()},
                                  ensure_ascii=False).encode("utf-8")
                self._send(200, body)
            elif path == "/characters":
                body = json.dumps({"characters": characters()},
                                  ensure_ascii=False).encode()
                self._send(200, body)
            elif path == "/healthz":
                self._send(200, b'{"ok":true}')
            else:
                self._send(404, b'{"error":"not found"}')
        except Exception as exc:  # noqa: BLE001
            payload = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}).encode()
            self._send(500, payload)

    def log_message(self, fmt, *args):  # 静音：面板每 10 秒拉一次，别刷屏
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    snapshot(force=True)  # 启动即预热，首次请求不用等
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"capabilities server on http://{args.host}:{args.port}/inventory", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
