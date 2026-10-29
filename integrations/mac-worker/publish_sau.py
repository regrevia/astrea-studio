"""Douyin publication through the pinned social-auto-upload CLI.

Publication is the only step that can produce irreversible side effects, so this
module never decides "published". It returns whatever evidence the platform page
gave back, and the backend records it as `submitted_unverified` unless a real
work ID/URL was read back. Any ambiguity becomes `submission_unknown`, which the
ledger refuses to resubmit automatically.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import SAU_CHECKOUT

SAU_PYTHON = SAU_CHECKOUT / ".venv/bin/python"
WORK_URL_RE = re.compile(r"https://(?:www\.)?douyin\.com/(?:video|note)/(\d{6,})")
AWEME_RE = re.compile(r'"aweme_id"\s*:\s*"(\d{6,})"')
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
# 抖音对「作品没了」和「要登录」给的页面完全不一样，靠这几个特征区分，
# 分不出来的情况一律 unknown —— 猜错会把没发出去的记成已发布。
GONE_MARKERS = ("作品不存在", "页面不存在", "内容不存在", "该作品已被删除", "你访问的页面不见了")
LOGIN_MARKERS = ("登录后查看", "请先登录", "扫码登录", "captcha-verify", "middle_page")
GENERIC_TITLES = ("抖音", "抖音-记录美好生活", "在抖音记录美好生活", "Douyin")


def _has_real_work(title: str, post_id: str, visible: str) -> bool:
    """作品页的标题是作品文案；只有通用站名/带验证字样的标题不算数。"""
    if not title or any(title.startswith(g) for g in GENERIC_TITLES):
        return False
    if "验证" in title or post_id in visible and len(visible) > 40:
        return True
    return len(title) > 8 and "记录美好生活" not in title


class PublishError(RuntimeError):
    pass


@dataclass
class PublishSpec:
    """What to publish. Either a video (one file) or a note (1..N images).

    Douyin has two separate entry points — upload-video and upload-note — and
    they take different argv, so the kind has to be carried explicitly instead
    of inferred at call time.
    """

    kind: str = "video"          # "video" | "note"
    video: Path | None = None
    images: list[Path] = field(default_factory=list)
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    account: str = ""
    schedule: str | None = None
    thumbnail: Path | None = None
    bgm: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PublishSpec:
        content = payload.get("content") or {}
        account = str(payload.get("accountRef") or "").strip()
        if not account:
            raise PublishError("publish payload has no accountRef (SAU cookie account name)")

        title = str(content.get("title") or payload.get("title") or "").strip()
        if not title:
            raise PublishError("publish payload has no title")

        # content.type is the explicit switch; fall back to sniffing the payload
        # so existing video callers keep working untouched.
        raw_kind = str(content.get("type") or payload.get("type") or "").strip().lower()
        image_raw = content.get("imagePaths") or content.get("images")

        if raw_kind in ("note", "image", "images", "photo") or (not raw_kind and image_raw):
            kind = "note"
        elif raw_kind in ("video", "reel", ""):
            kind = "video"
        else:
            raise PublishError(f"unsupported publication type: {raw_kind!r} (use 'video' or 'note')")

        tags = [str(t).lstrip("#") for t in (content.get("tags") or []) if str(t).strip()]
        schedule = payload.get("schedule") or content.get("schedule")
        desc = str(content.get("desc") or content.get("note") or "")

        if kind == "note":
            images = [Path(str(p)).expanduser() for p in (image_raw or [])]
            if not images:
                raise PublishError(
                    "note publication needs content.imagePaths (list of local image files)"
                )
            missing = [str(p) for p in images if not p.is_file()]
            if missing:
                raise PublishError(f"note image(s) do not exist: {', '.join(missing)}")
            return cls(
                kind="note", images=images, title=title, description=desc,
                tags=tags, account=account, schedule=schedule,
                bgm=str(content.get("bgm") or ""),
            )

        video_raw = payload.get("videoPath") or content.get("videoPath")
        if not video_raw:
            raise PublishError("publish payload has no videoPath")
        video = Path(str(video_raw)).expanduser()
        if not video.is_file():
            raise PublishError(f"video for publication does not exist: {video}")

        thumb = content.get("thumbnailPath")
        return cls(
            kind="video",
            video=video,
            title=title,
            description=desc,
            tags=tags,
            account=account,
            schedule=schedule,
            thumbnail=Path(thumb).expanduser() if thumb else None,
        )


def check_cookie(account: str, cfg_tz: str) -> bool:
    """`sau douyin check` exits 0 only for a live cookie file."""
    proc = subprocess.run(
        [str(SAU_PYTHON), "sau_cli.py", "douyin", "check", "--account", account],
        cwd=str(SAU_CHECKOUT),
        capture_output=True, text=True, timeout=180, check=False,
        env={**os.environ, "TZ": cfg_tz, "PYTHONUNBUFFERED": "1"},
    )
    return proc.returncode == 0 and "invalid" not in (proc.stdout or "")


def build_argv(spec: PublishSpec) -> list[str]:
    if spec.kind == "note":
        # upload-note takes --images (nargs +), --note / --notef, --bgm instead
        # of --file / --desc / --thumbnail. Passing video flags here would make
        # argparse reject the whole call.
        argv = [
            str(SAU_PYTHON), "sau_cli.py", "douyin", "upload-note",
            "--account", spec.account,
            "--images", *[str(p) for p in spec.images],
            "--title", spec.title,
        ]
        if spec.description:
            argv += ["--note", spec.description]
        if spec.tags:
            argv += ["--tags", ",".join(spec.tags)]
        if spec.bgm:
            argv += ["--bgm", spec.bgm]
        if spec.schedule:
            argv += ["--schedule", spec.schedule]
        return argv

    if spec.video is None:
        raise PublishError("video publication has no file")
    argv = [
        str(SAU_PYTHON), "sau_cli.py", "douyin", "upload-video",
        "--account", spec.account,
        "--file", str(spec.video),
        "--title", spec.title,
        "--desc", spec.description,
        "--tags", ",".join(spec.tags),
    ]
    if spec.schedule:
        argv += ["--schedule", spec.schedule]
    if spec.thumbnail and spec.thumbnail.is_file():
        argv += ["--thumbnail", str(spec.thumbnail)]
    return argv


def submit(job_id: str, payload: dict[str, Any], timezone: str,
           on_progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
    if not SAU_PYTHON.exists():
        raise PublishError(f"SAU venv missing at {SAU_PYTHON}; run deploy/bootstrap_sau.sh")

    spec = PublishSpec.from_payload(payload)

    if on_progress:
        on_progress(10, "publish:checking-cookie")
    if not check_cookie(spec.account, timezone):
        raise PublishError(
            f"Douyin cookie for account '{spec.account}' is missing or expired; "
            f"run: sau douyin login --account {spec.account}"
        )

    argv = build_argv(spec)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "TZ": timezone,
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8",
    }
    if on_progress:
        on_progress(20, "publish:submitting")

    proc = subprocess.run(
        argv, cwd=str(SAU_CHECKOUT), env=env,
        capture_output=True, text=True, timeout=1800, check=False,
        start_new_session=True,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    if proc.returncode != 0:
        raise PublishError(f"sau exited {proc.returncode}: {out.strip()[-500:]}")

    if "submitted" not in out.lower():
        # A zero exit without the CLI's success line is not evidence of success.
        return {
            "status": "submission_unknown",
            "evidence": {"stdout": out[-2000:], "returncode": proc.returncode},
            "reason": "sau exited 0 but printed no submission confirmation",
        }

    found = WORK_URL_RE.search(out)
    remote_id = found.group(1) if found else ""
    return {
        "status": "submitted_unverified",
        "remotePostId": remote_id,
        "remoteUrl": found.group(0) if found else "",
        "evidence": {"stdout": out[-2000:], "returncode": proc.returncode},
        "reason": "" if remote_id else "page confirmed submission but no work ID was readable",
    }


@dataclass
class Readback:
    """回读一条已发布链接的结论。

    `state` 只有三档：published 表示页面确实给出了作品；not_found 表示平台明确说没有这条；
    其它（登录墙、验证码、网络失败、页面结构变了）都是 unknown，交给人工看。
    """

    state: str                 # "published" | "not_found" | "unknown"
    post_id: str = ""
    title: str = ""
    detail: str = ""


def _cookie_header(account: str) -> str:
    """复用 SAU 已登录的 cookie：私密/未公开作品只有本人 cookie 才看得到。"""
    if not account:
        return ""
    for name in (f"douyin_{account}.json", f"{account}.json"):
        path = SAU_CHECKOUT / "cookies" / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return ""
        pairs = [f"{c.get('name')}={c.get('value')}" for c in data.get("cookies", []) if c.get("name")]
        return "; ".join(pairs)
    return ""


_RENDER_SCRIPT = r"""
import asyncio, json, sys
from patchright.async_api import async_playwright

