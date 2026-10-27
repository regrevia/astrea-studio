"""人物动画层：把逐帧立绘攒成一条带 alpha 的视频，替掉「每段口型贴一张图」。

原来每个口型分段都往成片里塞一张 1080×1920 的静帧叠加层，一条 30 秒的片子
要叠近百层，人物却始终是同一张图——观众看到的就是「贴图」。这里改成：

1. 按成片帧率逐帧画人物（动作 + 口型 + 眨眼 + 动画序列帧）；
2. 整条人攒成一个 qtrle/argb 的 mov（alpha 无损）；
3. 成片只叠这一层。

实测 9 秒片子连出片 27 秒跑完，比近百个叠加层便宜，人也是活的。

分段（segment）字段::

    start / end          出现区间（秒）
    width / x / y        立绘摆位，坐标按 1080×1920 设计稿；缺省同剧情片
    pose / mouth / eyes / expression / outfit
    anim                 可选动画名（talk / idle_blink / wave），给了就用序列帧
    anim_t0              动画相位偏移（秒），换段时接着播，不从头起
    fade_in / fade_out   图层 alpha 淡入淡出（秒）
    speaking             仅用于日志

摆位复用 `jianying_engine._stage_canvas`：影子、轮廓柔光、贴底对齐都只有一份实现，
动起来的人和停住的人才不会错位。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

_ANIM_CACHE: dict[tuple, list] = {}
_PIECE_CACHE: dict[tuple, tuple] = {}
_SPRITE_CACHE: dict[tuple, object] = {}
_PIECE_LIMIT = 200
# 口型直接读这句配音的能量包络：每 20ms 一个点，成片帧率 30 时约 1.5 帧一格，
# 比「按 0.1 秒切段」细五倍，张合跟着字走而不是跟着节拍走。
_LIP_HZ = 50.0
# 四档口型的能量分界（按说话声部中上水平归一到 1.0 之后）。
# 静音和字与字之间的缝都要落到闭口：门槛压太低会一直半张着嘴，
# 看着像在喘气而不是在说话。0.20 大约让三成时间闭嘴。
_LIP_CLOSED = 0.20
_LIP_HALF = 0.32
_LIP_OPEN = 0.62
_ENV_CACHE: dict[str, list[float]] = {}


def _envelope(mp3: str) -> list[float]:
    """配音的响度包络（50Hz，按峰值归一）。解不出来返回空表，调用方退回分段口型。"""
    hit = _ENV_CACHE.get(mp3)
    if hit is not None:
        return hit
    import array
    import subprocess

    raw = b""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", mp3, "-ac", "1", "-ar", "16000",
             "-f", "s16le", "-"], capture_output=True, timeout=180)
        raw = proc.stdout or b""
    except Exception:  # noqa: BLE001 - 缺 ffmpeg 不该让整片挂掉
        raw = b""
    levels: list[float] = []
    if raw:
        arr = array.array("h")
        arr.frombytes(raw[: len(raw) // 2 * 2])
        step = max(1, int(16000 / _LIP_HZ))
        for i in range(0, len(arr) - step + 1, step):
            total = 0
            for v in arr[i:i + step]:
                total += v * v
            levels.append((total / step) ** 0.5 / 32768.0)
    if levels:
        # 先减底噪、再按说话声部上沿拉伸：峰值归一会让每句都顶到 1.0，
        # 嘴巴于是一直张着；只按 85 分位归一，录音里的底噪又会被抬成半个字。
        heard = sorted(v for v in levels if v > 0.0)
        if heard:
            floor = heard[int(len(heard) * 0.10)]
            top = heard[int(len(heard) * 0.90)]
            span = max(1e-4, top - floor)
            levels = [min(1.0, max(0.0, (lv - floor) / span)) for lv in levels]
        else:
            levels = []
    # 50ms 滑动平均：音节周期 ~200ms，平滑之后仍跟得上字，但不会逐帧抖。
    # 原来 80ms 会把相邻两字抹成一整块，口型看着「一直在半张」。
    if levels:
        span = max(1, int(_LIP_HZ * 0.05))
        smooth: list[float] = []
        acc = 0.0
        for i, v in enumerate(levels):
            acc += v
            if i >= span:
                acc -= levels[i - span]
            smooth.append(acc / min(i + 1, span))
        levels = smooth
    _ENV_CACHE[mp3] = levels
    return levels


_STATE_CACHE: dict[str, list[str]] = {}
# 换档的最小驻留：50Hz 下 3 格 = 60ms，短于这个的抖动一律当噪声。
# 只在「收口」方向生效，张嘴不驻留（见 _mouth_states）。
_LIP_DWELL = 3
# 最短口型驻留：50Hz 下 5 格 = 100ms
_LIP_MIN_RUN = 6
# 走 lipsync 的逐字口型时，它内部已经做过协同发音/最小驻留，这里只做轻收口：
# 50Hz 下 4 格 = 80ms。再按 100ms 扫一遍会把「齿」「努嘴」这些短音并掉。
_LIP_MIN_RUN_TEXT = 4
# 口型开合大小排序，用来判断这次换档是张嘴还是收口。
# closed < teeth/pucker < narrow < mid < wide/open/round
_LIP_RANK = {"closed": 0, "teeth": 1, "pucker": 1, "narrow": 2,
             "half": 3, "mid": 3, "wide": 4, "open": 4, "round": 4}
# 从闭到最开的一把尺：补过渡档时按档位取中点（老档仍走 open↔closed→half）
_LADDER = ("closed", "teeth", "narrow", "mid", "wide")
# 嘴部精灵要预先算出来的档：并集框得把它们都算进去
_LIP_STATES = ("closed", "half", "open", "round",
               "wide", "mid", "narrow", "pucker", "teeth")


def _settle(states: list[str], min_run: int = 0) -> list[str]:
    """把短于 min_run 的口型段并进前一段。

    逐格换是 50Hz：嘴型 20ms 一换，成片里就是「嘴在抖」。驻留只能解决
    「同一档反复横跳」，解决不了 A→B→A 这种 40ms 的来回，所以统一再扫一遍。
    """
    if min_run <= 1:
        return states
    out = list(states)
    i = 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and out[j + 1] == out[i]:
            j += 1
        if (j - i + 1) < min_run and i > 0:
            for k in range(i, j + 1):
                out[k] = out[i - 1]
            i = j + 1
            continue
        i = j + 1
    return out


_LIP_BRIDGE = 2   # 40ms 的中间档：张↔闭之间必须有半开，否则嘴是「啪」地翻面


def _bridge_state(a: str, b: str) -> str | None:
    """a→b 直接翻面时该补哪一档；差得不够多就不补。"""
    ra, rb = _LIP_RANK.get(a, 0), _LIP_RANK.get(b, 0)
    if abs(ra - rb) < 3:
        return None
    if {a, b} == {"open", "closed"}:
        # 老四档的老约定：张↔闭之间补半张
        return "half"
    mid = _LADDER[min(len(_LADDER) - 1, (ra + rb) // 2)]
    return None if mid in (a, b) else mid


def _bridge(states: list[str], span: int = _LIP_BRIDGE) -> list[str]:
    """开合两个极端之间补过渡档。

    30fps 下张口直接翻成闭嘴，观众看到的是嘴唇「闪一下」。现实里嘴是先
    收到半开再闭合，补 1–2 格中间档，速度不变但读起来是动作不是错帧。
    """
    out: list[str] = []
    for st in states:
        if out:
            mid = _bridge_state(out[-1], st)
            if mid:
                out.extend([mid] * span)
        out.append(st)
    return out


def _mouth_states(mp3: str) -> list[str]:
    """把包络量化成逐格口型，带驻留，避免每 20ms 换一次嘴。"""
    hit = _STATE_CACHE.get(mp3)
    if hit is not None:
        return hit
    env = _envelope(mp3)
    # 分界线按这句自己的能量分布定：每个字都「重音」等于没有重音，
    # 固定阈值会让一半帧都停在张大嘴的那一档。
    heard = sorted(v for v in env if v >= _LIP_CLOSED)
    if heard:
        # 说话时大部分帧应该是「张着的」，半张只当过渡：原来 60 分位当分界，
        # 结果一半配音帧停在半张，远看就是全程闭嘴。
        t_half = heard[int(len(heard) * 0.30)]
        # 圆口只留给真正的重音：中文里 o/u 本来就少，四分之一帧都是圆口
        # 会像一直在「哦」，看久了比不动还假。
        t_open = heard[int(len(heard) * 0.88)]
    else:
        t_half, t_open = _LIP_HALF, _LIP_OPEN
    states: list[str] = []
    cand = "closed"
    run = _LIP_DWELL
    last = len(env) - 1
    for i, v in enumerate(env):
        # 往后看一格：口型比声音晚 40~60ms 就已经「不像在说话」了
        v = max(v, env[i + 1]) if i < last else v
        if v < _LIP_CLOSED:
            want = "closed"
        elif v < t_half:
            want = "half"
        elif v < t_open:
            want = "open"
        else:
            want = "round"
        if _LIP_RANK[want] > _LIP_RANK[cand]:
            # 张大立刻生效，不驻留：攻击段延迟是口型对不上的主因
            cand, run = want, _LIP_DWELL
        elif want == cand:
            run = _LIP_DWELL
        else:
            run += 1
            if run >= _LIP_DWELL:
                cand, run = want, _LIP_DWELL
        states.append(cand)
    # 100ms 是最短可读的口型段：短于它，观众读到的不是「这个字」，是「抽了一下」
    states = _settle(states, _LIP_MIN_RUN)
    states = _bridge(states)
    if len(_STATE_CACHE) > 64:
        _STATE_CACHE.clear()
    _STATE_CACHE[mp3] = states
    return states


_LIP_TRACK_CACHE: dict[tuple, list[str]] = {}


_LIP_WARNED = False
# 静音硬约束（口型范式第 4 条「开头不早开」）：低于 -45dBFS 连续 ≥120ms 就是
# 没人说话。90ms 最小驻留只防换档抖动、不防停顿——驻留跨过停顿尾巴，嘴就
# 留到静音里去了。
_LIP_DB_SILENT = -45.0
_LIP_TAIL_PAD = 0.120
_LIP_PAUSE_MIN = 0.100   # 算"句间停顿"的最短静音长度
_LIP_TAIL_CATCH = 0.060  # 长停顿前补闭的时长（收字尾）
# 验收口径（tools/lipsync_qa.py 的 `_silence_spec`）：10ms 不重叠格、阈值取
# 全段峰值 -40dB、连续 ≥120ms 才算「停顿」。25ms 重叠窗 + 绝对 -45dB 是另一把
# 尺子：句首低电平段里只要混进一个响样本，整格就不算静音，于是"我这边关了、
# 成片那格还开着"。同一部片子只能有一把尺——这里按验收的定义再补一遍，覆盖
# 整段静音区（含两端各一格余量），长度不变。
_LIP_SILENCE_REL_DB = -40.0
_LIP_SILENCE_MIN = 0.120
_DB10_CACHE: dict[str, list[float]] = {}
_DB_CACHE: dict[str, list[float]] = {}


def _envelope_db(mp3: str) -> list[float]:
    """原始 dBFS 包络（50Hz，25ms 窗、窗心对齐，与 tools/lipsync_qa.py 同尺）。

    `_envelope` 是减底噪后按 10/90 分位拉伸的**相对**响度，专门喂四档口型用的；
    它的 0 不代表静音，拿它判 -45dBFS 会把气声判成说话。静音约束要绝对电平，
    所以单独解一遍、单独缓存——窗长和窗心必须跟验收工具一致，否则我这边判
    "不静音"的格子，验收那边按 -45dB 还是算误开。
    """
    hit = _DB_CACHE.get(mp3)
    if hit is not None:
        return hit
    import array
    import math as _math
    import subprocess

    levels: list[float] = []
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", mp3, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "f32le", "-"], capture_output=True, timeout=180)
        raw = proc.stdout or b""
    except Exception:  # noqa: BLE001 - 缺 ffmpeg 就退化成「没有静音信息」
        raw = b""
    if raw:
        arr = array.array("f")
        arr.frombytes(raw[: len(raw) // 4 * 4])
        hop = 16000.0 / _LIP_HZ
        half = int(16000 * 0.025 / 2)
        n = int(len(arr) // hop)
        for i in range(n):
            c = int(i * hop + hop / 2)
            seg = arr[max(0, c - half):max(0, c + half)]
            if not seg:
                levels.append(-120.0)
                continue
            total = 0.0
            for v in seg:
                total += v * v
            rms = (total / len(seg)) ** 0.5
            levels.append(20.0 * _math.log10(max(rms, 1e-7)))
    _DB_CACHE[mp3] = levels
    return levels


def _envelope_db10(mp3: str) -> list[float]:
    """10ms 不重叠的 dBFS（与验收 `_silence_spec` 同一把尺）。

    和 `_envelope_db` 的区别不只是窗长：这里阈值要用**全段峰值 -40dB**，因为
    验收就是这么判的。两把尺在句首/句尾的低电平段会给出相反结论，只有钉住
    同一个定义，成片上「这格该不该闭」才不会有第二种答案。
    """
    hit = _DB10_CACHE.get(mp3)
    if hit is not None:
        return hit
    import array
    import math as _math
    import subprocess

    levels: list[float] = []
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", mp3, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "f32le", "-"], capture_output=True, timeout=180)
        raw = proc.stdout or b""
    except Exception:  # noqa: BLE001 - 缺 ffmpeg 就退化成「没有静音信息」
        raw = b""
    if raw:
        arr = array.array("f")
        arr.frombytes(raw[: len(raw) // 4 * 4])
        win = 160  # 10ms @16k
        for s in range(0, len(arr) - win + 1, win):
            total = 0.0
            for v in arr[s:s + win]:
                total += v * v
            rms = (total / win) ** 0.5
            levels.append(20.0 * _math.log10(max(rms, 1e-7)))
    _DB10_CACHE[mp3] = levels
    return levels


def _silence_run_frames(mp3: str, total: int) -> set[int]:
    """验收口径下「整段闭口」的帧号：峰值 -40dB、连 ≥120ms 的静音区全覆盖。

    两端各多闭一格（20ms）：成片是 30fps 从 50Hz 轨上按 int(t*50) 取样，取样
    相位可能落在静音区前一格，余量不够就漏出一帧张着的嘴。
    """
    db10 = _envelope_db10(mp3)
    out: set[int] = set()
    if not db10 or total <= 0:
        return out
    thr = max(db10) + _LIP_SILENCE_REL_DB
    cpf = 100.0 / float(_LIP_HZ)  # 每个 50Hz 帧盖几格 10ms
    need = int(round(_LIP_SILENCE_MIN * 100.0))
    i, n = 0, len(db10)
    while i < n:
        if db10[i] >= thr:
            i += 1
            continue
        j = i
        while j + 1 < n and db10[j + 1] < thr:
            j += 1
        if (j - i + 1) >= max(1, need):
            k0 = max(0, int(math.floor(i / cpf)) - 1)
            k1 = min(total, int(math.ceil((j + 1) / cpf)) + 1)
            out.update(range(k0, k1))
        i = j + 1
    return out


def _frame_db(mp3: str, total: int, fps: float = 30.0) -> list[float]:
    """30fps 逐帧平均 dB，用来在 2 帧闭口里挑更静的一帧。"""
    db10 = _envelope_db10(mp3)
    if not db10 or total <= 0:
        return []
    n_cells = len(db10)
    n = min(total, int(round(n_cells * 10.0 / 1000.0 * fps)))
    if n < 2:
        return []
    edges = [int(math.ceil(k * (1000.0 / fps) / 10.0)) for k in range(n + 1)]
    out = []
    for k in range(n):
        a0, b0 = edges[k], min(edges[k + 1], n_cells)
        if b0 <= a0:
            a0, b0 = min(a0, n_cells - 1), min(a0 + 1, n_cells)
        seg = db10[a0:b0]
        out.append(sum(seg) / len(seg))
    return out


def _film_frame_db(segs: list[dict], frames: int, fps: float = 30.0) -> list[float]:
    """成片帧号 → 该帧所在段的配音 dB（非台词帧记 -140）。

    成片帧和逐段轨之间隔着段位移与重采样，只有按段自己的 ``lip_t0`` 折回去取
    才是验收看的那条包络；不做这一步，放宽就会落在错帧上（f45a 实测无效）。
    """
    out = [-140.0] * frames
    for seg in segs:
        mp3 = seg.get("lip_audio")
        if not mp3:
            continue
        try:
            db = _envelope_db(str(mp3))
        except Exception:  # noqa: BLE001 - 量不出就交给逐段那两道
            continue
        if not db:
            continue
        t0 = seg.get("lip_t0")
        t0 = float(seg["start"] if t0 is None else t0)
        start, end = float(seg["start"]), float(seg["end"])
        for k in range(max(0, int(start * fps) - 1), min(frames, int(end * fps) + 2)):
            i = int(round((k / fps - t0) * _LIP_HZ))
            if 0 <= i < len(db):
                out[k] = db[i]
    return out


def _relax_stop_gaps(states: list[str], db_frame: list[float] | None = None) -> list[str]:
    """塞音空隙不得整段闭死：2 帧闭口里只把「更深的那一帧」改成微张。

    验收（lipsync_qa）把 40~80ms 的词内静音单列：整段全闭记 ``stop_misclosed``，
    必须 0；而 30fps 下 40~80ms 只能是 2 帧，规则 3「低于 -45dB 一律 closed」
    会把 /t/ /k/ 的爆破闭死。这里只放开一帧而不是两帧——两帧全放会把上一句尾
    的开口帧和新句子的开口帧连成一片，起点前凭空多出 67ms 的开口，直接撞
    「开场领先张开 ≤60ms」。逐帧 dB 只在两帧都算不清时用来挑更静的那帧。
    """
    out = list(states)
    n = len(out)
    db = db_frame or []
    i = 0
    while i < n:
        if out[i] != "closed":
            i += 1
            continue
        j = i
        while j + 1 < n and out[j + 1] == "closed":
            j += 1
        if (j - i + 1) == 2 and i > 0 and j + 1 < n:
            pick = i
            if len(db) >= n and db[j] < db[i]:
                pick = j
            out[pick] = "narrow"
        i = j + 1
    return out


def _enforce_quiet(states: list[str], mp3: str,
                   window: tuple[float, float] | None = None) -> list[str]:
    """静音必闭口，放在最后一步，长度不变。

    三条边界都是硬覆盖、不参与合并：
    1. 前导：第一个字起点之前的帧全 closed（edge 前有 0.2s 静音，20ms 前瞻加
       90ms 驻留会把嘴提前拉开）；
    2. 尾部：最后一个字结束 +120ms 之后全 closed；
    3. 静音格：dBFS 低于 -45dB 的帧一律 closed，不设最短连续长度。
       原先想留 120ms 保护词内塞音空隙，但验收（lipsync_qa）是逐格统计的：
       句间换气一旦只有 60~100ms，整段嘴就开在静音里，误开率照样超标。这种
       20ms 的闭口在成片 30fps 里落不满一帧，看得见的是"字缝里嘴合一下"，
       而不是抖动；反过来，留一条开着的嘴在停顿里最刺眼。
    4. 句尾收口：≥100ms 的停顿往前多闭 60ms。驻留/前瞻只在开头拉嘴，结尾没人
       管——字尾的能量已经在掉，嘴还张着冲进停顿，验收会算成"停顿里嘴是开的"
       （a00 就是这样：1.04~1.10 的句尾 + 1.12~1.22 的换气，被数成开场领先
       80ms）。60ms 只吃掉字尾衰减，不动音节主体。
    帧数契约不动：只在原轨上改档，不插帧、不删帧。
    """
    total = len(states)
    if total <= 0:
        return states
    out = list(states)
    closed: set[int] = set()
    fps = float(_LIP_HZ)
    db = _envelope_db(mp3)
    if db:
        # db 本来就是 50Hz（20ms 一格，与口型轨同格），帧号直接对应，别再乘 fps
        for i, v in enumerate(db):
            if v >= _LIP_DB_SILENT:
                continue
            if 0 <= i < total:
                closed.add(i)
        # 句尾收口：长停顿（≥100ms）前补 60ms 闭口
        i = 0
        while i < len(db):
            if db[i] >= _LIP_DB_SILENT:
                i += 1
                continue
            j = i
            while j + 1 < len(db) and db[j + 1] < _LIP_DB_SILENT:
                j += 1
            if (j - i + 1) >= int(round(_LIP_PAUSE_MIN * _LIP_HZ)):
                for k in range(max(0, i - int(round(_LIP_TAIL_CATCH * _LIP_HZ))), i):
                    if k < total:
                        closed.add(k)
            i = j + 1
    if window is not None:
        first, last = window
        for k in range(0, min(total, int(math.ceil(first * fps - 1e-9)))):
            closed.add(k)
        tail_from = max(0, int(math.floor((last + _LIP_TAIL_PAD) * fps + 1e-9)))
        for k in range(tail_from, total):
            closed.add(k)
    # 5. 验收口径整段覆盖：≥120ms 的停顿，嘴一帧都不许开（含两端余量）
    closed.update(_silence_run_frames(mp3, total))
    for k in closed:
        out[k] = "closed"
    # 6. 塞音空隙例外（最后一步，纯按口型轨做，不碰音频口径）
    return out


def _warn_envelope(reason) -> None:
    """逐字口型走不通时提示一次：静默回退会让人以为新路径生效了。"""
    global _LIP_WARNED
    if _LIP_WARNED:
        return
    _LIP_WARNED = True
    try:
        import sys  # noqa: PLC0415

        sys.stderr.write(f"[char_layer] 逐字口型不可用（{reason}），已回退包络四档\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 - 打日志失败也不能打断渲染
        pass


def _fit_track(states: list[str], n: float) -> list[str]:
    """把口型轨钉到 n 格：多截少补，长度不再由下游随手改。

    轨长只要不等于 dur*50，`_lip_mouth` 按 50Hz 索引就会整体慢放/快放，越长越
    漂。所以长度护栏只有这一处出口：最近邻重采样到 n，再补齐/截断。
    """
    n = max(1, int(round(float(n))))
    if not states:
        return ["closed"] * n
    m = len(states)
    if m == n:
        return list(states)
    if n == 1:
        return [states[0]]
    return [states[min(m - 1, int(round(i * (m - 1) / (n - 1))))] for i in range(n)]


def _mouth_states_text(mp3: str, text: str, dur: float = 0.0) -> list[str]:
    """逐字口型：段里带台词时交给 lipsync（协同发音/最小驻留在它内部做）。

    lipsync 是 backend-timeline 的模块，可能还没落地；拿不到或它报错一律
    回包络四档——新增路径不允许把片子打掉。缓存 key 必须带 text：同一条
    配音配不同台词，口型不能串。
    """
    key = (mp3, text, round(float(dur), 3))
    hit = _LIP_TRACK_CACHE.get(key)
    if hit is not None:
        return hit
    states: list[str] = []
    if text:
        try:
            # 包内相对导入优先：调用方（test/工具脚本）不一定把 export/ 塞进
            # sys.path，裸 import 会静默找不到模块、退回包络四档，逐字口型白做。
            try:
                from . import lipsync as _ls  # noqa: PLC0415
            except Exception:  # noqa: BLE001 - 不是包内调用（脚本直跑）
                import lipsync as _ls  # noqa: PLC0415
            raw = _ls.build(text, mp3, max(0.0, float(dur)), fps=_LIP_HZ) or []
            # 不认识的档落到 mid：宁可近似，也不要 KeyError/丢帧
            states = [s if s in _LIP_RANK else "mid" for s in raw]
            if not states:
                _warn_envelope("lipsync.build 没产出帧")
        except Exception as exc:  # noqa: BLE001 - 缺模块/它内部报错都回包络
            states = []
            _warn_envelope(exc)
    if states:
        # lipsync.build 内部已经做了 20ms 预判 + 90ms 最小驻留；这里再 _settle/
        # _bridge 会让轨变长（每次换档 +2 格），画到时间轴上就成了慢放——实测
        # 1677 格对 1609 格音频，全长漂 +1356ms。文本路径长度只许等于 dur*50。
        states = _fit_track(states, round(max(0.0, float(dur)) * _LIP_HZ))
        # 静音必闭口：长度不改，只把静音格的档改回 closed（范式第 4 条）
        states = _enforce_quiet(states, mp3, _word_window(mp3, text))
    else:
        states = _enforce_quiet(_mouth_states(mp3), mp3)
    if len(_LIP_TRACK_CACHE) > 64:
        _LIP_TRACK_CACHE.clear()
    _LIP_TRACK_CACHE[key] = states
    return states


def _lip_states(seg: dict, mp3: str) -> list[str]:
    """这段的口型轨：有 lip_text 走逐字口型，否则保持原包络四档。"""
    text = str(seg.get("lip_text") or "").strip()
    if not text:
        return _mouth_states(mp3)
    dur = float(seg.get("lip_dur") or 0.0)
    if dur <= 0.0:
        # 配音本身的长度就是这段的口型长度（lip_t0 再把它对到时间轴上）
        dur = len(_envelope(mp3)) / _LIP_HZ
    return _mouth_states_text(mp3, text, dur)


def _lip_mouth(seg: dict, t: float):
    """这一刻的嘴型；可能是四档老名，也可能是逐字口型的新档。没有配音返回 None。"""
    audio = seg.get("lip_audio")
    if not audio:
        return None
    states = _lip_states(seg, str(audio))
    if not states:
        return None
    t0 = seg.get("lip_t0")
    t0 = float(seg["start"] if t0 is None else t0)
    # 轨长 ≠ dur 时（插件/上游又插了帧）按实际密度索引，别整体慢放
    rate = _LIP_HZ
    dur = float(seg.get("lip_dur") or 0.0)
    if dur <= 0.0:
        try:
            dur = len(_envelope(str(audio))) / _LIP_HZ
        except Exception:  # noqa: BLE001 - 量不出长度就按 50Hz
            dur = 0.0
    if dur > 0.0 and abs(len(states) - dur * _LIP_HZ) > 0.05 * dur * _LIP_HZ:
        rate = len(states) / dur
    i = int((t - t0) * rate)
    if i < 0:
        return "closed"
    if i >= len(states):
        return "closed"
    return states[i]


# 不说话时也要有气：呼吸 4.4 秒一个来回、左右轻晃 6.7 秒一个来回。
# 幅度按画面高度折算（1920 高约 12px / 4px）——再小就看不出来，人物一静，
# 观众第一秒就发现「这是张图」。
BOB_PERIOD = 4.4
# 每种手势的重心偏向（度）与晃动幅度倍数：手指出去时人往前压，耸肩往后仰，
# 思考偏在一侧、幅度小，开心最活。换势那段（见 POSE_FADE_MIN）里按叠化权重混，
# 所以是「重心挪过去」而不是「换了一张图」。
_POSE_ATTITUDE = {
    "point": (-0.55, 1.10),
    "think": (0.30, 0.70),
    "shrug": (0.65, 1.30),
    "happy": (-0.35, 1.45),
    # 新立绘多出的四个表情也要有体态差：惊要后仰，怯要前倾，赞要点头，
    # 不然八张立绘动起来只有四种重心。
    "surprise": (-0.10, 1.32),
    "pout": (0.45, 1.18),
    "shy": (0.22, 0.86),
    "approve": (-0.25, 1.16),
    "wave": (-0.30, 1.55),
}

SWAY_PERIOD = 6.7

# 换姿势的过渡时长（秒）。这一段长度决定「自然」还是「跳帧」：权重曲线
# 再平滑，交叠只有三四帧时观众看到的仍然是硬切。
#
# 下限 0.55：30fps 下 17 帧。原来交叠自然形成时常常只剩 3~6 帧，读起来就是
# 「换了一张图」；17 帧够两个重心各起落一次，又短到读不成两次动作。
# 上限 0.95：差异最大的两势（idle→shrug）也得在 1 秒内收完，否则一句话里
# 换三次手势就变成一直在溶解。
# 这两个数不是手感，是照 30fps 的帧量化反推的：两端导数为 0 的曲线在边界上
# 一帧只走 ks(1/30/T) ≈ 10/T³，T=0.73 秒时正好压在 1e-3 以下——再短一帧
# 就看得见「蹦」那一下。
POSE_FADE_MIN = 0.55
POSE_FADE_MAX = 0.95
# 后段起点最多提前到「前段起点 + 0.12 秒」：再往前，观众还没看清前一个手势
POSE_SETTLE = 0.12
# 兜底：素材切得再碎也不许退化成硬切（30fps 下 6 帧）
POSE_FADE_FLOOR = 0.20
# 交叠最多吃掉一段的一半：全吃进去等于把这段压成 0，下一处交接又变成硬切
POSE_FADE_SHARE = 0.5
# 后段特别短、手移又要求长交叠时，上限放宽到「吃掉后段的 55%…… 85%」
# （见 plan_transitions）：宁可这段几乎整段都在换手，也不让手瞬移。
POSE_FADE_SHORT_SHARE = 0.55

# 手能有多快。姿态差只回答「像不像换了个姿势」，管不住大动作（伸手指、
# 摊手）里那只手每秒跑多少像素——观众跟的是指尖。这里按指尖峰值速度反推
# 时长：need = 峰值斜率 × 手移 / POSE_HAND_SPEED。380 设计稿像素/秒
# ≈ 1.2 米/秒（人物可视高 517px ≈ 1.6m），是抬手说话的上限，再快就是甩。
POSE_HAND_SPEED = 380.0
# 权重曲线形状：两端二次收尾、中间匀速。两端导数为 0 保证换势不「蹦」，
# 匀速段把峰值压到 1/(1-a) 倍平均速度：a=0.32 时 1.47 倍，而 smootherstep
# 是 1.875 倍——同样的位移，肉眼看到的最快那一下慢 22%。
_POSE_EASE = 0.32
# 起势最早可以提前到「前段起点 + 0.04 秒」：窗口不够时宁可把抬手的前摇
# （lead-in）往前提，也不把叠化压短。压短就是「手瞬间到位」。
POSE_LEAD_MIN = 0.04

# 姿态别名归族：同族是同一个体态（只有口型/眼睛在换），不该混姿态；
# 叫法不同但画的是同一张的，素材里确实存在（stand / base / neutral）。
_POSE_ALIAS = {
    "stand": "idle", "base": "idle", "neutral": "idle", "still": "idle",
    "hello": "wave", "hi": "wave", "greet": "wave",
    "nod": "approve", "ok": "approve", "agree": "approve",
    "smile": "happy", "laugh": "happy", "joy": "happy",
    "thinking": "think", "ponder": "think",
    "sorry": "shrug", "apologize": "shrug",
    "shock": "surprise", "wow": "surprise",
    "sad": "pout", "angry": "pout", "upset": "pout",
    "bashful": "shy", "embarrassed": "shy",
    "explain": "point",
}


def _pose_family(pose) -> str:
    """姿势归族：`stand` 和 `idle` 是同一张图，混姿态只会让重心来回蹭。"""
    key = str(pose or "idle").strip().lower()
    return _POSE_ALIAS.get(key, key)


def pose_difference(pose_a, pose_b, box_a=None, box_b=None) -> float:
    """两个姿势差多少：0 = 同一体态，1 = 完全换了个姿势。

    三路信号加权相加，缺哪路就重新归一：
    - 语义族：不同族先记 0.45，再按 `_POSE_ATTITUDE` 的重心/幅度向量差放大；
    - 位移：两张静帧内容框中心的相对位移（抬手、蹲下都会挪中心）；
    - 面积：内容框高度比的对数（换一个「大一号」的姿势，叠化要更长才不突兀）。
    `box_*` 是 character_assets.sprite_ratio 的 (高度比, 中心x比, 中心y比)。
    """
    fam_a, fam_b = _pose_family(pose_a), _pose_family(pose_b)
    if fam_a == fam_b:
        return 0.0
    bias_a, mult_a = _POSE_ATTITUDE.get(fam_a, (0.0, 1.0))
    bias_b, mult_b = _POSE_ATTITUDE.get(fam_b, (0.0, 1.0))
    # bias 差最大约 1.2、mult 差最大约 0.85：除 1.5 之后落在 0~1
    att = math.hypot(bias_a - bias_b, mult_a - mult_b)
    parts = [(0.55, 0.45 + 0.55 * min(1.0, att / 1.5))]
    if box_a and box_b:
        parts.append((0.30, min(1.0, math.hypot(box_a[1] - box_b[1],
                                                box_a[2] - box_b[2]) / 0.10)))
        if box_a[0] > 0 and box_b[0] > 0:
            parts.append((0.15, min(1.0, abs(math.log(box_a[0] / box_b[0])) / 0.25)))
    total = sum(w for w, _ in parts)
    return min(1.0, max(0.0, sum(w * v for w, v in parts) / total))


def pose_fade_for(diff: float) -> float:
    """姿态差 → 过渡时长（秒）：线性落在 [POSE_FADE_MIN, POSE_FADE_MAX]。"""
    d = min(1.0, max(0.0, float(diff)))
    return POSE_FADE_MIN + (POSE_FADE_MAX - POSE_FADE_MIN) * d


def _pose_peak() -> float:
    """姿态权重曲线的峰值斜率相对平均斜率（匀速中段 = 1/(1-a)）。"""
    return 1.0 / (1.0 - _POSE_EASE)


def _pose_ramp(x: float) -> float:
    """姿态权重曲线：两端二次收尾（导数 0），中间匀速段。

    smootherstep 峰值 1.875 倍平均速度，大动作的中段会被「甩」一下；这里
    峰值只有 1.47 倍，两端仍是一阶连续（换势不蹦），中段慢 22%。
    """
    x = min(1.0, max(0.0, float(x)))
    a = _POSE_EASE
    m = 1.0 / (1.0 - a)
    if x < a:
        return m * x * x / (2.0 * a)
    if x > 1.0 - a:
        y = 1.0 - x
        return 1.0 - m * y * y / (2.0 * a)
    return m * (x - a / 2.0)


# 手位量一次就够：路径 + mtime + 尺寸当键，同一张立绘在整条片子里只读一次
# （2048² 的 PNG 解一次几十毫秒，换姿势十几处，重算就是几百毫秒）。
_HAND_TIP_CACHE: dict[tuple, tuple | None] = {}
# 一对姿势的手移（设计稿像素）同进程内复用；键里带各素材目录的 mtime 戳，
# 素材换了自动重算。
_HAND_SHIFT_CACHE: dict[tuple, float] = {}
# 设计稿上人物内容框的高度（width=620 时量出来 517px）：素材是无量纲比，
# 换算成「每秒多少像素」得有这把尺子。
_HAND_DESIGN_H = 517.2


def _pose_files(root: Path, pose: str) -> list[Path]:
    """这套角色下这个姿势的静帧（优先 poses/，再退回 base/ 的站姿）。"""
    fam = _pose_family(pose)
    hits = sorted(root.glob(f"outfits/*/poses/pose_{fam}_closed.png"))
    if hits:
        return hits
    return sorted(root.glob("outfits/*/base/*base_closed.png"))


def _hand_tip(path: Path):
    """一张立绘的「手位」：肤色像素按脸的左右两侧各聚成一枚（归一到内容框）。

    量重心会被躯干和头发稀释（头发最宽、占了最外侧），量轮廓尖端量到的是
    头发；手是画面里除了脸以外唯一的一块肤色，按「离脸中心多远」去找它，
    抬手/指方向位移多少就是多少。
    """
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), int(st.st_mtime), st.st_size)
    if key in _HAND_TIP_CACHE:
        return _HAND_TIP_CACHE[key]
    tip = None
    try:
        import numpy as np
        from PIL import Image

        arr = np.asarray(Image.open(path).convert("RGBA"))
        r = arr[..., 0].astype(np.int16)
        g = arr[..., 1].astype(np.int16)
        b = arr[..., 2].astype(np.int16)
        a = arr[..., 3]
        skin = (a > 40) & (r > 150) & (r > b + 18) & (g > 110) & (b < 215) & (g < r)
        ys, xs = np.nonzero(skin)
        if len(xs) < 400:
            tip = None
        else:
            y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
            h = max(1, y1 - y0 + 1)
            w = max(1, x1 - x0 + 1)
            # 脸 = 肤色像素的上半坨；手的判定基准就是这张脸的中心
            # 脸 = 肤色最上面那一坨：它的外框要从「手」的候选里剔掉，否则
            # 脸的两侧边缘会把两只手各拽住一半，量出来永远是「没动」。
            top = ys <= y0 + 0.32 * h
            if int(top.sum()) < 200:
                top = np.ones(len(ys), dtype=bool)
            fx0, fx1 = int(xs[top].min()), int(xs[top].max())
            fy1 = int(np.percentile(ys[top], 97))
            fcx = 0.5 * (fx0 + fx1)
            pad = 0.03 * h
            in_face = ((ys <= fy1 + pad) & (xs >= fx0 - pad) & (xs <= fx1 + pad))
            dx = (xs - fcx) / h
            dy = (ys - y0) / h
            out = {}
            for side, keep in (("L", (dx < 0) & ~in_face), ("R", (dx > 0) & ~in_face)):
                if int(keep.sum()) < 80:
                    continue
                out[side] = (float(dx[keep].mean()), float(dy[keep].mean()))
            tip = out or None
    except Exception:  # noqa: BLE001 - 量不出来就当没有，绝不拦着出片
        tip = None
    if len(_HAND_TIP_CACHE) > 512:
        _HAND_TIP_CACHE.clear()
    _HAND_TIP_CACHE[key] = tip
    return tip


def hand_shift(pose_a, pose_b, outfit_root=None) -> float:
    """两个姿势之间那只手走了多少设计稿像素（各服装取中位数，量不出返回 0）。

    指尖位置归一化到内容框高度，再乘设计稿上 517px 的等效高度：换一套服装、
    换一个画布尺寸都不影响这个数。失败一律 0（只影响时长，不影响能不能出片）。
    """
    fam_a, fam_b = _pose_family(pose_a), _pose_family(pose_b)
    if fam_a == fam_b:
        return 0.0
    root = None
    try:
        if outfit_root is not None:
            root = Path(outfit_root)
            if root.parent.name != "outfits" and root.name == "outfits":
                root = None
            elif root.parent.name == "outfits":
                root = root.parent.parent
            elif (root / "outfits").is_dir():
                pass
            elif (root.parent / "outfits").is_dir():
                root = root.parent
            else:
                root = None
        if root is None:
            import character

            root = Path(character.outfit_root(None)).parent.parent
        files_a = _pose_files(root, fam_a)
        files_b = _pose_files(root, fam_b)
        if not files_a or not files_b:
            return 0.0
        stem_a = {(p.parent.parent.name, p.name): p for p in files_a}
        stamp = tuple(sorted((k[0], int(p.stat().st_mtime)) for k, p in stem_a.items()))
        ckey = (stamp, fam_a, fam_b)
        hit = _HAND_SHIFT_CACHE.get(ckey)
        if hit is not None:
            return hit
        vals = []
        for key_a, pa in sorted(stem_a.items()):
            name_b = pa.name.replace(f"pose_{fam_a}_", f"pose_{fam_b}_")
            pb = pa.parent.parent / key_a[0] / "poses" / name_b
            if not pb.exists():
                cand = [p for p in files_b if p.parent.parent.name == key_a[0]]
                pb = cand[0] if cand else None
            if pb is None:
                continue
            ta, tb = _hand_tip(pa), _hand_tip(pb)
            if not ta or not tb:
                continue
            # 两只手各自找对面最近的一枚：手臂交叉时左右会换边，
            # 钉死同侧会把「换手」量成「手没动」。
            a_pts = list(ta.values())
            b_pts = list(tb.values())
            if len(a_pts) > len(b_pts):
                a_pts, b_pts = b_pts, a_pts
            best = 0.0
            for p in a_pts:
                d = min(math.hypot(q[0] - p[0], q[1] - p[1]) for q in b_pts)
                best = max(best, d)
            if best > 0:
                vals.append(best)
        if not vals:
            return 0.0
        vals.sort()
        med = vals[len(vals) // 2] * _HAND_DESIGN_H
        _HAND_SHIFT_CACHE[ckey] = med
        return med
    except Exception:  # noqa: BLE001
        return 0.0


def pose_fade_need(pose_a, pose_b, diff: float, outfit_root=None) -> float:
    """过渡到底要多长：姿态差给的下限与「手别跑太快」取大。

    need = 峰值斜率 × 手移 / POSE_HAND_SPEED。手移量不出来就只看姿态差。
    """
    need = pose_fade_for(diff)
    px = hand_shift(pose_a, pose_b, outfit_root)
    if px > 0:
        need = max(need, _pose_peak() * px / POSE_HAND_SPEED)
    return need


def _shift_seg(seg: dict, dt: float) -> None:
    """整段平移。动画相位跟着挪，口型相位**钉死在音频在片里的位置**。

    口型不是跟着立绘走的：这一段配的声音在片子里固定在 `lip_t0` 那一秒起播，
    立绘为了跟上一段叠出过渡而提前 100ms 抬手，声音并不会跟着提前。以前这里把
    `lip_t0` 一起平移，于是每个句首人物都比声音早张嘴 ~120ms —— 静音段里那两三
    帧「提前张开」是这么来的，不是阈值问题，也不是包络问题。
    """
    seg["start"] = float(seg["start"]) + dt
    seg["end"] = float(seg["end"]) + dt
    if seg.get("anim_t0") is not None:
        seg["anim_t0"] = float(seg["anim_t0"]) + dt
    if seg.get("lip_t0") is None and seg.get("lip_audio"):
        # 没显式给相位就是「从原段首开始」：原段首 = 现段首 - dt
        seg["lip_t0"] = float(seg["start"]) - dt
    if seg.get("fade_in") is not None:
        # 入场提前了，但淡入要在原来的时刻结束：时长跟着缩（到 0 就是直接实心）
        seg["fade_in"] = max(0.0, float(seg["fade_in"]) + dt)


def plan_transitions(segments: list[dict], box_of=None) -> list[dict]:
    """给相邻两段铺一段看得见的过渡时间，并标出该不该混姿态。

    `_fill_gaps` 只把缝补上（前段 end 顶到后段 start），交叠长度是 0；权重
    曲线再平滑，一帧就跨过去的换手也只会读成硬切。这里先把后段 start 往前挪，
    造出与姿态差成正比的交叠（`pose_fade_for`）；挪不动（前段太短、已经贴着
    POSE_SETTLE、后段要入场）就把前段 end 往后延——总之不许退化成硬切。

    只改时间，不动素材与摆位。返回新表（原表不改），方便单测。
    """
    out = [dict(s) for s in sorted(segments, key=lambda s: float(s["start"]))]
    for i in range(1, len(out)):
        a, b = out[i - 1], out[i]
        a_start, a_end = float(a["start"]), float(a["end"])
        b_start, b_end = float(b["start"]), float(b["end"])
        if a_end < b_start:
            # 调这个函数前通常已经 _fill_gaps 过；单独用时也把缝补上，
            # 否则「没有交叠」会被误当成「已经接好」
            a["end"] = a_end = b_start
        box_a = box_of(a) if box_of else None
        box_b = box_of(b) if box_of else None
        diff = pose_difference(a.get("pose"), b.get("pose"), box_a, box_b)
        b["_pose_diff"] = diff
        b["_pose_mix"] = diff > 1e-6
        if not b["_pose_mix"]:
            # 同一个姿势只换了口型和眼睛：不混姿态，也不造交叠——两张一样的
            # 立绘叠半秒，只有嘴在跳，读起来是重影不是换姿势
            b["_xfade"] = 0.0
            continue
        len_a = max(1e-6, a_end - a_start)
        len_b = max(1e-6, b_end - b_start)
        cap = POSE_FADE_SHARE * min(len_a, len_b)
        root = a.get("_outfit_root") or b.get("_outfit_root")
        need = pose_fade_need(a.get("pose"), b.get("pose"), diff, root)
        b["_fade_need"] = need
        if need > cap:
            # 手移要求比「半段」还长：短短一句手势（只剩 0.3~0.4 秒）不能硬塞进
            # 半段里，否则那只手 0.2 秒走完 150px，读起来就是瞬移。允许吃掉后段
            # 85%（这段本来就短，看完手势就换下一句了），换手不压缩。
            cap = min(max(cap, POSE_FADE_SHORT_SHARE * len_b), 0.85 * len_b)
            b["_fade_short"] = True
        # 入场段提前起会把人从画面下沿提前升起来，和上一段叠在一起
        room = 0.0 if b.get("enter") else max(0.0, a_end - (a_start + POSE_SETTLE))
        target = min(cap, max(POSE_FADE_FLOOR, need))
        cur = max(0.0, min(a_end, b_end) - b_start)
        gap = max(0.0, target - cur)
        if gap > 0.0:
            adv = min(gap, room, max(0.0, b_start))
            if adv > 1e-9:
                # 只把起点提前，收尾时刻不动：整段往前铺，人不会提前消失
                b_end_fix = float(b["end"])
                _shift_seg(b, -adv)
                b["end"] = b_end_fix
                b_start -= adv
                cur += adv
                gap -= adv
                b["_lead"] = float(b.get("_lead") or 0.0) + adv
            if gap > 1e-9:
                # 窗口还不够长就继续把起势往前挪（lead-in）：只给前段留
                # POSE_LEAD_MIN 让观众看清上一个手势，剩下的时间都给这次抬手。
                room2 = (0.0 if b.get("enter") else
                         max(0.0, a_end - (a_start + POSE_LEAD_MIN) - (b_start - adv)))
                adv2 = min(gap, room2, max(0.0, b_start - adv))
                if adv2 > 1e-9:
                    b_end_fix = float(b["end"])
                    _shift_seg(b, -adv2)
                    b["end"] = b_end_fix
                    b_start -= adv2
                    cur += adv2
                    gap -= adv2
                    b["_lead"] = float(b.get("_lead") or 0.0) + adv2
            if gap > 1e-9:
                # 挪不动就把前段往后延：合上最后一截，别留下硬切
                new_end = min(b_end, b_start + target)
                if new_end > a_end:
                    a["end"] = new_end
                    a_end = new_end
            b["_xfade"] = max(0.0, min(a_end, b_end) - b_start)
        else:
            b["_xfade"] = cur
    return out


def _norm_width(seg: dict, width: float) -> int:
    """立绘归一化：设计稿宽度 × 这套服装这个动作的尺度系数。

    只改宽度不改 x/y：`_stage_canvas` 把立绘等比缩到 (w, w)，人物可视高度
    就是内容框占比 × w；x/y 仍是内容框左上角的锚点，头顶脚底位置不变。
    系数来自素材本身（见 character_assets.outfit_scale），量不出来就是 1.0。
    """
    try:
        import character_assets as CA

        root = seg.get("_outfit_root")
        if root is None:
            import character

            root = character.outfit_root(seg.get("outfit") or None)
        scale = CA.outfit_scale(Path(root), seg.get("pose") or "idle")
    except Exception:  # noqa: BLE001 - 换算不出来就按原尺寸摆，别拦着出片
        scale = 1.0
    return max(16, int(round(float(width) * scale)))


def _pose_box(seg: dict):
    """这个动作静帧的内容框（相对画布）：量姿态差用，量不出来返回 None。"""
    try:
        import character_assets as CA

        root = seg.get("_outfit_root")
        if root is None:
            import character

            root = character.outfit_root(seg.get("outfit") or None)
        return CA.sprite_ratio(Path(root), seg.get("pose") or "idle")
    except Exception:  # noqa: BLE001 - 量不出来就当只有语义差
        return None


def anim_frames(outfit_root: Path, name: str, key_green) -> list:
    """动画序列帧（已抠绿、RGBA）。缺素材返回空表，调用方自己退回静帧。"""
    key = (str(outfit_root), name)
    hit = _ANIM_CACHE.get(key)
    if hit is not None:
        return hit
    base = Path(outfit_root)
    files = sorted((base / "anim" / name).glob("*.png"))
    if not files:
        # 传进来的是角色根目录（只有站姿）时，动画去默认那套服装里取
        for cand in sorted((base / "outfits").glob("*/anim/" + name)):
            files = sorted(cand.glob("*.png"))
            if files:
                break
    frames = []
    if files:
        from PIL import Image

        for p in files:
            try:
                frames.append(key_green(Image.open(p).convert("RGBA")))
            except Exception:  # noqa: BLE001 - 单帧坏了不该让整片挂掉
                continue
    _ANIM_CACHE[key] = frames
    return frames


def _piece(seg: dict, sprite, stage, canvas: tuple[int, int]):
    """立绘摆进画布后裁到内容框；同一张图同一摆位只做一次。"""
    from PIL import Image

    w, x, y = stage
    key = (sprite[0], w, x, y, canvas)
    hit = _PIECE_CACHE.get(key)
    if hit is not None:
        return hit
    cfg = {"width": w, "x": x, "y": y}
    full = _stage_canvas(sprite[1], cfg, canvas[0], canvas[1])
    bb = full.getbbox()
    if not bb:
        val = (None, 0, 0)
    else:
        val = (full.crop(bb), bb[0], bb[1])
    if len(_PIECE_CACHE) >= _PIECE_LIMIT:
        _PIECE_CACHE.clear()
    _PIECE_CACHE[key] = val
    return val


def _smootherstep(x: float) -> float:
    """两端速度为 0 的过渡：起手和收尾都不「一顿」再加速。"""
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


def _active(segments: list[dict], t: float) -> list[tuple[dict, float, float]]:
    """该帧要画哪些分段，以及 (alpha 权重, 姿态权重)。

    两套权重分开：alpha 要前段推上去（否则交叠区里人物发灰、体检判「透」），
    姿态/重心必须两端速度为 0（否则换手势那一刻身体会突然加速）。
    """
    hits = [s for s in segments if float(s["start"]) <= t < float(s["end"])]
    if not hits:
        return []
    if len(hits) == 1:
        return [(hits[0], 1.0, 1.0)]
    hits.sort(key=lambda s: float(s["start"]))
    a, b = hits[0], hits[1]
    span = max(1e-6, min(float(a["end"]), float(b["end"])) - float(b["start"]))
    x = min(1.0, max(0.0, (t - float(b["start"])) / span))
    # 权重前段加速：线性叠化等于让新人从 0 一路半透明地爬到 1，交叠区整段
    # 都在「发灰」，体检直接判人物发透。前半段推上去，只在头一两帧透。
    k = 1.0 - (1.0 - x) ** 2
    if not b.get("_pose_mix", True):
        # 同一个体态（只换了口型/眼睛）：姿态权重钉在前段。让两帧一模一样的
        # 身体互相权重混，重心会在半秒里来回蹭两下，看着像「抖了一下」。
        return [(a, 1.0 - k, 1.0), (b, k, 0.0)]
    ks = _pose_ramp(x)
    return [(a, 1.0 - k, 1.0 - ks), (b, k, ks)]


def _alpha_at(seg: dict, t: float) -> float:
    a = 1.0
    fi = float(seg.get("fade_in") or 0.0)
    fo = float(seg.get("fade_out") or 0.0)
    if fi > 0:
        a = min(a, max(0.0, (t - float(seg["start"])) / fi))
    if fo > 0:
        a = min(a, max(0.0, (float(seg["end"]) - t) / fo))
    return a


def _fill_gaps(segs: list[dict]) -> list[dict]:
    """分段之间留空，人物就会闪一下消失：把前一段顺延到下一段开头。"""
    segs = sorted(segs, key=lambda s: float(s["start"]))
    for a, b in zip(segs, segs[1:]):
        if float(a["end"]) < float(b["start"]):
            a["end"] = b["start"]
    return segs


def _static_sprite(seg: dict, render):
    """按口型画一张立绘，同一组参数只画一次（render 内部有缓存，重采样没有）。"""
    key = ("render", seg.get("outfit") or "", seg.get("pose") or "idle",
           seg.get("mouth") or "closed", seg.get("eyes") or "open",
           seg.get("expression") or "")
    hit = _SPRITE_CACHE.get(key)
    if hit is None:
        hit = render(pose=seg.get("pose") or "idle", mouth=seg.get("mouth") or "closed",
                     eyes=seg.get("eyes") or "open", outfit=seg.get("outfit") or None,
                     expression=seg.get("expression") or None)
        if len(_SPRITE_CACHE) > 240:
            _SPRITE_CACHE.clear()
        _SPRITE_CACHE[key] = hit
    return key, hit


def _sprite(seg: dict, t: float, render, key_green, mouth: str | None = None):
    """该时刻的立绘：有动画序列就用序列帧，否则按口型画一张。"""
    root = seg.get("_outfit_root")
    anim = str(seg.get("anim") or "")
    if mouth:
        seg = dict(seg)
        seg["mouth"] = mouth
    if anim and root is not None:
        frames = anim_frames(Path(root), anim, key_green)
        if frames:
            fps = float(seg.get("anim_fps") or 15.0)
            off = float(seg.get("anim_t0") or 0.0)
            n = int(max(0.0, t - float(seg["start"]) + off) * fps)
            # 一次性动作（挥手）播完就停在末帧，不能对帧数取模——取模就是每秒
            # 重挥一次，观众看到的是"反复挥手"。末帧与首帧是同一个放手站姿，
            # 停住等于自然把手放下；循环动作（眨眼）才取模。
            if str(seg.get("anim_mode") or "loop") == "once":
                idx = min(n, len(frames) - 1)
            else:
                idx = n % len(frames)
            frame = frames[idx]
            if mouth:
                # 序列帧自带一张烘好的嘴，不跟着配音换口型就是"挥手时嘴冻住"
                import mouth_art  # noqa: PLC0415 - 只在需要的帧上才付这个 import

                frame = mouth_art.apply(frame, mouth,
                                        key=("anim", str(root), anim, idx))
            return ("anim", str(root), anim, idx), frame
    return _static_sprite(seg, render)


def _segment_sprites(seg: dict, render, key_green):
    """一段里可能出现的全部立绘：先枚举出来，才知道人物层该裁多大。"""
    root = seg.get("_outfit_root")
    anim = str(seg.get("anim") or "")
    if anim and root is not None:
        frames = anim_frames(Path(root), anim, key_green)
        if frames:
            return [("anim", str(root), anim, k) for k in range(len(frames))], frames
    if seg.get("lip_audio"):
        # 口型按配音逐帧挑：并集框要把所有可能出现的嘴型都算进去，
        # 否则换嘴时人物被裁掉一截。逐字口型档多，但只有带台词的段才多算。
        states = _LIP_STATES if seg.get("lip_text") else ("closed", "half", "open", "round")
        keys, sprites = [], []
        for st in states:
            s2 = dict(seg)
            s2["mouth"] = st
            k, sp = _static_sprite(s2, render)
            keys.append(k)
            sprites.append(sp)
        return keys, sprites
    key, sprite = _static_sprite(seg, render)
    return [key], [sprite]


def _clamp_lip_to_audio(lip_log: dict[int, str], segs: list[dict],
                        fps: int) -> int:
    """成片级最后一道：把「静音里还开着」的帧改回 closed。

    逐段那道 `_enforce_quiet` 在轨上做，但轨到成片帧之间还有重采样、段位移、
    上游另插的口型段；每换一层都可能漏掉一两帧。实测 f40 在 4 处换场开头各开了
    2 帧（67ms，−78dBFS 里嘴是 mid），验收按「开场领先张开 133ms」记一笔。
    这里直接对成片帧号复核：拿该段自己的配音包络，帧落在段内且当格 <−45dBFS
    就闭口。只改档、不动轨道长度，所以不会把口型整体挪慢。
    """
    if not lip_log:
        return 0
    fixed = 0
    for seg in segs:
        mp3 = seg.get("lip_audio")
        if not mp3:
            continue
        try:
            db = _envelope_db(str(mp3))
        except Exception:  # noqa: BLE001 - 量不出包络就交给上游那两道
            continue
        if not db:
            continue
        t0 = seg.get("lip_t0")
        t0 = float(seg["start"] if t0 is None else t0)
        start = float(seg["start"])
        end = float(seg["end"])
        f0 = max(0, int(math.floor((start - t0) * _LIP_HZ)))
        f1 = min(len(db), int(math.ceil((end - t0) * _LIP_HZ)) + 1)
        for k in range(max(0, int(start * fps) - 1), min(len(lip_log), int(end * fps) + 2)):
            st = lip_log.get(k)
            if st is None or st == "closed":
                continue
            i = f0 + int(round((k / fps - start) * _LIP_HZ))
            if i < f0 or i >= f1:
                continue
            if db[i] < _LIP_DB_SILENT:
                lip_log[k] = "closed"
                fixed += 1
    return fixed


def build(segments: list[dict], *, width: int, height: int, fps: int, workdir: Path,
          duration: float, render, key_green, name: str = "char_layer",
          seed: int = 0,
          log=print) -> tuple[Path, int, int] | None:
    """把分段表渲染成一条带 alpha 的视频，裁到人物实际占的框。

    返回 (视频, x, y)：整幅 1080×1920 存下来四周透明留白占去大半体积
    （9 秒片 141MB），裁掉后成片按偏移叠回去，画面一模一样。
    """
    from PIL import Image

    segs = [dict(s) for s in segments
            if float(s.get("end") or 0) - float(s.get("start") or 0) > 0.01]
    # 同稿多母版：把呼吸/重心/眨眼/镜头感的相位挪一下，两条片子各有各的
    # 身体节奏，逐帧都不一样；不说话时也在动的观感不变，只是动的位置不同。
    _ph = (int(seed) % 360) * math.pi / 180.0 if seed else 0.0
    if not segs:
        return None
    segs = _fill_gaps(segs)
    # 补完缝还不够：交叠长度是 0 的时候，一帧就换完 = 硬切。这一步按姿态差
    # 铺出 0.45~0.80 秒的交叠，同一体态（只换口型）则不造交叠、也不混姿态。
    segs = plan_transitions(segs, _pose_box)
    total = max(float(duration), max(float(s["end"]) for s in segs))
    frames = max(1, int(round(total * fps)))

    def _stage_of(seg: dict):
        return (_norm_width(seg, float(seg.get("width") or 620)),
                int(float(seg.get("x") or 560)),
                int(float(seg.get("y") or 1014)))

    # 入场（从画面下沿升上来）会在开始时把人往下推一截：框底要留出这段行程，
    # 否则入场那半秒脚和小腿是被切平的一刀。
    enter_px = 0
    if any(isinstance(s.get("enter"), dict) for s in segs):
        enter_px = int(round(height * 0.16))

    # 先摆一遍所有人的姿态，取并集框：人物只会在这个框里动
    bx0, by0, bx1, by1 = width, height, 0, 0
    anim_stats: list[int] = []
    max_ph = 0
    for seg in segs:
        stage = _stage_of(seg)
        keys, sprites = _segment_sprites(seg, render, key_green)
        anim_stats.append(len(keys))
        for key, sprite in zip(keys, sprites):
            piece, px, py = _piece(seg, (key, sprite), stage, (width, height))
            if piece is None:
                continue
            bx0, by0 = min(bx0, px), min(by0, py)
            bx1 = max(bx1, px + piece.width)
            by1 = max(by1, py + piece.height)
            max_ph = max(max_ph, piece.height)
    if bx1 - bx0 < 8 or by1 - by0 < 8:
        return None
    # 呼吸/轻晃的位移要留在框内：三段正弦叠起来最大摆幅是 1.51 倍 bob、
    # 左右 1.5 倍 sway，留少了就把头顶和脚切平，人物每秒矮一点再长回来
    bob = max(4, int(round(height / 160.0)))
    sway = max(2, int(round(height / 480.0)))
    # 倾斜/呼吸的扩边：绕脚底中线最多转 2.6°（含手势偏向），头顶横移约 4.5% 身高，
    # 不预扩边，头顶和发梢就会被框边切掉一条
    rot = int(math.ceil(max_ph * 0.048)) + 3
    pad_x = int(math.ceil(sway * 1.6)) + 2 + rot
    pad_y = int(math.ceil(bob * 1.55)) + 2 + rot
    # 偶数尺寸：后面还要 scale，奇数尺寸会被迫补边
    bx0, by0 = max(0, bx0 - pad_x), max(0, by0 - pad_y)
    bw = min(width, bx1 + pad_x) - bx0
    bh = min(height, by1 + pad_y + enter_px) - by0
    bw, bh = bw - (bw % 2), bh - (bh % 2)

    tmp = Path(workdir) / f".{name}_frames"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    draws = 0
    # 换手势才点头：口型分段也走同一段入口，按段起点触发就变成每个字跳一下
    _prev_pose: dict[int, str] = {}
    _last_pose = ""
    for _s in sorted(segs, key=lambda x: float(x["start"])):
        _prev_pose[id(_s)] = _last_pose
        _last_pose = _pose_family(_s.get("pose"))
    # 人物身高整条片子必须一条直线：素材或摆位一变，观众读到的就是「人在跳」
    heights: list[int] = []
    lip_log: dict[int, str] = {}
    # 成片级静音兜底必须跑在**画之前**：它改的是「这一帧画什么嘴」，帧一旦编码
    # 进 webm 就改不回来。f41 的兜底跑在编码之后 —— lip.json 认了错，画面还是
    # 静音里张着嘴，验收照样记「开场领先张开 2 帧 / 67ms」。先把整条轨口型试算
    # 一遍、按段配音包络闭掉不该开的帧，再带着结论去画。
    _pre_lip: dict[int, str] = {}
    for _i in range(frames):
        for _s, _w, _wp in _active(segs, _i / fps):
            _m = _lip_mouth(_s, _i / fps)
            if _m:
                _pre_lip[_i] = _m
    try:
        _clamped = _clamp_lip_to_audio(_pre_lip, segs, fps)
    except Exception as _exc:  # noqa: BLE001 - 兜底崩了也不能没有人物层
        _clamped = -1
        sys.stderr.write(f"[char_layer] 成片级静音兜底失败: {_exc!r}\n")
        sys.stderr.flush()
    _pre_closed = {k for k, v in _pre_lip.items() if v == "closed"}
    # 塞音空隙（40~80ms）在成片这一层再放一帧微张：逐段那道看不见段位移后的帧号，
    # 验收却按成片帧号数「整段闭死」。放在画之前，帧编码进 webm 就改不回来了。
    _film_states = [_pre_lip.get(i, "closed") for i in range(frames)]
    try:
        _film_states = _relax_stop_gaps(_film_states, _film_frame_db(segs, frames, fps))
    except Exception as _exc:  # noqa: BLE001 - 兜底崩了也不能没有人物层
        sys.stderr.write(f"[char_layer] 塞音放宽失败: {_exc!r}\n")
        sys.stderr.flush()
    _lip_fix = {i: st for i, st in enumerate(_film_states)
                if st != _pre_lip.get(i, "closed")}
    for i in range(frames):
        t = i / fps
        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        active = _active(segs, t)
        entering = False
        for idx, (seg, weight, _wpose) in enumerate(active):
            _mt = _lip_mouth(seg, t)
            if _mt:
                if i in _pre_closed:
                    # 兜底判过：这一帧配音在静音里，四档口型统统闭口
                    _mt = "closed"
                _mt = _lip_fix.get(i, _mt)
                # 记一张「第几帧张的什么口型」：误差不靠肉眼猜，出片后拿它对帧
                lip_log[i] = _mt
            key, sprite = _sprite(seg, t, render, key_green, _mt)
            piece, px, py = _piece(seg, (key, sprite), _stage_of(seg), (width, height))
            if piece is None:
                continue
            # 叠化时旧段画满、新段按时间盖上去：两边都乘权重会把人物画透，
            # 换手势/换屏那半秒就成了半透明的鬼影。单段时才用段自己的淡入淡出
            a = _alpha_at(seg, t) if len(active) == 1 else (1.0 if idx == 0 else weight)
            # 入场：out-cubic 上升 + 同步淡入。硬出现（一开片就杵在那儿）是最典型的
            # 「贴图感」—— 人物得从画面外进来，观众才相信她在场景里
            e_dy, e_a = 0.0, 1.0
            ent = seg.get("enter")
            if isinstance(ent, dict) and enter_px:
                _ed = max(1e-3, float(ent.get("duration") or 0.55))
                _p = min(1.0, max(0.0, (t - float(seg["start"])) / _ed))
                # 位移走 smoothstep：out-cubic 初速是 3，人一开片是「弹」上来的，
                # 两头都慢的曲线才读成「站起来」。透明度仍按 out-cubic 快速拉起——
                # 跟着位移曲线走的话，前几帧只有半透明轮廓，量身高会偏小。
                _s = _p * _p * (3.0 - 2.0 * _p)
                e_dy = enter_px * (1.0 - _s)
                e_a = 1.0 - (1.0 - _p) ** 3
                entering = _p < 1.0
            a *= e_a
            if a < 0.999:
                piece = piece.copy()
                piece.putalpha(piece.getchannel("A").point(lambda v, k=a: int(v * k)))
            # 缓慢呼吸 + 轻晃：让「没在说话」的段落也在动
            # 三个不成整数倍的周期叠着走：单条正弦到顶点会停一会儿，
            # 叠起来才没有「正好都不动」的时刻
            dy = (bob * math.sin(2.0 * math.pi * t / BOB_PERIOD + _ph)
                  + bob * 0.34 * math.sin(2.0 * math.pi * t / 2.9 + 0.7 + _ph)
                  + bob * 0.17 * math.sin(2.0 * math.pi * t / 1.7 + 2.3 + _ph))
            dx = (sway * math.sin(2.0 * math.pi * t / SWAY_PERIOD + 1.1 + _ph)
                  + sway * 0.5 * math.sin(2.0 * math.pi * t / 3.7 + _ph))
            dy += e_dy
            # 换姿势时给一下小跳：立绘本身没换动作，只靠倾斜和呼吸太"稳"，
            # 0.26 秒的低幅起落读起来像答话时点了一下头。
            if _prev_pose.get(id(seg), "") != _pose_family(seg.get("pose")):
                _bh = (t - float(seg["start"])) / 0.42
                if float(seg["start"]) > 1e-6 and 0.0 <= _bh < 1.0:
                    # 正弦在起点速度最大，接上前一段的静止就是「猛地一顿」；
                    # 换成两端速度都为零的 smoothstep，只当重心沉了一下
                    _bs = _bh * _bh * (3.0 - 2.0 * _bh)
                    dy -= 4.6 * _bs
            # 亚像素位移：按整像素取整会出现几帧完全一样，观感上就是卡住
            fx, fy = px - bx0 + dx, py - by0 + dy
            ox, oy = int(round(fx)), int(round(fy))
            # 只有嘴在动、肩膀和重心一动不动，读起来还是贴纸：
            # 呼吸让身高微微起伏（±0.6%），倾斜让重心在两只脚之间换，
            # 支点压在脚底中线，脚不会跟着漂。合成一次仿射，不额外花钱。
            breath = 1.0 + 0.006 * math.sin(2.0 * math.pi * t / 3.30 + _ph)
            bias = mult = 0.0
            for seg_a, _wa, wgt in active:
                # 按族取体态：素材里 `stand`/`idle`、`hi`/`wave` 是同一张图，
                # 按名字查表会让同姿势叠化时重心凭空跳一下
                b_le, m_le = _POSE_ATTITUDE.get(_pose_family(seg_a.get("pose")),
                                                (0.0, 1.0))
                bias += b_le * wgt
                mult += m_le * wgt
            lean = math.radians(
                mult * (0.9 * math.sin(2.0 * math.pi * t / 6.10 + 0.4 + _ph)
                        + 0.45 * math.sin(2.0 * math.pi * t / 2.30 + 1.9 + _ph)) + bias)
            pw, ph = piece.size
            nw, nh = pw + 2 * rot, ph + 2 * rot
            cos_l, sin_l = math.cos(lean), math.sin(lean)
            m00, m01 = cos_l, sin_l
            # 注意这是「输出坐标 → 素材坐标」的逆变换：想让人矮一点，逆矩阵的
            # y 系数要变大（乘），不是变小。量过：绕脚底转 2.6° 身高只多 3px（0.5%，
            # 在 3% 容差内），不必再补偿，补偿写反了反而会让人凭空长 4%。
            m10, m11 = -sin_l / breath, cos_l / breath
            cx, cy = pw / 2.0 + rot, float(ph + rot)
            a = (m00, m01, pw / 2.0 - m00 * cx - m01 * cy)
            b = (m10, m11, float(ph) - m10 * cx - m11 * cy)
            piece = piece.transform((nw, nh), Image.AFFINE, a + b,
                                    resample=Image.BICUBIC)
            ox, oy = ox - rot, oy - rot
            canvas.alpha_composite(piece, (ox, oy))
            draws += 1
        if i % 3 == 0 and not entering:
            _bb = canvas.getchannel("A").getbbox()
            if _bb:
                heights.append(_bb[3] - _bb[1])
        canvas.save(tmp / f"f{i:05d}.png")
    # qtrle 只做行程编码：一条 30 秒的无损 argb 要 450MB，临时盘先撑爆。
    # vp9 带 alpha 把同样的画面压到二十分之一，解码端 ffmpeg 也认。
    mov = Path(workdir) / f"{name}.webm"
    cmd = ["ffmpeg", "-v", "error", "-y", "-framerate", str(fps),
           "-i", str(tmp / "f%05d.png"),
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
           "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
           "-auto-alt-ref", "0", str(mov)]
    try:
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _mix: dict[str, int] = {}
    for _st in lip_log.values():
        _mix[_st] = _mix.get(_st, 0) + 1
    (Path(workdir) / f"{name}_lip.json").write_text(
        json.dumps({"fps": fps, "frames": frames,
                    "states": {str(k): v for k, v in sorted(lip_log.items())},
                    "mix": _mix, "lip_clamped_to_audio": _clamped},
                   ensure_ascii=False))
    log(json.dumps({"layer": name, "frames": frames, "segments": len(segs),
                    "draws": draws, "box": [bx0, by0, bw, bh],
                    # 每段实际拿到几帧：全是 1 就说明动画素材没接进来，
                    # 人物只剩呼吸，出片时会被当成贴图
                    "anims": anim_stats,
                    "h": ([min(heights), sorted(heights)[len(heights) // 2], max(heights)]
                          if heights else []),
                    "lip_mix": _mix, "lip_clamped_to_audio": _clamped,
                    "mov_mb": round(mov.stat().st_size / 1e6, 1)}, ensure_ascii=False))
    return mov, bx0, by0


def _stage_canvas(figure, cfg: dict, width: int, height: int):
    """摆位实现只有一份，在 jianying_engine 里；这里只做延迟引用。"""
    from jianying_engine import _stage_canvas as impl

    return impl(figure, cfg, width, height)
