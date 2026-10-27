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
    两种输入口径不同，别交叉比：口型轨（音频级）没有取样损失；成片（视频级）量的是
    36px 嘴区代理 @30fps，r 天花板约 0.6，通过线只认「延迟 + 静音误开」，r 作参考。

用法：
    /usr/bin/python3 tools/lipsync_qa.py --audio a.mp3 --states lip.json
    /usr/bin/python3 tools/lipsync_qa.py --video ~/CreatorStudio/deliver/envoy-f32.mp4 \
        --spec deploy/specs/envoy_real.json
    ... --json        给流水线读；任一项不过 exit 1（--no-strict 变 0）

只依赖 numpy 与系统 ffmpeg，不装包、不改引擎。本机 /usr/bin/python3 被 Xcode 许可挡住时，
用带 numpy 的解释器：~/CreatorStudio/.motionvenv/bin/python。
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
# 视频级相关系数下限：只是「代理/定位可信」的参考线，不是失败线。
# 成片里嘴区只有 30~40px、30fps 取样，r 天花板 0.6 左右（实测 f32 +0.47 / f36 +0.61），
# 拿口型轨的 0.8 去要求成片，只会逼人放宽别的阈值。
VIDEO_R_REF = 0.30
DB_SILENT = -45.0        # 低于这个算静音
# 每 100ms 换档上限：口型最短驻留 80ms，一个 100ms 窗口里最多 1 次换档；
# 平均超过 1.25 次说明驻留已经是 50~80ms 级别，成片里读作「嘴在抖」。
SWITCH_PER_100MS_MAX = 1.25
MIN_DWELL_MS = 80.0      # 口型最短驻留：短于它观众读到的是抽动，不是一个字
# 开场领先张开：语音起点前这么多毫秒内出现非闭合档，就算「还没出声嘴先动」。
# f36 的毛病正是这个：整体延迟只有 +20ms，但每场开头 0.2s 嘴已经张着。
EARLY_OPEN_MS = 200.0
# 提前量预算：lipsync 的 LOOKAHEAD 是 20ms，加一帧量化就是 40ms；给到 60ms 与延迟同尺，
# 超过它就不是「预判」而是「整段提前」，f36 那种场场 200ms 提前会被这条抓住。
EARLY_OPEN_LEAD_MS = 60.0

# 静音判据的规范口径（范式第 4 条，2026-10-27 定）：10ms RMS 窗、相对峰值 -40dBFS、
# 连续 >=120ms 才算静音段。DB_SILENT=-45 是旧的绝对阈值（25ms 窗），保留作对照，
# 判据用 SILENCE_REL_DB；两者都给，免得同一段音频两套解释。
SILENCE_REL_DB = -40.0
SILENCE_WIN_MS = 10.0
SILENCE_MIN_S = 0.12

