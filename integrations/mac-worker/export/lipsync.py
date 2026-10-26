"""逐字口型轨：把一句话和它的配音变成逐格口型序列。

三个入口：

- :func:`build` 只要口型轨（长度 = ``round(duration*fps)``，默认 50Hz）。
- :func:`build_words` 额外给 QA 一份 meta：时间来源、逐字时间表、覆盖率。
- :func:`words_json` 走 edge-tts 合成时把逐字边界落成 sidecar JSON，下次零网络复用。

时间来源按可复现性从高到低：

1. 同目录同名 ``*.words.json``（edge-tts 的 WordBoundary，或任何外部工具给的逐字表）；
2. 音频包络做音节切分：找能量谷 → 有声段 → 按字数分配，静音帧一律闭口；
3. 都不行就按字数均分（``source="uniform"``）。

口型映射用 pypinyin 取韵母：a→wide、o/uo→round、e/ei→mid、i→narrow、u/ü→pucker，
声母 m/b/p→closed、齿音→teeth。一个字里前半给声母、后半给韵母——这一段决定
「像不像在说话」。没装 pypinyin 时退回韵母类降级表，不让链路断。

协同发音按 char_layer 已验证的规则：口型轨整体提前 20ms（早一点像在跟声音走，
晚 40–60ms 就已经「不像说话」），开口方向立即生效、收口方向要过最小驻留
（90ms），最后再扫一遍把短于 90ms 的段并进前一段。
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# canonical 口型名：出片、QA、素材命名都以这份顺序为准
STATE_ORDER: tuple[str, ...] = (
    "closed",   # m/b/p 闭唇，静音
    "wide",     # a
    "mid",      # e / ei
    "narrow",   # i
    "round",    # o / uo
    "pucker",   # u / ü
    "teeth",    # s/z/c/zh/ch/sh/r/j/q/x，微开露齿
    "half",     # 过渡档，兼容旧档
)

_DEFAULT_FPS = 50
# 口型提前量：20ms 比声音早一点点。再早会被读成「说错」，晚 40–60ms 就不像了。
LOOKAHEAD_MS = 20
# 最短可读口型段：短于 90ms 观众读到的不是「这个字」，是「抽了一下」
MIN_DWELL_MS = 90

# 开口大小排序：只看「这次换档是不是张嘴」，张嘴立刻生效，收口才驻留
_RANK = {
    "closed": 0,
    "half": 1,
    "pucker": 1,
    "teeth": 2,
    "narrow": 2,
    "mid": 2,
    "round": 2,
    "wide": 3,
}

_INITIAL_MOUTH = {
    "m": "closed",
    "b": "closed",
    "p": "closed",
    # f 是唇齿音，画面读起来更接近露齿而不是闭唇
    "f": "teeth",
    "s": "teeth",
    "z": "teeth",
    "c": "teeth",
    "zh": "teeth",
    "ch": "teeth",
    "sh": "teeth",
    "r": "teeth",
    "j": "teeth",
    "q": "teeth",
    "x": "teeth",
}

# 韵母主元音 → 口型。按 a > o > e > ü > u > i 的优先级取第一个命中的，
# 因为「韵腹」决定嘴张多大，「uo」是圆而非撮口。
_FINAL_ORDER = (
    ("a", "wide"),
    ("o", "round"),
    ("e", "mid"),
    ("v", "pucker"),   # pypinyin 的 ü 写作 v（lüe / nü）
    ("u", "pucker"),
    ("i", "narrow"),
)

# pypinyin 不可用时的降级表：只看韵母类（六类常见字），够出片，不追求精确
_FALLBACK_FINALS = {
    "a": "wide", "ia": "wide", "ua": "wide", "ai": "wide", "uai": "wide",
    "ao": "wide", "iao": "wide", "an": "wide", "ian": "wide", "uan": "wide",
    "ang": "wide", "iang": "wide", "uang": "wide",
    "o": "round", "uo": "round", "ou": "round", "ong": "round", "iong": "round",
    "e": "mid", "ei": "mid", "en": "mid", "eng": "mid", "er": "mid",
    "ui": "mid", "uei": "mid",
    "i": "narrow", "in": "narrow", "ing": "narrow", "ie": "narrow",
    "iu": "narrow", "iou": "narrow",
    "u": "pucker", "un": "pucker", "uen": "pucker",
    "v": "pucker", "ve": "pucker", "van": "pucker", "vn": "pucker",
}

_EN_FINAL = {"a": "wide", "e": "mid", "i": "narrow", "o": "round", "u": "pucker"}

_PUNCT = set("，。！？；：、,.!?;:…—～~ \t\n\r\"'“”‘’（）()【】[]《》<>·-")

_CACHE: dict[tuple[str, str, float, int], list[str]] = {}
_CACHE_LIMIT = 64

_HAN = re.compile(r"[\u4e00-\u9fff]")


class _Pinyin:
    """pypinyin 的可选包装：有就用，没有就用降级表。"""

    def __init__(self) -> None:
        self._lazy = None
        self._style = None
        try:
            from pypinyin import Style, lazy_pinyin  # noqa: PLC0415 - 可选依赖
        except Exception:
            return
        self._lazy = lazy_pinyin
        self._style = Style

    def initials(self, ch: str) -> str:
        if self._lazy is None:
            return _FALLBACK_INITIALS.get(ch, "")
        try:
            got = self._lazy(ch, style=self._style.INITIALS, strict=False)
        except Exception:
            return _FALLBACK_INITIALS.get(ch, "")
        return (got[0] if got else "").lower()

    def finals(self, ch: str) -> str:
        if self._lazy is None:
            return _FALLBACK_FINALS.get(ch, "")
        try:
            got = self._lazy(ch, style=self._style.FINALS, strict=False)
        except Exception:
            return ""
        return (got[0] if got else "").lower()


# 降级表只覆盖最常用的闭唇/齿音声母，其余按韵母决定
_FALLBACK_INITIALS = {
    "妈": "m", "妈": "m", "木": "m", "米": "m", "吗": "m",
    "不": "b", "白": "b", "本": "b", "北": "b", "边": "b",
    "怕": "p", "跑": "p", "片": "p", "平": "p", "朋": "p",
    "是": "sh", "说": "sh", "时": "sh", "声": "sh", "少": "sh",
    "在": "z", "字": "z", "走": "z", "做": "z", "最": "z",
    "着": "zh", "这": "zh", "中": "zh", "真": "zh", "只": "zh",
    "小": "x", "心": "x", "像": "x", "先": "x", "向": "x",
    "就": "j", "叫": "j", "讲": "j", "间": "j", "见": "j",
    "吃": "ch", "出": "ch", "成": "ch", "长": "ch", "场": "ch",
}

_RUNTIME_LIB = Path(os.path.expanduser("~/CreatorStudio/runtime/lib"))
if _RUNTIME_LIB.is_dir():
    # 宿主机上 pypinyin 是 uv --target 装在 runtime/lib 的，不装进系统 site-packages
    sys.path.insert(0, str(_RUNTIME_LIB))

_PYINYIN = _Pinyin()


def _final_mouth(final: str) -> str:
    """韵母 → 口型。没有韵母（纯声母/英文单词）返回 mid。"""
    if not final:
        return "mid"
    v = final.replace("ü", "v")
    for key, mouth in _FINAL_ORDER:
        if key in v:
            return mouth
    return "mid"


def _text_mouth(ch: str) -> str:
    """一个非汉字字符（英文/数字/符号）的口型。"""
    low = ch.lower()
    if low in _EN_FINAL:
        return _EN_FINAL[low]
    if ch.isdigit():
        return "mid"
    return "mid"


def _char_mouths(ch: str) -> tuple[str, str]:
    """返回 (声母口型, 韵母口型)；无声母时两档相同。"""
    if _HAN.match(ch):
        initial = _PYINYIN.initials(ch)
        final = _PYINYIN.finals(ch)
        mouth = _final_mouth(final)
        lead = _INITIAL_MOUTH.get(initial, mouth)
        return lead, mouth
    mouth = _text_mouth(ch)
    return mouth, mouth


def spoke_chars(text: str) -> list[str]:
    """去掉标点空白，按字切分（一个汉字/字母/数字就是一格时间片）。"""
    return [c for c in text if c not in _PUNCT and not c.isspace()]


def _sidecar(audio: Path) -> Path:
    return audio.with_suffix(".words.json")


def load_words(audio: Path) -> list[dict[str, Any]] | None:
    """读同目录同名 ``*.words.json``，坏文件当没有。"""
    path = _sidecar(audio)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    chars = blob.get("chars")
    if not isinstance(chars, list) or not chars:
        return None
    out: list[dict[str, Any]] = []
    for item in chars:
        if not isinstance(item, dict):
            return None
        ch = str(item.get("ch", ""))
        if not ch:
            continue
        try:
            t = float(item.get("t", 0.0))
            d = float(item.get("d", 0.0))
        except (TypeError, ValueError):
            return None
        if d <= 0:
            continue
        out.append({"ch": ch, "t": max(0.0, t), "d": d})
    return out or None


def _decode(audio: Path, rate: int = 16000) -> array.array | None:
    """ffmpeg 解成 16k 单声道 s16，纯 stdlib 算包络。没有 ffmpeg 返回 None。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", str(rate),
             "-f", "s16le", "-"],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    pcm = array.array("h")
    pcm.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm


