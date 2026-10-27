#!/usr/bin/env python3
"""一条命令抓一个素材包：URL 列表 / 主题 → materials/packs/<slug>/{index.json,shots/*.png,meta.md}

为什么存在
    竖屏短片以前靠人工跑 Envoy 截图，再在 spec 里手写绝对路径
    （``~/CreatorStudio/materials/shots/envoy-head.png#838#contain#396``）。
    换主题就得从头攒图，路径还会散落各处。这个脚本把「抓一批图 + 记清楚每张图的出处」
    收成一条命令，产物是有稳定命名和索引的「素材包」，spec 里只写 ``pack:<slug>/<name>``。

分工
    Python 这层只管：参数、主题→URL 搜索、素材包目录结构、index.json/meta.md、失败原因翻译。
    真正驱动 Chrome 的是同目录的 ``cdp_grab.mjs``（Node 内建 WebSocket 走 CDP，无第三方依赖），
    和仓库里 paper/cdp_shots.mjs 是同一套做法，但独立演进，不影响出片链路。

失败要说清楚
    cdp_grab.mjs 会把每张图判成 login_required / timeout / empty / http_error / nav_error /
    exception 之一；这里把它们翻成人话写进 index.json 的 failures 和屏幕输出，
    不用「截图失败了」这种没信息量的说法。

用法
    # 一串 URL，竖屏 1080x1920，每个页面 2 张
    python3 integrations/mac-worker/tools/grab_material.py \
        --pack envoy-demo \
        --urls https://github.com/regrevia/envoy https://example.com \
        --viewport 1080x1920 --n 2

    # 一个主题（走 DuckDuckGo/Bing 取前几条结果）
    python3 integrations/mac-worker/tools/grab_material.py \
        --pack hand-drawn --topic "手绘动画 分镜" --n 1 --wait-for "main"

    # 往下滚一屏再拍（长页面取下半部分）
    ... --urls https://example.com --scroll 900

产物
    <MATERIALS_ROOT>/packs/<slug>/index.json     机器读：每张图的 url/尺寸/字节/是否可用/失败原因
    <MATERIALS_ROOT>/packs/<slug>/shots/*.png    图，文件名 = 页面 slug + 序号
    <MATERIALS_ROOT>/packs/<slug>/meta.md        人读：表格 + 直接能粘进 spec 的 --shots 行
    MATERIALS_ROOT 默认 ~/CreatorStudio/materials（可用环境变量或 --root 覆盖）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GRABBER = HERE / "cdp_grab.mjs"
NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"

# 失败原因 → 人话。给运维看的是「下一步做什么」，不是内部枚举名。
REASON_ZH = {
    "login_required": "要登录：页面是登录/密码框，抓到的是登录页",
    "timeout": "超时：等不到指定选择器，或页面一直没渲染完",
    "empty": "空页：正文几乎没有文字，或截图接近纯色",
    "http_error": "HTTP 错误：状态码 4xx/5xx",
    "nav_error": "打不开：DNS/证书/断网/URL 不合法",
    "exception": "抓取异常：Chrome 侧报错，详见 detail",
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


@dataclass
class Shot:
    file: str
    url: str
    final_url: str
    title: str
    bytes: int
    scroll_y: int
    ok: bool
    page_height: int = 0
    reason: str = ""
    detail: str = ""

    @property
    def name(self) -> str:
        return Path(self.file).stem


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def materials_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("MATERIALS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / "CreatorStudio" / "materials").resolve()


def slugify(text: str, fallback: str = "") -> str:
    """ASCII slug；中文主题没拼音可用，退化成日期戳，别硬拼一串看不懂的码。"""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    if s:
        return s
    return fallback or "pack-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def parse_viewport(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX×]\s*(\d{2,5})\s*", text)
    if not m:
        raise argparse.ArgumentTypeError(f"viewport 要写成 1080x1920，收到 {text!r}")
    w, h = int(m.group(1)), int(m.group(2))
    if not (200 <= w <= 7680 and 200 <= h <= 7680):
        raise argparse.ArgumentTypeError(f"viewport 超出合理范围：{w}x{h}")
    return w, h


def http_get(url: str, timeout: float = 12.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def search_urls(topic: str, limit: int) -> list[str]:
    """主题 → URL。两个后端依次试，都失败就明确报「搜索被挡」，让人直接给 --urls。"""
    q = urllib.parse.quote(topic)
    found: list[str] = []

    # 1) Bing：结果包在 <li class="b_algo"> 里，链接在其中的 <h2><a href="...">。
    #    本机 DuckDuckGo（html/lite 都是）被 TLS 层掐断，所以不再把它放第一位。
    if len(found) < limit:
        try:
            html = http_get(f"https://www.bing.com/search?q={q}&setlang=zh-Hans")
            for m in re.finditer(r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]+href="(https?://[^"]+)"', html, re.S):
                u = _bing_target(m.group(1))
                if u and not _is_search_host(u, "bing") and not _is_search_host(u, "microsoft"):
                    found.append(u)
        except Exception as e:  # noqa: BLE001 - 搜索失败不是致命错误，换下一个后端
            log(f"[主题搜索] Bing 不可用：{e}")

    # 2) DuckDuckGo html 版（部分地区可用）：结果链接是 /l/?uddg=<encoded>
    if len(found) < limit:
        try:
            html = http_get(f"https://html.duckduckgo.com/html/?q={q}")
            for m in re.finditer(r'href="[^"]*?uddg=([^"&]+)', html):
                u = urllib.parse.unquote(m.group(1))
                if u.startswith("http") and not _is_search_host(u, "duckduckgo"):
                    found.append(u)
        except Exception as e:  # noqa: BLE001
            log(f"[主题搜索] DuckDuckGo 不可用：{e}")

    # 3) 维基百科搜索 API：搜索引擎都被挡时的兜底，条目页当素材够用（有图有正文）
    if len(found) < limit:
        try:
            for lang in ("zh", "en"):
                api = (f"https://{lang}.wikipedia.org/w/api.php?action=query&list=search"
                       f"&srsearch={q}&format=json&srlimit={limit}")
                data = json.loads(http_get(api))
                for item in (data.get("query") or {}).get("search") or []:
                    title = urllib.parse.quote(str(item.get("title") or "").replace(" ", "_"))
                    if title:
                        found.append(f"https://{lang}.wikipedia.org/wiki/{title}")
                if len(found) >= limit:
                    break
        except Exception as e:  # noqa: BLE001
            log(f"[主题搜索] 维基百科不可用：{e}")

    seen: set[str] = set()
    uniq = []
    for u in found:
        key = u.split("#")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            uniq.append(u)
    return uniq[:limit]


def _bing_target(url: str) -> str:
    """Bing 的结果链接是站内跳转 /ck/a?...&u=a1<base64url>，要把真实地址解出来。"""
    url = url.replace("&amp;", "&")
    if "/ck/a" not in url:
        return url
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", url)
    if not m:
        return ""
    raw = m.group(1)
    raw += "=" * (-len(raw) % 4)
    try:
        out = base64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - 解不出来就当这条没有
        return ""
    return out if out.startswith("http") else ""


def _is_search_host(url: str, host_part: str) -> bool:
    try:
        return host_part in urllib.parse.urlsplit(url).netloc
    except ValueError:
        return True


def run_grabber(plan: dict[str, Any], outdir: Path) -> list[dict[str, Any]]:
    """调 Node 抓图；node 有失败时 exit 1，但结果行仍然完整，所以不看 returncode 判成败。"""
    plan = dict(plan)
    plan["outdir"] = str(outdir)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False)
        plan_path = fh.name
    try:
        proc = subprocess.run(
            [NODE, str(GRABBER), plan_path],
            capture_output=True, text=True, timeout=plan.get("total_timeout_s", 300),
        )
    except FileNotFoundError:
        raise SystemExit(f"找不到 node（{NODE}）。素材抓取要 Node 内建 WebSocket，先装 node 或设 NODE_BIN。")
    except subprocess.TimeoutExpired:
        raise SystemExit("抓取整体超时（300s）。减少 URL 数或调低 --n 后重试。")
    finally:
        Path(plan_path).unlink(missing_ok=True)

    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        raise SystemExit("抓取脚本没有返回任何结果：" + (" | ".join(tail) or "无输出"))
    return rows


def to_shots(rows: list[dict[str, Any]]) -> tuple[list[Shot], list[dict[str, Any]]]:
    shots: list[Shot] = []
    failures: list[dict[str, Any]] = []
    for r in rows:
        ok = bool(r.get("ok"))
        if ok and r.get("file"):
            shots.append(Shot(
                file=r["file"], url=r.get("url", ""), final_url=r.get("final_url", ""),
                title=r.get("title", ""), bytes=int(r.get("bytes") or 0),
                scroll_y=int(r.get("scroll_y") or 0), ok=True,
                page_height=int(r.get("page_height") or 0),
            ))
        else:
            reason = r.get("reason") or "exception"
            failures.append({
                "url": r.get("url", ""),
                "reason": reason,
                "reason_zh": REASON_ZH.get(reason, reason),
                "detail": r.get("detail", ""),
                "final_url": r.get("final_url", ""),
            })
    return shots, failures


def write_index(pack: Path, slug: str, args: argparse.Namespace,
                viewport: tuple[int, int], shots: list[Shot],
                failures: list[dict[str, Any]], urls: list[str]) -> dict[str, Any]:
    payload = {
        "schema": "creator-studio/materials-pack/1",
        "slug": slug,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "topic" if args.topic else "urls",
        "topic": args.topic or "",
        "request": {
            "urls": urls,
            "viewport": list(viewport),
            "scale": args.scale,
            "n": args.n,
            "scroll": args.scroll,
            "wait_for": args.wait_for or "",
        },
        "count": len(shots),
        "failed": len(failures),
        "shots": [
            {
                "name": s.name,
                "file": f"shots/{s.file}",
                "url": s.url,
                "final_url": s.final_url,
                "title": s.title,
                "bytes": s.bytes,
                "scroll_y": s.scroll_y,
                "page_height": s.page_height,
                "ref": f"pack:{slug}/{s.name}",
            }
            for s in shots
        ],
        "failures": failures,
    }
    (pack / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_meta(pack: Path, payload: dict[str, Any], viewport: tuple[int, int]) -> None:
    w, h = viewport
    lines = [
        f"# 素材包 {payload['slug']}",
        "",
        f"- 生成：`{payload['created_at']}`",
        f"- viewport：`{w}x{h}`（scale {payload['request']['scale']}，每页 {payload['request']['n']} 张）",
        f"- 来源：{'主题搜索 `' + payload['topic'] + '`' if payload['topic'] else 'URL 列表'}",
        f"- 可用 {payload['count']} 张，失败 {payload['failed']} 条",
        "",
        "## 怎么用",
        "",
        "spec 里直接写包内引用，不用绝对路径：",
        "",
        "```",
    ]
    for s in payload["shots"]:
        lines.append(f"{s['ref']}#396#contain#396")
    lines += [
        "```",
        "",
        "`#396#contain#396` 是版位高/填充方式/最大高，按你的版式改；渲染层只认 `pack:` 前缀，",
        "其余后缀语法不变（见 docs/shorts/material-sources.md）。",
        "",
        "## 素材清单",
        "",
        "| name | 页面 | 尺寸 | 字节 | 滚动 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for s in payload["shots"]:
        title = (s["title"] or s["url"]).replace("|", "\\|")[:60]
        lines.append(f"| `{s['name']}` | {title} | {w}x{h} | {s['bytes']} | {s['scroll_y']} |")
    if payload["failures"]:
        lines += ["", "## 失败（这些页面没进包）", ""]
        for f in payload["failures"]:
            lines.append(f"- `{f['url']}` → **{f['reason']}**：{f['reason_zh']}"
                         + (f"（{f['detail']}）" if f["detail"] else ""))
    lines += ["", "重抓同一条命令即可覆盖本包（先备份就把 `--pack` 换个名字）。", ""]
    (pack / "meta.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="抓一个素材包（URL 列表或主题）→ materials/packs/<slug>/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法")[1] if "用法" in __doc__ else "",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--urls", nargs="+", metavar="URL", help="要抓的页面，空格分隔")
    src.add_argument("--urls-file", metavar="FILE", help="每行一个 URL 的文件（# 开头跳过）")
    src.add_argument("--topic", metavar="TEXT", help="主题关键词，自动搜前 N 条结果")
    ap.add_argument("--pack", metavar="SLUG", help="包名（默认由主题/域名生成）")
    ap.add_argument("--root", metavar="DIR", help="materials 根目录（默认 $MATERIALS_ROOT 或 ~/CreatorStudio/materials）")
    ap.add_argument("--viewport", default="1080x1920", type=parse_viewport, metavar="WxH")
    ap.add_argument("--n", type=int, default=1, help="每个页面抓几张（>1 时按视口高度分段下滚）")
    ap.add_argument("--scroll", type=int, default=0, metavar="PX", help="拍之前先向下滚多少像素")
    ap.add_argument("--wait-for", default="", metavar="SEL", help="等这个 CSS 选择器出现再拍")
    ap.add_argument("--scale", type=float, default=1.0, help="deviceScaleFactor，2 出 2x 图")
    ap.add_argument("--wait-ms", type=int, default=2500, help="导航后固定等多久")
    ap.add_argument("--timeout-ms", type=int, default=20000, help="--wait-for 的最长等待")
    ap.add_argument("--max-urls", type=int, default=8, help="主题搜索最多取几条")
    ap.add_argument("--json", action="store_true", help="只输出 index.json（给别的脚本用）")
    args = ap.parse_args(argv)

    if args.n < 1 or args.n > 20:
        ap.error("--n 范围 1..20")
    if not GRABBER.exists():
        ap.error(f"缺少抓取引擎 {GRABBER}")
    if not shutil.which(NODE) and not Path(NODE).exists():
        ap.error(f"找不到 node（{NODE}）")

    urls: list[str] = []
    if args.urls:
        urls = list(args.urls)
    elif args.urls_file:
        p = Path(args.urls_file).expanduser()
        if not p.exists():
            ap.error(f"URL 文件不存在：{p}")
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    else:
        urls = search_urls(args.topic, args.max_urls)
        if not urls:
            print("搜索没返回结果（可能被反爬或关键词太窄）。直接给页面：--urls <URL> ...", file=sys.stderr)
            return 2
        log(f"[主题] {args.topic} → {len(urls)} 条：")
        for u in urls:
            log(f"  - {u}")

    bad = [u for u in urls if not re.match(r"^https?://", u)]
    if bad:
        ap.error("只支持 http(s) URL，这几种不行：" + ", ".join(bad[:3]))

    slug = slugify(args.pack or args.topic or _host_of(urls[0]))
    pack = materials_root(args.root) / "packs" / slug
    shots_dir = pack / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    # 重抓时先清掉旧图，避免上一轮的残图混进新 index（同目录里 index.json 也会重写）
    for old in shots_dir.glob("*.png"):
        old.unlink()

    plan = {
        "viewport": {"width": args.viewport[0], "height": args.viewport[1]},
        "scale": args.scale,
        "wait_ms": args.wait_ms,
        "timeout_ms": args.timeout_ms,
        "scroll": args.scroll,
        "wait_for": args.wait_for or None,
        "jobs": [{"url": u, "n": args.n} for u in urls],
    }
    log(f"[抓取] {len(urls)} 个页面 × {args.n} 张 → {pack}")
    rows = run_grabber(plan, shots_dir)
    shots, failures = to_shots(rows)
    payload = write_index(pack, slug, args, args.viewport, shots, failures, urls)
    write_meta(pack, payload, args.viewport)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"包：{pack}")
        print(f"  index.json  {len(shots)} 张可用 / {len(failures)} 条失败")
        print(f"  shots/      {shots_dir}")
        if shots:
            print("spec 里这样写：")
            for s in shots[:3]:
                print(f'  "pack:{slug}/{s.name}"')
            if len(shots) > 3:
                print(f"  …（共 {len(shots)} 张，见 meta.md）")
        for f in failures:
            print(f"  失败 {f['url']} → {f['reason']}：{f['reason_zh']}", file=sys.stderr)

    if not shots:
        return 1
    return 0 if not failures else 3


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc
    except ValueError:
        return "pack"


if __name__ == "__main__":
    sys.exit(main())