url, cookie_file, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])

async def main():
    cookies = []
    if cookie_file:
        try:
            with open(cookie_file) as fh:
                raw = json.load(fh)
            for c in raw.get("cookies", []):
                if not c.get("name"):
                    continue
                item = {"name": c["name"], "value": c.get("value", ""),
                        "domain": c.get("domain") or ".douyin.com", "path": c.get("path") or "/"}
                if c.get("expires") and c["expires"] > 0:
                    item["expires"] = c["expires"]
                cookies.append(item)
        except Exception:
            cookies = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900})
        if cookies:
            await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            await page.wait_for_timeout(2500)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}))
            await browser.close()
            return
        try:
            title = await page.title()
        except Exception:
            title = ""
        body = ""
        try:
            body = await page.inner_text("body", timeout=3000)
        except Exception:
            body = ""
        print(json.dumps({"ok": True, "title": title, "url": page.url,
                          "body": body[:4000], "html": (await page.content())[:200000]}))
        await browser.close()

asyncio.run(main())
"""


def _render_fetch(url: str, account: str, timeout: float) -> tuple[int, str]:
    """抖音的普通 HTTP 只回一段 JS 检测壳，没有内容；要看结论必须真渲染一次。

    无头 chromium，只读打开一条已发布链接，不点任何按钮、不登出、不抢前台。
    """
    cookie_file = ""
    if account:
        for name in (f"douyin_{account}.json", f"{account}.json"):
            candidate = SAU_CHECKOUT / "cookies" / name
            if candidate.exists():
                cookie_file = str(candidate)
                break
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "TZ": "Asia/Shanghai", "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.run(
        [str(SAU_PYTHON), "-c", _RENDER_SCRIPT, url, cookie_file, str(timeout)],
        cwd=str(SAU_CHECKOUT), env=env, capture_output=True, text=True,
        timeout=timeout + 90, check=False, start_new_session=True,
    )
    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            break
    if not payload or not payload.get("ok"):
        raise PublishError("渲染失败：%s" % ((payload or {}).get("error") or (proc.stderr or "")[-300:]))
    return 200, "%s\n%s" % (payload.get("title", ""), payload.get("html", ""))


def _default_fetch(url: str, cookie: str, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        **({"Cookie": cookie} if cookie else {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 只读已发布链接
        return resp.status, resp.read(400_000).decode("utf-8", "replace")


def readback(
    url: str,
    account: str = "",
    timeout: float = 20,
    fetch: Callable[[str, str, float], tuple[int, str]] | None = None,
) -> Readback:
    """只读回读：不开浏览器上传、不改任何东西，用来收尾 submission_unknown。"""
    match = WORK_URL_RE.search(url or "")
    if not match:
        return Readback(state="unknown", detail="不是可识别的作品链接")
    post_id = match.group(1)
    if fetch is None and account is not None:
        # 有 SAU 环境时用无头浏览器渲染，HTTP 直取只会拿到 JS 检测壳。
        def fetch(url_: str, cookie_: str, timeout_: float) -> tuple[int, str]:
            return _render_fetch(url_, account, timeout_)
    try:
        status, html = (fetch or _default_fetch)(match.group(0), _cookie_header(account), timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return Readback(state="not_found", post_id=post_id, detail=f"HTTP {exc.code}")
        return Readback(state="unknown", post_id=post_id, detail=f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 —— 网络/超时都算没结论，不能当已发布
        return Readback(state="unknown", post_id=post_id, detail=f"{type(exc).__name__}: {exc}")

    title_match = TITLE_RE.search(html or "")
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    # 页面里塞着几十 KB 的脚本，里面本来就有「验证码」这些词；只看渲染出来的文字。
    visible = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    visible = re.sub(r"<[^>]+>", " ", visible)
    visible = re.sub(r"\s+", " ", visible).strip()
    if any(marker in visible for marker in GONE_MARKERS):
        return Readback(state="not_found", post_id=post_id, title=title, detail="平台说作品不存在")
    if any(marker in visible for marker in LOGIN_MARKERS):
        return Readback(state="unknown", post_id=post_id, title=title, detail="页面要求登录或验证码")
    if AWEME_RE.search(html or "") or _has_real_work(title, post_id, visible):
        return Readback(state="published", post_id=post_id, title=title, detail=f"HTTP {status}")
    return Readback(state="unknown", post_id=post_id, title=title, detail="页面里读不到作品标记")


_WORKS_SCRIPT = r"""
import asyncio, json, sys
from patchright.async_api import async_playwright