def _envelope(pcm: array.array, rate: int = 16000, hop_ms: int = 10) -> list[float]:
    """每 10ms 的 RMS，归一化到 0–1。"""
    hop = max(1, int(rate * hop_ms / 1000))
    env: list[float] = []
    for start in range(0, len(pcm), hop):
        chunk = pcm[start:start + hop]
        if not chunk:
            break
        acc = 0
        for v in chunk:
            acc += v * v
        env.append(math.sqrt(acc / len(chunk)) / 32768.0)
    peak = max(env) if env else 0.0
    if peak <= 0:
        return env
    return [min(1.0, v / peak) for v in env]


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return ordered[idx]


def _spans(env: list[float], hop: float) -> list[tuple[float, float]]:
    """包络 → 有声段（秒）。阈值取这句自己的分布，别用固定值。"""
    if not env:
        return []
    loud = [v for v in env if v > 0.01]
    if not loud:
        return []
    floor = max(0.03, _percentile(loud, 0.10) * 0.6)
    voiced = [v >= floor for v in env]
    # 60ms 以内的断点并回有声：单字里塞音的空挡不该切成两个字
    gap = max(1, int(0.06 / hop))
    i = 0
    while i < len(voiced):
        if not voiced[i]:
            j = i
            while j + 1 < len(voiced) and not voiced[j + 1]:
                j += 1
            if i > 0 and j + 1 < len(voiced) and (j - i + 1) <= gap:
                for k in range(i, j + 1):
                    voiced[k] = True
            i = j + 1
        else:
            i += 1
    spans: list[tuple[float, float]] = []
    i = 0
    while i < len(voiced):
        if voiced[i]:
            j = i
            while j + 1 < len(voiced) and voiced[j + 1]:
                j += 1
            start, end = i * hop, (j + 1) * hop
            # 短于 40ms 的碎片不是字，是噪声
            if end - start >= 0.04:
                spans.append((start, end))
            i = j + 1
        else:
            i += 1
    return spans


