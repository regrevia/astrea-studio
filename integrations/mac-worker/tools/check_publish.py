"""发布这一步的离线体检：只验「任务载荷 → sau 命令行」的契约，不开浏览器、不点鼠标。

发布是唯一有不可逆副作用的步骤，所以这里把「哪些载荷会被拒绝、命令行长什么样、
什么输出才算已提交」钉死；真发一版要在机器空闲时手动跑，不放在这条脚本里。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        t_payload(tmp)
        t_argv(tmp)
    t_output_parsing()
    print("\n结果: %d/%d 通过" % (PASS, PASS + FAIL))
    return 0 if FAIL == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
