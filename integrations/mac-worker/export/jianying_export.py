#!/usr/bin/env python3
"""剪映导出闭环：打开导出面板 → 按平台选参数 → 点导出 → 轮询产物 → 校验。

链路（每一步都有可核对的证据，不靠"看起来成功了"）：
    AX 菜单「文件→导出」
      → 面板出现（AXDialog 标题 `导出-<草稿名>`）
      → 逐参数按平台 profile 选中，**每次都读回**确认
      → 点「导出」
      → 轮询导出目录拿到新文件（大小稳定才算写完）
      → ffprobe 校验 时长/分辨率/帧率
      → 归位改名到 --outdir

用法:
    python3 jianying_export.py --list-options          # 只盘点面板参数/选项，不导出
    python3 jianying_export.py --platform douyin       # 真导出
    python3 jianying_export.py --platform bilibili --dry-run   # 选参数但不点导出
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import panel as P  # noqa: E402

try:
    from platform_profiles import profile_for, resolution_option, fps_option
except Exception:                                    # 平台矩阵未就绪时的兜底
    profile_for = resolution_option = fps_option = None

MOVIES = Path(os.path.expanduser("~/Movies"))
DRAFT_ROOT = Path(os.path.expanduser(
    "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft"))
REPO = Path(__file__).resolve().parents[3]
DEFAULT_OUTDIR = Path(os.path.expanduser("~/CreatorStudio/out"))
EVIDENCE = REPO / "docs/probe/export-panel/runs"
TIERS = {"480P": (854, 480), "720P": (1280, 720), "1080P": (1920, 1080),
         "2K": (2560, 1440), "4K": (3840, 2160), "8K": (7680, 4320)}


# ------------------------------------------------------------------ 通用
def sh(*cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def front() -> None:
    sh("osascript", "-e",
       'tell application "System Events" to set frontmost of process "%s" to true' % P.APP)
    time.sleep(2.2)          # 实测：set frontmost 立刻返回 true，但真正到前台要 ~2.5s


def playhead_duration() -> dict:
    """从编辑器读总时长（`totalProgress|00:00:19:00`）和帧率。

    注意 ax_tool 的列布局是: depth, role, subrole, title, desc, help, name, frame, actions
    —— 时间字符串在 **desc** 列（不是 title），所以这里扫所有列找 `totalProgress|`。
    """
    info = {"raw": None, "seconds": None, "fps": None}
    for r in P.ax_tree():
        for col in r:
            col = (col or "").strip()
            if col.startswith("totalProgress|"):
                info["raw"] = col.split("|", 1)[1]
            if col.startswith("fps|"):
                try:
                    info["fps"] = float(col.split("|", 1)[1])
                except ValueError:
                    pass
    raw = info["raw"]
    if raw:
        parts = [int(p) for p in raw.split(":")] if all(p.isdigit() for p in raw.split(":")) else []
        if len(parts) == 4:
            hh, mm, ss, ff = parts
            fps = info["fps"] or 30.0
            info["seconds"] = hh * 3600 + mm * 60 + ss + ff / max(fps, 1)
    return info


def legacy_panel(rect) -> bool:
    """当前"面板"是不是上一次导出的残留（导出成功页/进度页），而不是参数面板。

    判据：**没有任何 *Input 控件**。成功页只有 打开文件夹/关闭/发布，尺寸也更矮
    （实测 640x418 vs 参数面板 640x720 或 640x418 收起态——所以不能靠尺寸判断）。
    """
    return not P.controls(P.ax_tree())


def clear_legacy(rect) -> bool:
    """把残留的成功页/进度页关掉。只在确认无参数控件时用 ESC（此时 ESC 关的是它本身）。"""
    if not rect or not legacy_panel(rect):
        return True
    P.run([P.AX_TOOL, "key", "esc"])
    time.sleep(0.8)
    r, _ = P.dialog_rect()
    return r is None


def open_panel(wait=10.0):
    """打开导出面板。已开就直接返回。

    AXPress 标题栏 `MainWindowTitleBarExportBtn` 实测**无效**（返回 err=0 但什么也不弹），
    所以走应用菜单：文件 → 导出（这条稳）。
    """
    rect, title = P.dialog_rect()
    if rect and legacy_panel(rect):
        print("  （发现上次导出的残留页，先关掉）")
        clear_legacy(rect)
        rect = None
    if rect:
        return rect, title
    front()
    sh("osascript", "-e",
       'tell application "System Events" to tell process "%s" to click menu bar item "文件" of menu bar 1'
       % P.APP)
    time.sleep(0.7)
    sh("osascript", "-e",
       'tell application "System Events" to tell process "%s" to click menu item "导出" '
       'of menu 1 of menu bar item "文件" of menu bar 1' % P.APP)
    deadline = time.time() + wait
    while time.time() < deadline:
        rect, title = P.dialog_rect()
        if rect:
            return rect, title
        time.sleep(0.4)
    return None, None


# ------------------------------------------------------------------ 画布/档位
def draft_canvas(name: str, explicit: str = ""):
    """草稿画布 (w,h) 及来源。优先 sidecar（剪映保存后草稿 JSON 是密文，读不了）。"""
    if explicit:
        m = re.match(r"^(\d+)x(\d+)$", explicit.strip(), re.I)
        if m:
            return (int(m.group(1)), int(m.group(2))), "argv"
    side = DRAFT_ROOT / name / "studio_export_meta.json"
    if side.exists():
        try:
            d = json.loads(side.read_text(encoding="utf-8"))
            cc = d.get("canvas") or {}
            if cc.get("width") and cc.get("height"):
                return (int(cc["width"]), int(cc["height"])), str(side)
        except Exception:
            pass
    return None, None


def aspect_ratio(w: int, h: int) -> float:
    return (w / h) if h else 0.0


def clamp_tier(label: str, canvas):
    """档位不能超过画布：宁可比画布小，也不要放大（放大只是插值，白占体积）。"""
    cw, ch = canvas
    long_side, short_side = max(cw, ch), min(cw, ch)
    order = ["480P", "720P", "1080P", "2K", "4K", "8K"]
    want = order.index(label) if label in order else order.index("1080P")
    best = order[0]
    for name in order[:want + 1]:
        tw, th = TIERS[name]
        if max(tw, th) <= long_side and min(tw, th) <= short_side:
            best = name
    return best


def expected_dims(tier: str, canvas):
    """档位 × 画布方向 → 期望像素。剪映的 1080P 是"短边 1080"，朝向跟画布走。"""
    tw, th = TIERS[tier]
    cw, ch = canvas
    return (max(tw, th), min(tw, th)) if cw >= ch else (min(tw, th), max(tw, th))


# ------------------------------------------------------------------ 参数
def show_rows(rect) -> list[dict]:
    rs = P.rows(rect)
    print("\n面板参数（真实读屏，x=相对面板）:")
    print("%-10s %-28s %-8s %s" % ("参数", "ax_id", "hidden", "值"))
    for r in sorted(rs, key=lambda r: r["y"]):
        rel = P.rel(r["rect"], rect)
        print("%-10s %-28s %-8s %-14r rel(%.3f,%.3f)" % (
            r["name"], r["ax_id"], "是" if r["hidden"] else "否",
            r["value"], rel["rx"], rel["ry"]))
    return rs


def list_options(rect) -> dict:
    """盘点：每个下拉的真实选项集（结果落地成面板地图）。"""
    rs = P.rows(rect)
    inv = {}
    for r in rs:
        if r["hidden"] or r["name"] in ("名称", "导出至"):
            continue
        opts, pop = P.read_options(r, rect)
        texts = []
        for o in opts:
            t = o["text"].strip()
            if t and t not in texts:
                texts.append(t)
        inv[r["name"]] = {"ax_id": r["ax_id"], "value": r["value"],
                          "rel": P.rel(r["rect"], rect), "options": texts}
        print("%-10s = %-12r  选项: %s" % (r["name"], r["value"], ", ".join(texts) or "(非下拉)"))
    return inv


def apply(rect, profile: dict, verbose=True) -> list[dict]:
    """把 profile 落到面板上。分辨率/格式/帧率/码率 逐个选中并读回。"""
    wants = []
    if profile.get("resolution"):
        wants.append(("分辨率", profile["resolution"]))
    if profile.get("container"):
        wants.append(("格式", profile["container"].upper()))
    if profile.get("fps"):
        wants.append(("帧率", str(profile["fps"])))
    if profile.get("bitrate_preset"):
        wants.append(("视频码率", profile["bitrate_preset"]))
    evidence = []
    for name, target in wants:
        ev = P.select(rect, name, target, verbose=verbose)
        if not ev.get("ok"):
            # 格式可能只接受大写/小写差异；再试一次小写
            alt = target.lower() if target.isupper() else target.upper()
            ev2 = P.select(rect, name, alt, verbose=verbose)
            if ev2.get("ok"):
                ev = ev2
        evidence.append(ev)
    return evidence


# ------------------------------------------------------------------ 导出
def click_export(rect) -> bool:
    """点「导出」。按钮有 AXPress，但 Qt 上不总生效，所以直接点矩形中心。"""
    tree = P.ax_tree()
    btn = None
    for r in tree:
        if len(r) >= 8 and (r[3] or "").strip() == "ExportOkBtn":
            btn = P.parse_frame(r[7])
            break
    if not btn:
        return False
    x, y, w, h = btn
    P.dismiss(rect)
    P.run([P.AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
    return True


def poll_output(since: float, dirs, timeout=600, exts=(".mp4", ".mov")):
    """轮询导出目录，等一个 mtime>=since 的文件大小稳定下来。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        best = None
        for d in dirs:
            d = Path(d)
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() not in exts or not f.is_file():
                    continue
                st = f.stat()
                if st.st_mtime < since - 1:
                    continue
                if st.st_size < 1024:
                    continue
                if best is None or st.st_mtime > best[0]:
                    best = (st.st_mtime, f, st.st_size)
        if best:
            mtime, f, size = best
            time.sleep(1.2)
            if f.exists() and f.stat().st_size == size and size > 1024:
                return f
        time.sleep(1.5)
    return None


