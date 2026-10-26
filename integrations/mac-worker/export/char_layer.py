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
# 口型开合大小排序，用来判断这次换档是张嘴还是收口
_LIP_RANK = {"closed": 0, "half": 1, "open": 2, "round": 2}


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


def _bridge(states: list[str], span: int = _LIP_BRIDGE) -> list[str]:
    """开合两个极端之间补半开。

    30fps 下张口直接翻成闭嘴，观众看到的是嘴唇「闪一下」。现实里嘴是先
    收到半开再闭合，补 1–2 格半开，速度不变但读起来是动作不是错帧。
    """
    out: list[str] = []
    for st in states:
        if out and {out[-1], st} == {"open", "closed"}:
            out.extend(["half"] * span)
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


def _lip_mouth(seg: dict, t: float):
    """这一刻的嘴型：closed / half / open / round。没有配音数据返回 None。"""
    audio = seg.get("lip_audio")
    if not audio:
        return None
    states = _mouth_states(str(audio))
    if not states:
        return None
    t0 = seg.get("lip_t0")
    t0 = float(seg["start"] if t0 is None else t0)
    i = int((t - t0) * _LIP_HZ)
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
# 思考偏在一侧、幅度小，开心最活。换势那 0.22 秒里按叠化权重混，
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
    ks = _smootherstep(x)
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
        # 口型按配音逐帧挑：并集框要把四种嘴型都算进去，否则换嘴时人物被裁掉一截
        keys, sprites = [], []
        for st in ("closed", "half", "open", "round"):
            s2 = dict(seg)
            s2["mouth"] = st
            k, sp = _static_sprite(s2, render)
            keys.append(k)
            sprites.append(sp)
        return keys, sprites
    key, sprite = _static_sprite(seg, render)
    return [key], [sprite]


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
    total = max(float(duration), max(float(s["end"]) for s in segs))
    frames = max(1, int(round(total * fps)))

    def _stage_of(seg: dict):
        return (int(float(seg.get("width") or 620)),
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
        _last_pose = str(_s.get("pose") or "")
    # 人物身高整条片子必须一条直线：素材或摆位一变，观众读到的就是「人在跳」
    heights: list[int] = []
    lip_log: dict[int, str] = {}
    for i in range(frames):
        t = i / fps
        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        active = _active(segs, t)
        entering = False
        for idx, (seg, weight, _wpose) in enumerate(active):
            _mt = _lip_mouth(seg, t)
            if _mt:
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
            if _prev_pose.get(id(seg), "") != str(seg.get("pose") or ""):
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
                b_le, m_le = _POSE_ATTITUDE.get(str(seg_a.get("pose") or ""), (0.0, 1.0))
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
                    "mix": _mix}, ensure_ascii=False))
    log(json.dumps({"layer": name, "frames": frames, "segments": len(segs),
                    "draws": draws, "box": [bx0, by0, bw, bh],
                    # 每段实际拿到几帧：全是 1 就说明动画素材没接进来，
                    # 人物只剩呼吸，出片时会被当成贴图
                    "anims": anim_stats,
                    "h": ([min(heights), sorted(heights)[len(heights) // 2], max(heights)]
                          if heights else []),
                    "lip_mix": _mix,
                    "mov_mb": round(mov.stat().st_size / 1e6, 1)}, ensure_ascii=False))
    return mov, bx0, by0


def _stage_canvas(figure, cfg: dict, width: int, height: int):
    """摆位实现只有一份，在 jianying_engine 里；这里只做延迟引用。"""
    from jianying_engine import _stage_canvas as impl

    return impl(figure, cfg, width, height)
