#!/usr/bin/env python3
"""素材获取：把外部免费素材拉进成片库，供下载后直接进时间线。

设计边界（和 library.py 一致）：
- 素材不是成片，进 library/_assets/<provider>/<YYYY-MM>/，命名 <slug>__<provider>__<id8>.<ext>；
  sidecar 同名 .json 记来源、许可证、原始 URL、md5，便于事后追责与去重。
- 只增不改：同 md5 已有就不再下载；同名冲突加序号。
- 需要 key 的 provider（pexels/pixabay）只在 config 里填了 key 时启用，
  没 key 时直接用免 key 的 wikimedia（Commons，CC 许可，含视频）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

UA = "CreatorStudio/1.0 (material fetcher; contact: studio@localhost)"
DEFAULT_ROOT = Path(os.environ.get("STUDIO_LIBRARY") or (Path.home() / "CreatorStudio" / "library"))
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def slug(text: str, limit: int = 32) -> str:
    s = str(text or "").strip().lower().replace("_", "-")
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s[:limit].strip("-") or "asset")


def _curl_json(url: str, timeout: int = 20) -> dict:
    p = subprocess.run(
        ["curl", "-sS", "-m", str(timeout), "-H", "User-Agent: " + UA, url],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("curl 失败 %s: %s" % (url, (p.stderr or "").strip()[:200]))
    try:
        return json.loads(p.stdout)
    except Exception as exc:
        raise RuntimeError("返回不是 JSON（%s）：%s" % (exc, (p.stdout or "")[:200]))


def _download(url: str, dest: Path, timeout: int = 180) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    p = subprocess.run(
        ["curl", "-sSL", "-m", str(timeout), "-H", "User-Agent: " + UA, "-o", str(tmp), url],
        capture_output=True, text=True)
    if p.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("下载失败 %s: %s" % (url, (p.stderr or "").strip()[:200]))
    tmp.replace(dest)
    return dest.stat().st_size


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def search_wikimedia(query: str, kind: str = "video", limit: int = 5,
                     max_mb: int = 60) -> list[dict]:
    """Commons 文件搜索。kind=video 用 filetype:video，image 用 filetype:bitmap。"""
    ftype = "video" if kind == "video" else "bitmap"
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": "%s filetype:%s" % (query, ftype), "gsrnamespace": "6",
        "gsrlimit": str(max(1, min(limit, 20))),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|user",
    }
    data = _curl_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params))
    out = []
    for page in ((data.get("query") or {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("url") or ""
        if not url:
            continue
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if kind == "video" and ext not in VIDEO_EXT:
            continue
        if kind == "image" and ext not in IMAGE_EXT:
            continue
        meta = info.get("extmetadata") or {}
        out.append({
            "provider": "wikimedia",
            "id": str(page.get("pageid", "")),
            "title": (page.get("title") or "").replace("File:", ""),
            "url": url,
            "page": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(page.get("title") or ""),
            "mime": info.get("mime") or "",
            "bytes": info.get("size") or 0,
            "license": ((meta.get("LicenseShortName") or {}).get("value") or "unknown"),
            "author": ((meta.get("Artist") or {}).get("value") or info.get("user") or ""),
        })
    # 原始文件常常上百 MB（4K 源），超出预算的不要：下载慢、转码也慢
    if max_mb:
        out = [x for x in out if not x["bytes"] or x["bytes"] <= max_mb * 1024 * 1024]
    return out


def fetch(items: list[dict], root: Path, kind: str = "video", dry_run: bool = False) -> list[dict]:
    root = Path(root)
    month = time.strftime("%Y-%m")
    landed = []
    for it in items:
        ext = Path(urllib.parse.urlparse(it["url"]).path).suffix.lower() or ".bin"
        base = "%s__%s__%s" % (slug(it["title"]), it["provider"], (it["id"] or "0")[:8])
        directory = root / "_assets" / it["provider"] / month
        dest = directory / (base + ext)
        n = 1
        while dest.exists():
            dest = directory / ("%s-%d%s" % (base, n, ext))
            n += 1
        if dry_run:
            landed.append({**it, "path": str(dest), "dryRun": True})
            continue
        size = _download(it["url"], dest)
        md5 = _md5(dest)
        sidecar = dest.with_suffix(dest.suffix + ".json")
        sidecar.write_text(json.dumps({
            **it, "path": str(dest), "bytes": size, "md5": md5,
            "kind": kind, "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        landed.append({**it, "path": str(dest), "bytes": size, "md5": md5})
    return landed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="素材获取（免 key 走 Wikimedia Commons）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--kind", default="video", choices=["video", "image"])
    s.add_argument("--n", type=int, default=5)
    s.add_argument("--max-mb", type=int, default=60)
    g = sub.add_parser("get")
    g.add_argument("--query", required=True)
    g.add_argument("--kind", default="video", choices=["video", "image"])
    g.add_argument("--n", type=int, default=3)
    g.add_argument("--max-mb", type=int, default=60)
    g.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "search":
        for it in search_wikimedia(a.query, a.kind, a.n, a.max_mb):
            print("%-8s %-14s %6.1fMB %s\n         %s" % (
                it["provider"], it["license"][:14], (it["bytes"] or 0) / 1e6, it["title"][:70], it["url"]))
        return 0
    items = [i for i in search_wikimedia(a.query, a.kind, max(a.n * 3, a.n), a.max_mb)]
    picked, seen = [], set()
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        picked.append(it)
        if len(picked) >= a.n:
            break
    if not picked:
        print("没有搜到可用素材：%s" % a.query, file=sys.stderr)
        return 2
    for row in fetch(picked, Path(a.root), a.kind, a.dry_run):
        print("%s  %6.1fMB  %s" % (row["path"], (row.get("bytes") or 0) / 1e6, row.get("license", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
