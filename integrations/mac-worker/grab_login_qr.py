#!/usr/bin/env python3
"""抓取 SAU 各平台的登录二维码，转成可交付的图片。

原理：SAU 的 login 子命令会把二维码以 `data:image/...` 落盘成
`cookies/<platform>_<account>_login_qrcode_<时间戳>.png`（见
`utils/login_qrcode.py: build_login_qrcode_path`）。所以不用改 SAU 代码，
只要在后台起 login，然后盯住那个目录，把新出现的 PNG 拿出来即可。

用法：
  python3 grab_login_qr.py xiaohongshu myaccount --timeout 90

输出：找到的二维码 PNG 绝对路径（stdout 最后一行），供上层直接交付。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.path.expanduser(
    "~/Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"))
PY = BASE / ".venv/bin/python"
COOKIES = BASE / "cookies"


def _existing() -> set[Path]:
    return set(COOKIES.glob("*login_qrcode*.png"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("platform")
    ap.add_argument("account")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    before = _existing()
    log = Path(os.path.expanduser("~/.hermes/cache/scratch")) / f"login_{args.platform}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log, "w")

    proc = subprocess.Popen(
        [str(PY), "sau_cli.py", args.platform, "login",
         "--account", args.account, "--headed"],
        cwd=str(BASE), stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    qr: Path | None = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        new = _existing() - before
        if new:
            qr = sorted(new)[-1]
            # 等文件写完
            size1 = qr.stat().st_size
            time.sleep(1.0)
            if qr.stat().st_size == size1 and size1 > 1000:
                break
        if proc.poll() is not None:
            break
        time.sleep(1.5)

    if qr:
        print(f"QR_READY {qr}")
        # 不杀进程：用户扫码后进程自己会落 cookie 退出
        return 0

    print("QR_TIMEOUT", file=sys.stderr)
    try:
        print(log.read_text(encoding="utf-8", errors="replace")[-1500:], file=sys.stderr)
    except Exception:
        pass
    proc.terminate()
    return 1


if __name__ == "__main__":
    sys.exit(main())
