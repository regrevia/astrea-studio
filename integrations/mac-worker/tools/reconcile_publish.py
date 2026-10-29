#!/usr/bin/env python3
"""把一条作品链接的回读结论写回台账，收尾「发没发出去不知道」。

用法：
  python tools/reconcile_publish.py --url https://www.douyin.com/video/123 --account yefen
  python tools/reconcile_publish.py --url … --attempt 6ae… --apply --token /tmp/auto_token.txt

默认只打结论不写库。apply 只在回读给出明确结论时才发请求；unknown 一律不动。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publish_sau as P  # noqa: E402

API = "http://127.0.0.1:8080/api"


def main() -> int:
    ap = argparse.ArgumentParser(description="回读作品链接并把结论写回台账")
    ap.add_argument("--url", required=True, help="作品链接")
    ap.add_argument("--account", default="", help="复用哪个 SAU 账号的 cookie")
    ap.add_argument("--attempt", help="要回写的 attempt id")
    ap.add_argument("--apply", action="store_true", help="真的写回（默认只打印）")
    ap.add_argument("--token", default="/tmp/auto_token.txt", help="后台 token 文件")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    result = P.readback(args.url, account=args.account, timeout=args.timeout)
    print("回读结论：%s  %s" % (result.state, result.detail))
    if result.title:
        print("标题：%s" % result.title)

    payload = P.reconcile_payload(result)
    if payload is None:
        print("没有明确结论，台账不动。")
        return 0
    print("台账回写：%s" % json.dumps(payload, ensure_ascii=False))
    if not args.apply:
        print("（未加 --apply，没有请求后台）")
        return 0
    if not args.attempt:
        print("缺 --attempt，不知道写给哪条记录。")
        return 2

    token = Path(args.token).read_text().strip()
    req = urllib.request.Request(
        "%s/production/publications/%s/reconcile" % (API, args.attempt),
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer %s" % token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 —— 本机后台
        body = json.loads(resp.read().decode())
    print("后台返回：code=%s status=%s" % (body.get("code"), (body.get("data") or {}).get("status")))
    return 0 if body.get("code") == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