def _syllable_timeline(text: str, audio: Path, duration: float) -> list[dict[str, Any]] | None:
    """按能量谷切音节并分字。失败返回 None（交给均分）。"""
    pcm = _decode(audio)
    if not pcm:
        return None
    env = _envelope(pcm)
    spans = _spans(env, 0.01)
    chars = spoke_chars(text)
    if not chars or not spans:
        return None
    total = sum(end - start for start, end in spans)
    if total <= 0:
        return None
    # 每个有声段分多少字：按段长占全部有声时长的比例，至少 1 个字，
    # 这样「三连短句」不会全挤在最后一段
    raw = [max(1, round((end - start) / total * len(chars))) for start, end in spans]
    while sum(raw) > len(chars):
        i = max(range(len(raw)), key=lambda k: raw[k])
        if raw[i] <= 1:
            break
        raw[i] -= 1
    while sum(raw) < len(chars):
        i = max(range(len(raw)), key=lambda k: spans[k][1] - spans[k][0])
        raw[i] += 1
    out: list[dict[str, Any]] = []
    cursor = 0
    for (start, end), count in zip(spans, raw):
        if count <= 0:
            continue
        step = (end - start) / count
        for k in range(count):
            if cursor >= len(chars):
                break
            out.append({"ch": chars[cursor], "t": start + k * step, "d": step})
            cursor += 1
    if cursor < len(chars):
        return None
    if duration > 0:
        out = [c for c in out if c["t"] < duration]
    return out or None


