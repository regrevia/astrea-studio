#!/usr/bin/env python3
"""依次为多个平台生成登录二维码，全部落到 ~/CreatorStudio/deliver/qr/。

踩过的三个坑（都在这版修掉了）：
  1. 文件名不同平台前缀不同（`kuaishou_.._ks_login_qrcode_`），用
     `*login_qrcode*.png` 通配 + 平台名过滤，不能假设格式统一；
  2. 二维码 PNG 是先建文件后写入，早期快照只有几百字节，必须等体积稳定
     且 > 1500B 才算画好；
  3. 有些平台不接受 `--headed`（bilibili 会 `unrecognized arguments`），
     所以按平台配置额外参数，不硬编码。

用法：
  python3 batch_qr.py douyin xiaohongshu bilibili kuaishou tencent weibo ...
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.path.expanduser(
    "~/Desktop/work/Studio/creator-studio/integrations/vendor/social-auto-upload"))
PY = BASE / ".venv/bin/python"
COOKIES = BASE / "cookies"
OUT = Path(os.path.expanduser("~/CreatorStudio/deliver/qr"))
SCRATCH = Path(os.path.expanduser("~/.hermes/cache/scratch"))

LABELS = {
    "douyin": "抖音", "xiaohongshu": "小红书", "bilibili": "B站",
    "kuaishou": "快手", "tencent": "视频号", "weibo": "微博",
    "baijiahao": "百家号", "hupu": "虎扑", "alipay": "支付宝",
}

# 不接受 --headed 的平台
NO_HEADED = {"bilibili"}

ACCOUNT = "yefen"


def snapshot(plat: str) -> set[Path]:
    # 平台名可能在文件名中间（kuaishou_.._ks_..），用包含匹配
    return {p for p in COOKIES.glob("*login_qrcode*.png") if plat.split("_")[0][:4] in p.name}


def wait_qr(plat: str, before: set[Path], proc: subprocess.Popen,
            timeout: int = 75, min_bytes: int = 1500,
            rescue_dir: Path | None = None) -> Path | None:
    """等待二维码出现并把它救出来。

    快手这类平台会在扫码成功或超时后删掉 PNG，所以不能「先等稳定再拷」——
    一旦发现文件出现，立刻复制一份到 rescue_dir，之后只用副本判断体积。
    """
    end = time.time() + timeout
    while time.time() < end:
        new = snapshot(plat) - before
        if new:
            src = sorted(new)[-1]
            if rescue_dir is None:
                rescue_dir = SCRATCH
            rescue_dir.mkdir(parents=True, exist_ok=True)
            rescued = rescue_dir / f"rescue_{plat}.png"
            try:
                shutil.copy2(src, rescued)
            except FileNotFoundError:
                # 已被删掉，下一轮再看
                pass
            else:
                for _ in range(12):
                    if not rescued.exists():
                        break
                    s1 = rescued.stat().st_size
                    time.sleep(0.7)
                    if not rescued.exists():
                        break
                    s2 = rescued.stat().st_size
                    if s2 == s1 and s2 >= min_bytes:
                        return rescued
                    # 还在写，重新拷一次拿最新内容
                    try:
                        shutil.copy2(src, rescued)
                    except FileNotFoundError:
                        break
        if proc.poll() is not None:
            return None
        time.sleep(0.8)
    return None


def main() -> int:
    plats = sys.argv[1:] or list(LABELS)
    OUT.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    ok, fail = [], []
    for i, plat in enumerate(plats, 1):
        label = LABELS.get(plat, plat)
        before = snapshot(plat)
        log = SCRATCH / f"batch_{plat}.log"
        fh = open(log, "w")
        argv = [str(PY), "sau_cli.py", plat, "login", "--account", ACCOUNT]
        if plat not in NO_HEADED:
            argv.append("--headed")

        proc = subprocess.Popen(argv, cwd=str(BASE), stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
        qr = wait_qr(plat, before, proc, rescue_dir=SCRATCH)
        if qr:
            dst = OUT / f"{i:02d}_{label}.png"
            shutil.copy2(qr, dst)
            ok.append((label, dst))
            print(f"[{i}/{len(plats)}] {label:6s} OK  {dst.name}  ({dst.stat().st_size}B)", flush=True)
        else:
            # 判断是不是 cookie 仍然有效
            tailtxt = ""
            try:
                tailtxt = log.read_text(encoding="utf-8", errors="replace")[-400:]
            except Exception:
                pass
            if "completed" in tailtxt or "cookie" in tailtxt and "有效" in tailtxt:
                fail.append(f"{label}(cookie仍有效)")
                print(f"[{i}/{len(plats)}] {label:6s} 跳过：cookie 仍然有效", flush=True)
            else:
                fail.append(label)
                print(f"[{i}/{len(plats)}] {label:6s} 失败（需可见浏览器手动登录）", flush=True)
        time.sleep(2)

    print("\n=== 二维码已生成 %d / %d ===" % (len(ok), len(plats)))
    for label, p in ok:
        print(f"  {label}: {p}")
    if fail:
        print("=== 其他 ===")
        print("  " + "、".join(fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
