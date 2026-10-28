"""能力快照 HTTP 服务（只读，loopback）。

为什么单独起一个进程而不是塞进后端容器：
后端 aitoearn-server 跑在容器里、只挂了 config.yaml，读不到宿主上的
cookies 目录和 ffmpeg/vhs 二进制。而这份「哪些平台能发、有哪些音色」
本来就是宿主事实，在容器里看反而失真。

所以：宿主起一个零依赖的 stdlib HTTP 服务，只做 GET /inventory 和
GET /characters，面板跨域直读。只读、不联网、不启动浏览器、不碰发布；
另外挂上 /jobs 这组写接口，让平台自己接单出片（见 jobs.py）。

用法:
    python3 capabilities_server.py --port 8899
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import capabilities
import jobs

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


def _assets_mod():
    import character_assets as CA
    return CA


def character_model(cid: str = "whale") -> dict:
    """形象的完整素材模型：动作全集、表情全集、每套服装能不能挂表情层。"""
    CA = _assets_mod()
    ch = CA.load_character(cid)
    data = ch.to_public()
    data["root"] = str(ch.root)
    return data


def character_check(cid: str = "whale") -> dict:
    return _assets_mod().check(cid=cid)


def import_outfit(cid: str, payload: dict) -> dict:
    """装一套衣服：只要身体动作图，表情沿用共用那一套。"""
    CA = _assets_mod()
    src = str(payload.get("src") or "").strip()
    oid = str(payload.get("id") or "").strip()
    if not src or not oid:
        raise ValueError("src 和 id 都要给")
    src_path = Path(src).expanduser().resolve()
    home = Path.home().resolve()
    if not (src_path.is_dir() or src_path.suffix.lower() == ".zip"):
        raise ValueError(f"只接受目录或 zip：{src_path}")
    if home not in src_path.parents and not str(src_path).startswith("/tmp/"):
        raise ValueError("素材要放在本机用户目录或 /tmp 下")
    return CA.import_outfit(src_path, oid, str(payload.get("label") or ""), cid=cid,
                            force=bool(payload.get("force")))


def set_default_outfit(cid: str, outfit: str) -> dict:
    """换默认服装只改一个字段，不动任何一张图。"""
    CA = _assets_mod()
    ch = CA.load_character(cid)
    oid = str(outfit or "").strip()
    if oid not in {o.id for o in ch.outfits}:
        raise ValueError(f"没有这套服装：{oid}")
    cfg_path = ch.root / "character.json"
    cfg = {}
    if cfg_path.is_file():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
    cfg.setdefault("id", ch.id)
    cfg.setdefault("name", ch.name)
    cfg["default_outfit"] = oid
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return {"default_outfit": oid}


def material_file(rel: str) -> tuple[bytes, str]:
    """只读地吐一张素材原图，路径必须落在 materials 根目录里。

    面板要缩略图，但概念上素材不是成片，所以只按「根目录内相对路径」取，
    任何越界、非图片、不存在都当成 404，不暴露磁盘上的其它东西。
    """
    import mimetypes

    root = jobs.MATERIALS_DIR.resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise KeyError(rel)
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    if not ctype.startswith("image/"):
        raise KeyError(rel)
    return target.read_bytes(), ctype


def _utf8_query_value(value: str) -> str:
    """HTTP 头部按 latin-1 解，中文路径（成片文件名带中文）要按 utf-8 还原。"""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def media_file(abs_path: str) -> tuple[bytes, str]:
    """只读地吐一份成片文件（素材库 / 成片目录 / 交付目录）。

    详情面板要播放已入库的成片，但登录头带不进 `<video>`，所以由后端代理取回。
    允许的是本机成片根目录，越界、非视频、不存在一律 404，不暴露其它磁盘文件。
    """
    import mimetypes

    from library import DEFAULT_ROOT as LIBRARY_ROOT

    roots = [LIBRARY_ROOT.resolve(), (jobs.STUDIO_HOME / "out").resolve(),
             jobs.DELIVER_DIR.resolve()]
    raw = (abs_path or "").strip()
    if not raw:
        raise KeyError(abs_path)
    target = Path(raw).expanduser()
    if not target.is_absolute():
        raise KeyError(abs_path)
    target = target.resolve()
    inside = any(str(target).startswith(str(root) + os.sep) for root in roots)
    if not inside or not target.is_file():
        raise KeyError(abs_path)
    ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    if not ctype.startswith("video/"):
        raise KeyError(abs_path)
    return target.read_bytes(), ctype


_frame_cache: dict[tuple[str, float], bytes] = {}


def material_frame(rel: str, at: float = 0.0) -> tuple[bytes, str]:
    """视频素材的首帧/指定秒帧，给素材面板当缩略图。

    图片直接透传；视频按秒抽一帧、缩到 480 宽的 JPEG。路径同样只允许
    materials 根目录内的相对路径，越界一律 404。
    """
    import mimetypes

    root = jobs.MATERIALS_DIR.resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root) + os.sep) or not target.is_file():
        raise KeyError(rel)
    ctype = mimetypes.guess_type(str(target))[0] or ""
    if ctype.startswith("image/"):
        return target.read_bytes(), ctype
    if ctype not in ("video/mp4", "video/quicktime", "video/webm", "video/x-matroska"):
        raise KeyError(rel)
    key = (rel, round(at, 2))
    cached = _frame_cache.get(key)
    if cached is not None:
        return cached, "image/jpeg"
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{max(at, 0):.2f}", "-i", str(target),
            "-frames:v", "1", "-vf", "scale=480:-2", "-f", "mjpeg", "pipe:1",
        ],
        capture_output=True,
        timeout=20,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise KeyError(rel)
    if len(_frame_cache) > 256:
        _frame_cache.clear()
    _frame_cache[key] = proc.stdout
    return proc.stdout, "image/jpeg"


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
            elif path.startswith("/characters/"):
                parts = [x for x in path.split("/") if x]
                cid = parts[1] if len(parts) > 1 else "whale"
                tail = parts[2] if len(parts) > 2 else ""
                if tail == "check":
                    body = json.dumps(character_check(cid), ensure_ascii=False).encode()
                elif tail in ("", "assets"):
                    body = json.dumps(character_model(cid), ensure_ascii=False).encode()
                else:
                    body = b'{"error":"not found"}'
                    self._send(404, body)
                    return
                self._send(200, body)
            elif path == "/healthz":
                self._send(200, b'{"ok":true}')
            elif path == "/jobs":
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                limit = int((q.get("limit") or ["30"])[0])
                body = json.dumps({"jobs": jobs.list_jobs(limit)},
                                  ensure_ascii=False).encode()
                self._send(200, body)
            elif path.startswith("/jobs/"):
                parts = [x for x in path.split("/") if x]
                jid = parts[1] if len(parts) > 1 else ""
                tail = parts[2] if len(parts) > 2 else ""
                if tail == "log":
                    q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                    n = int((q.get("tail") or ["200"])[0])
                    self._send(200, json.dumps(
                        {"id": jid, "log": jobs.log_text(jid, n)},
                        ensure_ascii=False).encode())
                else:
                    self._send(200, json.dumps(jobs.get(jid), ensure_ascii=False).encode())
            elif path == "/media/file":
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                data, ctype = media_file(_utf8_query_value((q.get("path") or [""])[0]))
                self._send(200, data, ctype)
            elif path == "/materials/file":
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                data, ctype = material_file((q.get("path") or [""])[0])
                self._send(200, data, ctype)
            elif path == "/materials/frame":
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                try:
                    at = float((q.get("t") or ["0"])[0])
                except ValueError:
                    at = 0.0
                data, ctype = material_frame((q.get("path") or [""])[0], at)
                self._send(200, data, ctype)
            elif path == "/assets":
                self._send(200, json.dumps(jobs.assets(), ensure_ascii=False).encode())
            elif path == "/deliverables":
                q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
                limit = int((q.get("limit") or ["50"])[0])
                self._send(200, json.dumps({"files": jobs.deliverables(limit)},
                                           ensure_ascii=False).encode())
            else:
                self._send(404, b'{"error":"not found"}')
        except KeyError as exc:
            payload = json.dumps({"error": f"没有这个任务：{exc}"},
                                 ensure_ascii=False).encode()
            self._send(404, payload)
        except Exception as exc:  # noqa: BLE001
            payload = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}).encode()
            self._send(500, payload)

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        parts = [x for x in path.split("/") if x]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
            if len(parts) >= 3 and parts[0] == "characters" and parts[2] == "outfits":
                body = json.dumps(import_outfit(parts[1], payload),
                                  ensure_ascii=False).encode()
                self._send(200, body)
            elif len(parts) >= 1 and parts[0] == "jobs":
                if len(parts) >= 3 and parts[2] == "cancel":
                    body = json.dumps(jobs.cancel(parts[1]), ensure_ascii=False).encode()
                    self._send(200, body)
                elif len(parts) == 1:
                    job = jobs.submit(payload.get("kind"), payload.get("payload") or payload)
                    self._send(202, json.dumps(job, ensure_ascii=False).encode())
                else:
                    self._send(404, b'{"error":"not found"}')
            elif len(parts) >= 3 and parts[0] == "characters" and parts[2] == "default-outfit":
                body = json.dumps(set_default_outfit(parts[1], payload.get("outfit")),
                                  ensure_ascii=False).encode()
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)[:300]},
                                       ensure_ascii=False).encode())
        except KeyError as exc:
            payload = json.dumps({"error": f"没有这个任务：{exc}"},
                                 ensure_ascii=False).encode()
            self._send(404, payload)
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