def _uniform_timeline(text: str, duration: float) -> list[dict[str, Any]]:
    chars = spoke_chars(text)
    if not chars or duration <= 0:
        return []
    step = duration / len(chars)
    return [{"ch": ch, "t": i * step, "d": step} for i, ch in enumerate(chars)]


def _first_voiced(audio: Path) -> float | None:
    """包络里第一个算「出声」的时刻（秒）。校准逐字表用。"""
    pcm = _decode(audio)
    if not pcm:
        return None
    env = _envelope(pcm)
    if not env:
        return None
    loud = max(env)
    floor = max(0.04, loud * 0.12)
    for i, v in enumerate(env):
        if v >= floor:
            return i * 0.01
    return None


def _expand_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """edge-tts 的中文边界是按词的（"短片"一组、"跟着"一组），组内按字数均分。

    不展开就会把「短片」当成一个 4 秒的字，逐字表直接失效。
    """
    out: list[dict[str, Any]] = []
    for item in words:
        units = spoke_chars(item["ch"])
        if not units:
            continue
        step = item["d"] / len(units)
        for i, ch in enumerate(units):
            out.append({"ch": ch, "t": item["t"] + i * step, "d": step})
    return out


def _calibrate(words: list[dict[str, Any]], audio: Path) -> list[dict[str, Any]]:
    """把逐字表对齐到音频真实的第一个有声帧。

    edge-tts 的 offset 从 0.1s 起（前面是一段静音），如果按 offset 直接铺，
    口型会整体比声音晚 100ms —— 那正是「对不上」的来源。这里用包络量出真实
    起点，差得多就整体平移；差得不多（< 80ms）就认为 offset 已经是相对音频
    起点的，不动。
    """
    if not words:
        return words
    heard = _first_voiced(audio)
    if heard is None:
        return words
    delta = heard - words[0]["t"]
    if abs(delta) < 0.08:
        return words
    shifted = [dict(c, t=max(0.0, c["t"] + delta)) for c in words]
    return shifted


def _timeline(text: str, audio: Path, duration: float) -> tuple[list[dict[str, Any]], str]:
    words = load_words(audio)
    if words:
        return _calibrate(_expand_words(words), audio), "words"
    syll = _syllable_timeline(text, audio, duration)
    if syll:
        return syll, "syllable"
    return _uniform_timeline(text, duration), "uniform"