# 分段延迟只统计「有声占比 >= 这个数」的窗：静音窗里的互相关峰值是噪声，
# 会把极差和斜率都抬歪（f44 片尾窗 -220ms 就属这类）。
WINDOW_ACTIVE_MIN = 0.5

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
        return {"windows": [], "skipped": 0, "total": 0,
                "slope_ms_per_s": 0.0, "span_ms": 0.0}
    win = max(1, int(win_s * DENSE_HZ))
    hop = max(1, int(hop_s * DENSE_HZ))
    rows = []
    for start in range(0, max(1, n - win // 2), hop):
        end = min(n, start + win)
        if end - start < int(DENSE_HZ):
            continue
        seg_e = energy[start:end]
        # 有效窗判据：这一窗里至少一半时间是有声的。几乎全静音的窗（片尾、句间长停）
        # 里嘴和音量都没有可对齐的起伏，互相关峰值会落到任意值——实测 f44 片尾窗
        # 报 -220ms，和真漂移毫无关系，却足以把「极差」抬到 260ms。
        active = float((seg_e > DB_SILENT).mean())
        xc = _xcorr(mouth[start:end], seg_e, lag_max_ms=250.0)
        rows.append({"t": round(start / DENSE_HZ, 2), "lag_ms": round(xc["lag_ms"], 0),
                     "r": round(xc["r"], 2), "active": round(active, 2)})
    wins = [r for r in rows if r["active"] >= WINDOW_ACTIVE_MIN]
    slope, span = 0.0, 0.0
    if len(wins) >= 2:
        ts = np.array([w["t"] for w in wins])
        ls = np.array([w["lag_ms"] for w in wins])
        slope = float(np.polyfit(ts, ls, 1)[0])
        span = float(ls.max() - ls.min())
    return {"windows": wins, "skipped": len(rows) - len(wins), "total": len(rows),
            "slope_ms_per_s": round(slope, 1), "span_ms": round(span, 0)}


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

def _early_open(states: np.ndarray, db: np.ndarray, fps: float) -> dict:
    """开场领先张开：语音起点前 200ms 内「紧贴起点的连续开口段」有多长。

    只数「从静音切进有声」的起点：起点前窗口里本来就在响（句中重音/换气）不算。
    提前量从起点往回数 **连续** 非闭合帧：中间已经被闭口隔开的更早开口属于上一句
    的尾巴（说话人换气前嘴还张着），不算这一句抢跑——老实现数窗口内任意开口，
    会把上一句尾帧当成抢跑，报出 133ms 这种假值。
    """
    voiced = db >= DB_SILENT
    onsets_idx = np.flatnonzero(voiced & ~np.concatenate(([False], voiced[:-1])))
    look = max(1, int(round(EARLY_OPEN_MS / 1000.0 * fps)))
    openish = states != "closed"
    onsets = early = 0
    leads: list[float] = []
    for i in onsets_idx:
        lo = max(0, i - look)
        if voiced[lo:i].sum() > max(1, look // 2):
            continue  # 前面本来就在响，是句中重音，不是句子起点
        onsets += 1
        j = i - 1
        while j >= lo and openish[j]:
            j -= 1
        lead = i - 1 - j
        if lead > 0:
            early += 1
            leads.append(lead / fps * 1000.0)
    return {"window_ms": EARLY_OPEN_MS, "onsets": onsets, "early": early,
            "rate": round(early / onsets, 3) if onsets else 0.0,
            "lead_ms_max": round(max(leads), 0) if leads else 0.0}


def _onset_lags(mouth_open: np.ndarray, db: np.ndarray, fps: float,
                tol_ms: float = 300.0, refractory_ms: float = 150.0) -> dict:
    """第二把尺：按「起音配对」量延迟，不用互相关。

    互相关在能量起伏平缓的句子会把峰值拉到邻句上（实测换声后片尾窗报 +60ms、
    相关系数 0.79 照样是假峰）。这里只认真正的起点：音量从静音跨上来的一帧、
    嘴从闭合跨到张开的一帧，各自取最近的一对，``d = (t_audio - t_mouth) * 1000``，
    **正值 = 嘴早于声音**，与 onset 口径一致。配不上的起点单独计数，不摊进中位数。
    """
    ref = max(1, int(round(refractory_ms / 1000.0 * fps)))
    tol = max(1, int(round(tol_ms / 1000.0 * fps)))
    voiced = db >= DB_SILENT
    a_on: list[int] = []
    for i in np.flatnonzero(voiced & ~np.concatenate(([False], voiced[:-1]))):
        if not a_on or int(i) - a_on[-1] >= ref:
            a_on.append(int(i))
    opened = mouth_open >= VIDEO_R_REF
    m_on: list[int] = []
    for i in np.flatnonzero(opened & ~np.concatenate(([False], opened[:-1]))):
        if not m_on or int(i) - m_on[-1] >= ref:
            m_on.append(int(i))
    lags: list[float] = []
    unpaired = 0
    for i in a_on:
        cand = [j for j in m_on if abs(j - i) <= tol]
        if not cand:
            unpaired += 1
            continue
        j = min(cand, key=lambda k: abs(k - i))
        lags.append((i - j) / fps * 1000.0)
    if lags:
        arr = np.array(lags, dtype=np.float64)
        med = round(float(np.median(arr)), 0)
        p05 = round(float(np.percentile(arr, 5)), 0)
        p95 = round(float(np.percentile(arr, 95)), 0)
        span = round(float(arr.max() - arr.min()), 0)
    else:
        med = p05 = p95 = span = 0.0
    return {"audio_onsets": len(a_on), "mouth_onsets": len(m_on), "paired": len(lags),
            "unpaired": unpaired, "tol_ms": tol_ms,
            "median_ms": med, "p05_ms": p05, "p95_ms": p95, "span_ms": span}


def _rms_db(pcm: np.ndarray, rate: int, win_ms: float) -> np.ndarray:
    """不重叠 10ms 窗的 RMS→dBFS（规范口径用，区别于 _envelope 的 25ms 滑窗）。"""
    win = max(1, int(rate * win_ms / 1000.0))
    n = len(pcm) // win
    out = np.zeros(max(n, 1), dtype=np.float64)
    for i in range(n):
        seg = pcm[i * win:(i + 1) * win]
        out[i] = 20.0 * np.log10(max(float(np.sqrt(np.mean(seg * seg))), 1e-7))
    return out


def _silence_spec(pcm: np.ndarray, states: np.ndarray, fps: float) -> dict:
    """第 4 条「静音必闭」的三项统计，全部按规范口径（10ms RMS / 峰值-40dB / >=120ms）：

    - ``false_open``：**>=120ms 静音段**里的帧，非 closed 的比例（≤5% 才过）。
      40~80ms 的塞音空隙按范式中「不得误闭」处理，不计入这个分母 —— 否则同一批帧
      会同时被要求「不许闭」和「必须闭」。
    - ``lead_frames``：首字之前（第一个有声帧之前）非 closed 的帧数（必须 0）
    - ``tail_frames``：末字 + 120ms 之后非 closed 的帧数（必须 0）
    - ``long_silent`` / ``long_bad``：>=120ms 的静音段数、其中没全 closed 的段数
    - ``stop_gaps`` / ``stop_misclosed``：40~80ms 的句内空隙数、被整段闭成 closed 的数
      （塞音空隙不该被 120ms 静音规则误闭；这一项数越小越好，0 是目标）

    帧↔格的对齐按时间算（``ceil(k*1000/fps/win)`` 到 ``ceil((k+1)*1000/fps/win)``），
    同一段音频在 50fps / 30fps 下结论应一致；固定 per 会让 30fps 每帧累计超前 3.33ms。
    """
    win_ms = SILENCE_WIN_MS
    db10 = _rms_db(pcm, 16000, win_ms)
    thr = float(np.max(db10)) + SILENCE_REL_DB
    silent10 = db10 < thr
    # 10ms 格 → 口型帧：按**时间**取每帧覆盖的格（起 ceil(k*1000/fps/win)，止同式 k+1），
    # 一帧内多数格静音就算静音帧。绝不能用固定 per：30fps 一帧 33.3ms 只吃 3 格会
    # 每帧累 3.33ms 漂移，末帧能偏到半秒，还会把尾巴上的音频整段丢掉。
    n_cells = len(silent10)
    n = int(min(len(states), int(round(n_cells * win_ms / 1000.0 * fps))))
    if n < 2:
        return {"win_ms": win_ms, "rel_db": SILENCE_REL_DB, "threshold_db": round(thr, 1),
                "frames": 0, "silent_ratio": 0.0, "false_open": 0.0, "lead_frames": 0,
                "tail_frames": 0, "long_silent": 0, "long_bad": 0,
                "stop_gaps": 0, "stop_misclosed": 0}
    edges = np.ceil(np.arange(n + 1) * (1000.0 / fps) / win_ms).astype(int)
    sf = np.zeros(n, dtype=bool)
    for k in range(n):
        a, b = int(edges[k]), min(int(edges[k + 1]), n_cells)
        if b <= a:  # 帧比格还窄（fps 很低时），退化成最近一格
            a, b = min(a, n_cells - 1), min(a + 1, n_cells)
        sf[k] = bool(silent10[a:b].mean() >= 0.5)
    st = np.asarray([str(x) for x in states], dtype=object)[:n]
    openish = st != "closed"

    voiced = ~sf
    idx = np.flatnonzero(voiced)
    lead_frames = tail_frames = 0
    if idx.size:
        i0, i1 = int(idx[0]), int(idx[-1])
        lead_frames = int(openish[:i0].sum())
        tail_ms = SILENCE_MIN_S
        tail_start = i1 + 1 + int(round(tail_ms * fps))
        tail_frames = int(openish[tail_start:].sum()) if tail_start < n else 0

    def runs(mask):
        out, i = [], 0
        while i < len(mask):
            if not mask[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(mask) and mask[j + 1]:
                j += 1
            out.append((i, j + 1))
            i = j + 1
        return out

    min_frames = SILENCE_MIN_S * fps
    long_frames = long_open = 0
    long_silent = long_bad = 0
    stop_gaps = stop_misclosed = 0
    for a, b in runs(sf):
        span = b - a
        if span >= min_frames:
            long_silent += 1
            long_frames += span
            long_open += int(openish[a:b].sum())
            if openish[a:b].any():
                long_bad += 1
        elif 0.04 * fps <= span < 0.08 * fps + 1e-9:
            stop_gaps += 1
            if not openish[a:b].any():
                stop_misclosed += 1
    false_open = (long_open / long_frames) if long_frames else 0.0
    return {"win_ms": win_ms, "rel_db": SILENCE_REL_DB, "threshold_db": round(thr, 1),
            "frames": int(n), "silent_ratio": round(float(sf.mean()), 3),
            "false_open": round(false_open, 4), "false_open_frames": long_open,
            "false_open_denom": long_frames, "lead_frames": lead_frames,
            "tail_frames": tail_frames, "long_silent": long_silent, "long_bad": long_bad,
            "stop_gaps": stop_gaps, "stop_misclosed": stop_misclosed}


def _state_report(audio_db: np.ndarray, states: np.ndarray, fps: float, t0: float,
                  audio_s: float, pcm: np.ndarray | None = None) -> dict:
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
        "silence_spec": (_silence_spec(pcm, states, fps) if pcm is not None else {}),
        "early_open": _early_open(states_al, db, fps),
        "onset_lags": _onset_lags(open_m, db, fps),
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
    """判据：延迟与静音误开是硬门槛；相关性在视频级只作参考。

    视频级（成片）量的是「嘴区代理在 30fps 下的红色像素质量」，36px 的块 + 取样
    会把相关系数压到 0.6 上下，所以 r < 0.30 只说明「代理/定位不可信、延迟数字仅
    供参考」，不判失败；口型轨（音频级）没有这层损失，r 一并不设硬线。
    """
    lag = abs(rep["lag_ms"])
    video = rep["mode"] == "video"
    checks = [
        {"name": "对齐延迟", "value": f"{rep['lag_ms']:+.0f}ms", "limit": f"|≤{LAG_USABLE_MS:.0f}|",
         "ok": lag <= LAG_USABLE_MS, "note": "好档" if lag <= LAG_GOOD_MS else ""},
        # 规范口径（第 4 条）：>=120ms 静音段里的非闭合帧比例才是硬门槛；
        # 旧口径是绝对 -45dB/25ms 窗，会把 40~80ms 塞音空隙也算成静音，只作参考。
        *([{"name": "静音误开率",
            "value": f"{rep['silence_spec']['false_open'] * 100:.1f}%"
                     f"（{rep['silence_spec']['false_open_frames']}/"
                     f"{rep['silence_spec']['false_open_denom']} 帧）",
            "limit": f"≤{SILENT_FALSE_MAX * 100:.0f}%",
            "ok": rep["silence_spec"]["false_open"] <= SILENT_FALSE_MAX,
            "note": ">=120ms 静音段内；40~80ms 塞音空隙不在分母"}
           ] if rep.get("silence_spec", {}).get("frames", 0) and not video else
           [{"name": "静音误开率", "value": f"{rep['silent_false_open'] * 100:.1f}%",
             "limit": f"≤{SILENT_FALSE_MAX * 100:.0f}%",
             "ok": rep["silent_false_open"] <= SILENT_FALSE_MAX,
             "note": "成片模式：代理像素静音，口径见视频级说明"}]),
        {"name": "每100ms换档", "value": f"{rep['switch']['per_100ms']['mean']:.2f}",
         "limit": f"≤{SWITCH_PER_100MS_MAX}",
         "ok": rep["switch"]["per_100ms"]["mean"] <= SWITCH_PER_100MS_MAX},
        *([{"name": "静音必闭·前导", "value": f"{rep['silence_spec']['lead_frames']} 帧非闭",
            "limit": "= 0 帧",
            "ok": rep["silence_spec"]["lead_frames"] == 0,
            "note": "第一个有声帧之前必须全 closed"},
           {"name": "静音必闭·尾部", "value": f"{rep['silence_spec']['tail_frames']} 帧非闭",
            "limit": "= 0 帧",
            "ok": rep["silence_spec"]["tail_frames"] == 0,
            "note": "末字 + 120ms 之后必须全 closed"},
           {"name": "静音必闭·误闭", "value": f"{rep['silence_spec']['stop_misclosed']} 段",
            "limit": "= 0 段",
            "ok": rep["silence_spec"]["stop_misclosed"] == 0,
            "note": "词内 40~80ms 塞音空隙不该被静音规则整段闭掉"}]
          if rep.get("silence_spec", {}).get("frames", 0) and not video else []),
        *([{"name": "开场领先张开",
            "value": f"最大提前 {rep['early_open']['lead_ms_max']:.0f}ms"
                     f"（{rep['early_open']['early']}/{rep['early_open']['onsets']} 处）",
            "limit": f"≤{EARLY_OPEN_LEAD_MS:.0f}ms",
            "ok": rep["early_open"]["lead_ms_max"] <= EARLY_OPEN_LEAD_MS,
            "note": f"量的是起点前 {EARLY_OPEN_MS:.0f}ms 内的非闭合档；"
                    f"{EARLY_OPEN_LEAD_MS:.0f}ms 是协同发音预判预算"}]
          if "early_open" in rep and not video else []),
        {"name": "峰值相关", "value": f"r{rep['r']:+.2f}", "limit": f"≥{VIDEO_R_REF:.2f}（视频级仅参考）",
         "ok": rep["r"] >= VIDEO_R_REF or video,
         "note": "视频级 r<0.30 只作参考，不判失败" if video and rep["r"] < VIDEO_R_REF else ""},
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
    if rep.get("silence_spec", {}).get("frames"):
        ss = rep["silence_spec"]
        print(f"  静音必闭 前导 {ss['lead_frames']} 帧 / 尾部 {ss['tail_frames']} 帧非闭"
              f" · 长静音 {ss['long_silent']} 段其中 {ss['long_bad']} 段没全闭"
              f" · 塞音 {ss['stop_gaps']} 处误闭 {ss['stop_misclosed']}"
              f" · 口径 {ss['win_ms']:.0f}ms RMS / 峰值{ss['rel_db']:+.0f}dB → "
              f"{ss['threshold_db']}dBFS / ≥{SILENCE_MIN_S * 1000:.0f}ms")
    if "early_open" in rep:
        e = rep["early_open"]
        print(f"  开场领先张开 {e['early']}/{e['onsets']} 处（起点前 {e['window_ms']:.0f}ms 内）"
              f"· 最大提前 {e['lead_ms_max']:.0f}ms · 逐场必须各算")
    on = rep.get("onset_lags") or {}
    if on:
        print(f"  起音配对 中位 {on['median_ms']:+.0f}ms p05 {on['p05_ms']:+.0f} p95 {on['p95_ms']:+.0f}"
              f" · 配 {on['paired']}/{on['audio_onsets']} 处（未配 {on['unpaired']}）· 距差 {on['span_ms']:.0f}ms")
    s = rep["switch"]["per_100ms"]
    print(f"  每100ms换档 均值 {s['mean']:.2f} p50 {s['p50']:.1f} p95 {s['p95']:.1f} 最大 {s['max']}")
    bad = [c for c in checks if not c["ok"]]
    print("  判定：" + ("通过" if not bad else "不过 —— " + "；".join(
        f"{c['name']} {c['value']}（要求 {c['limit']}）" for c in bad)))
    if rep["mode"] == "video":
        print("  ↳ 视频级口径：嘴区代理 @30fps，r 天花板约 0.6；通过线只认延迟与静音误开")
    lag = abs(rep["lag_ms"])
    if lag <= LAG_GOOD_MS:
        print(f"  ↳ 延迟 {lag:.0f}ms 在 {LAG_GOOD_MS:.0f}ms 以内：好档")
    elif lag <= LAG_USABLE_MS:
        print(f"  ↳ 延迟 {lag:.0f}ms 在 {LAG_USABLE_MS:.0f}ms 以内：可用档，还能再收 30ms")
    if abs(rep["r"]) < VIDEO_R_REF:
        if rep["mode"] == "video":
            print(f"  ⚠ 视频级 r={rep['r']:+.2f} < {VIDEO_R_REF:.2f}：代理/定位不可信，"
                  "延迟与静音误开数字只作参考，改用口型轨复核")
        else:
            print("  ⚠ 相关系数低于 0.30：口型和音量整体不相关，延迟数字只是陪衬，先修设计")
    if wl["windows"] and wl["span_ms"] > 2 * LAG_USABLE_MS:
        print(f"  ⚠ 分段延迟极差 {wl['span_ms']:.0f}ms：不是恒定偏移，是逐段漂移——"
              f"整体推一帧修不掉，得改口型轨生成")
    print()


def _j(v):
    """把 numpy 标量/数组洗成原生类型：numpy.bool_ 不是 bool，json 会直接抛。"""
    if isinstance(v, dict):
        return {str(k): _j(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if isinstance(v, np.ndarray):
        return [_j(x) for x in v.tolist()]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return round(float(v), 4)
    return v


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
    # 第四把尺：开场领先张开（口型轨模式）——语音起点前 200ms 不许有开口档
    # 通过线：延迟 ≤60ms 且 静音误开 ≤5%（视频级 r<0.30 只作参考，不判失败）
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
        rep = _state_report(db, states, fps, t0, len(pcm) / 16000.0, pcm)
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
        print(json.dumps(_j(rep), ensure_ascii=False, indent=2))
    else:
        _print_report(rep)
    return 0 if (ok or args.no_strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
