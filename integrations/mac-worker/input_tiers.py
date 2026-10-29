"""剪映 GUI 输入通道阶梯：AXPress → 进程内投递 → 借物理指针。

为什么需要这个文件：平台要在应用内点按钮，但**不能碰用户的鼠标**。macOS 只有一根硬件
指针，所以"第二个光标"在系统层面不存在；能做的是先试两条不移动指针的通道，只有都无效时
才短暂借指针，并把用了哪一级、试了几次记进证据，便于换版本后重测。

2026-10-29 在剪映 11.6.0 build 490 上实测（导出面板「取消」按钮，窗口截图逐字节对比）：

    通道          命令                        结果
    AXPress       ax_tool click --via ax       err=0，窗口内容零变化（Qt 不实现 press action）
    postToPid     ax_tool bgclick <x> <y>      窗口内容零变化（非 HID 事件被过滤）
    HID           ax_tool click-at <x> <y>     窗口立即关闭（唯一有效）

所以剪映这条链路里，`pid` 级只是"顺手试一下、为将来版本留位"，真正生效的是 `hid`。
判定标准不是退出码，而是**调用方给的 verify()**：AXPress 返回 err=0 也可能什么都没发生，
只看返回码会把"点了没反应"记成成功。
"""
from __future__ import annotations

import os
import subprocess
import time

TIERS = ("ax", "pid", "hid")

#: 借指针前后必须有的"用户空闲"门（秒）；由 panel.quiet_start 负责等
QUIET_ENV = "STUDIO_GUI_IDLE_SEC"


def argv_ax(ax_tool: str, app: str, needle: str, index: int = 0) -> list[str]:
    return [ax_tool, "click", app, needle, "--via", "ax", "--index", str(index)]


def argv_pid(ax_tool: str, x: int, y: int, count: int = 1) -> list[str]:
    cmd = [ax_tool, "bgclick", str(int(x)), str(int(y))]
    if count > 1:
        cmd += ["--count", str(int(count))]
    return cmd


def argv_hid(ax_tool: str, x: int, y: int, count: int = 1) -> list[str]:
    cmd = [ax_tool, "click-at", str(int(x)), str(int(y))]
    if count > 1:
        cmd += ["--count", str(int(count))]
    return cmd


def argv_for(tier: str, ax_tool: str, *, app: str = "VideoFusion-macOS",
             needle: str = "", x: int = 0, y: int = 0, index: int = 0,
             count: int = 1) -> list[str]:
    if tier == "ax":
        if not needle:
            raise ValueError("ax 级需要控件名")
        return argv_ax(ax_tool, app, needle, index)
    if tier == "pid":
        return argv_pid(ax_tool, x, y, count)
    if tier == "hid":
        return argv_hid(ax_tool, x, y, count)
    raise ValueError("未知通道 %r" % (tier,))


def _default_run(cmd: list[str], timeout: float = 30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def click(tier: str, ax_tool: str, runner=_default_run, *, app: str = "VideoFusion-macOS",
          needle: str = "", x: int = 0, y: int = 0, index: int = 0, count: int = 1,
          verify=None, pause: float = 0.35) -> dict:
    """按指定通道点一次，返回一条可入证据的记录。

    `verify` 必须是"点了以后界面真的变了"的判据（读窗口标题/AX 树/截图哈希）。没有
    verify 时只能信任返回码，这种情况记录里带 `verified=False`，别当成功证据用。
    """
    cmd = argv_for(tier, ax_tool, app=app, needle=needle, x=x, y=y, index=index, count=count)
    started = time.time()
    p = runner(cmd)
    if pause:
        time.sleep(pause)
    ok = p.returncode == 0
    confirmed = bool(verify()) if (ok and verify is not None) else None
    return {
        "tier": tier,
        "argv": cmd[1:],
        "rc": p.returncode,
        "elapsed_ms": int((time.time() - started) * 1000),
        "ok": ok,
        "verified": confirmed,
        "effective": bool(ok and (confirmed is not False)),
    }


def ladder(ax_tool: str, *, app: str = "VideoFusion-macOS", needle: str = "",
           x: int = 0, y: int = 0, index: int = 0, count: int = 1,
           tiers=TIERS, runner=_default_run, verify=None, pause: float = 0.35) -> dict:
    """按顺序试通道，第一次"真的生效"就停。

    返回 `{"tier": 生效通道或 None, "tried": [...], "effective": bool}`。
    verify 为 None 时，AXPress / postToPid 的成功码不算生效——剪映这两种都返回成功而界面不动。
    """
    tried = []
    for tier in tiers:
        rec = click(tier, ax_tool, runner, app=app, needle=needle, x=x, y=y,
                    index=index, count=count, verify=verify, pause=pause)
        tried.append(rec)
        if rec["effective"]:
            return {"tier": tier, "tried": tried, "effective": True}
    return {"tier": None, "tried": tried, "effective": False}


def borrow_allowed() -> bool:
    """借物理指针前的自检：没设空闲门就不要动鼠标。"""
    return os.environ.get(QUIET_ENV) not in (None, "", "0")


def receipt(result: dict, *, app: str = "VideoFusion-macOS", note: str = "") -> dict:
    """把阶梯结果整理成可以写进 probe 证据的形态。"""
    return {
        "app": app,
        "tier": result.get("tier"),
        "effective": bool(result.get("effective")),
        "tried": [{"tier": t["tier"], "rc": t["rc"], "verified": t["verified"],
                   "elapsed_ms": t["elapsed_ms"]} for t in result.get("tried", [])],
        "note": note,
    }
