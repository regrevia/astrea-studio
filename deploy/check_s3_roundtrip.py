#!/usr/bin/env python3
"""S3 往返自检：上传一个探针对象 → 直连读回 → 走 nginx /oss/ 读回 → 删掉。

验的是应用层真正依赖的那条路：后端用 S3 客户端上传，前端拿 /oss/<key> 展示。
只用标准库 SigV4（复用 ensure_bucket.py 的签名实现），不额外装 SDK。

    python3 deploy/check_s3_roundtrip.py                       # 默认 127.0.0.1:9000
    python3 deploy/check_s3_roundtrip.py --endpoint http://127.0.0.1:9000
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ensure_bucket import BUCKET, load_env, request, sigv4_headers  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://127.0.0.1:9000")
    ap.add_argument("--public", default="http://127.0.0.1:8080", help="经 nginx 的对外地址")
    ap.add_argument(
        "--signed-base",
        default="http://localhost:9000",
        help="后端 publicEndpoint：presigned URL 用的那个地址",
    )
    args = ap.parse_args()

    home = Path("~/CreatorStudio").expanduser()
    env = load_env(home / "config" / "compose.env")
    access, secret = env["STUDIO_S3_ACCESS_KEY"], env["STUDIO_S3_SECRET_KEY"]
    ep = args.endpoint.rstrip("/")
    key = f"probe/s3-roundtrip-{int(time.time())}.txt"
    body = b"creator-studio s3 roundtrip probe\n"

    status, resp = request("PUT", f"{ep}/{BUCKET}/{key}", access, secret, body)
    print(f"PUT  {ep}/{BUCKET}/{key} -> HTTP {status}")
    if status not in (200, 204):
        print(f"上传失败: {resp[:200]!r}", file=sys.stderr)
        return 1

    status, resp = request("GET", f"{ep}/{BUCKET}/{key}", access, secret)
    direct = status == 200 and resp == body
    print(f"GET  direct          -> HTTP {status} bytes={len(resp)} match={direct}")

    # 1) 容器内/服务端读回：nginx 把 /oss/<key> 重写成 /aitoearn/<key>，
    #    Host 也换成了 upstream 名，所以这条链路靠桶策略的匿名 GET，不能带签名。
    proxy_status, proxy_body = _get(f"{args.public.rstrip('/')}/oss/{key}")
    proxy = proxy_status == 200 and proxy_body == body
    print(f"GET  via /oss/       -> HTTP {proxy_status} bytes={len(proxy_body)} match={proxy}")
    if not proxy:
        print(f"/oss/ 读回失败: {proxy_body[:200]!r}", file=sys.stderr)

    # 2) 客户端读回：后端签发的是 publicEndpoint（localhost:9000）上的 presigned
    #    URL，这里用同样的 Host + 路径算 SigV4，验「签名 URL 真能用」。
    signed_url = f"{args.signed_base.rstrip('/')}/{BUCKET}/{key}"
    signed_status, signed_body = _get(
        signed_url, sigv4_headers("GET", signed_url, access, secret, "UNSIGNED-PAYLOAD")
    )
    signed = signed_status == 200 and signed_body == body
    print(f"GET  signed {signed_url} -> HTTP {signed_status} match={signed}")
    if not signed:
        print(f"签名 URL 读回失败: {signed_body[:200]!r}", file=sys.stderr)

    request("DELETE", f"{ep}/{BUCKET}/{key}", access, secret)
    return 0 if direct and proxy and signed else 1


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers or {}), timeout=20
        ) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


if __name__ == "__main__":
    raise SystemExit(main())
