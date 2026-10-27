#!/usr/bin/env python3
"""vertical_short.py — 竖屏factcard → 母版 mp4，纯 ffmpeg/Pillow，不起剪映。

剪映草稿要人点导出，这条不需要：卡面、立绘、配音全部先在本地落成文件，
再交给 timeline_render 合成一条 1080×1920 的 mp4。同一条 spec 也能进容器跑。

版式与 ``factcard`` 引擎一致：卡面就是 ``render_previews`` 烧好字的那张，
立绘复用 ``jianying_engine._character_frame``（含影子与描边柔光），
所以「剪映草稿」和「mp4」看到的不是两种片子。

用法::

    python3 export/vertical_short.py /tmp/one.json --outdir /tmp/vs1 --mp4 /tmp/vs1.mp4
"""

from __future__ import annotations

import hashlib
import shutil
import argparse
import json
import os
import re
import zlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKER = HERE.parent
sys.path.insert(0, str(WORKER))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(WORKER / "tools"))

import char_layer as CL  # noqa: E402
import character  # noqa: E402
import factcard  # noqa: E402
import jianying_engine as JE  # noqa: E402
import make_episode as MEP  # noqa: E402
import timeline_render as TR  # noqa: E402
from slides_render import _tts  # noqa: E402

DEFAULT_VOICE = factcard.DEFAULT_VOICE
# 收尾卡比正文多停一点：观众要时间读完那句结论再走
TAIL_HOLD = 0.35
FADE = 0.22
XFADE = 0.40
# 换手势的交叉长度：太短是「跳一下」，太长两只手同时在画面里像重影
# 换手势的叠化只有 0.1 秒：0.22 秒的线性叠化里，人物有三分之一时间处在
# 两张立绘互相透着的状态，体检判「发透」，眼睛也看得出鬼影。0.1 秒读起来
# 仍是「接住」，但透的那一两帧一闪就过。
# 换姿势的叠化时长：0.10 秒等于「一帧半就换完」，观众看到的是动作突然换挡。
# 拉到 0.28 秒，跟卡面 0.4 秒转场同拍，动作是接过去的，不是切过去的。
POSE_XFADE = 0.40
# 换手势躲开眨眼：两个动作贴在一起，观众看到的是抽搐
POSE_BLINK_GUARD = 0.35
TAIL = 1.00  # 话说完再定一秒：立刻黑屏是最廉价的收尾  # 相邻两屏叠化时长：整屏硬切是「PPT 翻页」最重的一笔


class VerticalError(RuntimeError):
    """素材/配音/渲染任一步失败。"""