def ffprobe(path) -> dict:
    p = sh("ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path), timeout=120)
    try:
        data = json.loads(p.stdout or "{}")
    except Exception:
        return {"error": (p.stderr or "ffprobe failed").strip()[:300]}
    out = {"path": str(path), "format": (data.get("format") or {}).get("format_name"),
           "duration": float((data.get("format") or {}).get("duration") or 0),
           "size": int((data.get("format") or {}).get("size") or 0)}
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and "width" not in out:
            out.update(width=s.get("width"), height=s.get("height"),
                       codec=s.get("codec_name"), pix_fmt=s.get("pix_fmt"))
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            try:
                out["fps"] = round(float(num) / float(den or 1), 3)
            except Exception:
                out["fps"] = None
        elif s.get("codec_type") == "audio" and "audio_codec" not in out:
            out.update(audio_codec=s.get("codec_name"), audio_rate=s.get("sample_rate"),
                       audio_channels=s.get("channels"))
    return out


def validate(info: dict, expect: dict) -> dict:
    checks = {}
    if expect.get("duration") and info.get("duration"):
        tol = max(0.4, 2.0 / max(expect.get("fps") or 30, 1))
        checks["duration"] = (abs(info["duration"] - expect["duration"]) <= tol,
                              "%.2fs vs 期望 %.2fs (±%.2f)" % (info["duration"], expect["duration"], tol))
    if expect.get("width") and info.get("width"):
        checks["resolution"] = ((info["width"], info["height"]) == (expect["width"], expect["height"]),
                                "%dx%d vs 期望 %dx%d" % (info["width"], info["height"],
                                                         expect["width"], expect["height"]))
    if expect.get("fps") and info.get("fps"):
        checks["fps"] = (abs(info["fps"] - expect["fps"]) < 0.06,
                         "%.3f vs 期望 %.3f" % (info["fps"], expect["fps"]))
    return {k: {"ok": v[0], "detail": v[1]} for k, v in checks.items()}


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="")
    ap.add_argument("--list-options", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="选参数但不点导出")
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--canvas", default="", help="草稿画布 WxH；缺省读草稿目录的 studio_export_meta.json")
    ap.add_argument("--allow-aspect-mismatch", action="store_true",
                    help="比例与平台要求不符时仍然导出（默认拒绝，避免导出去被平台二次裁切）")
    ap.add_argument("--map-out", default=str(REPO / "docs/probe/export-panel/panel-map.json"))
    args = ap.parse_args(argv)

    # 时长必须在开面板之前读：面板是模态的，开着的时候编辑器的时间线节点可能不在 AX 树里
    dur = playhead_duration()
    rect, title = open_panel()
    if not rect:
        print("打不开导出面板（需要剪映在前台且草稿已打开）", file=sys.stderr)
        return 2
    print("面板: %r  %dx%d @ %d,%d  相对坐标基准 = 此窗口" % (title, rect[2], rect[3], rect[0], rect[1]))
    draft = (title or "").replace(P.PANEL_PREFIX, "").strip()
    print("草稿时长: %s（%.2fs）" % (dur.get("raw"), dur.get("seconds") or 0))

    if args.list_options:
        inv = list_options(rect)
        P.snapshot(rect, args.map_out)
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        p = EVIDENCE / "panel-options.json"
        p.write_text(json.dumps({"panel": title, "draft": draft,
                                 "duration": dur, "inventory": inv},
                                ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n面板地图 → %s\n选项盘点 → %s" % (args.map_out, p))
        return 0

    if not args.platform:
        print("要 --platform（或 --list-options）", file=sys.stderr)
        return 2
    if profile_for is None:
        print("platform_profiles.py 还没就绪，只能 --list-options", file=sys.stderr)
        return 2

    prof_raw = profile_for(args.platform)
    prof = prof_raw.as_dict() if hasattr(prof_raw, "as_dict") else dict(prof_raw)

    canvas, canvas_src = draft_canvas(draft, args.canvas)
    if not canvas:
        side = DRAFT_ROOT / draft / "studio_export_meta.json"
        print("不知道草稿画布：%s 不存在，也没给 --canvas WxH" % side, file=sys.stderr)
        print("  （剪映 11.6 保存后会把 draft_info.json 加密，读不出画布；"
              "所以必须由建草稿的一方写 sidecar）", file=sys.stderr)
        return 2
    print("草稿画布: %dx%d  来源=%s" % (canvas[0], canvas[1], canvas_src))

    ca = aspect_ratio(*canvas)
    pa = aspect_ratio(prof["width"], prof["height"])
    if pa and abs(ca - pa) / pa > 0.01:
        msg = ("比例冲突：草稿 %dx%d (%.3f) ≠ %s 要求 %s (%dx%d, %.3f)。\n"
               "  导出面板只能缩放、不能改比例（面板原文：以草稿尺寸（%dx%d）导出），\n"
               "  因此要么按 %s 重建草稿画布，要么换一个接受当前比例的平台。") % (
            canvas[0], canvas[1], ca, prof.get("label") or args.platform,
            prof.get("aspect"), prof["width"], prof["height"], pa,
            canvas[0], canvas[1], prof.get("aspect"))
        if not args.allow_aspect_mismatch:
            print(msg, file=sys.stderr)
            return 7
        print("⚠ " + msg + "\n  （--allow-aspect-mismatch 放行；产物比例与平台要求不符）")

    want_tier = str(resolution_option(prof["width"], prof["height"]))
    tier = clamp_tier(want_tier, canvas)
    exp_w, exp_h = expected_dims(tier, canvas)
    if tier != want_tier:
        print("  档位下调 %s → %s（画布 %dx%d 不够，不放大）" % (want_tier, tier, canvas[0], canvas[1]))
    prof["resolution"] = tier
    prof["expect_dims"] = [exp_w, exp_h]
    prof["fps"] = str(fps_option(prof["fps"]))
    prof["format"] = str(prof.get("container") or "mp4").upper()
    print("\n平台 %s → 名义目标 %sx%s @ %sfps %s；实际导出 %dx%d（%s 档 × 草稿比例）" % (
        prof.get("label") or args.platform, prof["width"], prof["height"], prof["fps"],
        prof["format"], exp_w, exp_h, tier))
    print("  落到面板: 分辨率=%s 格式=%s 帧率=%s 视频码率=%s" % (
        tier, prof["format"], prof["fps"], prof.get("bitrate_preset")))

    evidence = apply(rect, prof)
    if args.dry_run:
        print("\n--dry-run：参数已选，不点导出")
        return 0 if all(e.get("ok") for e in evidence) else 3
    if not all(e.get("ok") for e in evidence):
        print("\n参数没全部落上，中止导出（避免导错规格）", file=sys.stderr)
        for e in evidence:
            if not e.get("ok"):
                print(json.dumps(e, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3

    since = time.time()
    if not click_export(rect):
        print("找不到导出按钮", file=sys.stderr)
        return 4
    print("\n已点导出，轮询产物 …")
    out = poll_output(since, [MOVIES], timeout=args.timeout)
    if not out:
        print("超时没等到产物", file=sys.stderr)
        return 5
    info = ffprobe(out)
    expect = {"duration": dur.get("seconds"), "width": prof["expect_dims"][0],
              "height": prof["expect_dims"][1], "fps": float(prof.get("fps") or 30)}
    checks = validate(info, expect)
    mb = (info.get("size") or 0) / 1048576.0
    print("\n产物: %s (%.2f MB)" % (out, mb))
    print("实际: %sx%s @ %sfps  %s  %.2fs  音频=%s" % (
        info.get("width"), info.get("height"), info.get("fps"), info.get("codec"),
        info.get("duration") or 0, info.get("audio_codec")))
    for k, v in checks.items():
        print("  校验 %-11s %s  %s" % (k, "OK" if v["ok"] else "FAIL", v["detail"]))

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    final = Path(args.outdir) / ("%s__%s__%sx%s.%s" % (
        draft, args.platform, info.get("width"), info.get("height"), out.suffix.lstrip(".")))
    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), str(final))
    rec = {"draft": draft, "platform": args.platform, "profile": prof,
           "panel": title, "params": evidence, "probe": info,
           "expect": expect, "checks": checks, "final": str(final),
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ev = EVIDENCE / ("%s__%s.json" % (draft, args.platform))
    ev.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已归位 → %s\n证据 → %s" % (final, ev))
    return 0 if all(v["ok"] for v in checks.values()) else 6


if __name__ == "__main__":
    sys.exit(main())
