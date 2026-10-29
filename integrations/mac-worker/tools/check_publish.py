"""发布这一步的离线体检：只验「任务载荷 → sau 命令行」的契约，不开浏览器、不点鼠标。

发布是唯一有不可逆副作用的步骤，所以这里把「哪些载荷会被拒绝、命令行长什么样、
什么输出才算已提交」钉死；真发一版要在机器空闲时手动跑，不放在这条脚本里。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import publish_sau as P  # noqa: E402

PASS = 0
FAIL = 0


def require(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % msg)
    else:
        FAIL += 1
        print("  FAIL %s" % msg)


def t_payload(tmp: Path) -> None:
    print("载荷解析")
    video = tmp / "clip.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    spec = P.PublishSpec.from_payload({
        "accountRef": "acct", "videoPath": str(video),
        "content": {"title": "标题", "desc": "描述", "tags": ["#a", "b"]},
    })
    require(spec.kind == "video" and spec.video == video, "视频载荷带出文件")
    require(spec.tags == ["a", "b"], "话题去掉 # 号")
    for label, payload in (
        ("缺 accountRef", {"videoPath": str(video), "content": {"title": "t"}}),
        ("缺标题", {"accountRef": "a", "videoPath": str(video), "content": {}}),
        ("文件不存在", {"accountRef": "a", "videoPath": str(tmp / "no.mp4"),
                        "content": {"title": "t"}}),
        ("类型不认识", {"accountRef": "a", "content": {"type": "story", "title": "t"}}),
    ):
        try:
            P.PublishSpec.from_payload(payload)
        except P.PublishError:
            require(True, "拒绝：%s" % label)
        else:
            require(False, "拒绝：%s" % label)


def t_argv(tmp: Path) -> None:
    print("命令行")
    video = tmp / "clip.mp4"
    spec = P.PublishSpec(video=video, title="t", description="d", tags=["a"],
                         account="acct", schedule="2026-01-01 10:00", thumbnail=video)
    argv = P.build_argv(spec)
    require("upload-video" in argv and str(video) in argv, "视频走 upload-video")
    require(argv[argv.index("--tags") + 1] == "a", "话题用逗号串联")
    img = tmp / "a.png"
    img.write_bytes(b"png")
    note = P.build_argv(P.PublishSpec(kind="note", images=[img], title="t",
                                      description="d", account="a", bgm="x.m4a"))
    require("upload-note" in note and "--bgm" in note, "图文走 upload-note 且带 BGM")
    require(all(f not in note for f in ("--file", "--desc", "--thumbnail")),
            "图文不带视频参数")


def t_output_parsing() -> None:
    print("输出判读")
    import subprocess
    from types import SimpleNamespace
    from unittest import mock

    def fake(stdout: str, rc: int = 0, python: Path | None = None):
        # SAU_PYTHON 只用来判断 venv 在不在，指到任一已存在文件即可，不会被执行
        return mock.patch.object(P.subprocess, "run", return_value=SimpleNamespace(
            returncode=rc, stdout=stdout, stderr="")), \
            mock.patch.object(P, "check_cookie", return_value=True), \
            mock.patch.object(P, "SAU_PYTHON", python or Path("/bin/true"))

    url = "https://www.douyin.com/video/7456000000000000000"
    with tempfile.TemporaryDirectory() as td:
        video = Path(td) / "clip.mp4"
        video.write_bytes(b"\x00")
        payload = {"accountRef": "a", "videoPath": str(video), "content": {"title": "t"}}
        a, b, c = fake("Submitted ok\n%s\n" % url, python=video)
        with a, b, c:
            out = P.submit("j1", payload, "Asia/Shanghai")
        require(out["status"] == "submitted_unverified", "有确认行 → submitted_unverified")
        require(out["remotePostId"] == "7456000000000000000", "读回作品 ID")

        a, b, c = fake("nothing happened", python=video)
        with a, b, c:
            out = P.submit("j1", payload, "Asia/Shanghai")
        require(out["status"] == "submission_unknown", "零退出但没确认 → submission_unknown")

        a, b, c = fake("boom", rc=3, python=video)
        with a, b, c:
            try:
                P.submit("j1", payload, "Asia/Shanghai")
            except P.PublishError as exc:
                require("exited 3" in str(exc), "非零退出 → PublishError 带退出码")
            else:
                require(False, "非零退出 → PublishError 带退出码")

        with mock.patch.object(P, "check_cookie", return_value=False), \
             mock.patch.object(P.subprocess, "run") as run:
            try:
                P.submit("j1", payload, "Asia/Shanghai")
            except P.PublishError as exc:
                require("cookie" in str(exc), "cookie 失效不启动浏览器")
            else:
                require(False, "cookie 失效不启动浏览器")
            require(not run.called, "cookie 失效时没有起浏览器进程")


def t_readback() -> None:
    """回读只用来收尾「发没发出去不知道」：宁可 unknown，不能猜成已发布。"""
    import urllib.error

    URL = "https://www.douyin.com/video/7456000000000000000"

    def fake(html="", status=200, error=None):
        def fetch(url, cookie, timeout):
            if error:
                raise error
            return status, html
        return fetch

    hit = P.readback(URL, fetch=fake('<title>成片</title>{"aweme_id":"7456000000000000000"}'))
    require(hit.state == "published" and hit.post_id == "7456000000000000000", "页面有作品 → published")
    require(hit.title == "成片", "读回标题")

    gone = P.readback(URL, fetch=fake("作品不存在"))
    require(gone.state == "not_found", "平台说没了 → not_found")

    wall = P.readback(URL, fetch=fake("登录后查看"))
    require(wall.state == "unknown", "登录墙 → unknown 不猜")

    missing = P.readback(URL, fetch=fake(error=urllib.error.HTTPError(URL, 404, "x", {}, None)))
    require(missing.state == "not_found", "404 → not_found")
    forbidden = P.readback(URL, fetch=fake(error=urllib.error.HTTPError(URL, 403, "x", {}, None)))
    require(forbidden.state == "unknown", "403 → unknown")

    blank = P.readback(URL, fetch=fake("<title>抖音</title>"))
    require(blank.state == "unknown", "读不到作品标记 → unknown")

    calls = []
    other = P.readback("https://www.douyin.com/user/abc", fetch=lambda *a: calls.append(1) or (200, ""))
    require(other.state == "unknown" and not calls, "非作品链接不发请求")

    payload = P.reconcile_payload(hit)
    require(payload["status"] == "published" and payload["remotePostId"], "回读 published → 回写带作品 ID")
    require(P.reconcile_payload(P.Readback(state="published", post_id="")) is None, "没 ID 的 published 不回写")
    require(P.reconcile_payload(gone)["status"] == "not_submitted", "回读 not_found → 回写 not_submitted")
    require(P.reconcile_payload(wall) is None, "回读 unknown → 台账不动")

    works = P.parse_works('<a href="/video/7456000000000000005"><span>作品一</span></a>')
    require(len(works) == 1 and works[0]["postId"] == "7456000000000000005", "作品列表抽出 ID 与标题")
    require(P.parse_works("登录后查看") == [], "登录页不臆造作品 ID")


def cookie_preflight(account: str, timeout: int) -> int:
    """只验「这个账号现在能不能发」：cookie 文件在不在、内容还有没有效。

    检查本身跑无头 chromium（不弹窗、不抢前台），但会联网，所以单独用 --cookie 触发，
    不混进默认的离线体检。
    """
    print("cookie 预检：%s" % account)
    if not P.SAU_PYTHON.exists():
        print("  FAIL SAU venv 不在：%s（先跑 deploy/bootstrap_sau.sh）" % P.SAU_PYTHON)
        return 2
    cookie_file = config.SAU_CHECKOUT / "cookies" / ("douyin_%s.json" % account)
    if not cookie_file.is_file():
        print("  FAIL 没有 cookie 文件：%s" % cookie_file)
        print("       修：%s sau_cli.py douyin login --account %s" % (P.SAU_PYTHON, account))
        return 2
    print("  ok   cookie 文件在：%s" % cookie_file.name)
    try:
        proc = subprocess.run(
            [str(P.SAU_PYTHON), "sau_cli.py", "douyin", "check", "--account", account],
            cwd=str(config.SAU_CHECKOUT), capture_output=True, text=True,
            timeout=timeout, check=False,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                 "HOME": str(Path.home()), "TZ": "Asia/Shanghai",
                 "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1",
                 "DOUYIN_COOKIE_AUTH_HEADLESS": "true"},
        )
    except subprocess.TimeoutExpired:
        print("  FAIL 检查超时（%ds）：可能网络不通或页面卡住" % timeout)
        return 2
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    if proc.returncode == 0 and "invalid" not in (proc.stdout or ""):
        print("  ok   cookie 有效（sau douyin check 通过）")
        return 0
    print("  FAIL cookie 失效或检查失败（rc=%d）：%s" % (proc.returncode, tail[-1] if tail else ""))
    print("       修：%s sau_cli.py douyin login --account %s" % (P.SAU_PYTHON, account))
    return 2


def main() -> int:
    ap = argparse.ArgumentParser(description="发布段离线体检；加 --cookie 顺带验账号 cookie")
    ap.add_argument("--cookie", metavar="ACCOUNT", help="顺带跑 sau douyin check（无头，不弹窗）")
    ap.add_argument("--timeout", type=int, default=180, help="cookie 预检超时秒数")
    ap.add_argument("--readback", metavar="URL", help="只读回读一条作品链接，看它还在不在（无头渲染，不写任何东西）")
    ap.add_argument("--account", metavar="ACCOUNT", help="回读时复用这个 SAU 账号的 cookie（私密作品只有本人看得到）")
    ap.add_argument("--works", metavar="ACCOUNT", help="只读拉本人最近作品，用来认领没回执的提交（联网，无头）")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        t_payload(tmp)
        t_argv(tmp)
    t_output_parsing()
    t_readback()
    if args.works:
        works = P.list_works(args.works, timeout=min(args.timeout, 60))
        print("\n作品列表：%s  共 %d 条" % (args.works, len(works)))
        for row in works[:20]:
            print("  %s  %s" % (row["postId"], row["title"][:40] or "(无标题)"))
        if not works:
            print("  读不到作品（cookie 失效 / 需要验证码）——不能据此判定没发出去")
    if args.readback:
        if not P.SAU_PYTHON.exists():
            print("\n回读需要 SAU 运行时：%s" % P.SAU_PYTHON)
            return 2
        result = P.readback(args.readback, account=args.account or "", timeout=min(args.timeout, 60))
        print("\n回读：%s" % args.readback)
        print("  结论：%s  %s" % (result.state, result.detail))
        if result.title:
            print("  标题：%s" % result.title)
        print("  下一步：published 且带作品 ID 才回写 published；not_found 回 not_submitted；unknown 保持不动等人工")
    if PASS + FAIL and FAIL == 0 and args.cookie:
        return cookie_preflight(args.cookie, args.timeout)
    return 0 if FAIL == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