def _frames(chars: list[dict[str, Any]], duration: float, fps: int) -> list[str]:
    """逐字表 → 逐格口型（未做驻留/前瞻）。"""
    total = max(1, round(duration * fps))
    track = ["closed"] * total
    for item in chars:
        ch = item["ch"]
        start = item["t"]
        span = max(1e-3, item["d"])
        lead_mouth, final_mouth = _char_mouths(ch)
        first = max(0, int(round(start * fps)))
        last = min(total, int(round((start + span) * fps)))
        if last <= first:
            last = min(total, first + 1)
        # 前半声母、后半韵母：这个分界是「像在说话」的关键
        mid = first + max(1, (last - first) // 2) if lead_mouth != final_mouth else last
        for i in range(first, min(total, mid)):
            track[i] = lead_mouth
        for i in range(min(total, mid), last):
            track[i] = final_mouth
    return track


def _coarticulate(track: list[str], fps: int) -> list[str]:
    """前瞻 + 收口驻留 + 短段合并，三条都按 char_layer 的实测结论来。"""
    if not track:
        return track
    lead = max(0, int(round(LOOKAHEAD_MS / 1000 * fps)))
    min_run = max(2, int(math.ceil(MIN_DWELL_MS / 1000 * fps)))
    total = len(track)
    out: list[str] = []
    last = track[0]
    run = min_run
    for i in range(total):
        # 往后看 1 格：口型略早于声音，观众才觉得是「跟着字走」
        want = track[i + lead] if i + lead < total else "closed"
        if _RANK.get(want, 1) > _RANK.get(last, 1):
            last, run = want, 0
        elif want == last:
            run = 0
        else:
            run += 1
            if run >= min_run:
                last, run = want, 0
        out.append(last)
    return _settle(out, min_run)


def _settle(track: list[str], min_run: int) -> list[str]:
    """把短于 min_run 的段并进相邻段。反复扫到稳定：一次扫过去会漏掉
    「并完又变短」的连锁，最后还是会闪一下。
    """
    out = list(track)
    if min_run <= 1 or len(out) < 2:
        return out
    for _ in range(64):
        runs: list[list[int]] = []   # [state_id, start, count]
        i = 0
        while i < len(out):
            j = i
            while j + 1 < len(out) and out[j + 1] == out[i]:
                j += 1
            runs.append([i, j - i + 1])
            i = j + 1
        if len(runs) < 2:
            return out
        short = next(((k, runs[k][1]) for k in range(len(runs)) if runs[k][1] < min_run), None)
        if short is None:
            return out
        k, count = short
        start = runs[k][0]
        target = runs[k - 1][0] if k > 0 else runs[k + 1][0]
        fill = out[target]
        for i in range(start, start + count):
            out[i] = fill
    return out


def _silence_mask(audio: Path, duration: float, fps: int) -> list[bool]:
    """哪几格是静音。只认够长的静音段：字与字之间 40ms 的能量谷是塞音，
    不是静音，把它也涂成闭口，嘴就会一路在抖。
    """
    total = max(1, round(duration * fps))
    pcm = _decode(audio)
    if not pcm:
        return [False] * total
    env = _envelope(pcm)
    if not env:
        return [False] * total
    heard = [v for v in env if v > 0.01]
    if not heard:
        return [True] * total
    floor = max(0.02, _percentile(heard, 0.08) * 0.5)
    mask = [False] * total
    # 静音段：连续低于 floor，允许 60ms 的短脉冲（气声、轻声尾音）
    gap = max(1, int(0.06 / 0.01))
    spans: list[list[int]] = []
    i = 0
    while i < len(env):
        if env[i] < floor:
            j = i
            while j + 1 < len(env) and env[j + 1] < floor:
                j += 1
            if spans and (i - spans[-1][1]) <= gap:
                spans[-1][1] = j
            else:
                spans.append([i, j])
            i = j + 1
        else:
            i += 1
    for start, end in spans:
        if (end - start + 1) * 0.01 < 0.10:   # 短于 100ms 不是静音
            continue
        t0, t1 = start * 0.01, (end + 1) * 0.01
        for frame in range(total):
            t = frame / fps
            if t0 <= t < t1:
                mask[frame] = True
    return mask


def _cache_key(text: str, audio: Path, fps: int) -> tuple[str, str, float, int]:
    try:
        mtime = audio.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (text, str(audio), mtime, fps)


def build(text: str, audio: Path, duration: float, *, fps: int = _DEFAULT_FPS) -> list[str]:
    """逐格口型，长度 = ``round(duration*fps)``。带 (text, audio 路径+mtime, fps) 缓存。"""
    audio = Path(audio)
    key = _cache_key(text, audio, fps)
    hit = _CACHE.get(key)
    if hit is not None:
        return list(hit)
    chars, _source = _timeline(text, audio, duration)
    track = _frames(chars, duration, fps)
    track = _coarticulate(track, fps)
    # 静音一定闭口，且静音段够长（>=100ms），不会被后面的驻留合并吃掉
    mask = _silence_mask(audio, duration, fps)
    track = ["closed" if m else st for st, m in zip(track, mask)]
    min_run = max(2, int(math.ceil(MIN_DWELL_MS / 1000 * fps)))
    track = _settle(track, min_run)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = list(track)
    return track


def build_words(
    text: str,
    audio: Path,
    duration: float,
    fps: int = _DEFAULT_FPS,
) -> tuple[list[str], dict[str, Any]]:
    """同 :func:`build`，另给 QA 一份 meta：时间来源、逐字表、覆盖率。"""
    audio = Path(audio)
    chars, source = _timeline(text, audio, duration)
    track = build(text, audio, duration, fps=fps)
    spoken = max(1, len(spoke_chars(text)))
    described = min(spoken, len(chars))
    span = sum(c["d"] for c in chars) if chars else 0.0
    meta: dict[str, Any] = {
        "source": source,
        "fps": fps,
        "frames": len(track),
        "chars": chars,
        "char_count": described,
        "spoken_count": spoken,
        "coverage": round(described / spoken, 4),
        "timed_seconds": round(span, 3),
        "duration": round(float(duration), 3),
    }
    return track, meta


_VENV_PYTHON = Path(
    os.environ.get(
        "LIPSYNC_VENV_PYTHON",
        str(Path.home() / "CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python"),
    )
)
_GEN_SCRIPT = (
    "import asyncio, json, sys\n"
    "import edge_tts\n"
    "text, out, voice, rate = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]\n"
    "async def main():\n"
    "    chars = []\n"
    "    comm = edge_tts.Communicate(text, voice, rate=rate, boundary='WordBoundary')\n"
    "    with open(out, 'wb') as fh:\n"
    "        async for chunk in comm.stream():\n"
    "            if chunk['type'] == 'audio':\n"
    "                fh.write(chunk['data'])\n"
    "            elif chunk['type'] == 'WordBoundary':\n"
    "                chars.append({'ch': chunk['text'], 't': chunk['offset'] / 1e7,\n"
    "                              'd': chunk['duration'] / 1e7})\n"
    "    print(json.dumps(chars, ensure_ascii=False))\n"
    "asyncio.run(main())\n"
)


def words_json(text: str, audio: Path, out: Path) -> Path:
    """edge-tts 合成并把逐字边界落成 sidecar JSON，返回 sidecar 路径。

    已有 sidecar 就直接返回；音频已存在但没有 sidecar 时只补写边界（重跑合成
    会换一条采样，口型轨跟着变，复现性会丢）。
    """
    audio = Path(audio)
    out = Path(out)
    origin = _sidecar(audio)
    if origin.exists() and origin != out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(origin.read_text(encoding="utf-8"), encoding="utf-8")
        return out
    voice = os.environ.get("LIPSYNC_VOICE", "zh-CN-XiaoxiaoNeural")
    rate = os.environ.get("LIPSYNC_RATE", "+0%")
    if not _VENV_PYTHON.exists():
        raise RuntimeError(f"edge-tts 解释器不存在：{_VENV_PYTHON}（可设 LIPSYNC_VENV_PYTHON）")
    audio.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(_VENV_PYTHON), "-c", _GEN_SCRIPT, text, str(audio), voice, rate],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 or not audio.exists():
        raise RuntimeError((proc.stderr or proc.stdout or "edge-tts 无输出").strip()[-400:])
    rows = json.loads(proc.stdout.strip() or "[]")
    blob = {"voice": voice, "rate": rate, "text": text, "chars": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(blob, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
