#!/usr/bin/env python3
"""口型体检：量「嘴动」和「声音」到底是不是同一件事。

两条输入路径，出的指标是同一套：
  1) 有口型轨：--audio a.mp3 --states lip.json
     口型轨格式 {"fps": 50, "states": ["closed", "wide", ...], "t0": 0.0}
     （t0 可省；states 从 t0 起每 1/fps 秒一格）
  2) 只有成片：--video out.mp4 [--spec deploy/specs/envoy_real.json]
     在人物框里搜「嘴上那块会变红/变暗的像素」，逐帧量面积，再和音轨包络对齐。

为什么不是「看嘴有没有动」：嘴一直开合也可能一个字都没对上。这里量的是
**时间对齐**——互相关峰值的延迟。延迟为正 = 嘴早于声音，为负 = 嘴晚于声音。
另外量全长漂移：恒定偏移可以整体推一帧修掉，逐段增长的漂移修不掉，只能返工。

判定（与验收规范一致，见 docs/shorts/lipsync-paradigm.md）：
    |延迟| ≤ 60ms 可用，≤ 30ms 好；静音段（< -45dBFS）误开率 ≤ 5%；全长漂移 ≤ 60ms。

用法：
    /usr/bin/python3 tools/lipsync_qa.py --audio a.mp3 --states lip.json
    /usr/bin/python3 tools/lipsync_qa.py --video ~/CreatorStudio/deliver/envoy-f32.mp4 \
        --spec deploy/specs/envoy_real.json
    ... --json        给流水线读；任一项不过 exit 1（--no-strict 变 0）

只依赖 /usr/bin/python3 自带的 numpy 与系统 ffmpeg，不装包、不改引擎。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"

# —— 判定线 ——
LAG_USABLE_MS = 60.0     # |延迟| ≤ 60ms 算可用
LAG_GOOD_MS = 30.0       # |延迟| ≤ 30ms 算好
SILENT_FALSE_MAX = 0.05  # 静音段误开率上限 5%
DRIFT_MAX_MS = 60.0      # 全长漂移上限：和延迟同一把尺
DB_SILENT = -45.0        # 低于这个算静音
# 每 100ms 换档上限：口型最短驻留 80ms，一个 100ms 窗口里最多 1 次换档；
# 平均超过 1.25 次说明驻留已经是 50~80ms 级别，成片里读作「嘴在抖」。
SWITCH_PER_100MS_MAX = 1.25
MIN_DWELL_MS = 80.0      # 口型最短驻留：短于它观众读到的是抽动，不是一个字

# 8 档口型 → 开口度 0..1。数值只用来排序和算相关，不参与出片。
OPENNESS = {
    "closed": 0.00,
    "half": 0.35,
    "narrow": 0.40,
    "mid": 0.52,
    "round": 0.58,
    "pucker": 0.62,
    "teeth": 0.80,
    "open": 0.90,
    "wide": 1.00,
}
KNOWN = set(OPENNESS)

DENSE_HZ = 200.0   # 对齐用的重采样网格：5ms 一格，够分辨 30/60ms 两档
ENV_WIN_MS = 25.0  # 能量窗
WIN_S = 4.0        # 分段延迟用的窗口长度
HOP_S = 2.0        # 窗口步长


def _die(msg: str, code: int = 2) -> None:
    print(f"错误：{msg}", file=sys.stderr)
    raise SystemExit(code)


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, check=True).stdout
    data = json.loads(out)
    info = {"duration": float(data.get("format", {}).get("duration") or 0.0),
            "fps": 0.0, "width": 0, "height": 0, "audio": False}
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info["width"]:
            info["width"] = int(st.get("width") or 0)
            info["height"] = int(st.get("height") or 0)
            num, _, den = str(st.get("avg_frame_rate") or st.get("r_frame_rate") or "0/1").partition("/")
            try:
                info["fps"] = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                info["fps"] = 0.0
        elif st.get("codec_type") == "audio":
            info["audio"] = True
    if info["fps"] <= 0:
        info["fps"] = 30.0
    return info


def _audio_pcm(path: Path, rate: int = 16000) -> np.ndarray:
    """解码成单声道 float32。空音轨是盘点得出来的错，不要静默返回空数组。"""
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
         "-f", "f32le", "-"], capture_output=True, check=False)
    if raw.returncode != 0 or not raw.stdout:
        _die(f"音频解码失败：{path}（{raw.stderr.decode('utf-8', 'ignore')[:200]}）")
    return np.frombuffer(raw.stdout, dtype=np.float32).astype(np.float64)


def _envelope(pcm: np.ndarray, rate: int, fps: float) -> np.ndarray:
    """按帧算 RMS 再转 dBFS，每帧一个值（25ms 窗，帧中心对齐）。
    dB 用相对满刻度的 20log10(rms)；对 TTS 来说 -45 以下就是句间静音。"""
    win = max(1, int(rate * ENV_WIN_MS / 1000))
    hop = rate / fps
    n = int(np.floor(len(pcm) / hop)) if len(pcm) else 0
    out = np.zeros(max(n, 1), dtype=np.float64)
    for i in range(n):
        c = int(i * hop + hop / 2)
        seg = pcm[max(0, c - win // 2):max(0, c + win // 2)]
        rms = float(np.sqrt(np.mean(seg * seg))) if seg.size else 0.0
        out[i] = rms
    return 20.0 * np.log10(np.maximum(out, 1e-7))


def _load_states(path: Path) -> tuple[float, np.ndarray, float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die(f"口型轨不是合法 JSON：{path}：{exc}")
    states = raw.get("states")
    if not isinstance(states, list) or not states:
        _die(f"口型轨缺 states 数组：{path}")
    fps = float(raw.get("fps") or 50.0)
    if fps <= 0:
        _die(f"口型轨 fps 不合法：{raw.get('fps')!r}")
    unknown = sorted({str(s) for s in states} - KNOWN)
    if unknown:
        _die(f"口型轨里有没定义的档：{unknown}（只认 {sorted(KNOWN)}）")
    t0 = float(raw.get("t0") or 0.0)
    return fps, np.array([str(s) for s in states], dtype=object), t0


def _openness_of(states: np.ndarray) -> np.ndarray:
    return np.array([OPENNESS[s] for s in states], dtype=np.float64)


def _resample(src: np.ndarray, src_fps: float, dst_fps: float, t0: float = 0.0) -> np.ndarray:
    """线性重采样到对齐网格。src 的第 i 格代表 t0 + i/src_fps。"""
    if len(src) == 0:
        return np.zeros(1)
    t_src = t0 + np.arange(len(src)) / src_fps
    n_dst = max(2, int(round((t_src[-1] - t0) * dst_fps)) + 1)
    t_dst = t0 + np.arange(n_dst) / dst_fps
    return np.interp(t_dst, t_src, src)


def _xcorr(mouth: np.ndarray, energy: np.ndarray, lag_max_ms: float, step_ms: float = 5.0) -> dict:
    """r(L) = corr(嘴[t], 声[t+L])。L>0 表示嘴早于声音 L 毫秒。"""
    n = min(len(mouth), len(energy))
    mouth, energy = mouth[:n], energy[:n]
    if n < 2 or mouth.std() < 1e-9 or energy.std() < 1e-9:
        return {"lag_ms": 0.0, "r": 0.0, "r0": 0.0, "curve": []}
    mz = (mouth - mouth.mean()) / mouth.std()
    ez = (energy - energy.mean()) / energy.std()
    curve = []
    for lag in np.arange(-lag_max_ms, lag_max_ms + 1e-9, step_ms):
        k = int(round(lag / 1000.0 * DENSE_HZ))
        if k >= 0:
            a, b = mz[k:], ez[:len(ez) - k]
        else:
            a, b = mz[:len(mz) + k], ez[-k:]
        m = min(len(a), len(b))
        if m < int(DENSE_HZ):  # 至少 1 秒重叠，否则相关系数没意义
            continue
        curve.append((float(lag), float(np.mean(a[:m] * b[:m]))))
    if not curve:
        return {"lag_ms": 0.0, "r": 0.0, "r0": 0.0, "curve": []}
    lags = np.array([c[0] for c in curve])
    rs = np.array([c[1] for c in curve])
    peak = int(np.argmax(rs))
    r0 = float(rs[np.argmin(np.abs(lags))])
    return {"lag_ms": float(lags[peak]), "r": float(rs[peak]), "r0": r0, "curve": curve}


def _window_lags(mouth: np.ndarray, energy: np.ndarray, win_s: float = WIN_S,
                 hop_s: float = HOP_S) -> dict:
    """分段求延迟。恒定偏移 → 各窗延迟一样；漂移 → 延迟随窗递增。
    这是「嘴对不上」最常见的两种病，一个总数看不出来，必须分窗。"""
    n = min(len(mouth), len(energy))
    if n < 2:
        return {"windows": [], "slope_ms_per_s": 0.0, "span_ms": 0.0}
    win = max(1, int(win_s * DENSE_HZ))
    hop = max(1, int(hop_s * DENSE_HZ))
    wins = []
    for start in range(0, max(1, n - win // 2), hop):
        end = min(n, start + win)
        if end - start < int(DENSE_HZ):
            continue
        xc = _xcorr(mouth[start:end], energy[start:end], lag_max_ms=250.0)
        wins.append({"t": round(start / DENSE_HZ, 2), "lag_ms": round(xc["lag_ms"], 0),
                     "r": round(xc["r"], 2)})
    slope, span = 0.0, 0.0
    if len(wins) >= 2:
        ts = np.array([w["t"] for w in wins])
        ls = np.array([w["lag_ms"] for w in wins])
        slope = float(np.polyfit(ts, ls, 1)[0])
        span = float(ls.max() - ls.min())
    return {"windows": wins, "slope_ms_per_s": round(slope, 1), "span_ms": round(span, 0)}


def _runs(series: np.ndarray) -> list[int]:
    runs, i = [], 0
    while i < len(series):
        j = i
        while j + 1 < len(series) and series[j + 1] == series[i]:
            j += 1
        runs.append(j - i + 1)
        i = j + 1
    return runs


def _dwell(runs: list[int], fps: float) -> dict:
    if not runs:
        return {"min_ms": 0.0, "median_ms": 0.0, "p05_ms": 0.0, "count": 0}
    arr = np.array(runs, dtype=np.float64) / fps * 1000.0
    return {"min_ms": round(float(arr.min()), 1), "median_ms": round(float(np.median(arr)), 1),
            "p05_ms": round(float(np.percentile(arr, 5)), 1), "count": int(len(arr))}


def _switch_stats(series: np.ndarray, fps: float) -> dict:
    """每 100ms 的换档次数（口型轨按档算，视频按过阈值二值算）。"""
    if len(series) < 2:
        return {"per_100ms": {"mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0}, "changes": 0}
    change = np.array([series[i] != series[i - 1] for i in range(1, len(series))])
    per = max(1, int(round(fps / 10.0)))
    counts = [int(change[i:i + per].sum()) for i in range(0, max(1, len(change)), per)]
    arr = np.array(counts or [0], dtype=np.float64)
    return {"per_100ms": {"mean": round(float(arr.mean()), 2),
                          "p50": round(float(np.percentile(arr, 50)), 2),
                          "p95": round(float(np.percentile(arr, 95)), 2),
                          "max": int(arr.max())},
            "changes": int(change.sum())}


# ———————————————————— 口型轨模式 ————————————————————

def _state_report(audio_db: np.ndarray, states: np.ndarray, fps: float, t0: float,
                  audio_s: float) -> dict:
    open_m = _openness_of(states)
    i0 = int(round(t0 * fps))
    db = audio_db[i0:i0 + len(open_m)] if i0 < len(audio_db) else np.zeros(1)
    n = min(len(db), len(open_m))
    if n < 2:
        _die("口型轨比音频长太多，没有重叠区间，检查 t0 与 fps")
    db, open_m = db[:n], open_m[:n]
    states_al = states[i0:i0 + n]

    m = _resample(open_m, fps, DENSE_HZ)
    e = _resample(db, fps, DENSE_HZ)
    xc = _xcorr(m, e, lag_max_ms=250.0)
    wl = _window_lags(m, e)

    # 全长漂移：口型轨覆盖的时长 - 音频时长。>1 帧就说明补帧时没有重映射时间。
    track_s = t0 + len(states) / fps
    drift_ms = (track_s - audio_s) * 1000.0

    silent = db < DB_SILENT
    false_open = float((states_al[silent] != "closed").mean()) if silent.any() else 0.0
    return {
        "mode": "states",
        "fps": fps,
        "frames": int(n),
        "track_frames": int(len(states)),
        "track_seconds": round(track_s, 3),
        "audio_seconds": round(audio_s, 3),
        "drift_ms": round(drift_ms, 1),
        "audio": {"mean_db": round(float(np.mean(db)), 1), "peak_db": round(float(np.max(db)), 1),
                  "silent_ratio": round(float(silent.mean()), 3)},
        "lag_ms": round(xc["lag_ms"], 1),
        "r": round(xc["r"], 3),
        "r_at_zero": round(xc["r0"], 3),
        "silent_false_open": round(false_open, 4),
        "switch": _switch_stats(states_al, fps),
        "dwell": _dwell(_runs(states_al), fps),
        "window_lags": wl,
        "states_used": sorted(set(map(str, states_al))),
        "curve": xc["curve"],
    }


# ———————————————————— 成片模式 ————————————————————

# 人物框里「脸」的位置：左右中点往右一点、上下偏上。这是 chibi 站姿的构图，
# 不是靠 spec 里的坐标——交付片都是同一套版式（见 video_qa.CHAR_BOX）。
FACE_FRAC = (0.42, 0.20, 0.90, 0.62)


def _face_window(roi: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = roi
    w, h = x1 - x0, y1 - y0
    return (x0 + int(round(w * FACE_FRAC[0])), y0 + int(round(h * FACE_FRAC[1])),
            x0 + int(round(w * FACE_FRAC[2])), y0 + int(round(h * FACE_FRAC[3])))


def _crop_rgb(video: Path, box: tuple[int, int, int, int], fps: float, half: bool = True) -> np.ndarray:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if half:
        w, h = max(2, w // 2), max(2, h // 2)
    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(video),
         "-vf", f"fps={fps:.4f},crop={x1 - x0}:{y1 - y0}:{x0}:{y0},scale={w}:{h},format=rgb24",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=False)
    if raw.returncode != 0 or not raw.stdout:
        _die(f"抽帧失败：{video}（{raw.stderr.decode('utf-8', 'ignore')[:200]}）")
    buf = np.frombuffer(raw.stdout, dtype=np.uint8)
    frame = w * h * 3
    return buf[:len(buf) // frame * frame].reshape(-1, h, w, 3)


def _proxy_series(frames: np.ndarray) -> dict[str, np.ndarray]:
    """两路「嘴」代理，都是逐像素的二值掩码，再按块求和：
    red  —— 暗红（r-g>25、r>b、亮度低）：张嘴时嘴内是暗红的，闭口只剩一条细线
    dark —— 很暗的像素：张嘴时嘴内暗、且面积变大
    头发是蓝的、皮肤是粉的，这两条能把嘴从脸上分出来。"""
    f = frames.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    lum = r * 0.2126 + g * 0.7152 + b * 0.0722
    return {
        "red": ((r - g > 25) & (r - b > 0) & (lum < 170)).astype(np.float32),
        "dark": (lum < 80).astype(np.float32),
    }


def _patch_series(mask: np.ndarray, size: int = 18, step: int = 6) -> tuple[np.ndarray, list]:
    """把每帧的掩码做成「任意块和」表，再抽网格：返回 (帧数, 块数) 的均值序列 + 块坐标。"""
    n, h, w = mask.shape
    c = mask.cumsum(axis=1).cumsum(axis=2)
    sums = (c[:, size:, size:] - c[:, :-size, size:] - c[:, size:, :-size] + c[:, :-size, :-size])
    sh, sw = sums.shape[1], sums.shape[2]
    ys = list(range(0, sh, step))
    xs = list(range(0, sw, step))
    grid = sums[:, ys, :][:, :, xs] / (size * size)  # (n, len(ys), len(xs))
    flat = grid.reshape(n, -1)
    coords = [(y, x) for y in ys for x in xs]
    return flat, coords


def _best_patch(series: dict[str, np.ndarray], energy: np.ndarray, fps: float) -> dict:
    """在脸窗里挑「和音量最同步」的那块：它就是要量的嘴。
    只用来定位，最终指标重新在定位好的块上算一遍，且报两半的一致性。"""
    ez = (energy - energy.mean()) / max(1e-9, energy.std())
    best: dict = {"r": 0.0}
    span = max(1, int(250 / 1000 * fps))
    for name, flat in series.items():
        n = min(len(flat), len(ez))
        m = flat[:n]
        mu, sd = m.mean(axis=0), m.std(axis=0)
        sd[sd < 1e-9] = 1e-9
        mz = (m - mu) / sd
        for lag in range(-span, span + 1):
            if lag >= 0:
                a, b = mz[lag:], ez[:n - lag]
            else:
                a, b = mz[:n + lag], ez[-lag:]
            k = min(len(a), len(b))
            if k < int(fps):
                continue
            rs = (a[:k] * b[:k][:, None]).mean(axis=0)
            j = int(np.argmax(rs))
            if abs(rs[j]) > abs(best["r"]):
                best = {"r": float(rs[j]), "proxy": name, "index": j,
                        "lag_ms": round(float(lag) / fps * 1000.0, 0)}
    return best


def _video_report(video: Path, roi: tuple[int, int, int, int], fps_cap: float) -> dict:
    info = _probe(video)
    if not info["audio"]:
        _die(f"{video.name} 没有音轨，没法做对齐（画面上有动作、声音不在，量不了延迟）")
    fps = min(info["fps"] or 30.0, fps_cap)
    face = _face_window(roi)
    frames = _crop_rgb(video, face, fps)
    if len(frames) < int(fps * 0.5):
        _die("抽到的帧太少，检查视频与 --roi")
    masks = _proxy_series(frames)

    pcm = _audio_pcm(video)
    db = _envelope(pcm, 16000, fps)
    n = min(len(db), len(frames))
    db = db[:n]
    ez = _resample(db, fps, DENSE_HZ)

    grids, coords = {}, None
    for name, mask in masks.items():
        grids[name], coords = _patch_series(mask)
    pick = _best_patch({k: v[:n] for k, v in grids.items()}, db, fps)
    if not pick.get("proxy"):
        _die("脸窗里找不到跟着声音变的块（片子可能没有口型动画）")

    size, half_px = 18, 2
    y, x = coords[pick["index"]]
    mouth_series = grids[pick["proxy"]][:n, pick["index"]]
    mouth_box = (face[0] + x * half_px, face[1] + y * half_px,
                 face[0] + (x + size) * half_px, face[1] + (y + size) * half_px)

    m = _resample(mouth_series, fps, DENSE_HZ)
    xc = _xcorr(m, ez, lag_max_ms=250.0)
    wl = _window_lags(m, ez)

    # 两半一致性：定位用的块如果是嘴，两半的相关系数与延迟应该接近
    half = n // 2
    halves = []
    for lo, hi in ((0, half), (half, n)):
        hr = _xcorr(_resample(mouth_series[lo:hi], fps, DENSE_HZ),
                    _resample(db[lo:hi], fps, DENSE_HZ), lag_max_ms=250.0)
        halves.append({"r": round(hr["r"], 2), "lag_ms": round(hr["lag_ms"], 0)})

    med = float(np.median(mouth_series))
    hi = float(np.percentile(mouth_series, 95))
    thr = med + 0.5 * (hi - med) if hi > med else med + 1e-9
    is_open = mouth_series > thr
    silent = db < DB_SILENT
    silent_open = float(is_open[silent].mean()) if silent.any() else 0.0
    return {
        "mode": "video",
        "video": str(video),
        "fps": round(fps, 2),
        "size": [info["width"], info["height"]],
        "duration": round(info["duration"], 2),
        "roi": list(roi),
        "face_window": list(face),
        "mouth_box": list(mouth_box),
        "mouth_px": [mouth_box[2] - mouth_box[0], mouth_box[3] - mouth_box[1]],
        "proxy": pick["proxy"],
        "patch_r": round(pick["r"], 3),
        "patch_lag_ms": pick["lag_ms"],
        "halves": halves,
        "frames": int(n),
        "audio": {"mean_db": round(float(np.mean(db)), 1), "peak_db": round(float(np.max(db)), 1),
                  "silent_ratio": round(float(silent.mean()), 3)},
        "lag_ms": round(xc["lag_ms"], 1),
        "r": round(xc["r"], 3),
        "r_at_zero": round(xc["r0"], 3),
        "silent_false_open": round(silent_open, 4),
        "switch": _switch_stats(is_open.astype(np.int8), fps),
        "window_lags": wl,
        "curve": xc["curve"],
    }


def _verdict(rep: dict) -> list[dict]:
    lag = abs(rep["lag_ms"])
    checks = [
        {"name": "对齐延迟", "value": f"{rep['lag_ms']:+.0f}ms", "limit": f"|≤{LAG_USABLE_MS:.0f}|",
         "ok": lag <= LAG_USABLE_MS, "note": "好档" if lag <= LAG_GOOD_MS else ""},
        {"name": "静音误开率", "value": f"{rep['silent_false_open'] * 100:.1f}%",
         "limit": f"≤{SILENT_FALSE_MAX * 100:.0f}%", "ok": rep["silent_false_open"] <= SILENT_FALSE_MAX},
        {"name": "每100ms换档", "value": f"{rep['switch']['per_100ms']['mean']:.2f}",
         "limit": f"≤{SWITCH_PER_100MS_MAX}", "ok": rep["switch"]["per_100ms"]["mean"] <= SWITCH_PER_100MS_MAX},
    ]
    if "drift_ms" in rep:
        checks.append({"name": "全长漂移", "value": f"{rep['drift_ms']:+.0f}ms",
                       "limit": f"|≤{DRIFT_MAX_MS:.0f}|",
                       "ok": abs(rep["drift_ms"]) <= DRIFT_MAX_MS})
    return checks


def _print_report(rep: dict) -> None:
    checks = _verdict(rep)
    print(f"口型体检 · {rep.get('video') or '口型轨'}")
    if rep["mode"] == "video":
        mb = rep["mouth_box"]
        print(f"  片子 {rep['size'][0]}x{rep['size'][1]} @{rep['fps']}fps {rep['duration']}s"
              f" · 人物框 x{rep['roi'][0]}-{rep['roi'][2]} y{rep['roi'][1]}-{rep['roi'][3]}")
        print(f"  嘴部 {mb[2] - mb[0]}x{mb[3] - mb[1]}px @({mb[0]},{mb[1]})"
              f" · 代理 {rep['proxy']}（定位 r={rep['patch_r']:+.2f}）")
        h1, h2 = rep["halves"]
        print(f"  两半复核 前 {h1['r']:+.2f}/{h1['lag_ms']:+.0f}ms"
              f" · 后 {h2['r']:+.2f}/{h2['lag_ms']:+.0f}ms")
    else:
        print(f"  口型轨 {rep['track_frames']} 格 @{rep['fps']:.0f}fps = {rep['track_seconds']}s"
              f" · 音频 {rep['audio_seconds']}s · 档 {', '.join(rep['states_used'])}")
        d = rep["dwell"]
        print(f"  驻留 最短 {d['min_ms']:.0f}ms / 中位 {d['median_ms']:.0f}ms / p05 {d['p05_ms']:.0f}ms"
              f" · {d['count']} 段（规则 ≥{MIN_DWELL_MS:.0f}ms）")
    a = rep["audio"]
    print(f"  音频 均值 {a['mean_db']}dBFS 峰值 {a['peak_db']}dBFS · 静音帧 {a['silent_ratio'] * 100:.1f}%")
    lead = "嘴早于声音" if rep["lag_ms"] > 0 else ("嘴晚于声音" if rep["lag_ms"] < 0 else "同步")
    print(f"  延迟 {rep['lag_ms']:+.0f}ms（{lead}）· 峰值相关 r={rep['r']:+.2f}"
          f"（0 延迟时 r={rep['r_at_zero']:+.2f}）")
    if "drift_ms" in rep:
        print(f"  全长漂移 {rep['drift_ms']:+.0f}ms（口型轨 {rep['track_seconds']}s vs 音频 "
              f"{rep['audio_seconds']}s）")
    wl = rep["window_lags"]
    if wl["windows"]:
        seg = " ".join(f"{w['lag_ms']:+.0f}" for w in wl["windows"][:12])
        print(f"  分段延迟每 {HOP_S:.0f}s：{seg}"
              f" · 斜率 {wl['slope_ms_per_s']:+.0f}ms/s · 极差 {wl['span_ms']:.0f}ms")
    s = rep["switch"]["per_100ms"]
    print(f"  每100ms换档 均值 {s['mean']:.2f} p50 {s['p50']:.1f} p95 {s['p95']:.1f} 最大 {s['max']}")
    bad = [c for c in checks if not c["ok"]]
    print("  判定：" + ("通过" if not bad else "不过 —— " + "；".join(
        f"{c['name']} {c['value']}（要求 {c['limit']}）" for c in bad)))
    lag = abs(rep["lag_ms"])
    if lag <= LAG_GOOD_MS:
        print(f"  ↳ 延迟 {lag:.0f}ms 在 {LAG_GOOD_MS:.0f}ms 以内：好档")
    elif lag <= LAG_USABLE_MS:
        print(f"  ↳ 延迟 {lag:.0f}ms 在 {LAG_USABLE_MS:.0f}ms 以内：可用档，还能再收 30ms")
    if abs(rep["r"]) < 0.30:
        print("  ⚠ 相关系数低于 0.30：口型和音量整体不相关，延迟数字只是陪衬，先修设计")
    if wl["windows"] and wl["span_ms"] > 2 * LAG_USABLE_MS:
        print(f"  ⚠ 分段延迟极差 {wl['span_ms']:.0f}ms：不是恒定偏移，是逐段漂移——"
              f"整体推一帧修不掉，得改口型轨生成")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="口型 vs 音频对齐体检（延迟/漂移/相关/静音误开/换档频率）")
    ap.add_argument("--audio", type=Path, help="配音文件（mp3/wav/m4a）")
    ap.add_argument("--states", type=Path, help='口型轨 json：{"fps":50,"states":[...],"t0":0}')
    ap.add_argument("--video", type=Path, help="成片（从画面量嘴部）")
    ap.add_argument("--spec", type=Path, help="片子 spec（只做记录，可选）")
    ap.add_argument("--roi", default="280,900,1040,1700",
                    help="人物框 x0,y0,x1,y1（默认取 video_qa.CHAR_BOX），脸窗按比例收窄")
    ap.add_argument("--fps-cap", type=float, default=50.0, help="抽帧上限 fps，默认 50")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是报告")
    ap.add_argument("--no-strict", action="store_true", help="不过也 exit 0")
    args = ap.parse_args(argv)

    if args.states:
        if not args.audio:
            _die("--states 要配 --audio：口型轨本身不含声音")
        for p in (args.audio, args.states):
            if not p.exists():
                _die(f"文件不存在：{p}")
        fps, states, t0 = _load_states(args.states)
        pcm = _audio_pcm(args.audio)
        db = _envelope(pcm, 16000, fps)
        rep = _state_report(db, states, fps, t0, len(pcm) / 16000.0)
        rep["input"] = {"audio": str(args.audio), "states": str(args.states)}
    elif args.video:
        if not args.video.exists():
            _die(f"文件不存在：{args.video}")
        if args.spec and not args.spec.exists():
            _die(f"spec 不存在：{args.spec}")
        try:
            roi = tuple(int(v) for v in args.roi.split(","))
            if len(roi) != 4:
                raise ValueError
        except ValueError:
            _die(f"--roi 要四个整数 x0,y0,x1,y1，收到 {args.roi!r}")
        if roi[2] <= roi[0] or roi[3] <= roi[1]:
            _die(f"--roi 右下角必须大于左上角：{roi}")
        rep = _video_report(args.video, roi, args.fps_cap)
        if args.spec:
            rep["spec"] = str(args.spec)
    else:
        _die("要么 --audio + --states，要么 --video（+ 可选 --spec）")

    rep["checks"] = _verdict(rep)
    ok = all(c["ok"] for c in rep["checks"])
    rep["ok"] = ok
    if args.json:
        rep.pop("curve", None)
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _print_report(rep)
    return 0 if (ok or args.no_strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
