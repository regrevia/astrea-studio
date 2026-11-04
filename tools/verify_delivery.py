#!/usr/bin/env python3
"""按「小块读」校验交付文件，避开外置盘的大块读串数据问题。

用法：
    tools/verify_delivery.py <文件>                      # 打印 sha256 + 大小 + 解码检查
    tools/verify_delivery.py <文件> --expect <sha256>    # 比对预期值，不一致退出码 1
    tools/verify_delivery.py <文件> --copy <源文件>      # 跟源逐块比对（比哈希更早看出错在哪 MB）

为什么不用 shasum/cp：
  本机这块 USB 外置盘读「已经冷掉的大文件」时会随机返回别的数据，块越大越容易踩：
  实测同一个 23 MB 文件连续读 8 遍，16 KB 块 8/8 正确，64 KB 块只有 5/8 正确，
  128 KB 以上基本全错。所以 shasum 报的不一致、ffmpeg 报的 h264 解码错，
  都是读路径串味，文件本身没坏。校验和拷贝走 16 KB 块。
  判据：错的数据是随机的，能反复读出同一个正确 sha256 就说明盘上的字节是对的。
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import subprocess
import sys

F_NOCACHE = 48
CHUNK = 16 << 10


def sha(path: str, chunk: int = CHUNK) -> str:
    fd = os.open(path, os.O_RDONLY)
    fcntl.fcntl(fd, F_NOCACHE, 1)
    h = hashlib.sha256()
    try:
        while True:
            b = os.read(fd, chunk)
            if not b:
                break
            h.update(b)
    finally:
        os.close(fd)
    return h.hexdigest()


def copy_small(src: str, dst: str, chunk: int = CHUNK) -> str:
    """小块拷贝：从这种盘往外拷时用，别用 cp（它读 128 KB 以上）。"""
    tmp = dst + ".part"
    fd_in = os.open(src, os.O_RDONLY)
    fcntl.fcntl(fd_in, F_NOCACHE, 1)
    h = hashlib.sha256()
    try:
        with open(tmp, "wb") as out:
            while True:
                b = os.read(fd_in, chunk)
                if not b:
                    break
                h.update(b)
                out.write(b)
            out.flush()
            os.fsync(out.fileno())
    finally:
        os.close(fd_in)
    os.replace(tmp, dst)
    dfd = os.open(os.path.dirname(os.path.abspath(dst)), os.O_RDONLY)
    os.fsync(dfd)
    os.close(dfd)
    return h.hexdigest()


def first_diffs(a: str, b: str, chunk: int = CHUNK, limit: int = 5) -> list[int]:
    """返回前几个不一致的 MB 编号，好判断是「整盘坏」还是「个别区段」。"""
    bad: list[int] = []
    fd_a = os.open(a, os.O_RDONLY)
    fd_b = os.open(b, os.O_RDONLY)
    fcntl.fcntl(fd_a, F_NOCACHE, 1)
    fcntl.fcntl(fd_b, F_NOCACHE, 1)
    off = 0
    try:
        while len(bad) < limit:
            ba = os.read(fd_a, chunk)
            bb = os.read(fd_b, chunk)
            if ba != bb:
                bad.append(off >> 20)
            if not ba:
                break
            off += len(ba)
    finally:
        os.close(fd_a)
        os.close(fd_b)
    return bad


def decode_ok(path: str) -> tuple[bool, str]:
    """真解码一遍：文件能读对并不等于码流没问题。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
            capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    err = proc.stderr.strip()
    return (proc.returncode == 0 and not err), err.splitlines()[-1][:200] if err else ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="小块读校验交付文件")
    ap.add_argument("path")
    ap.add_argument("--expect", default="", help="预期 sha256")
    ap.add_argument("--copy", default="", help="写一份小块拷贝到这个路径")
    ap.add_argument("--no-decode", action="store_true", help="跳过 ffmpeg 解码检查")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.path):
        print(f"没有这个文件：{args.path}", file=sys.stderr)
        return 2

    digest = sha(args.path)
    size = os.path.getsize(args.path)
    print(f"文件  {args.path}")
    print(f"大小  {size} B")
    print(f"sha256  {digest}  （16 KB 块 + F_NOCACHE）")

    code = 0
    if args.expect and digest != args.expect.strip().lower():
        print(f"不一致  预期 {args.expect.strip().lower()}", file=sys.stderr)
        code = 1

    if args.copy:
        got = copy_small(args.path, args.copy)
        print(f"拷贝  {args.copy}  sha256 {got}  {'一致' if got == digest else '不一致'}")
        if got != digest:
            code = 1
        else:
            bad = first_diffs(args.copy, args.path)
            print(f"逐块比对  前 {len(bad)} 处差异 {bad or '无'}")

    if not args.no_decode:
        ok, detail = decode_ok(args.copy or args.path)
        print(f"解码  {'通过' if ok else '有问题：' + detail}")
        if not ok:
            print("  注意：从外置盘直接解码可能读到串味数据，先小块拷到内置盘再解码",
                  file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
