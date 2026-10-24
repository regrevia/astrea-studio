#!/usr/bin/env python3
"""Ensure the local RustFS bucket exists and allows public read (upstream does
`mc anonymous set download`). Pure stdlib AWS SigV4 so no `mc` image is needed.

Usage: python3 deploy/ensure_bucket.py [--endpoint http://127.0.0.1:9001]
Reads credentials from $STUDIO_HOME/config/compose.env.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

BUCKET = "aitoearn"
REGION = "us-east-1"
SERVICE = "s3"


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_headers(method: str, url: str, access: str, secret: str, payload_hash: str) -> dict[str, str]:
    assert url.startswith("http://")
    host_port, _, path_q = url[len("http://"):].partition("/")
    path = "/" + path_q
    # canonical query string
    qs = ""
    if "?" in path:
        path, qs = path.split("?", 1)
    body_hash = payload_hash
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    canonical_headers = f"host:{host_port}\nx-amz-content-sha256:{body_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [method, path, qs, canonical_headers, signed_headers, body_hash]
    )
    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    k = sign(("AWS4" + secret).encode(), date_stamp)
    k = sign(k, REGION)
    k = sign(k, SERVICE)
    k = sign(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Host": host_port,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": body_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def request(method: str, url: str, access: str, secret: str, body: bytes = b"") -> tuple[int, bytes]:
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = sigv4_headers(method, url, access, secret, payload_hash)
    req = urllib.request.Request(url, data=body if body else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://127.0.0.1:9001")
    args = ap.parse_args()

    home = Path(os.environ.get("STUDIO_HOME", Path.home() / "CreatorStudio")).expanduser()
    env = load_env(home / "config/compose.env")
    access = env["STUDIO_S3_ACCESS_KEY"]
    secret = env["STUDIO_S3_SECRET_KEY"]
    ep = args.endpoint.rstrip("/")

    status, body = request("PUT", f"{ep}/{BUCKET}", access, secret)
    if status in (200, 409):
        print(f"bucket {BUCKET}: {'created' if status == 200 else 'already exists'}")
    else:
        print(f"bucket create failed: HTTP {status} {body[:200]!r}", file=sys.stderr)
        return 1

    policy = (
        '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":["*"]},'
        '"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::%s/*"]}]}' % BUCKET
    )
    # RustFS canonicalises an empty query value as `policy=`; `?policy` alone is
    # signed differently and returns SignatureDoesNotMatch.
    status, body = request("PUT", f"{ep}/{BUCKET}?policy=", access, secret, policy.encode())
    if status in (200, 204):
        print("bucket policy: anonymous read enabled")
    else:
        print(f"bucket policy failed: HTTP {status} {body[:200]!r}", file=sys.stderr)
        return 1

    status, body = request("GET", f"{ep}/{BUCKET}", access, secret)
    print(f"bucket reachable: HTTP {status}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