def _dur(path: Path) -> float:
    """时长只用 worker 配好的 ffprobe：launchd 的 PATH 里没有 homebrew。"""
    r = subprocess.run([TR.FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise VerticalError(f"读不出配音时长: {path} {r.stderr.strip()[:200]}") from None


def _scene_duration(audio: float, last: bool) -> float:
    """画面长度跟着配音走，多留的只有气口。"""
    pad = 0.18 + (TAIL_HOLD if last else 0.0)
    # 上限放宽到 12s：念白长过 9s 时原来会被硬切，后半句叠到下一屏去
    return max(1.6, min(12.0, audio + pad))


def _fit_title(text: str, max_w: int, sizes=(92, 84, 76, 66, 60)) -> tuple[int, list[str]]:
    """标题挑一个能排进版心的字号，最多两行；排不下就换小一号。"""
    from PIL import Image, ImageDraw

    measure = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    for size in sizes:
        lines = factcard._wrap_text(measure, text, factcard._font(size), max_w)
        if len(lines) <= 2:
            return size, lines
    return sizes[-1], lines


def _measure():
    from PIL import Image, ImageDraw
    return ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _text_layer(line: str, size: int, workdir: Path | None, name: str, *,
                fill=(255, 255, 255, 240)) -> tuple[tuple[int, int], Path]:
    """把一行字渲成紧贴字形的透明 PNG，返回 ((w,h), path)。"""
    from PIL import Image, ImageDraw

    font = factcard._font(size)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    box = probe.textbbox((0, 0), line, font=font)
    pad = max(4, size // 8)
    w = max(2, box[2] - box[0]) + pad * 2
    w += w % 2  # 时间轴按 scale=w:-2 缩放，宽度必须偶数
    h = max(1, box[3] - box[1]) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((pad - box[0], pad - box[1]), line, font=font, fill=fill)
    path = Path(workdir or "/tmp") / name
    img.save(path, "PNG")
    return (w, h), path


def _block_layers(text: str, size: int, top: int, workdir: Path, prefix: str, *,
                  align: str, left: int, max_w: int, fill, spacing: float = 1.42,
                  center_in: tuple[int, int] | None = None,
                  tail_fill=None) -> list[dict]:
    """一段正文按 factcard 的排版规则切成逐行贴片（行高、断句、对齐都跟卡面一致）。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    draw = _measure()
    font = factcard._font(size)
    rows = [ln for ln in factcard._wrap_text(draw, text, font, max_w) if ln]
    if center_in is not None and rows:
        # 封面空出一整片：字块在「进度条下沿到人物头顶」之间居中，
        # 避免一屏里上半截挤着字、下半截空着人
        lo, hi = center_in
        block_h = int(len(rows) * size * spacing)
        top = max(lo, int(lo + (hi - lo - block_h) / 2))
    # 一句话的落点（最后一个逗号后的短句）染强调色：整块正文一个颜色，
    # 读者得自己找重点；落点先亮起来，眼睛才跟着这句话走。
    cut = int(factcard._tail_cut(text)) if tail_fill else -1
    pos = 0
    out: list[dict] = []
    y = top
    for n, ln in enumerate(rows):
        head, tail = ln, ""
        if cut >= 0:
            lead = len(ln) - len(ln.lstrip())
            take = max(0, min(len(ln) - lead, cut - pos))
            head = ln[:lead + take]
            tail = ln[lead + take:]
            pos += len(ln)
        (w, _h), png = _text_layer(head, size, workdir, f"{prefix}_l{n}.png", fill=fill)
        x = left + (max_w - w) // 2 if align == "center" else left
        out.append({"png": png, "x": int(x), "y": int(y), "w": int(w)})
        if tail:
            (tw, _th), tpng = _text_layer(tail, size, workdir, f"{prefix}_t{n}.png",
                                          fill=tail_fill)
            out.append({"png": tpng, "x": int(x + w), "y": int(y), "w": int(tw)})
        y += int(size * spacing)
    return out


def _hero_split(text: str) -> tuple[str, str]:
    """正文里「开头那个数」值不值得放大成主视觉。

    只在开头分句很短、又带数字时才拆：把「216 秒」提到 100 多号、「，109 段」压成
    小字，观众一眼先拿数字再读那半句。拆不开就返回空，走原来的整段排版。
    """
    text = " ".join(str(text or "").split())
    if "，" not in text:
        return "", text
    lead, rest = text.split("，", 1)
    lead, rest = lead.strip(), rest.strip().lstrip("，。；,; ")
    if not rest or not (2 <= len(lead) <= 8):
        return "", text
    if not any(ch.isdigit() for ch in lead):
        return "", text
    return lead, rest


def _scene_accent(scene: dict, idx: int, total: int) -> tuple:
    """这一屏卡面的强调色，跟卡底色温和 HUD 同一套，别让正文亮成另一个牌子。"""
    pal = scene.get("palette")
    n = len(factcard.PALETTES)
    i = int(pal) if pal is not None else (0 if idx == 0 else (n - 1 if idx == total - 1 else idx))
    return factcard.PALETTES[int(i) % n][2]


def _cover_hook_size(text: str) -> int:
    """封面钩子取能排进三行的最大字号：字大才有钩子，行数多了就不像标题。"""
    draw = _measure()
    for size in (76, 72, 66, factcard.HOOK_SIZE):
        rows = [ln for ln in factcard._wrap_text(draw, " ".join(str(text or "").split()),
                                                 factcard._font(size), 700) if ln]
        if len(rows) <= 3:
            return size
    return factcard.HOOK_SIZE


def _cta_copy(script: dict, cta_text: str) -> dict:
    """收尾卡的字：一句主张压一句补白，字号按行宽反选。

    面板高度得跟着这块字走：字到哪卡面到哪。原来卡底一律压在 1000，
    字只写到 880，收尾屏下半截空一大块，卡框下缘还正好横切在人物头上。
    """
    raw = str((script.get("narration") or {}).get("outro")
              or (script.get("narration") or {}).get("cta")
              or cta_text).strip()
    raw = " ".join(raw.split())
    parts = [x.strip() for x in re.split(r"(?<=[。！？])", raw) if x.strip()]
    head = parts[0] if parts else raw
    tail = "".join(parts[1:])
    draw = _measure()
    center_w = factcard.W - 248
    size = 84
    for cand in (84, 78, 72, 66, 60, 56):
        size = cand
        if draw.textlength(head, font=factcard._font(cand)) <= center_w:
            break
    head_rows = len([ln for ln in factcard._wrap_text(
        draw, head, factcard._font(size), center_w) if ln]) or 1
    tail_size, tail_rows = 0, 0
    if tail:
        tail_size = max(40, int(size * 0.58))
        while tail_size > 40 and draw.textlength(
                tail, font=factcard._font(tail_size)) > center_w:
            tail_size -= 2
        tail_rows = len([ln for ln in factcard._wrap_text(
            draw, tail, factcard._font(tail_size), center_w) if ln]) or 1
    # 字块底不许越过人物头顶：立绘顶边在 838，卡框横切在头上等于把人切开
    content_h = int(size * 1.42) * head_rows + int(tail_size * 1.35) * tail_rows
    bottom_pad, char_top = 52, 838
    top = min(700, char_top - 40 - bottom_pad - content_h)
    bottom = top + content_h
    label = script.get("cta_label")
    return {"head": head, "tail": tail, "size": size, "tail_size": tail_size,
            "top": top, "bottom": bottom,
            "card_bottom": min(bottom + bottom_pad, char_top - 40),
            # 面板顶比左边那条强调条低 30px：贴着圆角画会像两块没对齐的板
            "card_top": top - 104,
            "label": "轮到你了" if label is None else str(label),
            # 栏目标签退成小字：和主张同字号会互相抢，读起来像没排完
            "label_size": min(36, size - 18), "label_top": top - 62}


def _cta_tail_at(mp3: Path | None, dur: float) -> float:
    """收尾卡补白那行什么时候出现：口播念到第二句才落字。

    以前按整段时长乘 0.52 估，补白句第三秒多才落，观众先看到一块只写了两行的板。
    改成量第一句念完的那条静音：补白句一开口，字已经在。
    """
    if mp3:
        try:
            hold = _dur(mp3)
            if hold > 0.4:
                brk = _first_break(mp3)
                if brk:
                    return round(min(max(0.45, brk + 0.12), max(0.45, dur - 0.9)), 2)
                return round(min(max(0.55, hold * 0.52), max(0.55, dur - 0.9)), 2)
        except Exception:  # noqa: BLE001 - 量不出就按屏长估
            pass
    return round(max(0.55, dur * 0.55), 2)


def _first_break(mp3: Path) -> float:
    """念白里最长那条停顿结束在哪：句号比逗号停得久，最长的就是换句处。"""
    r = subprocess.run(
        [TR.FFMPEG, "-hide_banner", "-nostats", "-i", str(mp3), "-af",
         "silencedetect=noise=-38dB:d=0.18", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    log = r.stderr or ""
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", log)]
    spans = [(ends[i] - starts[i], ends[i]) for i in range(min(len(starts), len(ends)))]
    # 句尾那条静音不算换句：它后面没有下一句
    if len(spans) > 1:
        spans = spans[:-1]
    if not spans:
        return 0.0
    return max(spans)[1]


def _stage(cfg: dict, idx: int, total: int) -> dict:
    """按屏给人物换机位：整片钉死在一个角上，再灵的口型也像贴图。

    开头略远（把话说给你听），正文推进，收尾靠近（话是对着你说的）。
    脚本里显式给了 width/x/y 就照用，这里只补空位。
    """
    out = dict(cfg)
    # 人物尺寸整片只有一个：换屏、换手势时大小一跳，观众立刻看出是拼的。
    # 素材里十几种姿态的轮廓高度本身只差 0.2%，所以固定宽度就等于固定身高
    out.setdefault("width", 696)
    # 竖屏里人物占宽太小就只是贴纸：整片都落在画面 1/3 以上
    # 二十几屏里人物从 x500/y940 挪到 x520/y1014，两次切点各跳一次：
    # 观众读到的不是「机位换了」，是「她被挪了一下」。整片钉同一处，
    # 变化交给呼吸、倾斜和手势，位置一动不动。
    want = {"x": 520, "y": 1014}
    for k, v in want.items():
        if not out.get(k):
            out[k] = v
    return out


def _title_layers(text: str, workdir: Path, idx: int) -> list[dict]:
    """要点标题：贴左边强调条，和原来烧进卡面的位置一致。"""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    size, lines = _fit_title(text, 1004 - 140 - 24)
    top = 500 if len(lines) > 1 else 528
    out: list[dict] = []
    for n, ln in enumerate(lines):
        (w, _h), png = _text_layer(ln, size, workdir, f"t{idx:02d}_{n}.png",
                                   fill=(255, 255, 255, 226))
        out.append({"png": png, "x": 140, "y": int(top + n * size * 1.26), "w": int(w)})
    return out


def _text_layer(line: str, size: int, workdir: Path | None, name: str, *,
                align: str = "left", fill=(255, 255, 255, 240)):
    """把一行字渲成紧贴字形的透明 PNG，返回 (path, w, h) 与相对基线偏移。"""
    from PIL import Image, ImageDraw

    font = factcard._font(size)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    box = probe.textbbox((0, 0), line, font=font)
    pad = max(4, size // 8)
    w = max(2, box[2] - box[0]) + pad * 2
    w += w % 2  # 时间轴按 scale=w:-2 缩放，宽度必须偶数
    h = max(1, box[3] - box[1]) + pad * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((pad - box[0], pad - box[1]), line, font=font, fill=fill)
    if workdir is None:
        workdir = Path("/tmp")
    path = Path(workdir) / name
    img.save(path, "PNG")
    return (w, h), path



# 口型循环：四拍一轮，读起来像在说话，又不会每帧都换脸
_MOUTH_CYCLE = ("open", "half", "round", "half")
# 姿态决定动幅：想事情就该比挥手安静，兴奋时起伏更大
_POSE_MOTION = {
    "happy": {"type": "bob", "amplitude": 9.0, "period": 1.9},
    "wave": {"type": "bob", "amplitude": 8.0, "period": 2.1},
    "point": {"type": "bob", "amplitude": 5.0, "period": 2.6},
    "think": {"type": "sway", "amplitude": 2.0, "period": 3.4},
    "shrug": {"type": "sway", "amplitude": 1.5, "period": 3.0},
    # 情绪姿态也得有自己的动幅：掉进 idle 的默认起伏，惊讶和赌气看起来像在发呆
    "surprise": {"type": "bob", "amplitude": 10.0, "period": 1.7},
    "pout": {"type": "sway", "amplitude": 2.4, "period": 2.8},
    "shy": {"type": "sway", "amplitude": 2.0, "period": 3.2},
    "approve": {"type": "bob", "amplitude": 7.0, "period": 2.2},
    "idle": {"type": "bob", "amplitude": 6.0, "period": 2.4},
}


BLINK_WINDOW = 0.09   # 一次眨眼占 90ms：再长会像睡着，再短检查不出来
BLINK_MIN = 0.05      # 前后睁眼段别短于这个，否则一闪一闪像掉帧


def _blink_times(dur: float, cfg: dict, idx: int) -> list[float]:
    """每 2.6~3.8 秒眨一次眼，相位跟场景序号错开，免得整片动作像节拍器。"""
    if cfg.get("blink") is False or dur < 1.2:
        return []
    # 同上：内置 hash 按进程加盐，换成 crc32 才能「同一份稿子重跑字节一致」
    seed = zlib.crc32(f"blink{idx}".encode()) % 1000 / 1000.0
    out: list[float] = []
    at = 0.9 + seed * 0.9
    # 收在换屏前 0.5s 之外：眨眼段自带淡入，压在切点上就是人物先淡掉再冒出来
    while at < dur - 0.50:
        out.append(round(at, 3))
        at += 2.6 + (1.2 * seed if len(out) % 2 else 1.2 * (1 - seed))
    return out


def _blink_spans(start: float, seg: float, blinks: list[float]) -> list[tuple[float, float, str]]:
    """把一段口型按眨眼点切成 (起点, 时长, 眼态)；眼态空串表示交给引擎默认。"""
    cuts = [b for b in blinks if start < b < start + seg]
    spans: list[tuple[float, float, str]] = []
    cur = start
    for b in cuts:
        if b - cur > BLINK_MIN:
            spans.append((cur, b - cur, ""))
        close = min(BLINK_WINDOW, start + seg - b)
        if close > 0:
            spans.append((b, close, "closed"))
            cur = b + close
    if start + seg - cur > 1e-6:
        spans.append((cur, start + seg - cur, ""))
    return spans


def _gesture_summary(audit: list[dict], switches: list[float] | None = None,
                     gains: list[float] | None = None) -> dict:
    """手势自检：这句话本身有明确动作线索时，演的姿势该跟它一致。

    没有线索的句子按奇偶兜底，不算错；线索和姿势打架才是「贴图」——
    比如说缺口却在挥手。
    """
    # 首屏挥手、末屏高兴是礼数，不参与对齐核算
    checked = [a for a in audit if a["want"] and a["idx"] > 0 and not a["last"]]
    clash = [a for a in checked
             if a["pose"] not in (a.get("supported") or [a["want"]])]
    played = {a["pose"] for a in audit} | {a.get("alt") for a in audit if a.get("alt")}
    return {
        "scenes": len(audit),
        "checked": len(checked),
        "mismatch": len(clash),
        "poses": sorted(played),
        "detail": [{"idx": a["idx"], "pose": a["pose"], "alt": a.get("alt") or "",
                    "want": a.get("want") or ""} for a in audit],
        "clash_at": [a["at"] for a in clash[:5]],
        # 换势时机（占本屏比例）：太晚等于后半句没手势
        "switch": list(switches or []),
        # 每屏动作幅度系数：问句更活、结论更稳，全片才是同一个人在讲
        "gains": list(gains or []),
    }


# 动作已经表达态度，表情层只补一个不占地方的符号；两套动作共用同一张表情，
# 换服装时不用重画表情，加表情也不用碰任何一张立绘。
_EXPRESSION_BY_POSE = {
    "happy": "sparkle",
    "approve": "sparkle",
    "shy": "blush",
    "pout": "anger",
    "surprise": "sweat",
}


def _char_anim_name(pose: str, speaking: bool) -> str:
    """序列帧只给「动作本身就是动画」的姿态用。

    talk/idle_blink 两套序列是按站姿烘的：一旦挂上，char_layer 就用序列帧顶掉
    当前姿态的立绘，八张手势稿全部退化成垂手站桩——片子看着像只有嘴在动。
    所以只有 idle 用序列帧，其余姿态一律用姿态稿本身，口型靠 open/closed
    差分，身体起伏交给 char_layer 的 bob/sway/lean。
    """
    # 烘好的 wave 序列是「垂手站姿的小幅起伏」：手没抬，人物还比姿态稿大 4%，
    # 挂上去既不是挥手，身高也会在收尾突然长一截。挥手直接用姿态稿里的抬手掌心，
    # 身体起伏交给 char_layer 的 bob/sway/lean，尺寸跟前面每一屏对齐。
    if pose in ("idle", ""):
        return "idle_blink"
    return ""


def _pose_expression(pose: str) -> str:
    return _EXPRESSION_BY_POSE.get(str(pose or "").strip(), "")


# 一屏四五秒只给一个姿势，观众看到的就是贴图。句子后半句必须换一次身体：
# 但只能换「中性讲解类」姿势——情绪类（笑、惊、羞）文本里没有线索就换出来，
# 讲缺口时突然笑，比定格还假。
_NEUTRAL_ALT = {"wave": "think", "think": "point", "point": "think",
                "approve": "point", "happy": "approve", "surprise": "think",
                "shy": "think", "pout": "think", "idle": "think", "": "think"}


def _fallback_alt_pose(pose: str) -> str | None:
    """文本没有给出两种动作时，用一个不打架的中性姿势接过后半句。"""
    alt = _NEUTRAL_ALT.get(str(pose or ""))
    return alt if alt and alt != pose else None


# 整片只用一个摆动周期。以前周期跟着姿势和语气变（1.46~2.82 秒），
# 换屏那一刻正弦频率突然变，观众看到的就是「动作忽然加速」——
# 幅度可以跟着语气走，节奏不行，节奏一变动作就断成两截。
MOTION_PERIOD = 2.4
# 幅度换挡用 0.36 秒渐变：切点前后摆幅不同，硬切等于在切点上凭空挪一下人
AMP_RAMP = 0.36
# 竖轴整片固定成 2.4px 的轻呼吸，周期也锁死。以前竖晃和横摆按姿势二选一，
# 换姿势那一刻另一个轴归零，人物在切点上凭空挪一格——那才是「位置会有变化」。
BODY_AMP_Y = 2.4
BODY_PERIOD_Y = 3.3


def _pose_motion(pose: str, say: str = "") -> dict:
    """动作幅度跟着语气走：问句摆得活一点，下结论收稳一点。"""
    motion = dict(_POSE_MOTION.get(pose, _POSE_MOTION["idle"]))
    text = str(say or "").strip()
    gain = 1.0
    if "？" in text or "?" in text:
        gain += 0.22
    if "！" in text or "!" in text:
        gain += 0.15
    # 句子长短本身就是节奏：短句是抛出来的，动作要脆；长句在铺陈，动作得收。
    # 只看句末标点的话，一整片全是句号，五屏动作幅度一模一样，像按节拍器摆。
    body = len(text.rstrip("。.？！!?"))
    if body <= 10:
        gain += 0.12
    elif body >= 20:
        gain -= 0.08
    if text.endswith(("。", ".")) and not any(m in text for m in "？?！!"):
        gain -= 0.10
    gain = round(min(1.3, max(0.85, gain)), 3)
    motion["amplitude"] = round(float(motion["amplitude"]) * gain, 2)
    motion["period"] = MOTION_PERIOD
    # 两个轴一直都在：横摆幅度跟语气，竖呼吸全片一个值，切点上不会有人错位
    motion["type"] = "orbit"
    motion["amp_y"] = BODY_AMP_Y
    motion["period_y"] = BODY_PERIOD_Y
    motion["gain"] = gain
    return motion


def _energy_states(mp3: Path, dur: float) -> list[tuple[float, float]]:
    """解码成 16k 单声道，按 0.1 秒算音量，返回 (起点, 相对音量 0..1)。"""
    import array
    import subprocess
    import wave

    wav = mp3.with_suffix(".wav")
    if not wav.exists():
        subprocess.run([TR.FFMPEG, "-hide_banner", "-loglevel", "error",
                        "-i", str(mp3), "-ac", "1", "-ar", "16000",
                        "-y", str(wav)], check=True)
    with wave.open(str(wav), "rb") as f:
        rate = f.getframerate()
        pcm = array.array("h", f.readframes(f.getnframes()))
    step = max(1, int(rate * 0.1))
    levels: list[tuple[float, float]] = []
    for off in range(0, len(pcm) - step + 1, step):
        chunk = pcm[off:off + step]
        if not chunk:
            continue
        rms = (sum(v * v for v in chunk) / len(chunk)) ** 0.5
        levels.append((off / rate, rms))
    peak = max((v for _, v in levels), default=0.0) or 1.0
    if not levels:
        return [(0.0, 0.0)]
    # 归一化后静音底噪也算「在说」：按峰值的一半做参考，宁可多动嘴
    return [(a, min(1.0, v / peak)) for a, v in levels]


def _punctuation_anchor(say: str) -> float | None:
    """按标点猜「半句」在哪：TTS 的停顿和逗号位置基本对得上。

    取正文 25%–75% 之间最靠近中间的那个断句符，把它在字数里的比例当锚点。
    """
    text = " ".join(str(say or "").split())
    cut = JE._clause_cut(text)
    return (cut / len(text)) if cut > 0 else None


def _pause_fracs(mp3: Path, dur: float, cfg: dict) -> list[float]:
    """整句话里所有够长的停顿位置（占本屏比例），按时间排好。"""
    try:
        levels = _energy_states(mp3, dur)
    except Exception:   # noqa: BLE001 - 没音频就没有停顿
        return []
    if len(levels) < 8:
        return []
    vals = [v for _, v in levels]
    floor = max(0.06, sorted(vals)[int(len(vals) * 0.15)])
    median = sorted(vals)[len(vals) // 2]
    floor = min(floor * 1.6, median * 0.6)
    out: list[float] = []
    run, run_start = 0, None
    for i in range(int(len(levels) * 0.18), int(len(levels) * 0.86)):
        if levels[i][1] <= floor:
            if run_start is None:
                run_start = i
            run += 1
        elif run_start is not None:
            if run >= 2:
                out.append(levels[min(run_start + run // 2, len(levels) - 1)][0]
                           / max(0.4, dur))
            run, run_start = 0, None
    if run_start is not None and run >= 2:
        out.append(levels[min(run_start + run // 2, len(levels) - 1)][0]
                   / max(0.4, dur))
    return [f for f in out if 0.20 <= f <= 0.86]


def _clause_plan(say: str, poses: list[str], dur: float, mp3: Path | None,
                 cfg: dict) -> list[tuple[float, str, str]]:
    """按句读的先后排换势：第几段换到什么姿态、换在第几成的位置。

    位置先按字数算，再被实测停顿拉过去：人是在句读完的那一下换手，不是在
    「刚好过半」的时候。位置反了或挤在一起就丢掉这一档，宁可少换一次。
    """
    segs = JE._clause_split(say)
    if not segs:
        return []
    total = sum(len(x) for x in segs) or 1
    pauses = _pause_fracs(mp3, dur, cfg) if mp3 else []
    out: list[tuple[float, str, str]] = []
    used: list[int] = []
    for pose in poses:
        idx = next((k for k, s in enumerate(segs)
                    if k not in used and JE._keyword_pose(s) == pose), None)
        if idx is None or idx == 0:
            continue
        used.append(idx)
        # 换在这段话的中间：按段尾算，最后一段会落在八成半，等于后半句没动作
        frac = (sum(len(x) for x in segs[:idx]) + len(segs[idx]) / 2) / total
        frac = min(0.78, max(0.28, frac))
        near = [p for p in pauses if abs(p - frac) <= 0.12]
        if near:
            frac = min(near, key=lambda p: abs(p - frac))
        if out:
            frac = max(frac, round(out[-1][0] + 0.18, 2))
        if frac > 0.80:
            continue
        out.append((round(frac, 2), pose, segs[idx]))
    return out


def _clause_switch(mp3: Path, dur: float, cfg: dict, say: str = "",
                   lo_frac: float = 0.0) -> float | None:
    """手势换在句内停顿处，而不是死板的 55%：人换手势总在说完半句的时候。"""
    try:
        levels = _energy_states(mp3, dur)
    except Exception:
        return None
    if len(levels) < 8:
        return None
    vals = [v for _, v in levels]
    floor = max(0.06, sorted(vals)[int(len(vals) * 0.15)])
    median = sorted(vals)[len(vals) // 2]
    # 句读的停顿比静音底噪响：门槛放到中位音量的一半，才抓得到逗号后的换气
    floor = min(floor * 1.6, median * 0.6)
    lo = int(len(levels) * 0.18)
    hi = int(len(levels) * 0.86)
    runs: list[tuple[int, int]] = []      # (起点下标, 长度)
    run, run_start = 0, None
    for i in range(lo, hi):
        if levels[i][1] <= floor:
            if run_start is None:
                run_start = i
            run += 1
        else:
            if run_start is not None:
                runs.append((run_start, run))
            run, run_start = 0, None
    if run_start is not None:
        runs.append((run_start, run))
    if not runs:
        return None
    # 换在句中最舒服：太早像抽筋，太晚等于后半段没手势。
    # 所以不挑最长的停顿，而是挑「够长且靠中间」的那个。
    anchor = _punctuation_anchor(say)
    best_frac, best_score = None, -1e9
    for start, length in runs:
        if length < 2:
            continue
        mid = levels[min(start + length // 2, len(levels) - 1)][0]
        frac = mid / max(0.4, dur)
        if not max(0.25, lo_frac + 0.10) <= frac <= 0.78:
            continue
        # 有标点锚点就以它为准：停在哪不重要，重要的是停在那个逗号后
        score = (length - abs(frac - anchor) * 22) if anchor else (length - abs(frac - 0.5) * 10)
        if score > best_score:
            best_frac, best_score = frac, score
    # 句子里真有逗号，但那段没测出停顿（气声、连读）时，宁可按字面位置换，
    # 也不要为了「有停顿」把换势甩到半句中间。
    if anchor and anchor >= max(0.25, lo_frac + 0.10) and \
            (best_frac is None or abs(best_frac - anchor) > 0.10):
        return anchor
    return best_frac


def _audio_beats(mp3: Path, dur: float, cfg: dict) -> list[tuple[str, float]]:
    """口型跟着实际音量走：静音闭口、轻读半开、重音张开，长短句节奏就不同了。"""
    levels = _energy_states(mp3, dur)
    if len(levels) < 3:
        return []
    # 三窗口中值平滑，避免单帧抖动把嘴抖成抽搐
    smooth = [levels[0][1]]
    for i in range(1, len(levels) - 1):
        smooth.append(sorted(v for _, v in levels[i - 1:i + 2])[1])
    smooth.append(levels[-1][1])
    floor = max(0.06, sorted(smooth)[int(len(smooth) * 0.15)])
    states: list[str] = []
    for v in smooth:
        if v < floor:
            states.append("closed")
        elif v < floor + (1.0 - floor) * 0.55:
            states.append("half")
        else:
            states.append("open")
    segs: list[tuple[str, float]] = []
    min_seg = float(cfg.get("lip_min") or 0.18)
    for i, st in enumerate(states):
        at = levels[min(i, len(levels) - 1)][0]
        if segs and segs[-1][0] == st:
            segs[-1] = (st, segs[-1][1] + 0.1)
        elif segs and segs[-1][1] < min_seg:
            # 太短的一段口型看不出来，并进前一段，别让嘴在原地闪
            segs[-1] = (segs[-1][0], segs[-1][1] + 0.1)
        else:
            segs.append((st, 0.1))
    # 一句话说完嘴不会「啪」地闭上：先落到半开，再闭。句末留一点余开更像在呼吸
    eased: list[tuple[str, float]] = []
    release = float(cfg.get("lip_release") or 0.12)
    for st, sec in segs:
        if st == "closed" and sec > release * 2 and eased and eased[-1][0] != "closed":
            eased.append(("half", release))
            eased.append((st, sec - release))
        else:
            eased.append((st, sec))
    merged: list[tuple[str, float]] = []
    for st, sec in eased:
        if merged and merged[-1][0] == st:
            merged[-1] = (st, merged[-1][1] + sec)
        else:
            merged.append((st, sec))
    eased = merged
    total = sum(sec for _, sec in eased) or 1.0
    scale = dur / total
    return [(st, sec * scale) for st, sec in eased if st]


_SENT_END = "。！？!?；;…"
_CLAUSE_END = "，,、：:"


def _say_parts(say: str) -> list[tuple[str, float]]:
    """把一屏的口播拆成句子，句末留一口气、逗号留半口。

    TTS 一口气念完一整段时，句号之间几乎不留缝，听着像在赶稿。
    拆开各合成一次再补静音，停顿多长才是我们能定的。
    """
    text = " ".join(str(say or "").split())
    if not text:
        return []
    parts: list[tuple[str, float]] = []
    for sent in re.split(r"(?<=[。！？!?；;…])", text):
        sent = sent.strip()
        if not sent:
            continue
        # 二十多个字一口气读完，中间没有换气点，按逗号再断一次
        chunks = ([c.strip() for c in re.split(r"(?<=[，,、：:])", sent) if c.strip()]
                  if len(sent) > 16 else [sent])
        for k, chunk in enumerate(chunks):
            gap = 0.34 if chunk[-1] in _SENT_END else 0.15
            if k == len(chunks) - 1 and chunk is not sent and chunk[-1] in _CLAUSE_END:
                gap = 0.0     # 半句落在句尾，交给句子那一下停顿
            parts.append((chunk, gap))
    if len(parts) < 2:
        return [(text, 0.0)]
    parts[-1] = (parts[-1][0], 0.0)
    return parts


# 铺垫句照常录，只把包袱那一句放慢：整段一起拉慢，「由大肥鱼制作完成」
# 也会跟着拖成念稿腔，梗反而更不像随口说的。
_GAG_RE = re.compile(r"大肥鱼|token\s*受到伤害|没有任何一个")
_GAG_SETUP = re.compile(r"大肥鱼|制作完成")
_GAG_PUNCH = re.compile(r"token\s*受到伤害|没有任何一个|没受伤")
# SAMI 没有语速参数（voice_rate 传进去也只是签名占位），只能在合成后拉伸。
# 0.95/0.86 用 atempo 走、不改音高：铺垫略缓，包袱慢半拍。
_GAG_SETUP_TEMPO = 0.95
_GAG_PUNCH_TEMPO = 0.86
_GAG_LEAD = 0.45


def _gag_tempo(text: str) -> float:
    if _GAG_PUNCH.search(text):
        return _GAG_PUNCH_TEMPO
    if _GAG_SETUP.search(text):
        return _GAG_SETUP_TEMPO
    return 1.0


def _gag_lead(text: str) -> float:
    """包袱前面多留一口气，才像「顺口一提」；铺陈句不用。"""
    return _GAG_LEAD if _GAG_PUNCH.search(text) else 0.0


def _slow(src: Path, tempo: float) -> Path:
    """把一句配音拉慢（保持音高）。失手就返回原文件，别为语气把整屏弄没声。"""
    if not (0.5 <= tempo <= 1.0):
        return src
    out = src.with_name(src.stem + "_slow.mp3")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-filter:a", f"atempo={tempo:.3f}", "-c:a", "libmp3lame", "-q:a", "3", str(out)],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 800:
        return src
    return out


def _mp3_dur(path: Path) -> float:
    """秒数；ffprobe 失手给 0，调用方按 0 处理（宁可少补时间也不要瞎猜）。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=60)
    try:
        return max(0.0, float(r.stdout.strip()))
    except (TypeError, ValueError):
        return 0.0


def _read_chars(path: Path) -> list[dict]:
    """读配音的逐字时间（edge 通道会写 sidecar）；没有就空表。"""
    side = path.with_suffix(".words.json")
    try:
        blob = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    chars = blob.get("chars") if isinstance(blob, dict) else None
    out = []
    for c in chars or []:
        try:
            out.append({"ch": str(c["ch"]), "t": float(c["t"]), "d": float(c["d"])})
        except (KeyError, TypeError, ValueError):
            return []
    return out


def _write_chars(path: Path, chars: list[dict]) -> None:
    if not chars:
        return
    side = path.with_suffix(".words.json")
    side.write_text(json.dumps({"chars": chars}, ensure_ascii=False),
                    encoding="utf-8")


def _retime(chars: list[dict], tempo: float, offset: float) -> list[dict]:
    """音频被 atempo 拉过、又被拼到某个偏移上：逐字时间跟着一起换算。

    不做这一步，口型会按没拉慢前的字走，包袱那句就「嘴快过声音」。
    """
    if tempo <= 0:
        return []
    inv = 1.0 / tempo if tempo < 1.0 else 1.0
    return [{"ch": c["ch"], "t": offset + c["t"] * inv, "d": c["d"] * inv}
            for c in chars]


def _say(mp3: Path, say: str, voice: str, workdir: Path, stem: str,
         rate: float = 1.0) -> None:
    """一屏的配音：逐句合成、补静音、拼回一条。

    每句各自走配音缓存，片子重出时只有改过的那句重新合成。
    拼完后把逐字时间（有就拼、没有就算了）落到成片 mp3 的 sidecar 上，
    口型模块只认这条 sidecar，认不到就退音节切分。
    """
    parts = _say_parts(say)
    if len(parts) < 2:
        _tts(say, mp3, voice, rate)
        tempo = _gag_tempo(say)
        if tempo < 1.0:
            chars = _read_chars(mp3)
            shutil.copyfile(_slow(mp3, tempo), mp3)
            _write_chars(mp3, _retime(chars, tempo, 0.0))
        return
    parts = [(text, gap + (_gag_lead(text) if k else 0.0))
             for k, (text, gap) in enumerate(parts)]
    inputs: list[str] = []
    filters: list[str] = []
    rows: list[tuple[Path, Path, float]] = []
    for k, (text, _gap) in enumerate(parts):
        part = workdir / f"{stem}_p{k}.mp3"
        _tts(text, part, voice, rate)
        tempo = _gag_tempo(text)
        used = part
        if tempo < 1.0:
            used = _slow(part, tempo)
        rows.append((part, used, tempo))
        inputs += ["-i", str(used)]
        filters.append(f"[{k}:a]aresample=44100,"
                       "aformat=sample_fmts=fltp:channel_layouts=mono["
                       f"a{k}]")
    chain: list[str] = []
    for k, (_text, gap) in enumerate(parts):
        chain.append(f"[a{k}]")
        if gap > 0.01:
            filters.append(f"aevalsrc=0:d={gap:.2f}:s=44100,"
                           "aformat=sample_fmts=fltp:channel_layouts=mono["
                           f"g{k}]")
            chain.append(f"[g{k}]")
    filters.append("".join(chain) + f"concat=n={len(chain)}:v=0:a=1[out]")
    cmd = ["ffmpeg", "-v", "error", "-y", *inputs,
           "-filter_complex", ";".join(filters), "-map", "[out]",
           "-c:a", "libmp3lame", "-q:a", "3", str(mp3)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not mp3.exists() or mp3.stat().st_size < 800:
        # 拼接失手就退回整段合成：停顿差一点，总比这一屏没声音强
        _tts(say, mp3, voice, rate)
        return
    # 拼接成功才拼逐字时间：偏移 = 前面所有「句子 + 停顿」的累加，
    # 某一句没有 sidecar 就整条不写（半截时间表比没有更糟，口型会一路跑偏）。
    merged: list[dict] = []
    offset = 0.0
    for (orig, used, tempo), (_text, gap) in zip(rows, parts):
        chars = _read_chars(orig)
        if not chars:
            return
        merged += _retime(chars, tempo, offset)
        offset += _mp3_dur(used) + (gap if gap > 0.01 else 0.0)
    _write_chars(mp3, merged)


def _beats(say: str, dur: float, last: bool, cfg: dict,
           mp3: Path | None = None) -> list[tuple[str, float]]:
    """把一屏切成若干口型段；不说话的屏给一段闭口静止。"""
    if not say:
        return [("", max(0.6, dur))]
    if not cfg.get("lip_flap", True):
        return [("half", dur)]
    if mp3 is not None and cfg.get("lip_from_audio", True):
        try:
            heard = _audio_beats(mp3, dur, cfg)
        except Exception:      # 解码失败就退回固定周期，别为口型把整片打掉
            heard = []
        if len(heard) >= 2:
            return heard
    period = float(cfg.get("lip_period") or 0.42)
    count = max(2, min(8, int(round(dur / period)) or 2))
    seg = dur / count
    return [(_MOUTH_CYCLE[k % 4], seg) for k in range(count)]



_CHAR_CROPS: dict = {}


def _crop_frame(png: Path, workdir: Path, name: str) -> tuple[Path, int, int, int]:
    """立绘整幅画布是 1080×1920 的透明 PNG，一段口型就 8MB。

    按不透明内容裁到最小框再当叠加层：x/y 仍用画布坐标定位，
    渲染输入小一个数量级，切片再多也不会把 ffmpeg 拖垮。
    """
    # 缓存按「文件名 + 源图内容指纹」：worker 是常驻进程，只按文件名缓存会让
    # 下一条成片引用上一条的立绘——换了服装还是旧衣服；只按目录又会每条都重裁。
    # 内容一样就复用裁剪，路径不在本次目录时拷一份过来，叠加层始终落在自己的
    # 运行目录里。
    try:
        st = png.stat()
        head = png.read_bytes()[:262144]
        key = (name, st.st_size, hashlib.md5(head).hexdigest())
    except OSError:
        key = (name, 0, "")
    cached = _CHAR_CROPS.get(key)
    if cached and cached[0].exists():
        if cached[0].parent == workdir:
            return cached
        local = workdir / name
        shutil.copy2(cached[0], local)
        val = (local, cached[1], cached[2], cached[3])
        _CHAR_CROPS[key] = val
        return val
    from PIL import Image

    src = Image.open(png).convert("RGBA")
    bb = src.getbbox() or (0, 0, src.width, src.height)
    pad = 2
    left = max(0, bb[0] - pad)
    top = max(0, bb[1] - pad)
    right = min(src.width, bb[2] + pad)
    bottom = min(src.height, bb[3] + pad)
    crop = src.crop((left, top, right, bottom))
    if crop.width % 2:
        crop = crop.crop((0, 0, crop.width - 1, crop.height))
    out = workdir / name
    crop.save(out)
    val = (out, left, top, crop.width)
    if len(_CHAR_CROPS) > 512:
        _CHAR_CROPS.clear()
    _CHAR_CROPS[key] = val
    return val


def _film_glow(workdir: Path, total: float, palette_idx: int = 0,
               mirror: bool = False) -> list[dict]:
    """整片一块环境光：同一条光从开场照到收尾，换屏时不挪位置也不换颜色。

    按屏生成柔光，切点就是「旧灯灭、新灯亮」，位置还左右镜像一次；动作
    衔接的时候观众先看见背景闪一下，才觉得这一下接得不顺。这里只留两盏，
    颜色取统一底色那套色温，正弦相位从 0 走到片尾，换屏只是内容在换。
    """
    total = max(1.0, float(total))
    out: list[dict] = []
    for k, (alpha, amp, per, x, y, axis) in enumerate((
        (20, 54.0, max(9.0, total / 2.6), -150.0, 520.0, "sway"),
        (13, 28.0, max(7.0, total / 3.3), -190.0, 880.0, "bob"),
    )):
        png = factcard.render_glow(workdir / f"glow_film{k}.png",
                                   palette_idx=palette_idx, alpha=alpha)
        out.append({
            "src": str(png), "start": 0.0, "end": round(total, 3),
            "x": x, "y": y, "opacity": 1.0, "width": 1360,
            "fade": 0.0,
            "motion": {"type": axis, "amplitude": -amp if mirror else amp,
                       "period": per},
        })
    return out


def build_spec(script: dict, workdir: Path, *, voice: str = "", logo: Path | None = None) -> dict:
    """脚本 → timeline spec（静帧卡 + 立绘 + 逐屏配音）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    # 一次排多条母版时，同稿不同表演：种子取自 runId，只影响手势选择，
    # 文案/配音/时长一个字都不动。没有种子（老 spec 直发）时行为完全不变。
    _raw_seed = str(script.get("_variant_seed") or "")
    variant_seed = int(hashlib.md5(_raw_seed.encode()).hexdigest()[:6], 16) if _raw_seed else 0
    voice = voice or str(script.get("voice") or DEFAULT_VOICE)
    # 语速只对 edge 通道生效（SAMI 没有这个参数）；gag 的 atempo 仍走 _slow
    rate = float(script.get("rate") or 1.0)
    # 口播稿可能是文件路径（和 make_episode 一样）：不认路径就会退回要点里的说明句，
    # 同一份脚本出的 mp4 比剪映草稿长几秒，听起来还不像人写的
    narration = script.get("narration")
    points_raw = list(script.get("points") or [])
    if isinstance(narration, str) and narration.strip():
        raw = narration.strip()
        # 口播稿两种来源：make_episode 传文件路径，平台表单直接传文本框里的多行稿。
        # 只认路径的话，表单发来的稿子会被当成文件名，出片当场 FileNotFoundError。
        path = Path(os.path.expanduser(raw))
        lines = (path.read_text(encoding="utf-8").splitlines()
                 if "\n" not in raw and path.is_file() else raw.splitlines())
        points_raw, narration = MEP.apply_narration(points_raw, lines, None)
    if isinstance(narration, dict):
        # 结构化口播稿里每条要点也各有一句 spoken 文本。之前只拿走了 intro/cta，
        # points 被丢掉：接口直发时口播读的是要点自带那句，配音和稿子对不上，
        # 文案体检却按稿子打分。要点没显式写口播时，就用稿子这句去配。
        fallback = [str(x) for x in (narration.get("points") or [])]
        if fallback:
            merged = []
            for i, item in enumerate(points_raw):
                raw = str(item)
                has_say = (bool(item.get("say") or item.get("narration"))
                           if isinstance(item, dict) else raw.count("::") >= 2)
                say = fallback[i] if i < len(fallback) else ""
                merged.append(raw if (has_say or not say) else f"{raw}::{say}")
            points_raw = merged
    # 顶部小标题和首屏正文说的是同一句话时，同一句在画面上出现两遍，
    # 第一屏就成了目录页。按「小标题的字在正文里按序出现多少」判断重复。
    kicker = " ".join(str(script.get("kicker") or "").split())
    if not kicker:
        # 没给顶栏就取主题的第一个分句；主题过长或缺失时宁可不显示，
        # 别把模板默认的「大模型面试题」印到跟它无关的片上。
        _topic_line = " ".join(str(script.get("topic") or "").split())
        for _sep in ("，", "。", "！", "？", "；", ",", "：", ":"):
            if _sep in _topic_line:
                _topic_line = _topic_line.split(_sep)[0]
                break
        kicker = _topic_line if 0 < len(_topic_line) <= 18 else ""
    _first = ""
    if points_raw:
        _p0 = str(points_raw[0]).split("::")
        _first = _p0[1] if len(_p0) > 1 else _p0[0]
    _body_src = " ".join(f"{script.get('hook') or ''} {_first}".split())

    def _dup(a: str, b: str) -> bool:
        # 按字集合算覆盖：顺序无关，长短不同也不吃亏。逐字在 b 里找会一失败就吞掉后文，
        # 短标题反而被判成不重复。
        ka = set(" ".join(a.split()).replace(" ", ""))
        kb = set(b)
        return bool(ka) and len(ka & kb) / len(ka) >= 0.8

    if kicker and _body_src and _dup(kicker, _body_src):
        # 重复时换成主题名：顶栏本来就该说「这条讲什么」，重复正文只是废话
        _topic = " ".join(str(script.get("topic") or "").split())
        # topic 常被写成一句完整的话（还带逗号），顶栏就变成第二句钩子。
        # 顶栏是标签位：只取第一个分句，超过 18 字就不要了。
        for _sep in ("，", "。", "！", "？", "；", ","):
            if _sep in _topic:
                _topic = _topic.split(_sep)[0]
                break
        kicker = "" if (not _topic or len(_topic) > 18 or _dup(_topic, _body_src)) else _topic
    spec = factcard.build_spec(
        topic=str(script.get("topic") or "").strip(),
        hook=str(script.get("hook") or factcard.DEFAULT_HOOK),
        points_raw=points_raw,
        cta=str(script.get("cta") or factcard.DEFAULT_CTA),
        voice=voice,
        kicker=kicker,
        narration=narration if isinstance(narration, dict) else None,
        logo=logo,
        out_dir=workdir,
        draw_text=False,
        # 界面层单独贴：卡面要推近、要叠化，HUD 跟着缩放就会在转场里重影
        hud=False,
        # 批量出片时同一份稿子错开一套版式（进卡方向/色温/转场），
        # 手势相位那边也用同一个种子，两批看起来才像两条不同的片子
        variant=variant_seed,
    )
    scenes = spec["scenes"]
    total = len(scenes)
    # 卡面文字自己排：预览版是给剪映层用的相对坐标，烧进 mp4 会溢出画面
    # 标题取原始文本：剪映层那份被压掉空格，"KV Cache" 会粘成 "KVCache"
    raw_bodies = []
    for raw in points_raw:
        # 三段式是「标题::卡面正文::口播」：卡面只放第二段，
        # 口播那段是给耳朵的，混进卡面就会看见一串 ::
        parts = str(raw).split("::")
        raw_bodies.append(" ".join(parts[1].split()) if len(parts) > 1 else "")
    hook_text = " ".join(str(script.get("hook") or factcard.DEFAULT_HOOK).split())
    cta_text = " ".join(str(script.get("cta") or factcard.DEFAULT_CTA).split())
    center_w = factcard.W - 248
    cards: list[Path] = []
    layer_sets: list[list[dict]] = []
    gesture_audit: list[dict] = []
    switch_audit: list[float] = []
    motion_audit: list[float] = []
    char_widths: list[int] = []
    # 上一屏、上上屏演的是什么：跨屏换姿势靠这两个，别用它记屏内逐帧状态
    scene_prev = ""
    scene_prev2 = ""
    prev_pose = ""
    for i, sc in enumerate(scenes):
        card = Path(str(sc["image"]))
        if i == 0:
            # 封面卡自己铺：build_spec 那张带一条钩子分隔线，字被挪到进度条下之后
            # 分隔线就孤零零挂在正文底下，像没删干净的版式零件
            card = factcard.render_card(
                workdir / "cover.png", kicker=kicker, index="", palette_idx=0,
                progress=0, total=total, slug="cover", plain=True, draw_body=False,
                hud=False)
        cta_copy = _cta_copy(script, cta_text) if i == total - 1 else None
        if i == total - 1 and not script.get("cta_card"):
            # 收尾空卡 + 一句飘着的字，最容易读成「没做完」：给收尾也铺一块卡面
            card = factcard.render_card(
                workdir / f"cta{i:02d}.png", kicker=kicker, index="",
                palette_idx=len(factcard.PALETTES) - 1, progress=total - 1,
                total=total, slug="cta", hook=False, draw_body=False, hud=False,
                card_top=cta_copy["card_top"], card_bottom=cta_copy["card_bottom"])
        cards.append(card)
        if i == 0:
            # 封面：标题靠左压在进度条下，人物占右下，两者拼成一条对角线。
            # 字号按断行反选，字块在进度条到人头顶之间居中
            layers = _block_layers(hook_text, _cover_hook_size(hook_text), 470, workdir,
                                   f"h{i:02d}", align="left", left=124, max_w=700,
                                   fill=(255, 255, 255, 214), center_in=(440, 906))
        elif i == total - 1:
            # 卡面左上角是空的强调条，配一句栏目名才不像是漏排了
            label = cta_copy["label"]
            # 收尾卡是整条的落点：栏目名退成一行小字，那句话当标题居中，
            # 两句同一字号会互相抢，读起来像没排完。
            # 收尾卡以前是整块居中：跟前面左对齐的要点卡摆在一起，
            # 一眼就看得出是另一套版式（像片尾字幕接错了）。统一到同一网格。
            layers = (_block_layers(label, cta_copy["label_size"], cta_copy["label_top"],
                                    workdir, f"l{i:02d}",
                                    align="left", left=140, max_w=factcard.W - 264,
                                    fill=(255, 255, 255, 205)) if label.strip() else [])
            # 收尾念的是 outro，卡面却只写 cta：耳朵多听到半句，眼睛对不上。
            # 卡面改成同一句，按句号拆成「主张 / 补一句」，主次分两级而不是硬塞一屏。
            # 收尾三行分三个节拍落座：同一帧全盖上去，观众一眼看完就等片尾。
            # 时间从这一屏起点算，最后一行赶上口播的补白那句
            # 栏目名跟着切点就到：卡面在叠化里先亮起来、字晚半拍，
            # 中间那几帧就是一块空板，读起来像没加载完
            for lay in layers:
                lay["delay"] = 0.0
            for lay in _block_layers(cta_copy["head"], cta_copy["size"], cta_copy["top"],
                                     workdir, f"h{i:02d}", align="left", left=140,
                                     max_w=factcard.W - 264, fill=(255, 255, 255, 236)):
                # 收尾那张板在叠化结束时必须已经有字：以前拖 0.22s 才淡入，
                # 观众看到的是「一块空板 + 一个栏目名」停半秒，像没加载完。
                lay["delay"] = 0.08
                lay["fade"] = 0.26
                layers.append(lay)
            if cta_copy["tail"]:
                for lay in _block_layers(cta_copy["tail"], cta_copy["tail_size"],
                                         cta_copy["top"] + int(cta_copy["size"] * 1.42),
                                         workdir, f"t{i:02d}", align="left", left=140,
                                         max_w=factcard.W - 264,
                                         fill=(*_scene_accent(sc, i, total), 244)):
                    # 具体几点出现要等这一屏的口播时长，先占位后对表
                    lay["delay"] = 1.05
                    lay["_cta_tail"] = True
                    lay["fade"] = 0.34
                    layers.append(lay)
        else:
            layers = _title_layers(str(sc.get("text") or ""), workdir, i)
            body = raw_bodies[i - 1] if 0 < i <= len(raw_bodies) else ""
            if body:
                size, _lines, top, _bottom = factcard.body_layout(body, _measure())
                accent = (*_scene_accent(sc, i, total), 245)
                lead, rest = _hero_split(body)
                if lead:
                    # 要点屏的正文常常就是「216 秒，109 段」这种一行数据：
                    # 字号跟标题差不多大，一屏读下来只有一句话的份量，摆六屏就像
                    # 六张同样的卡。把开头的量化信息提成主视觉，副句退成小字，
                    # 跟着解说念到哪，眼睛先抓数字再读那半句。
                    big = 0
                    for cand in range(104, 76, -6):
                        if _measure().textlength(lead, font=factcard._font(cand)) <= factcard.W - 420:
                            big = cand
                            break
                    if big:
                        # 卡面板下沿只到 818：主视觉和副句上下叠会掉出面板外。
                        # 改成同一行——大数字在左、副句贴着它右下角，横向占位、纵向不出界。
                        lead_size = max(42, int(big * 0.42))
                        lead_layers = _block_layers(lead, big, top, workdir, f"g{i:02d}",
                                                    align="left", left=140,
                                                    max_w=factcard.W - 264, fill=accent)
                        layers += lead_layers
                        right = max((l["x"] + l["w"] for l in lead_layers), default=140)
                        for lay in _block_layers(rest, lead_size, top + int(big * 0.58),
                                                 workdir, f"b{i:02d}", align="left",
                                                 left=140, max_w=factcard.W - 264,
                                                 fill=(255, 255, 255, 232)):
                            lay["x"] = int(right + 20)
                            layers.append(lay)
                    else:
                        lead = ""
                if not lead:
                    layers += _block_layers(body, size, top, workdir, f"b{i:02d}",
                                            align="left", left=140, max_w=factcard.W - 264,
                                            fill=(255, 255, 255, 236),
                                            tail_fill=accent)
        layer_sets.append(layers)
    char_cfg = script.get("character")
    if char_cfg is True:
        char_cfg = {}
    elif char_cfg is False:
        char_cfg = None
    elif char_cfg is None:
        char_cfg = {}

    video, overlay, audio = [], [], []
    # 换屏的整屏卡面必须压在所有文字/人物之下：按场景顺序 append 的话，
    # 下一屏的卡面会盖住上一屏还没收走的人物，叠化中段就成了「人物凭空消失」
    bg_overlay: list[dict] = []
    card_overlays: list[dict] = []
    char_ovs: list[dict] = []
    # 上一段人物的摆幅：新一段从它渐变过来，切点上不凭空挪一下
    _prev_amp: list[float] = [-1.0]
    # 逐段静帧仍然照生成（体检和回退都要它），成片前若动画层建成，
    # 这些静帧就整批摘掉，换成一条带 alpha 的人物视频。
    char_segs: list[dict] = []
    # 开关放在脚本层：spec 由 factcard 生成，不会替我们透传任意键。
    # 默认开：动画层和静帧摆位同一套，成片前把静帧整批换掉；
    # 要退回逐段静帧（老片复现、排障）时传 char_layer: false。
    char_anim = bool(script.get("char_layer", True))
    pacing: list[dict] = []
    last_ov = None
    t = 0.0
    for i, (scene, card) in enumerate(zip(scenes, cards)):
        text_layers = layer_sets[i]
        say = str(scene.get("say") or "").strip()
        mp3 = workdir / f"a{i:02d}.mp3"
        if say:
            _say(mp3, say, voice, workdir, f"a{i:02d}", rate)
            tts_dur = _dur(mp3)
            dur = _scene_duration(tts_dur, i == total - 1)
            for lay in text_layers:
                if lay.get("_cta_tail"):
                    lay["delay"] = _cta_tail_at(mp3, dur)
            audio.append({"src": str(mp3), "start": round(t, 3), "volume": 1.0})
            # 念白留白：顶到切点就是半句话被切走，拖太长就是画面空等
            pacing.append({"scene": i, "tts": round(tts_dur, 3),
                           "slot": round(dur, 3),
                           "gap": round(dur - tts_dur, 3)})
        else:
            dur = 2.0
        # 卡面在讲述期间慢慢推近/拉远：一秒一帧的静卡最像 PPT
        # 同稿多版时推镜深浅和起手屏都错开（一版从封面开始推、一版从第二屏推），
        # 两版看起来是两条不同调度的片子，文案和节奏一点没动
        _flip = variant_seed % 2
        # 推镜幅度也收小：7% 推近会把卡面那几团柔光一起放大，
        # 看着像背景在慢慢变亮。4.5% 够破 PPT 感，又看不出背景在动
        _deep = 1.045 + 0.010 * ((variant_seed // 2) % 2)
        z0, z1 = ((1.0, _deep) if (i + _flip) % 2 == 0 else (_deep, 1.0))
        video.append({
            "src": str(card), "out": round(dur, 3), "zoom": [z0, z1],
            # 封面第一帧就是成片封面：平台取首帧当图，黑起头等于没封面
            "fade_in": 0.0,
            "fade_out": FADE if i == total - 1 else 0.0,
            "xfade_in": 0.0 if i == 0 else XFADE,
        })
        # 换屏是引擎里的真交叠（video.xfade_in）：以前用「下一张卡当叠层」模拟，
        # 叠层是固定 1.0 倍、底图在 1.0~1.07 之间推拉，0.4s 里两张同图案的卡
        # 对不齐，网格点和光斑就互相咬，看着像背后的光在抖。现在底图自己融，
        # 缩放也接着上一屏走，切点只剩内容在换。
        # 这里的柔光原来按屏生成，换屏时旧光淡出、新光从另一侧淡入，
        # 观众看到的是「背后那盏灯换了位置、还换了颜色」。改成整片一块光，
        # 见循环外的 _film_glow：位置、颜色、呼吸周期都只跟全片时间有关。
        # 界面层：kicker / 进度条 / 角标。整屏叠化的那 0.4s 里，
        # 卡面和人物在融，这两样必须一动不动，否则就是两层字压在一起。
        # HUD 的强调色要跟卡面色温一致：卡面按语气挑色，这里不能还按第几屏
        hud_idx = int(sc.get("palette") if sc.get("palette") is not None else
                      (0 if i == 0 else (len(factcard.PALETTES) - 1 if i == total - 1 else i)))
        hud_png = factcard.render_hud(
            workdir / f"hud{i:02d}.png", kicker=kicker, progress=i, total=total,
            palette_idx=hud_idx)
        # 底图的叠化是「切点前 0.4s 就已经融完」：到切点画面已经是新卡。
        # 所以字和 HUD 都该在切点这一刻换，早半拍换就成了「旧卡配新字 + 空板」。
        hud_shift = 0.0
        hud_end = (t + dur + (TAIL if i == total - 1 else 0.0)) if i == total - 1 \
            else t + dur
        overlay.append({
            "src": str(hud_png),
            "start": round(t - hud_shift, 3),
            "end": round(hud_end, 3),
            "x": 0.0, "y": 0.0, "width": int(spec["width"]),
            "opacity": 1.0, "fade": 0.0,
            "motion": {"type": "none", "amplitude": 0.0, "period": 1.0},
        })
        # 标题一律从切点后淡入：旧标题在切点一并消失，中间只留半口气，
        # 叠化中段不会出现两套字压在一起。封面例外，首帧就要有字
        lead = 0.06
        # 换字必须和「新卡成为唯一底图」那一帧对齐，也就是切点本身：
        # 底图从切点前 XFADE 开始融，到切点已经融完。字要是提前半拍换，
        # 叠化后半程就是「新卡配旧句」，再往后还会剩一屏空板。
        handoff = 0.0
        # 收尾定住的那一秒里字要还在，否则卡面是空的
        text_end = (t + dur + (TAIL if i == total - 1 else 0.0)) if i == total - 1 \
            else t + dur
        for n, layer in enumerate(text_layers):
            at = max(0.0, t - handoff + 0.02 * n) if i > 0 else 0.0
            if layer.get("delay"):
                at = round(t + float(layer["delay"]), 3)
            overlay.append({
                "src": str(layer["png"]),
                "start": round(at, 3),
                "end": round(text_end, 3),
                "x": float(layer["x"]), "y": float(layer["y"]),
                "opacity": 1.0, "width": int(layer["w"]),
                # 标题从切点淡入 0.20s：口播已经开口，字要跟上，不能再拖半拍
                # 文字在切点硬换：底图已经融完，再淡入 0.18s 就会露出
                # 一屏「有板无字」的空拍；两屏都要淡又会在叠化里压出两层字。
                "fade": float(layer.get("fade") or 0.0),
                "motion": {"type": "none", "amplitude": 0.0, "period": 1.0},
            })
        if char_cfg is not None:
            # 一屏一张立绘就是贴图：按口型拍点切成几段，手势在中段换一次，
            # 角色才是在"讲这句话"，而不是被贴上来定格。
            pose = str(char_cfg.get("pose")
                       or JE._scene_pose(char_cfg, i, total, scene, avoid=scene_prev,
                                         avoid_extra=scene_prev2, seed=variant_seed))
            # 屏内逐帧状态（prev_pose）会在下面被改写，跨屏记忆单独存
            scene_prev2, scene_prev = scene_prev, pose
            # 作者用 poses 列表指定了整条片的手势，那是意图不是错配；显式 pose 同理
            ga_base = None
            if not char_cfg.get("pose") and not char_cfg.get("poses"):
                # 记下「这句话该做什么动作」和「实际做了什么」，交给成片体检核对
                said = " ".join(str(scene.get(k) or "")
                                for k in ("say", "text", "caption"))
                want = JE._keyword_pose(said)
                # 一句话可能同时有「在指」和「在问」两层线索，挑哪个都算跟上了；
                # 只要不是跑出这句话的范围，就不该判成贴图
                supported = sorted(JE._keyword_scores(said))
                gesture_audit.append({"at": round(t, 2), "idx": i, "pose": pose,
                                      "want": want, "supported": supported,
                                      "last": i == total - 1})
                # 末尾姿势还会被收尾规则和换势兜底改写，等定稿再回填这一条
                ga_base = gesture_audit[-1]
            alt_pose = (None if char_cfg.get("pose") else
                        JE._second_half_pose(char_cfg, i, total, scene, first=pose,
                                             seed=variant_seed))
            # 一句话里有几段就换几档，按段落的先后演；段数不够才回落到「半句」那套
            clause_tail: list[str] = []
            if (say and dur >= 3.6 and not char_cfg.get("pose")
                    and not char_cfg.get("poses")
                    and char_cfg.get("gesture_sync", True)):
                # 一屏里同一个动作只演一次：来回弹（认可→摊手→认可）像抽搐
                cps = list(dict.fromkeys(JE._clause_poses(say)))
                if len(cps) >= 2 and cps[0] != pose:
                    # 第一段说什么，这一屏就从什么动作起：按整句搜索挑到的起势
                    # 可能属于后半句，先演后半句再演前半句，看着像倒放
                    pose = cps[0]
                clause_tail = [pp for pp in cps if pp != pose][:2]
                if clause_tail:
                    alt_pose = clause_tail[0]
            # 收尾那屏往往八九秒，闭眼大笑的稿撑过五秒就是贴图。先睁着眼把
            # 话说完（认可），笑点留给最后一句——情绪落在收尾，不是从头笑到尾。
            if i == total - 1 and pose == "happy" and dur >= 6.0:
                pose = "approve"
                alt_pose = "happy"
            if not char_cfg.get("gesture_sync", True):
                alt_pose = None
            # 后半句还是同一个动作，就别为了「换」而换：白做一次溶解，观众看不出来
            if (alt_pose is None and not clause_tail and dur >= 3.2
                    and not char_cfg.get("poses")):
                alt_pose = _fallback_alt_pose(pose)
            if alt_pose == pose:
                alt_pose = None
            if ga_base is not None:
                ga_base["pose"] = pose
                ga_base["alt"] = alt_pose or ""
            motion = _pose_motion(pose, say)
            motion_audit.append(motion.get("gain", 1.0))
            char_scene = _stage(char_cfg, i, total)
            char_widths.append(int(float(char_scene.get("width") or 0)))
            beats = _beats(say, dur, i == total - 1, char_cfg,
                           mp3=mp3 if say else None)
            switch_at = 0.55
            if alt_pose and say and not clause_tail:
                heard = _clause_switch(mp3, dur, char_cfg, say)
                if heard:
                    switch_at = heard
                # 收尾的笑别从 31 秒一直挂到 35.9：只想让人在最后一句笑，
                # 所以「笑」只占片尾两秒多，前面都交给认可那个姿势。
                if i == total - 1 and alt_pose == "happy" and dur > 4.0:
                    switch_at = max(switch_at, 1.0 - 2.8 / dur)
                switch_audit.append(round(switch_at, 2))
            elapsed = 0.0
            prev_ov = None      # 上一段口型：换姿态时要和它交叉，不能硬替换
            prev_pose = None
            blinks = _blink_times(dur, char_cfg, i)
            # 一屏最多两次换势：句子中间一次、收束前一次。只换一次的话，
            # 四秒多的句子后半截身体是定住的，嘴动得再准也还是贴图。
            plan: list[tuple[float, str]] = []
            if clause_tail:
                for frac, pp, seg in _clause_plan(say, clause_tail, dur, mp3, char_cfg):
                    # 收尾的笑别从三十一秒挂到三十五秒：只让最后一句笑
                    if i == total - 1 and pp == "happy" and dur > 4.0:
                        frac = max(frac, round(1.0 - 2.8 / dur, 2))
                    if plan and frac <= plan[-1][0]:
                        continue
                    plan.append((frac, pp))
                    switch_audit.append(round(frac, 2))
                    seg_want = JE._keyword_pose(seg)
                    gesture_audit.append({
                        "at": round(t + dur * frac, 2), "idx": i, "pose": pp,
                        "want": seg_want,
                        "supported": sorted(JE._keyword_scores(seg)),
                        "last": False})
            elif alt_pose:
                plan.append((switch_at, alt_pose))
                if (dur >= 3.6 and not char_cfg.get("pose")
                        and not char_cfg.get("poses")
                        and char_cfg.get("gesture_sync", True)):
                    alt2 = JE._third_pose(char_cfg, i, total, scene, pose, alt_pose)
                    if alt2:
                        heard2 = None
                        if say:
                            try:
                                heard2 = _clause_switch(mp3, dur, char_cfg, say,
                                                        lo_frac=switch_at)
                            except Exception:   # noqa: BLE001 - 没音频就按位置切
                                heard2 = None
                        at2 = round(heard2 if heard2 and heard2 > switch_at + 0.15
                                    else switch_at + 0.22, 2)
                        # 两次换势必须一前一后：位置算反了就等于第三次没演
                        at2 = min(0.80, max(at2, round(switch_at + 0.15, 2)))
                        plan.append((at2, alt2))
                        switch_audit.append(at2)
                        if not char_cfg.get("poses"):
                            gesture_audit.append({"at": round(t + dur * at2, 2),
                                                  "idx": i, "pose": alt2,
                                                  "want": want, "supported": supported,
                                                  "last": False})
            # 收尾要有个「再见」：讲完最后一屏还在原地换势，等于话停了人还僵着。
            # 挥手是礼数，不参与线索核算（last=True 的条目本来就不查）。
            if (char_cfg is not None and i == total - 1 and dur >= 6.0
                    and not char_cfg.get("pose") and not char_cfg.get("poses")
                    and char_cfg.get("gesture_sync", True)):
                last_pose = plan[-1][1] if plan else pose
                sign_at = round(min(0.93, max(0.55, 1.0 - 1.6 / dur)), 2)
                if last_pose != "wave" and (not plan or sign_at > plan[-1][0] + 0.10):
                    plan.append((sign_at, "wave"))
                    # 收尾挥手是礼数不是「换势太晚」：不进换势表，免得体检报警
                    gesture_audit.append({"at": round(t + dur * sign_at, 2),
                                          "idx": i, "pose": "wave", "want": "",
                                          "supported": [], "last": True})
            # 审计里的 alt 要记真正会播的第二档：句读计划生效后，候选姿势已经被替换
            if ga_base is not None:
                ga_base["alt"] = plan[0][1] if plan else ""
            seat = 0
            for mouth, seg in beats:
                use_pose = plan[seat - 1][1] if seat else pose
                while (seat < len(plan) and dur > 0
                       and (elapsed / dur) >= plan[seat][0]
                       and all(abs(elapsed - b) >= POSE_BLINK_GUARD for b in blinks)):
                    seat += 1
                    use_pose = plan[seat - 1][1]
                # 眨眼把一段口型切成「睁—眯—睁」：全片瞪着一只大眼不动，就是贴图
                for s_at, s_seg, s_eyes in _blink_spans(elapsed, seg, blinks):
                    frame = JE._character_frame(
                        char_scene, i, total, workdir, spec["width"], spec["height"],
                        speaking=bool(say), scene=scene, pose=use_pose, mouth=mouth,
                        eyes=s_eyes, expression=_pose_expression(use_pose),
                    )
                    if frame is None:
                        continue
                    frame, fx, fy, fw = _crop_frame(
                        frame, workdir, f"c{i:02d}_{use_pose}_{mouth}_{s_eyes or 'open'}.png")
                    start = t + s_at
                    end = start + s_seg
                    head = s_at == 0.0
                    if head and i == 0:
                        start = max(0.0, start - FADE)
                    elif head and char_ovs:
                        # 人物一直接到切点：上一屏最后一帧撑到 t，下一屏从 t 淡入。
                        # 提前露面会在叠化中段并排站成一对重影，提前收场则是消失又冒出来。
                        prev_char = char_ovs[-1]
                        prev_char["end"] = round(max(float(prev_char["end"]), t), 3)
                        prev_char["fade"] = 0.0
                    fade_in = FADE if head else 0.0
                    if head and i > 0:
                        # 上一屏人物撑到切点，新人物在切点整幅出现：中间留半拍透明，
                        # 就是「人闪了一下」。卡面在这里已经换完，人物硬切反而像一次换镜
                        fade_in = 0.0
                    if head and i > 0 and start > t:
                        start = t
                    fade_out = FADE if (i == total - 1 and end >= t + dur - 1e-6) else 0.0
                    ov = {
                        "src": str(frame),
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "x": float(fx), "y": float(fy), "opacity": 1.0,
                        "width": fw,
                        "fade": max(fade_in, fade_out),
                        # 相位对齐到绝对时间：口型分段不打断呼吸节奏
                        "motion": {**motion, "phase": round(start, 3),
                                   "amp_from": (_prev_amp[0] if _prev_amp[0] >= 0
                                                else float(motion.get("amplitude") or 0.0)),
                                   "ramp": AMP_RAMP},
                    }
                    _prev_amp[0] = float(motion.get("amplitude") or 0.0)
                    if head and i == 0:
                        # 首屏从画面下沿升上来，别一开片就杵在那儿
                        ov["enter"] = {"from": "bottom", "duration": 0.55,
                                       "ease": "out-cubic"}
                    if prev_ov is not None and use_pose != prev_pose:
                        # 换手势瞬间直接替换会「跳一下」，两头交叉叠过去，
                        # 上一段撑到叠化中点：新动作接住时旧动作刚好退干净
                        start = max(0.0, start - POSE_XFADE)
                        ov["start"] = round(start, 3)
                        ov["fade"] = max(ov["fade"], POSE_XFADE)
                        prev_ov["end"] = round(start + POSE_XFADE, 3)
                        prev_ov["fade"] = max(prev_ov["fade"], POSE_XFADE)
                    overlay.append(ov)
                    char_ovs.append(ov)
                    if char_anim:
                        char_segs.append({
                            "start": ov["start"], "end": ov["end"],
                            "width": char_scene.get("width"),
                            "x": char_scene.get("x"), "y": char_scene.get("y"),
                            "pose": use_pose, "mouth": mouth,
                            "eyes": s_eyes or "open",
                            "expression": _pose_expression(use_pose),
                            "outfit": char_scene.get("outfit") or None,
                            "anim": _char_anim_name(use_pose, bool(say)),
                            # 挥手是一次性动作：播一遍就停在放手站姿，不能循环
                            "anim_mode": "once" if use_pose == "wave" else "loop",
                            # 相位取绝对时间：同一条动画跨段接着播，不每段归零。
                            # 挥手晚 0.3 秒起：入场还在往上走就抬手，动作是抢的
                            "anim_t0": round(ov["start"] - (0.30 if use_pose == "wave" else 0.0), 3),
                            # 段自己的淡入淡出只在片头片尾用：每段末尾都淡一次，
                            # 换姿势那 0.22 秒人物会整片淡到看不见再跳回来（眨眼式闪断）。
                            # 段与段之间靠叠化时的权重过渡，这里留 0
                            "fade_in": 0.0,
                            "fade_out": 0.0,
                            "speaking": bool(say),
                            # 逐帧口型要的是这句配音本身和它在成片里的起点：
                            # 分段口型（0.1 秒一格）只能说"在动"，对不上字
                            "lip_audio": str(mp3) if say else None,
                            # 这句话实际念的字：口型从「按音量开合」改成「按说的是
                            # 哪个字定形」（见 export/lipsync.py）。缺了它只能退回
                            # 老的包络四档——嘴会动，但对不上字。
                            "lip_text": say if say else None,
                            "lip_t0": round(t, 3),
                            # 入场规格从 overlay 带过来：分层模式下运行时
                            # 只看得到分段表，不带就会整片站桩
                            "enter": ov.get("enter"),
                            # 角色根目录只有站姿、没有 anim/：动画得落到具体那套服装，
                            # 否则人物眨眼一次后就不动了，整条片子后半段是静帧
                            "_outfit_root": str(character.outfit_root(
                                char_scene.get("outfit") or character.DEFAULT_OUTFIT)),
                        })
                    prev_ov, prev_pose = ov, use_pose
                    last_ov = ov
                elapsed += seg
        t += dur

    # 收尾定住：最后一张卡接着往前推、人物留到最后，然后才淡出。
    # 这里不能再挂一段同图：同一张卡切成两段，缩放值从 1.0 跳回 1.015，
    # 观众看到的是收尾前一次明显的硬跳（体检里那两条 hard_cut）。
    if cards:
        last = video[-1]
        last["out"] = round(float(last["out"]) + TAIL, 3)
        z0 = float((last.get("zoom") or [1.0, 1.0])[0])
        last["zoom"] = [round(z0, 3), 1.05]
        # 收尾淡出拉长到 0.55 秒：0.3 秒从满亮掉到黑，像被人突然关了灯
        last["fade_out"] = round(TAIL * 0.55, 3)
        if last_ov is not None:
            last_ov["end"] = round(t + TAIL, 3)
            last_ov["fade"] = max(last_ov.get("fade") or 0.0,
                                  round(TAIL * 0.30, 3))
        if char_segs:
            char_segs[-1]["end"] = round(t + TAIL, 3)
            char_segs[-1]["fade_out"] = max(float(char_segs[-1].get("fade_out") or 0.0),
                                            round(TAIL * 0.30, 3))
        t += TAIL

    # 整片那块环境光的色温也跟着种子走：深蓝 / 深琥珀 / 深青轮着来，
    # 色温差得不多，但两条片子并排放能看出来不是同一条
    # 环境光整层去掉：两层柔光悬在卡面上慢慢摆，观众看到的是「背景那盏灯
    # 一会在动」，动作一切换就被读成「光效跟着变」。卡面自己已经有底光，
    # 不再加任何会动的光。
    _ = (workdir, variant_seed)

    char_mov = None
    char_x = char_y = 0
    if char_anim and char_segs:
        try:
            char_mov = CL.build(
                char_segs, width=int(spec["width"]), height=int(spec["height"]),
                fps=int(spec["fps"]), workdir=workdir, duration=round(t, 3),
                render=character.render, key_green=character._key_green,
                seed=variant_seed, log=lambda _m: None)
            if char_mov is not None:
                char_mov, char_x, char_y = char_mov
        except Exception as exc:  # noqa: BLE001
            # 以前这里一声不吭退回静帧：片子照样出，但口型轨整个没了，
            # 质检只看成片也说不清哪一步丢的。要静帧必须显式放行。
            msg = f"char_layer 构建失败（{type(exc).__name__}: {exc}）"
            print(msg + "，回落逐段静帧", file=sys.stderr)
            if os.environ.get("STUDIO_ALLOW_STATIC_CHARACTER", "").strip() not in ("1", "true", "yes"):
                raise RuntimeError(
                    msg + "：角色层是硬要求，不接受静默降级。"
                    " 真要出静帧片，设 STUDIO_ALLOW_STATIC_CHARACTER=1。") from exc
            char_mov = None
    lip_info = {}
    if char_mov is not None:
        try:
            lip_info = json.loads(
                (Path(workdir) / (char_mov.stem + "_lip.json")).read_text())
            lip_info = {"frames": lip_info.get("frames"),
                        "mix": lip_info.get("mix") or {}}
        except Exception:  # noqa: BLE001 - 只是给质检看的附注，读不到不影响出片
            lip_info = {}
        drop = {id(o) for o in char_ovs}
        overlay = [o for o in overlay if id(o) not in drop]
        overlay.append({
            "src": str(char_mov), "start": 0.0, "end": round(t, 3),
            "x": float(char_x), "y": float(char_y), "width": 0,
            "opacity": 1.0, "fade": 0.0,
            "motion": {"type": "none", "amplitude": 0.0, "period": 1.0},
        })

    return {
        "canvas": {"width": spec["width"], "height": spec["height"], "fps": spec["fps"]},
        "video": video,
        "overlay": bg_overlay + card_overlays + overlay,
        "audio": audio,
        "assetBase": str(workdir),
        "meta": {"engine": "vertical_short", "duration": round(t, 3),
                 "char_render": ("layer" if char_mov is not None else "stills"),
                 "scenes": total, "voice": voice,
                 "gesture": _gesture_summary(gesture_audit, switch_audit, motion_audit),
                 # 人物占宽：竖屏里缩成一张小贴纸，再准的手势也没人看见
                 "char_widths": char_widths,
                 "lip": lip_info,
                 "pacing": {"scenes": pacing,
                            "min_gap": min((x["gap"] for x in pacing), default=0.0),
                            "max_gap": max((x["gap"] for x in pacing), default=0.0)}},
    }


def _narration_dict(narr, n_points: int) -> dict:
    """口播稿两种形态：结构化 dict，或表单里的多行文本框（一行一说）。"""
    if isinstance(narr, str):
        lines = [x.strip() for x in narr.splitlines() if x.strip()]
        full = len(lines) == n_points + 2
        return {"intro": lines[0] if full and lines else "",
                "points": lines[1:-1] if full else lines,
                "outro": lines[-1] if full and lines else ""}
    return narr if isinstance(narr, dict) else {}


def _copy_report(script: dict) -> dict | None:
    """竖屏这条链路也要体检文案：写稿的人看的是同一个结论。"""
    try:
        from copy_check import check_copy
    except Exception:  # noqa: BLE001
        return None
    narr = _narration_dict(script.get("narration"), len(script.get("points") or []))
    pts = [str(x) for x in (script.get("points") or [])]
    says = [x.split("::")[2] if x.count("::") >= 2 else "" for x in pts]
    spoken = [str(narr.get("intro") or "")] if narr.get("intro") else []
    fallback = [str(x) for x in (narr.get("points") or [])]
    # 平台表单只填「标题::卡面正文」，口播稿另在 narration 里。不按序号拼成三段，
    # 回声判据就退化成拿全篇口播去比：同一句话换个说法照样过。
    if fallback and says != fallback:
        merged = []
        for i, raw in enumerate(pts):
            say = says[i] if i < len(says) and says[i] else (fallback[i] if i < len(fallback) else "")
            merged.append(raw if (not say or raw.count("::") >= 2) else f"{raw}::{say}")
        pts = merged
    spoken += [x for x in (says or fallback) if x]
    if narr.get("cta"):
        spoken.append(str(narr["cta"]))
    try:
        return check_copy(
            topic=str(script.get("topic") or ""),
            hook=str(script.get("hook") or ""),
            points=pts,
            cta=str(script.get("cta") or ""),
            narration=spoken,
        )
    except Exception as exc:  # noqa: BLE001 - 体检失败不影响出片
        return {"findings": [], "metrics": {}, "ok": True, "error": str(exc)[:160]}


def render(script: dict, workdir: Path, mp4: Path, *,
           voice: str = "", logo: Path | None = None) -> dict:
    workdir = Path(os.path.expanduser(str(workdir)))
    spec = build_spec(script, workdir, voice=voice, logo=logo)
    spec_path = workdir / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded = TR.load_spec(spec_path, base=workdir)
    ev = TR.render(loaded, mp4)
    copy_report = _copy_report(script)
    out = {
        "engine": "vertical_short",
        "videoPath": ev["output"],
        "specPath": str(spec_path),
        "duration": ev["duration"],
        "clips": ev["clips"],
        "texts": ev["texts"],
        "overlays": len(spec["overlay"]),
        "audios": ev["audios"],
        "renderSeconds": ev["seconds"],
    }
    if copy_report is not None:
        out["copyCheck"] = copy_report
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="竖屏 factcard 直接出 mp4（无剪映）")
    ap.add_argument("script", help="脚本 JSON：topic/hook/points/cta，或含这些字段的数组取第一条")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--mp4", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--logo", default="")
    args = ap.parse_args(argv)

    raw = json.loads(Path(os.path.expanduser(args.script)).read_text(encoding="utf-8"))
    script = raw[0] if isinstance(raw, list) else raw
    outdir = Path(args.outdir or "/tmp/vertical_short")
    mp4 = Path(args.mp4 or (outdir / "master.mp4"))
    logo = Path(args.logo).expanduser() if args.logo else (
        Path(str(script["logo"])).expanduser() if script.get("logo") else None)
    try:
        ev = render(script, outdir, mp4, voice=args.voice, logo=logo)
    except (VerticalError, TR.SpecError, ValueError) as exc:
        print(f"失败: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(ev, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
