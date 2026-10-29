"""open_draft.py — 把剪映的某个草稿打开到编辑器，供导出驱动接手。

为什么单独一步：导出驱动（jianying_export.py）只认「导出面板能开出来」，而面板
只有在**草稿已经打开**时才存在。剪映没有「按路径打开草稿」的 CLI 参数，草稿只能
从首页草稿箱点进去；这一步以前靠人点，于是整条 GUI 链路永远停在「等桌面」。

做法：首页用 AX 树读到每张草稿卡（`HomePageDraftTitle:<名字>` + 卡片的矩形），
双击卡片中心把它打开，然后等编辑器窗口出现 `MainWindowTitleBarExportBtn`。
Qt 控件对 AXPress 无效，所以点击是真实鼠标坐标点击。

退出码：0 已打开；2 找不到草稿/没进编辑器；9 用户在忙，这轮不动鼠标。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel as P  # noqa: E402
import draft_listing as DL  # noqa: E402

APP_PATH = Path("/Applications/VideoFusion-macOS.app")
DRAFT_ROOT = Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
EDITOR_MARKERS = ("MainWindowTitleBarExportBtn", "MainWindowTitleBarExport")


def sh(*cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tree_text(depth=1) -> str:
    p = sh(P.AX_TOOL, "tree", P.APP, str(depth), timeout=40)
    return (p.stdout or "") + (p.stderr or "")


def app_running() -> bool:
    # 进程名就是 bundle 可执行名（VideoFusion-macOS），窗口标题才是「剪映专业版」。
    # osascript 数进程要辅助功能授权、还会被自动化拦截，直接用 pgrep。
    return sh("pgrep", "-x", P.APP).returncode == 0


def launch() -> None:
    if not APP_PATH.exists():
        raise SystemExit("找不到剪映：%s" % APP_PATH)
    sh("open", "-a", str(APP_PATH))
    deadline = time.time() + 60
    while time.time() < deadline:
        if app_running():
            return
        time.sleep(1.0)
    raise SystemExit("剪映没起来")


def editor_open() -> bool:
    t = tree_text(1)
    return any(m in t for m in EDITOR_MARKERS)


def _rects(t: str):
    """把 ax_tool tree 的每行拆成 (标识, (x,y,w,h))，坐标解析不了的行跳过。"""
    out = []
    for line in t.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        try:
            xy, wh = parts[7].strip().split()[0], parts[7].strip().split()[1]
            x, y = (int(v) for v in xy.split(","))
            w, h = (int(v) for v in wh.split(","))
        except Exception:
            continue
        out.append((line, (x, y, w, h)))
    return out


def on_screen_windows(pid: str) -> int:
    """当前桌面上的窗口数。0 表示窗口在别的 Space / 已最小化。

    这条必须查：AX 树能读到别的 Space 的窗口，坐标看着正常，但真实鼠标点会落在
    **当前** Space 的别的 App 上（实测把点击打给了正在打字的编辑器）。宁可不点。
    """
    p = sh(P.AX_TOOL, "wins", pid, timeout=20)
    try:
        return int((p.stderr or "").strip().rsplit(":", 1)[-1].strip())
    except Exception:
        return -1


def card_rect(name: str):
    """草稿卡：卡片节点本身不带草稿名，标题节点才带。

    所以先定位标题文字矩形，再取同一列、位于标题上方的那张卡（`HomePageDraft`）。
    拿标题矩形去点会点在文字上——Qt 列表单击文字只选中不打开，必须点缩略图卡片。
    """
    rows = _rects(tree_text(1))
    title = next((r for line, r in rows
                  if "HomePageDraftTitle:" in line and name in line), None)
    if not title:
        return None
    tx, ty = title[0], title[1]
    cards = [(line, r) for line, r in rows
             if "HomePageDraft" in line and "HomePageDraftTitle:" not in line]
    best = None
    for line, (x, y, w, h) in cards:
        # 标题文字落在卡片矩形内部（剪映的卡片比标题高，底部会越过标题）
        if x <= tx <= x + w and y <= ty < y + h and h > 20:
            if best is None or h < best[1][3]:
                best = (line, (x, y, w, h))
    return best[1] if best else title


def open_draft_name(pid=None) -> str:
    """剪映当前打开的是哪张草稿：直接问文件句柄，比读 AX 树可靠。

    编辑器界面的 AX 树里没有草稿名（标题栏是应用名），从首页卡片判断又要求
    人已经在首页。lsof 是唯一不需要界面配合的入口。
    """
    pids = [str(pid)] if pid else (sh("pgrep", "-x", P.APP).stdout or "").split()
    for one in pids:
        # -n -P 必须带：lsof 默认反解主机名/端口，剪映连着网络时会卡到超时（实测 >20s）
        out = (sh("lsof", "-n", "-P", "-p", one, timeout=8).stdout or "")
        names = set()
        for line in out.splitlines():
            if "com.lveditor.draft/" in line:
                tail = line.split("com.lveditor.draft/", 1)[1]
                names.add(tail.split("/", 1)[0])
        if len(names) == 1:
            return names.pop()
        if len(names) > 1:
            return "|".join(sorted(names))
    return ""


def restart_app(timeout: float = 60.0) -> bool:
    """硬重启剪映，并等首页卡片重新出现。

    为什么要重启：首页草稿箱的排序/收录在启动时读索引算一次，改完索引不重启不生效
    （实测改完 tm_draft_modified 后必须重启才看到卡片位置变化）。
    """
    sh("pkill", "-9", "-x", P.APP)
    time.sleep(2.0)
    sh("open", "-a", str(APP_PATH))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "HomePageDraftTitle:" in tree_text(1):
            return True
        time.sleep(1.0)
    return False


def open_draft(name: str, wait: float = 25.0, surface: bool = False) -> int:
    if not app_running():
        print("启动剪映…")
        launch()
        time.sleep(6.0)
    P.front()
    time.sleep(0.8)
    opened = open_draft_name()
    if opened == name:
        # 编辑器里就是这张草稿：AX 树看不到草稿名，用文件句柄确认，别再点首页
        print("草稿已在编辑器（lsof 确认）：%s" % name)
        return 0
    if editor_open() and name in tree_text(1):
        print("草稿已在编辑器：%s" % name)
        return 0
    # 不在首页就先回首页（编辑器里点返回，Esc 关掉可能的弹层）
    if not any("HomePageDraftTitle:" in l for l in tree_text(1).splitlines()):
        P.run([P.AX_TOOL, "key", "esc"])
        time.sleep(0.5)
        P.run([P.AX_TOOL, "key", "cmd+shift+h"])  # 无副作用：剪映首页快捷键不中也不报错
        time.sleep(1.0)
    rect = card_rect(name)
    if not rect and surface:
        # 首页只显示「最新的一屏」（实测 8 张）卡；旧草稿要先把索引里的修改时间顶到最新，
        # 重启剪映才会出现在第一屏。改之前 draft_listing 会备份索引，可 restore 还原。
        r = DL.surface(name)
        print("草稿不在第一屏，顶时间戳：%s" % json.dumps(r, ensure_ascii=False), file=sys.stderr)
        if r.get("changed"):
            print("重启剪映让首页重排…", file=sys.stderr)
            restart_app()
            P.front()
            time.sleep(0.8)
            rect = card_rect(name)
    if not rect:
        print("首页里没有这张草稿：%s" % name, file=sys.stderr)
        print(tree_text(1)[:2000], file=sys.stderr)
        return 2
    pids = (sh("pgrep", "-x", P.APP).stdout or "").split()
    n = on_screen_windows(pids[0]) if pids else 0
    if n <= 0:
        print("剪映窗口不在当前桌面（可见窗口数=%d），拒绝盲点坐标："
              "先切到剪映所在桌面，或把窗口拖过来再跑" % n, file=sys.stderr)
        return 2
    x, y, w, h = rect
    cx, cy = x + w // 2, y + h // 2
    print("双击草稿卡 %s @ %d,%d（%dx%d）" % (name, cx, cy, w, h))
    # 走三级通道阶梯：先试不动光标的通道，无效才借指针；点完用「编辑器是否出现」验证
    hit = P.click_point(cx, cy, verify=lambda: editor_open(), tiers=("pid", "hid"))
    print("点击通道：%s" % json.dumps(hit, ensure_ascii=False) if hit else "点击通道：失败")
    deadline = time.time() + wait
    while time.time() < deadline:
        if editor_open():
            print("已进编辑器")
            return 0
        time.sleep(1.0)
    print("点了但没进编辑器（导出按钮始终没出现）", file=sys.stderr)
    return 2


def check(name: str, draft_path: str | None = None) -> int:
    """只体检、不开剪映：草稿目录在不在、App 装没装、当前是不是编辑器态。

    `--check` 不点鼠标、不抢前台，所以可以在人正在用机器时跑；真打开才需要空闲。
    """
    import json
    draft = Path(draft_path) if draft_path else DRAFT_ROOT / name
    running = app_running()
    editor = editor_open() if running else False
    try:
        from .draft_inspect import inspect as inspect_draft
    except ImportError:  # 直接当脚本跑时没有包上下文
        from draft_inspect import inspect as inspect_draft
    details = inspect_draft(draft)
    blockers = []
    if not draft.is_dir():
        blockers.append("draft_missing")
    if not APP_PATH.exists():
        blockers.append("app_missing")
    if details["missingMedia"]:
        blockers.append("media_missing")
    if details["mismatches"]:
        blockers.append("spec_mismatch")
    info = {
        "draft": name,
        "draftPath": str(draft),
        "draftExists": draft.is_dir(),
        "appPath": str(APP_PATH),
        "appInstalled": APP_PATH.exists(),
        "appRunning": running,
        "editorOpen": editor,
        "canOpen": not blockers,
        "blockers": blockers,
        "infoReadable": details["infoReadable"],
        "encrypted": details["encrypted"],
        "canvas": details["canvas"],
        "durationSec": details["durationSec"],
        "fps": details["fps"],
        "trackCount": details["trackCount"],
        "media": details["media"],
        "missingMedia": details["missingMedia"][:10],
        "schema": details["schema"],
        "stampState": details["stampState"],
        "mediaDigest": details["mediaDigest"],
        "expected": details["expected"],
        "mismatches": details["mismatches"],
    }
    print(json.dumps(info, ensure_ascii=False))
    return 0 if info["canOpen"] else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="草稿名（草稿目录的 basename）")
    ap.add_argument("--draft-path", help="草稿目录绝对路径；体检时用来核对非默认位置")
    ap.add_argument("--check", action="store_true", help="只体检不开剪映，不碰鼠标")
    ap.add_argument("--wait-idle", type=float, default=0.0)
    ap.add_argument("--wait", type=float, default=25.0)
    ap.add_argument("--surface", action="store_true",
                    help="草稿不在首页第一屏时，把它在索引里的修改时间顶到最新并重启剪映")
    ap.add_argument("--list", action="store_true",
                    help="只列出首页第一屏能读到的草稿名（不点鼠标）")
    args = ap.parse_args(argv)
    if args.list:
        print(json.dumps({"visible": DL.visible_titles(tree_text(1)) or [],
                          "stale": DL.stale_entries() if app_running() else []},
                         ensure_ascii=False))
        return 0
    if args.check:
        return check(args.name, args.draft_path)
    if not P.quiet_start(args.wait_idle, restore=False, strict=True):
        print("排队：用户未空闲，这轮不点草稿", file=sys.stderr)
        return 9
    if args.name not in tree_text(1) and not (DRAFT_ROOT / args.name).is_dir():
        print("草稿目录不存在：%s" % (DRAFT_ROOT / args.name), file=sys.stderr)
        return 2
    return open_draft(args.name, wait=args.wait, surface=args.surface)


if __name__ == "__main__":
    raise SystemExit(main())
