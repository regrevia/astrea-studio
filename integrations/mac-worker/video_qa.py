"""成片体检：出片之后先自检一遍，长出问题的片段不进交付。

只查能机器判定、且用户一眼看得出毛病的三件事：
时长对不对、有没有整帧发黑/发灰的空镜、每个镜头该有的字和人物是不是都在。
文案不在这里查（见 copy_check.py），这里只盯画面。

用法：
    python -m video_qa <video.mp4> --spec <spec.json> [--json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"
STEP = 0.25
# 整帧标准差低于这个值，等于画面没有内容（黑场、纯色、还是导出的空镜）
FLAT_STD = 6.0
# 硬切：相邻两帧（低分辨率灰度）平均绝对差。真片子里逐帧差异最大 ~30（开场上升、结尾推近），
# 换成深色场景直接跳浅色场景会到 90 以上，45 这条线两边都留得住。
CUT_DIFF = 45.0
# 定格：1 秒窗口内几乎不变。带运镜的卡面每帧都有 ~2 的差，纯静态图才是 0。
FREEZE_DIFF = 0.4
FREEZE_WINDOW = 4
# 角色区定格：真人片子里角色区最长静默 1.25s（换姿势的叠化），2s 不动就是贴图了
CHAR_STATIC_DIFF = 0.3
CHAR_STATIC_WINDOW = 8
# 文字区、人物区：判断这两块有没有东西，比整帧更容易抓住「字没贴上」
TEXT_BOX = (124, 470, 1004, 1000)
CHAR_BOX = (280, 900, 1040, 1700)


def _frames(path: Path, start: float, end: float, fps: float) -> np.ndarray:
    out = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
         "-i", str(path), "-vf", f"fps={fps},scale=270:480", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(-1, 480, 270)


def _refine_cut(video: Path, around: float, span: float = 0.6, fps: float = 25.0) -> float:
    """体检按 4 fps 采样，硬切的秒数只能到 0.25s；在命中点附近按 25 fps 复核一次。
    人眼对齐转场时 0.25s 是看得出来的，报准一点才有用。"""
    start = max(0.0, around - span)
    frames = _frames(video, start, around + span, fps)
    if len(frames) < 2:
        return around
    diff = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2))
    return round(start + float(np.argmax(diff) + 1) / fps, 2)


def _box_std(frames: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    sx, sy = 270 / 1080, 480 / 1920
    part = frames[:, int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)]
    return part.reshape(len(part), -1).std(axis=1)


# 人物层采样步长：每 6 帧取一帧，20 秒的片子概 100 个点，够抓住一次闪断
LAYER_STRIDE = 6
# 人物发透：叠化时两边都乘权重，实心像素会整段掉到 0；亮过这个值的像素算实心
SOLID_MIN = 0.40
# 人物大小：全片轮廓高度极差。素材本身只差 0.2%，超过 3% 就是换屏换姿势时人在缩放
RESIZE_MAX = 1.03
# 片头片尾各自淡入淡出，不算缺陷
LAYER_GUARD = 1.0


def _char_layer_metrics(meta: dict) -> dict | None:
    """把人物层单独解一遍：发灰、忽大忽小这两件事在成片里被背景糊住，
    只有拆开这一层才量得准。"""
    ovs = [o for o in (meta.get("overlay") or [])
           if str(o.get("src", "")).endswith("char_layer.webm")]
    if not ovs:
        return None
    src = Path(str(ovs[0]["src"]))
    if not src.exists():
        return None
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames", "-of", "json", str(src)],
        capture_output=True, text=True, check=True).stdout
    info = (json.loads(probe).get("streams") or [{}])[0]
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if not w or not h:
        return None
    num, _, den = str(info.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(num or 0) else 0.0
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(src),
         "-vf", f"select='not(mod(n,{LAYER_STRIDE}))',format=rgba",
         "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True).stdout
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < w * h * 4:
        return None
    arr = arr[: (arr.size // (w * h * 4)) * w * h * 4].reshape(-1, h, w, 4)
    a = arr[..., 3]
    on = a > 8
    mass = on.sum(axis=(1, 2))
    mean = np.where(on, a, 0).sum(axis=(1, 2)) / np.maximum(1, mass)
    solid = ((a > 240) & on).sum(axis=(1, 2)) / np.maximum(1, mass)
    # 身高只按实心像素算：带 alpha 的 vp9 是有损的，头发丝和尾巴边缘每帧
    # 会有一两行在「半透明/全透明」之间晃，按 >8 量出来是 3% 的假抖动
    rows = (a > 128).any(axis=2)
    tall = rows.sum(axis=1)
    ts = np.arange(len(arr)) * LAYER_STRIDE / (fps or 30.0)
    keep = (ts > LAYER_GUARD) & (ts < (ts[-1] if len(ts) else 0) - LAYER_GUARD)
    if keep.sum() < 3:
        return None
    return {"mean": mean[keep], "solid": solid[keep], "tall": tall[keep],
            "t": ts[keep], "w": w, "h": h}


def _layer_findings(meta: dict, findings: list) -> None:
    got = _char_layer_metrics(meta)
    if not got:
        return
    mean, solid, tall, ts = got["mean"], got["solid"], got["tall"], got["t"]
    # 换姿态时新的一段本来就从半透明盖上去，那一瞬实心必然掉一半。
    # 连着一秒以上都透才是真的漏了底，所以只在「连着几帧都透」时才报。
    thin = np.where((mean < 150) | (solid < SOLID_MIN))[0]
    if thin.size:
        runs, cur = [], [int(thin[0])]
        for i in thin[1:]:
            if i - cur[-1] <= 1:
                cur.append(int(i))
            else:
                runs.append(cur); cur = [int(i)]
        runs.append(cur)
        thin = np.array([i for run in runs if len(run) >= 3 for i in run])
    for i in thin[:3]:
        findings.append({"code": "character_thin", "level": "warn",
                         "at": round(float(ts[i]), 2),
                         "detail": f"{ts[i]:.1f}s 人物发透（实心 {solid[i]:.2f}）"})
    live = tall[tall > 0]
    if live.size >= 3 and float(live.max()) / float(max(1, live.min())) > RESIZE_MAX:
        findings.append({"code": "character_resize", "level": "warn",
                         "detail": f"人物忽大忽小：身高 {int(live.min())}→{int(live.max())}"})


def check(video: Path, spec: Path) -> dict:
    meta = json.loads(spec.read_text(encoding="utf-8"))
    want = meta.get("meta", {}).get("duration") if isinstance(meta, dict) else None
    got = float(subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip())
    frames = _frames(video, 0, got, 1 / STEP)
    whole = frames.reshape(len(frames), -1).std(axis=1)
    text = _box_std(frames, TEXT_BOX)
    char = _box_std(frames, CHAR_BOX)
    findings = []
    # 收尾是主动压黑收的（最后一屏连人带卡一起淡出）。末尾这段不算缺陷，
    # 否则每条片子都会在最后一两帧被报「整帧发黑 + 硬切」。
    tail_guard = 0.45
    if want and abs(got - float(want)) > 0.35:
        findings.append({"code": "duration_mismatch", "level": "warn",
                         "detail": f"{got:.2f}s vs {float(want):.2f}s"})
    # 人物占宽：竖屏里缩成一张小贴纸，再准的手势也没人看见
    widths = [int(w) for w in ((meta.get("meta") or {}).get("char_widths") or []) if w]
    canvas_w = int(((meta.get("canvas") or {}).get("width")) or 1080)
    if widths and max(widths) < int(0.55 * canvas_w):
        findings.append({"code": "character_small", "level": "warn", "at": 0.0,
                         "detail": f"人物最大占宽 {max(widths)}px，不到画面六成，像贴纸"})
    # at 是秒数：界面上直接能跳到那一帧，不然只说「有问题」等于没说。
    for i in np.flatnonzero(whole < FLAT_STD)[:5]:
        at = float(i) * STEP
        if at >= got - tail_guard:
            continue
        findings.append({"code": "blank_frame", "level": "error",
                         "at": round(at, 2),
                         "detail": f"{at:.2f}s 整帧发黑"})
    # 卡面上有字的时间段，应该占满整条片子（叠化时也不该两块同时消失）
    for code, series, label in (("text_gap", text, "卡面没字"),
                                ("character_gap", char, "画面里没人")):
        if not len(series):
            continue
        miss = series < FLAT_STD
        if miss.mean() <= 0.03:
            continue
        at = float(np.flatnonzero(miss)[0]) * STEP
        findings.append({"code": code, "level": "warn", "at": round(at, 2),
                         "detail": f"{at:.1f}s 起{label}（{(miss.mean() * 100):.0f}% 的帧）"})
    # 转场与节奏：该叠化的地方硬切、该动的画面定格，屏幕上就是 PPT 翻页感
    if len(frames) > 1:
        diff = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2))
        for i in np.flatnonzero(diff > CUT_DIFF)[:3]:
            at = _refine_cut(video, float(i + 1) * STEP)
            if at >= got - tail_guard:
                continue
            findings.append({"code": "hard_cut", "level": "warn", "at": at,
                             "detail": f"{at:.2f}s 硬切"})
        if len(diff) >= FREEZE_WINDOW:
            quiet = diff < FREEZE_DIFF
            run = 0
            frozen: list[float] = []
            for i, q in enumerate(quiet):
                run = run + 1 if q else 0
                if run == FREEZE_WINDOW:
                    frozen.append(float(i - FREEZE_WINDOW + 1) * STEP)
            for at in frozen[:3]:
                findings.append({"code": "frozen", "level": "warn",
                                 "at": round(at, 2),
                                 "detail": f"{at:.1f}s 起画面定格"})
    # 角色是不是「贴图」：嘴和姿势不动，画面再花也是块贴纸
    if len(frames) > CHAR_STATIC_WINDOW:
        x0, y0, x1, y1 = CHAR_BOX
        sx, sy = 270 / 1080, 480 / 1920
        box = frames[:, int(y0 * sy):int(y1 * sy), int(x0 * sx):int(x1 * sx)].astype(np.int16)
        cdiff = np.abs(np.diff(box, axis=0)).mean(axis=(1, 2))
        run = 0
        stuck: list[float] = []
        for i, q in enumerate(cdiff < CHAR_STATIC_DIFF):
            run = run + 1 if q else 0
            if run == CHAR_STATIC_WINDOW:
                stuck.append(float(i - CHAR_STATIC_WINDOW + 1) * STEP)
        for at in stuck[:3]:
            findings.append({"code": "character_static", "level": "warn",
                             "at": round(at, 2),
                             "detail": f"{at:.1f}s 起角色不动"})
    # 角色手势有没有跟上这句话：姿势线索和实际姿势打架，就是「贴图」
    gesture = (meta.get("meta") or {}).get("gesture") if isinstance(meta, dict) else None
    if isinstance(gesture, dict):
        poses = gesture.get("poses") or []
        if int(gesture.get("scenes") or 0) >= 3 and len(poses) == 1:
            findings.append({"code": "gesture_flat", "level": "warn",
                             "detail": f"全片只有一种手势（{poses[0]}），像贴图"})
        checked = int(gesture.get("checked") or 0)
        clash = int(gesture.get("mismatch") or 0)
        late = [f for f in (gesture.get("switch") or []) if float(f) > 0.78]
        if late:
            findings.append({"code": "gesture_late", "level": "warn",
                             "detail": f"{len(late)} 屏的手势换得太晚（{late[0]:.2f}），后半句没动作"})
        if checked >= 2 and clash * 2 >= checked:
            at = (gesture.get("clash_at") or [None])[0]
            findings.append({"code": "gesture_offtext", "level": "warn", "at": at,
                             "detail": f"{clash}/{checked} 屏的手势和这句话对不上"})
    if isinstance(meta, dict):
        _layer_findings(meta, findings)
    return {"video": str(video), "duration": got, "findings": findings,
            "ok": not [f for f in findings if f["level"] == "error"]}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="成片体检")
    ap.add_argument("video")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check(Path(a.video), Path(a.spec))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
