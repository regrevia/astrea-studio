#!/usr/bin/env python3
"""剪映「导出」面板的低层原语：定位、读行、开关弹层、按标签选值。

为什么要有这一层（全部来自实测，别改回"猜坐标"）：
  * 面板是 Qt 自绘，**值文本不进 AX**；但每个参数有一个稳定 automation id
    节点和精确矩形（``ExportSharpnessInput`` 等）。所以行识别 = AX 给
    id+矩形，OCR 给同一 y 上的标签和值 —— 两者按 y 对齐，不按我脑子里的顺序。
    踩过的坑：把 ``ExportEncoderInput`` 当成「编码」行，其实那一行在某些格式下
    **根本不渲染**，硬按 id→标签映射会把值读串。
  * 弹层是 layer>=1 的独立窗口，选项文字虽然也在 AX 里，但**点选没有 AXPress**，
    必须鼠标点。点完必须**确认弹层真的关了**：踩过的坑是 ESC 没生效，下一次
    点击落进上一个还开着的弹层，把分辨率从 1080P 改成了 480P。
  * 窗口尺寸/位置每次都变（面板 640x720 @ 960,330，编辑器 2560x1320），
    所以对外只存**相对面板的坐标**，运行时 ``to_abs()`` 换算。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

APP = "VideoFusion-macOS"
PANEL_PREFIX = "导出-"
AX_TOOL = os.environ.get("AX_TOOL", "/tmp/ax_tool")
OCR_TOOL = os.environ.get("OCR_TOOL", "/tmp/ocr")
WINLIST = os.environ.get("WINLIST", "/tmp/winlist")

# AX id → 我们内部的参数名。这里只做"命名"，不代表面板顺序。
ID_NAME = {
    "ExportFileNameInput": "名称",
    "ExportPathInput": "导出至",
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
NAME_ID = {v: k for k, v in ID_NAME.items()}

# OCR 认繁体/错字是常态，比对前统一收一下
_FOLD = str.maketrans({
    "辦": "辨", "標": "标", "頻": "频", "視": "视", "碼": "码", "導": "导",
    "幀": "帧", "質": "质", "採": "采", "樣": "样", "聲": "声", "時": "时",
})


def fold(s: str) -> str:
    return re.sub(r"[\s\|·•,，:：]", "", (s or "").translate(_FOLD))


def run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def is_front() -> bool:
    p = run(["osascript", "-e",
             'tell application "System Events" to get frontmost of process "%s"' % APP])
    return p.stdout.strip() == "true"


def front(wait=2.2) -> bool:
    """把剪映抬到前台。`set frontmost` 立刻返回 true，但真正生效要 ~2.5s，必须等。"""
    run(["osascript", "-e",
         'tell application "System Events" to set frontmost of process "%s" to true' % APP])
    time.sleep(wait)
    return is_front()


def ensure_front(wait=2.2) -> bool:
    if is_front():
        return True
    return front(wait)


def parse_frame(text: str):
    """'1408,660 176,22' / '1356,586 280x448' → (x, y, w, h)"""
    m = re.match(r"^(-?\d+),(-?\d+)\s+(\d+)[,x](\d+)$", (text or "").strip())
    return tuple(int(v) for v in m.groups()) if m else None


# ---------------------------------------------------------------- AX
def ax_tree(depth: int = 9):
    p = run([AX_TOOL, "tree", APP, str(depth)], timeout=30)
    return [ln.split("\t") for ln in p.stdout.splitlines()
            if ln.strip() and not ln.startswith("---")]


def dialog_rect(tree=None):
    """导出对话框窗口矩形；返回 (rect, title) 或 (None, None)。

    坑：剪映会冒出 195x20 @ 10,48 这类 subrole=AXDialog 的**菜单/提示浮层**，
    如果只按 subrole 匹配就会把它当成导出面板（实测踩过，后续全部行都读空）。
    所以先按"像对话框的尺寸"过一遍，再要求 AXDialog 或标题以「导出」开头。
    """
    cand = []
    for r in tree if tree is not None else ax_tree():
        if len(r) < 8 or r[0] != "0" or r[1] != "AXWindow":
            continue
        fr = parse_frame(r[7])
        if not fr:
            continue
        x, y, w, h = fr
        if w < 300 or h < 180:
            continue
        title = r[3] or ""
        if (r[2] or "") == "AXDialog":
            cand.append((0, fr, title))
        elif title.startswith(PANEL_PREFIX):
            cand.append((1, fr, title))
    if not cand:
        return None, None
    cand.sort(key=lambda c: c[0])
    return cand[0][1], cand[0][2]


CLICKABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*Input$")


def controls(tree):
    """面板内的参数控件 {ax_id: rect}。"""
    out = {}
    for r in tree:
        if len(r) < 8:
            continue
        nid = (r[3] or r[4] or "").strip()
        if not nid or not CLICKABLE.match(nid):
            continue
        fr = parse_frame(r[7])
        if fr:
            out.setdefault(nid, fr)
    return out


def press(needle: str, index: int = 0) -> bool:
    """对某个 AX 节点做 AXPress（按钮类走这条，不抢鼠标）。"""
    p = run([AX_TOOL, "click", APP, needle, "--via", "ax", "--index", str(index)])
    return p.returncode == 0


# ---------------------------------------------------------------- 截屏 + OCR
def capture(rect, path="/tmp/_cap.png"):
    # 只有剪映在前台时，-R 区域截图才拿到面板本身；否则拍到的是盖在上面的窗口
    ensure_front()
    x, y, w, h = rect
    run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x, y, w, h), path], timeout=15)
    return path


def ocr(png, ox=0, oy=0, min_conf=0.25):
    p = run([OCR_TOOL, png, "--json", "--min-conf", "0.2"], timeout=40)
    try:
        items = json.loads(p.stdout or "[]")
    except Exception:
        return []
    out = []
    for it in items:
        if it.get("conf", 0) < min_conf:
            continue
        it["x"] += ox
        it["y"] += oy
        out.append(it)
    return out


# ---------------------------------------------------------------- 行模型
def rows(panel_rect, tree=None):
    """把面板读成行：[{name, ax_id, label, value, y, rect, hidden}]。

    行 = AX 控件；标签/值 = 同一 y(±14px) 上的 OCR 文字。
    同一 y 上没有标签 → 这一行当前没渲染(hidden=True)，值一律不认。
    """
    px, py, pw, ph = panel_rect
    ctrls = controls(tree if tree is not None else ax_tree())
    capture(panel_rect)
    items = ocr("/tmp/_cap.png", px, py)
    # 值列左边界：实测值为 x≈0.70w（1408/640→0.70），标签在 0.56w。
    # 所以"值列左边的一切"都算标签 —— 不要再用固定左边界，否则长标签会被丢掉。
    value_x = px + pw * 0.66
    out = []
    for ax_id, rect in sorted(ctrls.items(), key=lambda kv: kv[1][1]):
        x, y, w, h = rect
        cy = y + h / 2
        same = [i for i in items if abs((i["y"] + i["h"] / 2) - cy) <= 14]
        left = [i for i in same if i["x"] < value_x]
        right = [i for i in same if i["x"] >= value_x]
        label = " ".join(i["text"] for i in sorted(left, key=lambda i: i["x"])).strip()
        value = " ".join(i["text"] for i in sorted(right, key=lambda i: i["x"])).strip()
        out.append({
            "name": ID_NAME.get(ax_id, ax_id),
            "ax_id": ax_id,
            "label": label,
            "value": value,
            "y": y,
            "rect": rect,
            "hidden": not label,
        })
    return out


def row_of(rows_, name: str):
    key = fold(name)
    for r in rows_:
        if fold(r["name"]) == key or fold(r["label"]) == key:
            return r
    for r in rows_:                      # 宽松：包含即可
        if key and (key in fold(r["label"]) or key in fold(r["name"])):
            return r
    return None


# ---------------------------------------------------------------- 弹层
def popups():
    """当前 **在屏** 的 layer>=1 剪映窗口（下拉弹层/右键菜单）。

    过滤条件都是踩出来的：只认 onscreen=1（离屏的历史窗口会永久留在 CGWindowList 里，
    误判会让后面的"关弹层"把整个面板 ESC 掉）；只认无标题窗口（像 `版本更新`
    这种命名窗口是应用自己的浮层，不是下拉）。
    """
    p = run([WINLIST, "--all"])
    out = []
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or parts[3] != "剪映专业版":
            continue
        try:
            layer = int(parts[1])
        except ValueError:
            continue
        if layer < 1 or parts[2] != "1" or (parts[4] or "").strip():
            continue
        fr = parse_frame(parts[5])
        if fr and fr[3] >= 40:
            out.append(fr)
    return out


def popup_near(rect):
    """紧贴某个控件、且在屏的弹层；没有则 None。

    面板靠底部的行（音频格式/码率）弹层会**向上**开，所以判据要覆盖两种方向。
    """
    x, y, w, h = rect
    for px, py, pw, ph in popups():
        dx_ok = abs(px - x) < 240
        below = -20 <= (py - y) <= h + 20
        above = -20 <= ((y + h) - (py + ph)) <= h + 20
        if dx_ok and (below or above) and pw > 80:
            return (px, py, pw, ph)
    return None


def dismiss(panel_rect, tries=3) -> bool:
    """收起弹层但**不关面板**。

    为什么不用 ESC：ESC 在模态导出面板里既是"关键盘弹层"又是"取消导出"，
    实测会把整个面板关掉，于是后续参数全变成"非下拉"，看起来像面板不支持。
    所以用"点面板标题条空白处"让焦点移出，弹层自然收起。
    """
    if not popups():
        return True
    px, py, pw, ph = panel_rect
    for _ in range(tries):
        run([AX_TOOL, "click-at", str(px + int(pw * 0.5)), str(py + int(ph * 0.035))])
        time.sleep(0.45)
        if not popups():
            return True
    return False


def close_popups(panel_rect=None, tries=4) -> bool:
    """兼容旧调用名；有面板矩形时走 dismiss（安全），否则才 ESC。"""
    if panel_rect:
        return dismiss(panel_rect, tries)
    for _ in range(tries):
        if not popups():
            return True
        run([AX_TOOL, "key", "esc"])
        time.sleep(0.35)
    return not popups()


def read_options(row, panel_rect):
    """点开一个下拉，读出选项（文字+屏幕矩形），再关掉弹层。不改变选择。"""
    x, y, w, h = row["rect"]
    dismiss(panel_rect)
    run([AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
    deadline = time.time() + 2.5
    pop = None
    while time.time() < deadline:
        pop = popup_near(row["rect"])
        if pop:
            break
        time.sleep(0.2)
    if not pop:
        dismiss(panel_rect)
        return [], None
    opts = options_from_ax(pop)
    dismiss(panel_rect)
    return opts, (pop if not popups() else None)


def options_from_ax(pop_rect):
    """弹层里的选项文字：直接读 AX（每个选项都是带 frame 的 AXStaticText）。

    为什么不用 OCR：弹层窗口有透明留白，按窗口矩形截图会把**下方参数行**一起拍进来，
    实测出现过"格式的选项 = MOV, 30, 更低, H.264"这种串行。AX 只返回真实选项节点。
    """
    px, py, pw, ph = pop_rect
    out = []
    for r in ax_tree():
        if len(r) < 8 or r[1] != "AXStaticText":
            continue
        txt = (r[6] or r[3] or "").strip()
        fr = parse_frame(r[7])
        if not txt or not fr:
            continue
        x, y, w, h = fr
        cx, cy = x + w / 2, y + h / 2
        if px - 2 <= cx <= px + pw + 2 and py - 2 <= cy <= py + ph + 2:
            out.append({"text": txt, "rect": fr})
    # 同一文字只留最上面那个（弹层里不会重复，重复说明是别处的同名节点）
    seen, uniq = set(), []
    for o in sorted(out, key=lambda o: (o["rect"][1], o["rect"][0])):
        key = (o["text"], round(o["rect"][1] / 4))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(o)
    return uniq


def _norm_opt(s: str) -> str:
    return fold(s).replace("*", "×").replace("~", "×")


def match_option(opts, target: str):
    """在选项里找目标：全等 → 前缀 → 归一化包含（'1280×720' vs '1280*720'）。"""
    tnorm = _norm_opt(target)
    for o in opts:
        if o["text"] == target:
            return o
    for o in opts:
        if _norm_opt(o["text"]) == tnorm:
            return o
    for o in opts:
        if tnorm and tnorm in _norm_opt(o["text"]):
            return o
    return None


def select(panel_rect, name: str, target: str, verbose=True) -> dict:
    """选一个参数值，返回带前后值的证据。失败不抛异常，让调用方决定。"""
    res = {"param": name, "target": target, "ok": False}
    tree = ax_tree()
    rs = rows(panel_rect, tree)
    row = row_of(rs, name)
    if not row:
        res["error"] = "面板里没有这一行"
        return res
    if row["hidden"]:
        res["error"] = "该行当前未渲染（可能被格式/会员开关隐藏）"
        res["before"] = row["value"]
        return res
    res["ax_id"] = row["ax_id"]
    res["before"] = row["value"]
    if _norm_opt(row["value"]) == _norm_opt(target):
        res.update(ok=True, after=row["value"], note="已是目标值")
        return res

    x, y, w, h = row["rect"]
    dismiss(panel_rect)
    run([AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
    deadline = time.time() + 2.5
    pop = None
    while time.time() < deadline:
        pop = popup_near(row["rect"])
        if pop:
            break
        time.sleep(0.2)
    if not pop:
        dismiss(panel_rect)
        res["error"] = "下拉没打开"
        return res

    opts = options_from_ax(pop)
    res["options"] = [o["text"] for o in opts]
    hit = match_option(opts, target)
    if not hit:
        dismiss(panel_rect)
        res["error"] = "选项里没有 %r" % target
        return res

    ox, oy, ow, oh = hit["rect"]
    run([AX_TOOL, "click-at", str(ox + ow // 2), str(oy + oh // 2)])
    closed = dismiss(panel_rect)
    res["popup_closed"] = closed
    time.sleep(0.4)
    after = row_of(rows(panel_rect), name)
    res["after"] = after["value"] if after else None
    res["ok"] = bool(after) and _norm_opt(after["value"]) == _norm_opt(target)
    if verbose:
        print("  %s: %r → %r  %s" % (name, res.get("before"), res.get("after"),
                                     "OK" if res["ok"] else "FAIL"))
    return res


# ---------------------------------------------------------------- 相对坐标
def rel(rect, panel):
    px, py, pw, ph = panel
    x, y, w, h = rect
    return {"rx": round((x - px) / pw, 5), "ry": round((y - py) / ph, 5),
            "rw": round(w / pw, 5), "rh": round(h / ph, 5)}


def to_abs(r, panel):
    px, py, pw, ph = panel
    return (int(round(px + r["rx"] * pw)), int(round(py + r["ry"] * ph)),
            int(round(r["rw"] * pw)), int(round(r["rh"] * ph)))


def snapshot(panel_rect, out_path=None):
    """当前面板的参数地图（相对坐标），可直接落盘当基线。"""
    rs = rows(panel_rect)
    data = {
        "app": APP,
        "panel_size": [panel_rect[2], panel_rect[3]],
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "params": [{
            "name": r["name"], "ax_id": r["ax_id"], "label": r["label"],
            "value": r["value"], "hidden": r["hidden"],
            "rel": rel(r["rect"], panel_rect),
        } for r in rs],
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data
