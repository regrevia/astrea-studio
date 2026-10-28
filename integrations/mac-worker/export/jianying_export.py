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

# 面板辅助工具是编译出来的；缺了就现场补，别让导出卡在工具不存在上。
P.ensure_tools()

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
    """把残留的成功页/进度页关掉。

    11.6 的成功页（640x418）对 ESC 没反应，必须点它自己的「关闭」
    （AXButton automationcloseBtn）。AXPress 对它有 ~50% 概率静默失败，
    所以：先 AXPress，验证；不行再真点它的中心，再验证。
    """
    if not rect or not legacy_panel(rect):
        return True
    front()
    for attempt in ("axpress", "mouse", "esc"):
        tree = P.ax_tree()
        fr = None
        for r in tree:
            if len(r) >= 8 and (r[3] or "").strip() == "automationcloseBtn":
                fr = P.parse_frame(r[7])
                break
        if attempt == "axpress" and fr:
            P.press("automationcloseBtn")
        elif attempt == "mouse" and fr:
            x, y, w, h = fr
            P.run([P.AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
        elif attempt == "esc":
            P.run([P.AX_TOOL, "key", "esc"])
        else:
            continue
        time.sleep(0.9)
        r, _ = P.dialog_rect()
        if r is None:
            print("  （残留页已关闭：%s）" % attempt)
            return True
    print("  ⚠ 残留页关不掉，仍为 %r" % (P.dialog_rect(),), file=sys.stderr)
    return False


def open_panel(wait=10.0):
    """打开导出面板。已开就直接返回。

    AXPress 标题栏 `MainWindowTitleBarExportBtn` 实测**无效**（返回 err=0 但什么也不弹），
    所以走应用菜单：文件 → 导出（这条稳）。
    """
    rect, title = P.dialog_rect()
    if rect and legacy_panel(rect):
        print("  （发现上次导出的残留页，先关掉）")
        if not clear_legacy(rect):
            return None, None
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
def load_sidecar(draft_dir: str) -> dict:
    """读建草稿那边写下的 studio_export_meta.json。

    剪映 11.6 存盘后 draft_info.json 是密文，画布/时长/修订版身份只剩 sidecar 这一份；
    读不到就是没有真相，返回空字典让调用方明确失败，不要退回去猜。
    """
    if not draft_dir:
        return {}
    path = Path(draft_dir).expanduser() / "studio_export_meta.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


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
def click_export(rect, prefer_ax=False) -> bool:
    """点「导出」。

    剪映是 Qt 自绘，AXPress 只在 App 前台时才可能生效，而且"生效"和"返回 err=0"
    完全不是一回事（实测成功页的「关闭」也是 err=0 但毫无反应）。所以这里不在
    本函数里判断成败——只负责投递，由调用方用"产物有没有出现"来验证：
    prefer_ax=True 先走 AXPress（不动光标），没产物再回退真点击；
    实测 11.6 上 AXPress 投递成功但**从不触发导出**，所以默认 False，鼠标点击才是有效路径。
    """
    tree = P.ax_tree()
    btn = None
    for r in tree:
        if len(r) >= 8 and (r[3] or "").strip() == "ExportOkBtn":
            btn = P.parse_frame(r[7])
            break
    if not btn:
        return False
    if prefer_ax:
        P.dismiss(rect)                      # 先把鼠标移出面板，避免悬停态干扰
        if P.press("ExportOkBtn"):
            print("  投递 AXPress 到导出按钮（光标未移动）")
            return True
        print("  导出按钮没有 AXPress 动作，回退鼠标点击")
    x, y, w, h = btn
    P.dismiss(rect)
    P.run([P.AX_TOOL, "click-at", str(x + w // 2), str(y + h // 2)])
    print("  鼠标点击导出 %d,%d" % (x + w // 2, y + h // 2))
    return True


def poll_outputs(since: float, dirs, timeout=600, exts=(".mp4", ".mov")):
    """轮询导出目录，返回 mtime>=since 且大小已稳定的文件列表。

    只负责「有哪些新文件」，不做「挑最新的那个」——归属由调用方按任务判定：
    剪映的导出目录是全局共享的，凭 mtime 认领在并发导出时必然拿错片子。
    """
    deadline = time.time() + timeout
    seen: dict = {}
    while time.time() < deadline:
        for d in dirs:
            d = Path(d)
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() not in exts or not f.is_file():
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_mtime < since - 1 or st.st_size < 1024:
                    continue
                seen[f] = st.st_size
        if seen:
            time.sleep(1.2)
            stable = []
            for f, size in seen.items():
                try:
                    if f.exists() and f.stat().st_size == size:
                        stable.append(f)
                except OSError:
                    continue
            if stable:
                return sorted(stable)
        time.sleep(1.5)
    return []


def poll_output(since: float, dirs, timeout=600, exts=(".mp4", ".mov")):
    """兼容旧调用：只在恰好一个候选时返回它，多个候选一律当失败（不猜）。"""
    found = poll_outputs(since, dirs, timeout=timeout, exts=exts)
    return found[0] if len(found) == 1 else None


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


PROBE_REQUIRED = ("duration", "width", "height", "fps")


def validate(info: dict, expect: dict) -> dict:
    """产出体检：ffprobe 失败、必需字段缺失、检查项为空，三条都算失败。

    旧写法只在 expect 和 info 两边都有值时才建检查，于是
    `all({}.values())` 恒为 True——ffprobe 崩了、文件是坏包、时长读不出来，
    全都会被当成「校验通过」放行（G11）。这里先把缺失本身变成检查项。
    """
    checks: dict = {}
    if not info:
        return {"probe": {"ok": False, "detail": "ffprobe 没有任何输出"}}
    if info.get("error"):
        return {"probe": {"ok": False, "detail": "ffprobe 失败: %s" % info["error"]}}
    for key in PROBE_REQUIRED:
        val = info.get(key)
        ok = val is not None and val != "" and float(val) > 0
        # 全部检查项统一成 (ok, detail) 二元组：混着 dict 写，下面汇总时
        # 会拿 v[0] 去索引 dict，直接 KeyError: 0 把体检路径炸掉。
        checks["has_" + key] = (bool(ok), "实测 %r" % (val,))
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
    out = {}
    for k, v in checks.items():
        out[k] = {"ok": bool(v[0]), "detail": v[1]}
    if not out:
        return {"probe": {"ok": False, "detail": "没有可用的体检项（元数据和期望都空）"}}
    return out


# ------------------------------------------------------------------ 产物绑定
def plan_output(job_id: str, revision: int, platform: str, outdir,
                container: str = "mp4") -> tuple[Path, Path]:
    """产物落点只由 (jobId, revision, platform) 决定，跟导出目录里有什么文件无关。

    旧实现是「导出完去 ~/Movies 挑最新文件」——并发导出时必然拿错别人的片子，
    而且重跑一次就把上一版覆盖掉。这里先算死落点，跑完只往这里归位。
    """
    out_dir = Path(outdir).expanduser() / str(job_id) / ("r%d" % int(revision))
    final = out_dir / ("%s__r%d__%s.%s" % (
        job_id, int(revision), platform or "unknown",
        str(container or "mp4").lstrip(".")))
    return out_dir, final


def ensure_free(final: Path) -> bool:
    """目标已存在就不许覆盖：同一修订版重跑会打架，历史归档按修订版留着。"""
    return not Path(final).exists()


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="")
    ap.add_argument("--list-options", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="选参数但不点导出")
    ap.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    ap.add_argument("--job-id", default="",
                    help="任务 id（必填）：产物落到 outdir/<jobId>/r<rev>/；不接受按目录里最新文件认领")
    ap.add_argument("--revision", type=int, default=0,
                    help="修订版号；同一任务的上一版产物不允许被覆盖")
    ap.add_argument("--draft-path", default="",
                    help="草稿目录；给了就优先读它的 studio_export_meta.json（画布/时长/runId）")
    ap.add_argument("--expect-duration", type=float, default=0.0,
                    help="期望片长（秒）；缺省读草稿 sidecar 的 expectedDuration")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--canvas", default="", help="草稿画布 WxH；缺省读草稿目录的 studio_export_meta.json")
    ap.add_argument("--allow-aspect-mismatch", action="store_true",
                    help="比例与平台要求不符时仍然导出（默认拒绝，避免导出去被平台二次裁切）")
    ap.add_argument("--try-axpress", action="store_true",
                    help="实验：先试 AXPress 触发导出（实测 11.6 上无效，仅保留做回归）")
    ap.add_argument("--wait-idle", type=float, default=0.0,
                    help="动手前先等用户空闲这么多秒（0=不等）；适合挂后台队列")
    ap.add_argument("--no-restore-focus", action="store_true",
                    help="跑完不把前台 App 和光标还回去（默认会还）")
    ap.add_argument("--map-out", default=str(REPO / "docs/probe/export-panel/panel-map.json"))
    args = ap.parse_args(argv)

    # 时长必须在开面板之前读：面板是模态的，开着的时候编辑器的时间线节点可能不在 AX 树里
    dur = playhead_duration()
    # 任务绑定先落实，再谈动 GUI：没有 jobId 的产物只能靠「导出目录里最新的文件」认领，
    # 并发导出时必拿错别人的片子，所以这里直接拒绝，不去 ~/Movies 里猜。
    if not args.job_id:
        print("要 --job-id：产物必须绑到任务，不接受在导出目录里挑最新文件", file=sys.stderr)
        return 2
    # G12：等不到用户空闲时，旧实现照样抢前台、抢光标。现在超时就直接放弃这一轮，
    # 把活留在队列里等下一次，而不是把人正在打字的鼠标挪走。
    if not P.quiet_start(args.wait_idle, restore=not args.no_restore_focus, strict=True):
        print("排队：用户未空闲，这次不动鼠标、不导出", file=sys.stderr)
        return 9
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
    sidecar = load_sidecar(args.draft_path or str(DRAFT_ROOT / draft))
    if not canvas and sidecar:
        cc = sidecar.get("canvas") or {}
        if cc.get("width") and cc.get("height"):
            canvas = (int(cc["width"]), int(cc["height"]))
            canvas_src = "sidecar:%s" % (args.draft_path or (DRAFT_ROOT / draft))
    if sidecar and args.revision:
        # 修订版号两边必须一致：拿 r1 的命令去导 r2 的草稿，产物会贴错标签
        want = str(sidecar.get("revision") or "").strip()
        if want.isdigit() and int(want) != args.revision:
            print("修订版对不上：草稿是 r%s，命令行给的是 r%d" % (want, args.revision),
                  file=sys.stderr)
            return 11
    if not args.expect_duration and sidecar.get("expectedDuration"):
        # 期望片长以建草稿时的 spec 为准，播放头读数只当兜底
        args.expect_duration = float(sidecar["expectedDuration"])
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

    # 产物路径在动手前就定死：跑完往这个位置归位，而不是跑完再想叫什么名字
    out_dir, final = plan_output(args.job_id, args.revision, args.platform,
                                 args.outdir, str(prof.get("container") or "mp4"))
    if not ensure_free(final):
        print("产物已存在，拒绝覆盖：%s\n  （新一版要换 --revision，历史归档按修订版留着）"
              % final, file=sys.stderr)
        return 8

    since = time.time()
    if not click_export(rect, prefer_ax=args.try_axpress):
        print("找不到导出按钮", file=sys.stderr)
        return 4
    print("\n已投递导出（先 AXPress），轮询产物 …")
    # 剪映只会往自己的导出目录写，所以这里只能看目录；但归属由 jobId 判定，
    # 一旦出现多个新文件就说明有并发导出在抢目录，直接失败而不是挑一个。
    cands = poll_outputs(since, [MOVIES], timeout=args.timeout)
    if not cands:
        print("超时没等到产物", file=sys.stderr)
        return 5
    if len(cands) > 1:
        print("导出目录里同时冒出 %d 个新文件，无法确定哪个属于本任务：" % len(cands),
              file=sys.stderr)
        for c in cands:
            print("  %s" % c, file=sys.stderr)
        print("  并发导出必须各用各的导出目录（剪映偏好设置里改默认导出位置）", file=sys.stderr)
        return 10
    out = cands[0]
    info = ffprobe(out)
    expect = {"duration": args.expect_duration or dur.get("seconds"),
              "width": prof["expect_dims"][0], "height": prof["expect_dims"][1],
              "fps": float(prof.get("fps") or 30)}
    checks = validate(info, expect)
    mb = (info.get("size") or 0) / 1048576.0
    print("\n产物: %s (%.2f MB)" % (out, mb))
    print("实际: %sx%s @ %sfps  %s  %.2fs  音频=%s" % (
        info.get("width"), info.get("height"), info.get("fps"), info.get("codec"),
        info.get("duration") or 0, info.get("audio_codec")))
    for k, v in checks.items():
        print("  校验 %-13s %s  %s" % (k, "OK" if v["ok"] else "FAIL", v["detail"]))
    failed = [k for k, v in checks.items() if not v["ok"]]
    if failed:
        print("\n产出体检没过（%s），不归档、不标记成片：%s"
              % (", ".join(failed), out), file=sys.stderr)
        return 6

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), str(final))
    ev_dir = EVIDENCE / args.job_id
    ev_dir.mkdir(parents=True, exist_ok=True)
    rec = {"jobId": args.job_id, "revision": args.revision,
           "draft": draft, "draftPath": args.draft_path or None,
           "platform": args.platform, "profile": prof,
           "panel": title, "params": evidence, "probe": info,
           "expect": expect, "checks": checks, "final": str(final),
           "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ev = ev_dir / ("r%d__%s.json" % (args.revision, args.platform))
    ev.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已归位 → %s\n证据 → %s" % (final, ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
