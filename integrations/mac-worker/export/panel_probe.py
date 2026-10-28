"""【已废弃·仅作历史参考】早期探针。

它用 ESC 关下拉，实测会把整个模态导出面板一起取消，导致后续点击落到编辑器里
（曾把面板改成 480P/24/更低）。现在请用 panel.py（dismiss/select/rows 三件套）
或直接跑 jianying_export.py --list-options。不要在任何自动化流程里调用本文件。
"""
#!/usr/bin/env python3
"""探测剪映「导出」面板的真实参数与选项集，产出面板地图(panel map)。

设计约束（来自实测，2026-10-24）：
  * 面板是 Qt 画的，参数的**标题/值文字**不会被 AX 暴露为字符串；
    但每个控件都有一个稳定的 automation id 节点（如 ``ExportbitRateInput``），
    带精确的屏幕矩形。所以：
        AX  → 控件身份 + 矩形（稳定，可定位）
        OCR → 当前值 + 弹层里的可选项（必须读屏）
    两者按「OCR 文字框落在 AX 矩形内」合并。
  * 窗口大小/位置会变（导出面板 640x720，弹层 280x448，编辑器 2560x1320），
    因此**绝不持久化绝对坐标**：一律存相对窗口的 (rx, ry, rw, rh)，运行时换算。
  * 弹层选项是 layer-8 独立窗口，选项文字同样是 AXStaticText（带 frame），
    但点选要走鼠标（无 AXPress），所以这里用 OCR 拿文字、用可选 AX 框校准。

用法:
    python3 panel_probe.py                 # 探测并打印，写 params.json
    python3 panel_probe.py --out x.json    # 指定输出
    python3 panel_probe.py --only 格式,帧率 # 只探这几个标签

依赖: ax_tool、ocr（由 panel.ensure_tools 编到 ~/CreatorStudio/bin）与 screencapture。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BIN_DIR = Path(os.environ.get("STUDIO_BIN") or os.path.expanduser("~/CreatorStudio/bin"))
AX_TOOL = os.environ.get("AX_TOOL") or str(BIN_DIR / "ax_tool")
OCR_TOOL = os.environ.get("OCR_TOOL") or str(BIN_DIR / "ocr")
WINLIST = os.environ.get("WINLIST") or str(BIN_DIR / "winlist")
APP = "VideoFusion-macOS"
PANEL_TITLE_PREFIX = "导出-"

# 探测顺序：按面板从上到下，避免"选了上面的导致下面重排"影响定位
PROBE_ORDER = [
    "分辨率", "格式", "帧率", "视频码率", "编码", "音频质量", "采样率",
]

# 已知 automation id → 中文标签（AX 不提供标签，靠这张表 + OCR 校正）
ID_LABEL = {
    "ExportSharpnessInput": "分辨率",
    "ExportFormatInput": "格式",
    "FrameRateInput": "帧率",
    "ExportbitRateInput": "视频码率",
    "ExportEncoderInput": "编码",
    "AudioQualityInput": "音频质量",
    "AudioSamplingInput": "采样率",
    "AudioFormatInput": "音频格式",
    "AudioBitRateInput": "音频码率",
    "samplingInput": "音频采样",
}
CLICKABLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*Input$")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ax_tree(depth=9) -> list[list[str]]:
    p = run([AX_TOOL, "tree", APP, str(depth)])
    rows = []
    for line in p.stdout.splitlines():
        if not line.strip() or line.startswith("---"):
            continue
        rows.append(line.split("\t"))
    return rows


def parse_frame(text: str):
    """'1408,660 176,22' → (1408, 660, 176, 22)"""
    m = re.match(r"^(-?\d+),(-?\d+)\s+(\d+),(\d+)$", text.strip())
    if not m:
        return None
    return tuple(int(v) for v in m.groups())


def panel_window(rows):
    """找到导出对话框窗口的矩形（AXDialog）。"""
    for r in rows:
        if len(r) >= 8 and r[0] == "0" and r[1] == "AXWindow" and r[2] == "AXDialog":
            fr = parse_frame(r[7])
            if fr:
                return fr, (r[3] or "")
    return None, None


def controls(rows):
    """收集对话框里的可点参数控件: [(id, frame)]，按 y 排序。"""
    out = {}
    for r in rows:
        if len(r) < 8:
            continue
        node_id = (r[3] or r[4] or "").strip()
        if not node_id or not CLICKABLE_ID.match(node_id):
            continue
        fr = parse_frame(r[7] or "")
        if not fr:
            continue
        # 同一 id 会出现多次（内层 AXStaticText 与外壳同矩形），只留一个
        out.setdefault(node_id, fr)
    return sorted(out.items(), key=lambda kv: (kv[1][1], kv[1][0]))


def capture(rect, path):
    x, y, w, h = rect
    run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x, y, w, h), path])
    return path


def ocr(png, ox=0, oy=0):
    """返回 [{text,x,y,w,h,conf}]，坐标已换算回屏幕绝对坐标。"""
    p = run([OCR_TOOL, png, "--json"])
    try:
        items = json.loads(p.stdout)
    except Exception:
        return []
    for it in items:
        it["x"] += ox
        it["y"] += oy
    return items


def read_row(input_rect, panel_rect, png="/tmp/_row.png"):
    """读一个参数行：裁剪该行左侧标签+右侧值，返回 (label, value)。"""
    x, y, w, h = input_rect
    left = 40                      # 往左看 40px 拿标签
    crop = (x - left, y - h * 0.2, w + left + 6, h * 1.6)
    capture(crop, png)
    items = ocr(png, crop[0], crop[1])
    label = " ".join(i["text"] for i in items if i["x"] < x - 2)
    value = " ".join(i["text"] for i in items if i["x"] >= x - 2)
    return label.strip(), value.strip()


def open_dropdown(input_rect, wait=1.3):
    x, y, w, h = input_rect
    run([AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
    time.sleep(wait)


def popup_rect(input_rect, panel_rect):
    """弹层窗口矩形：用 winlist 找紧邻输入框下方、layer>=1 的剪映窗口。"""
    p = run([WINLIST])
    ix, iy, iw, ih = input_rect
    best = None
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or parts[3] != "剪映专业版":
            continue
        try:
            layer = int(parts[1])
        except ValueError:
            continue
        if layer < 1:
            continue
        fr = parse_frame(parts[5])  # '1356,586 280x448' → 需转逗号
        if fr is None:
            m = re.match(r"^(-?\d+),(-?\d+)\s+(\d+)x(\d+)$", parts[5].strip())
            if not m:
                continue
            fr = tuple(int(v) for v in m.groups())
        px, py, pw, ph = fr
        # 弹层起点必须贴近输入框、且在面板范围内
        if abs(px - ix) < 200 and -10 <= py - iy <= ih + 10 and ph > 60:
            best = fr
    return best


def read_options(input_rect, panel_rect):
    """打开下拉，读取选项文字及其相对位置，然后 ESC 关闭。返回 [(text, rect|None)]。"""
    open_dropdown(input_rect)
    pr = popup_rect(input_rect, panel_rect)
    if not pr:
        run([AX_TOOL, "key", APP, "esc"])
        time.sleep(0.3)
        return [], None
    px, py, pw, ph = pr
    capture(pr, "/tmp/_popup.png")
    items = ocr("/tmp/_popup.png", px, py)
    opts, seen = [], set()
    for it in items:
        t = it["text"].strip()
        if not t or it["conf"] < 0.45:
            continue
        key = (t, int(it["y"] // 6))
        if key in seen:
            continue
        seen.add(key)
        opts.append((t, (it["x"], it["y"], it["w"], it["h"])))
    run([AX_TOOL, "key", APP, "esc"])
    time.sleep(0.35)
    return opts, pr


def rel(rect, panel):
    px, py, pw, ph = panel
    x, y, w, h = rect
    return {
        "rx": round((x - px) / pw, 5), "ry": round((y - py) / ph, 5),
        "rw": round(w / pw, 5), "rh": round(h / ph, 5),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/probe/export-panel/params.json")
    ap.add_argument("--only", default="")
    ap.add_argument("--app-front", action="store_true", help="先把剪映切到前台")
    args = ap.parse_args(argv)

    if args.app_front:
        run(["osascript", "-e",
             'tell application "System Events" to set frontmost of process "%s" to true' % APP])
        time.sleep(1.5)

    rows = ax_tree()
    panel, title = panel_window(rows)
    if not panel:
        print("找不到导出面板（AXDialog）。先在剪映里 文件→导出 或 Cmd+E 打开面板。", file=sys.stderr)
        return 2
    print("面板: %r  %dx%d @ %d,%d" % (title, panel[2], panel[3], panel[0], panel[1]))

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    result = {
        "panel_title": title,
        "panel_size": [panel[2], panel[3]],
        "app": APP,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": [],
    }

    for node_id, fr in controls(rows):
        label, value = read_row(fr, panel)
        cn = ID_LABEL.get(node_id, label)
        if only and cn not in only:
            continue
        opts, pr = [], None
        if cn in PROBE_ORDER or cn in ("音频格式", "音频码率"):
            opts, pr = read_options(fr, panel)
            label2, value2 = read_row(fr, panel)
            if value2:
                value = value2
        entry = {
            "ax_id": node_id,
            "label": cn,
            "panel_label_ocr": label,
            "value": value,
            "rel": rel(fr, panel),
            "abs_frame": list(fr),
            "options": [t for t, _ in opts],
            "option_rels": [dict(text=t, **rel(r, panel)) for t, r in opts if r],
        }
        result["params"].append(entry)
        print("\n[%s] %s = %r" % (node_id, cn, value))
        if opts:
            print("    选项: %s" % ", ".join(t for t, _ in opts))

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n已写入 %s（%d 个参数）" % (out, len(result["params"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