cookie_file, timeout = sys.argv[1], float(sys.argv[2])

async def main():
    cookies = []
    if cookie_file:
        try:
            with open(cookie_file) as fh:
                for c in json.load(fh).get("cookies", []):
                    if c.get("name"):
                        item = {"name": c["name"], "value": c.get("value", ""),
                                "domain": c.get("domain") or ".douyin.com", "path": c.get("path") or "/"}
                        if c.get("expires", 0) > 0:
                            item["expires"] = c["expires"]
                        cookies.append(item)
        except Exception:
            cookies = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 1600},
                                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                   "Chrome/126.0.0.0 Safari/537.36")
        if cookies:
            await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        try:
            await page.goto("https://www.douyin.com/user/self?showTab=post",
                            wait_until="domcontentloaded", timeout=int(timeout * 1000))
            await page.wait_for_timeout(4000)
            html = await page.content()
        except Exception as exc:
            print(json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}))
            await browser.close()
            return
        print(json.dumps({"ok": True, "title": await page.title(), "html": html[:400000]}))
        await browser.close()

asyncio.run(main())
"""

_WORK_LINK_RE = re.compile(r'href="(?:https://www\.douyin\.com)?/(?:video|note)/(\d{6,})"')
_WORK_TEXT_RE = re.compile(r'<a[^>]+href="[^"]*/(?:video|note)/(\d{6,})"[^>]*>(.*?)</a>', re.S)


def _render_html(script: str, args: list[str], timeout: float) -> dict[str, Any]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "TZ": "Asia/Shanghai", "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.run(
        [str(SAU_PYTHON), "-c", script, *args], cwd=str(SAU_CHECKOUT), env=env,
        capture_output=True, text=True, timeout=timeout + 90, check=False,
        start_new_session=True,
    )
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    raise PublishError("渲染失败：%s" % ((proc.stderr or "").strip()[-300:] or "没有输出"))


def parse_works(html: str, limit: int = 20) -> list[dict[str, str]]:
    """从「我的作品」页里抽出作品 ID 和标题，用来把没回执的提交认回真作品。

    页面是自绘的，只认 href 里的 /video/ 与 /note/ ID；抽不到就是空列表，
    调用方不能因此判定「没发出去」。
    """
    works: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _WORK_TEXT_RE.finditer(html or ""):
        post_id = match.group(1)
        if post_id in seen:
            continue
        text = re.sub(r"<[^>]+>", " ", match.group(2))
        text = re.sub(r"\s+", " ", text).strip()
        seen.add(post_id)
        works.append({"postId": post_id,
                      "url": "https://www.douyin.com/video/%s" % post_id,
                      "title": text})
        if len(works) >= limit:
            break
    if not works:
        for match in _WORK_LINK_RE.finditer(html or ""):
            post_id = match.group(1)
            if post_id in seen:
                continue
            seen.add(post_id)
            works.append({"postId": post_id,
                          "url": "https://www.douyin.com/video/%s" % post_id,
                          "title": ""})
            if len(works) >= limit:
                break
    return works


def list_works(account: str, timeout: float = 60, limit: int = 20) -> list[dict[str, str]]:
    """只读拉本人最近作品列表；没登录/验证码时返回空列表，绝不臆造 ID。"""
    cookie_file = ""
    for name in (f"douyin_{account}.json", f"{account}.json"):
        candidate = SAU_CHECKOUT / "cookies" / name
        if candidate.exists():
            cookie_file = str(candidate)
            break
    if not cookie_file:
        return []
    payload = _render_html(_WORKS_SCRIPT, [cookie_file, str(timeout)], timeout)
    if not payload.get("ok"):
        return []
    return parse_works(payload.get("html", ""), limit=limit)


def reconcile_payload(result: "Readback") -> dict[str, Any] | None:
    """把回读结论翻成台账能接受的回写；没有结论就返回 None（保持原状等人工）。

    published 必须带作品 ID 和链接，否则台账里就是一条没法核对的「已发布」。
    """
    if result.state == "published" and result.post_id:
        return {
            "status": "published",
            "remotePostId": result.post_id,
            "remoteUrl": "https://www.douyin.com/video/%s" % result.post_id,
            "evidence": {"kind": "readback", "state": result.state,
                         "title": result.title, "detail": result.detail},
        }
    if result.state == "not_found":
        return {
            "status": "not_submitted",
            "evidence": {"kind": "readback", "state": result.state, "detail": result.detail},
        }
    return None
