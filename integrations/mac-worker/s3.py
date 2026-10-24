"""SigV4 object upload for the local RustFS bucket (stdlib only).

The worker uploads the finished video itself: the backend then only records the
object key and reuses MaterialService, so no large file ever passes through the
API process.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REGION = "us-east-1"
SERVICE = "s3"
BUCKET = "aitoearn"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _put_headers(host: str, path: str, access: str, secret: str, payload_hash: str,
                 content_type: str, amz_date: str) -> dict[str, str]:
    date_stamp = amz_date[:8]
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["PUT", path, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    k = _sign(("AWS4" + secret).encode(), date_stamp)
    k = _sign(k, REGION)
    k = _sign(k, SERVICE)
    k = _sign(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Host": host,
        "Content-Type": content_type,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def upload_file(endpoint: str, access: str, secret: str, key: str, path: Path,
                bucket: str = BUCKET) -> str:
    """PUT one object and return its key (relative, no leading slash)."""
    body = path.read_bytes()
    payload_hash = hashlib.sha256(body).hexdigest()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    host = endpoint.split("://", 1)[1].rstrip("/")
    obj_key = key.lstrip("/")
    # Object keys are already UUID/hash shaped by the caller; encode defensively.
    safe_key = "/".join(urllib.parse.quote(seg, safe="") for seg in obj_key.split("/"))
    url_path = f"/{bucket}/{safe_key}"
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = _put_headers(host, url_path, access, secret, payload_hash, content_type, amz_date)
    req = urllib.request.Request(
        f"{endpoint.rstrip('/')}{url_path}", data=body, method="PUT", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"S3 PUT returned HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"S3 PUT failed: HTTP {exc.code} {exc.read()[:200]!r}") from None
    return safe_key
