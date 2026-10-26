"""知识科普幻灯片视频生成（slides 任务）。

版式来自对参考片的实测拆解（见 references/video-style-kvcache.md）：
三段固定结构 —— 顶部章节胶囊 / 中部图文 / 底部黑底字幕条；
镜头是静态图，节奏靠配音长度决定，中位停留 8.6 秒。

与 render_mpt 的分工：MPT 走「素材拼贴 + 转场」的视频路线，本模块走
「信息图卡 + 口播」的知识区路线。两者都只产出成片，不决定发布。
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import os
import re
import shutil
import subprocess
import time
import tempfile
from pathlib import Path
from typing import Any, Callable

W, H = 1920, 1080

# MPT 的 venv 里有 sami_tts 依赖（loguru / websockets），系统 python 没有。
MPT_HOME = Path(
    os.environ.get(
        "STUDIO_MPT_HOME",
        str(Path.home() / "CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo"),
    )
)

DEFAULT_VOICE = "sami:BV411_streaming"  # 剪映「解说小帅」

Progress = Callable[[int, str], None]


class SlidesError(RuntimeError):
    """幻灯片渲染失败（分镜非法、配音失败、合成失败）。"""


# ---------------------------------------------------------------- 版式模板

CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
html, body { width:__W__px; height:__H__px; overflow:hidden; }
body { font-family:"PingFang SC","Source Han Sans SC","Noto Sans CJK SC",sans-serif;
  background:#F6F7F9; color:#1A1F36; position:relative; }
body::before { content:""; position:absolute; inset:0;
  background:radial-gradient(circle at 50% 42%, #FFFFFF 0%, rgba(255,255,255,0) 62%);
  pointer-events:none; }
.nav { position:absolute; top:52px; left:96px; right:96px; display:flex;
  align-items:center; justify-content:space-between; z-index:2; }
.logo { display:flex; align-items:baseline; font-weight:800; font-size:29px; letter-spacing:1px; }
.logo .kv { color:#2B6BFF; } .logo .ca { color:#1A1F36; }
.tabs { display:flex; gap:9px; }
.tab { padding:9px 19px; border-radius:999px; font-size:18px; color:#6F7688;
  background:#FFFFFF; border:1px solid #E2E5EC; white-space:nowrap; font-weight:500; }
.tab.on { background:#1A1F36; color:#FFFFFF; border-color:#1A1F36; font-weight:600; }
.stage { position:absolute; top:148px; left:96px; right:96px; bottom:190px;
  display:flex; flex-direction:column; justify-content:center; z-index:2; }
.kicker { font-size:25px; color:#2B6BFF; font-weight:600; margin-bottom:16px; letter-spacing:0.4px; }
.title { font-size:76px; font-weight:700; line-height:1.18; letter-spacing:-0.5px; }
.title .hi { color:#2B6BFF; }
.sub { margin-top:18px; font-size:29px; color:#6B7280; font-weight:400; }
.caption-wrap { position:absolute; left:0; right:0; bottom:104px; display:flex;
  justify-content:center; z-index:3; }
.caption { background:rgba(26,31,54,0.94); color:#FFF; font-size:33px; line-height:1.42;
  font-weight:500; padding:14px 30px; border-radius:11px; max-width:1480px;
  text-align:center; box-shadow:0 8px 28px rgba(26,31,54,0.16); }
.bar { position:absolute; left:0; right:0; bottom:72px; height:3px; background:#D8DCE6; z-index:2; }
.bar .done { height:100%; background:#2B6BFF; }
.diagram { margin-top:60px; display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
.tok { width:176px; height:128px; border-radius:20px; display:flex; align-items:center;
  justify-content:center; font-size:44px; font-weight:600; background:#F0F5FF;
  border:1.5px solid #B9CFFF; color:#2B6BFF;
  box-shadow:0 6px 20px rgba(43,107,255,0.10), 0 1px 2px rgba(26,31,54,0.04); }
.tok.cur { background:#FFF7EC; border-color:#F6C77A; color:#B45309;
  box-shadow:0 6px 22px rgba(245,158,11,0.16), 0 1px 2px rgba(26,31,54,0.04); }
.tok.v { background:#EFFBF3; border-color:#A8E0BC; color:#15803D;
  box-shadow:0 6px 20px rgba(21,128,61,0.10), 0 1px 2px rgba(26,31,54,0.04); }
.tok.q { background:#FFF1F1; border-color:#F7B4B4; color:#B91C1C;
  box-shadow:0 6px 20px rgba(185,28,28,0.10), 0 1px 2px rgba(26,31,54,0.04); }
.arrow { font-size:36px; color:#2B6BFF; font-weight:600; margin:0 4px; }
.tag { font-size:21px; color:#8C92A4; margin-left:10px; font-weight:400; }
.cards { margin-top:52px; display:flex; gap:26px; align-items:stretch; }
.card { flex:1; background:#FFFFFF; border:1.5px solid #E2E5EC; border-radius:20px;
  padding:30px 32px; box-shadow:0 8px 26px rgba(26,31,54,0.06); }
.card .ct { font-size:27px; font-weight:700; margin-bottom:14px; color:#1A1F36; }
.card .cd { font-size:23px; color:#5B6478; line-height:1.55; }
.card.blue { border-color:#B9CFFF; } .card.blue .ct { color:#2B6BFF; }
.card.green { border-color:#A8E0BC; } .card.green .ct { color:#15803D; }
.card.orange { border-color:#F6C77A; } .card.orange .ct { color:#B45309; }
.flow { margin-top:48px; display:flex; align-items:center; gap:22px; flex-wrap:wrap; }
.box { border-radius:18px; padding:22px 26px; font-size:24px; font-weight:600;
  border:1.5px solid #E2E5EC; background:#FFF; box-shadow:0 6px 20px rgba(26,31,54,0.05); }
.box.warn { background:#FFF3F3; border-color:#F7B4B4; color:#B91C1C; }
.box.acc { background:#FFF7EC; border-color:#F6C77A; color:#B45309; }
.box.good { background:#EFFBF3; border-color:#A8E0BC; color:#15803D; }
.term { margin-top:52px; background:#1A1F36; border-radius:18px; padding:26px 30px;
  box-shadow:0 12px 34px rgba(26,31,54,0.18); max-width:1180px; }
.term .tb { display:flex; gap:8px; margin-bottom:18px; }
.term .tb i { width:13px; height:13px; border-radius:50%; display:block; }
.term .tb i:nth-child(1) { background:#FF5F57; }
.term .tb i:nth-child(2) { background:#FEBC2E; }
.term .tb i:nth-child(3) { background:#28C840; }
.term .tl { font-family:"SF Mono",Menlo,monospace; font-size:25px; line-height:1.75;
  color:#D7DCEA; white-space:pre-wrap; }
.term .tl .p { color:#7FB2FF; }
.term .tl .c { color:#E6EAF2; }
.term .tl .cm { color:#6B7590; }
""".replace("__W__", str(W)).replace("__H__", str(H))


def _e(v: Any) -> str:
    return _html.escape(str(v))


def _diagram(d: dict[str, Any]) -> str:
    if not d:
        return ""
    kind = d.get("kind", "tokens")
    out: list[str] = []

    if kind == "tokens":
        # tag_above: 标签放在行上方（默认侧边），避免「块内文字」与「块外标签」语义错位
        above = d.get("tag_above")
        if above and d.get("left_tag"):
            out.append(f'<div class="tag" style="margin:0 0 14px 2px">{_e(d["left_tag"])}</div>')
        out.append('<div class="diagram">')
        if d.get("left_tag") and not above:
            out.append(f'<span class="tag" style="margin-left:0">{_e(d["left_tag"])}</span>')
        for it in d.get("items", []):
            if str(it.get("text")) == "→":
                out.append('<div class="arrow">→</div>')
                continue
            cls = "tok" + (f' {it["cls"]}' if it.get("cls") else "")
            out.append(f'<div class="{cls}">{_e(it["text"])}</div>')
            if it.get("arrow"):
                out.append('<div class="arrow">→</div>')
        if d.get("right_tag"):
            out.append(f'<span class="tag">{_e(d["right_tag"])}</span>')
        out.append("</div>")

    elif kind == "cards":
        out.append('<div class="cards">')
        for c in d.get("items", []):
            out.append(
                f'<div class="card {c.get("color", "")}">'
                f'<div class="ct">{_e(c["title"])}</div>'
                f'<div class="cd">{_e(c["desc"])}</div></div>'
            )
        out.append("</div>")

    elif kind == "flow":
        out.append('<div class="flow">')
        for it in d.get("items", []):
            if str(it.get("text")) == "→":
                out.append('<div class="arrow">→</div>')
            else:
                out.append(f'<div class="box {it.get("cls", "")}">{_e(it["text"])}</div>')
        out.append("</div>")

    elif kind == "code":
        # 终端块：items 每项一行，kind 为 cmd（带 $ 提示符）或 out（输出）或 cm（注释）
        lines: list[str] = []
        for it in d.get("items", []):
            t = _e(it.get("text", ""))
            lk = it.get("kind", "cmd")
            if lk == "cmd":
                lines.append(f'<div><span class="p">$ </span><span class="c">{t}</span></div>')
            elif lk == "cm":
                lines.append(f'<div class="cm">{t}</div>')
            else:
                lines.append(f'<div>{t}</div>')
        out.append(
            '<div class="term"><div class="tb"><i></i><i></i><i></i></div>'
            f'<div class="tl">{"".join(lines)}</div></div>'
        )

    else:
        raise SlidesError(f"unknown diagram kind: {kind}")

    return "".join(out)


def _page(scene: dict[str, Any], idx: int, total: int, g: dict[str, Any]) -> str:
    chapters = g["chapters"]
    active = int(scene.get("active", 0))
    tabs = "".join(
        f'<div class="{"tab on" if i == active else "tab"}">{_e(c)}</div>'
        for i, c in enumerate(chapters)
    )
    title = _e(scene["title"])
    if scene.get("hi"):
        title = title.replace(_e(scene["hi"]), f'<span class="hi">{_e(scene["hi"])}</span>', 1)

    pct = (idx + 1) / max(total, 1) * 100
    sub = f'<div class="sub">{_e(scene["sub"])}</div>' if scene.get("sub") else ""

    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<style>{CSS}</style></head><body>"
        '<div class="nav">'
        f'<div class="logo"><span class="kv">{_e(g["logo_a"])}</span>'
        f'<span class="ca">{_e(g["logo_b"])}</span></div>'
        f'<div class="tabs">{tabs}</div></div>'
        '<div class="stage">'
        f'<div class="kicker">{_e(scene.get("kicker", ""))}</div>'
        f'<div class="title">{title}</div>{sub}'
        f'{_diagram(scene.get("diagram") or {})}'
        "</div>"
        f'<div class="caption-wrap"><div class="caption">{_e(scene["caption"])}</div></div>'
        f'<div class="bar"><div class="done" style="width:{pct:.2f}%"></div></div>'
        "</body></html>"
    )


# ---------------------------------------------------------------- 执行

def _shot(page: Path, png: Path) -> None:
    env = dict(os.environ)
    env["PATH"] = f"{Path.home()}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
    r = subprocess.run(
        ["uvx", "shot-scraper", "shot", str(page), "--width", str(W),
         "--height", str(H), "--output", str(png)],
        capture_output=True, text=True, env=env, timeout=240,
    )
    if not png.exists() or png.stat().st_size < 1000:
        raise SlidesError(
            f"shot-scraper failed: {(r.stderr or r.stdout or 'no output').strip()[-400:]}"
        )


TTS_CACHE = Path(os.path.expanduser("~/CreatorStudio/cache/tts"))


TTS_CACHE_MAX_BYTES = 1200 * 1024 * 1024   # 缓存上限：一只 20 秒片约 200KB，够存几千条
TTS_CACHE_MAX_AGE = 45 * 86400              # 45 天没再用到的旧稿没人会再出片


def prune_tts_cache(keep: Path | None = None) -> None:
    """配音缓存要封顶：一直只写不删，跑几个月磁盘满了，整个平台跟着躺。

    先按年龄删，再按总量从最久没用的删起；刚写进去的那条永远留着。
    清理失败不影响出片——缓存是加速用的，不是产物。
    """
    try:
        if not TTS_CACHE.exists():
            return
        now = time.time()
        files = []
        for f in TTS_CACHE.glob("*/*.mp3"):
            try:
                st = f.stat()
            except OSError:
                continue
            if keep is not None and f == keep:
                continue
            if now - st.st_mtime > TTS_CACHE_MAX_AGE:
                try:
                    f.unlink()
                except OSError:
                    pass
                continue
            files.append((st.st_mtime, st.st_size, f))
        total = sum(sz for _m, sz, _f in files)
        if total <= TTS_CACHE_MAX_BYTES:
            return
        for _m, sz, f in sorted(files):
            if total <= TTS_CACHE_MAX_BYTES:
                break
            try:
                f.unlink()
                total -= sz
            except OSError:
                pass
    except Exception:
        pass


def _tts_cache(text: str, voice: str, rate: float = 1.0) -> Path | None:
    """配音缓存键：音色 + 语速 + 原文。稿子改一个字就重新合成，不改就复用。"""
    key = hashlib.sha1(f"{voice}\x00{rate:.3f}\x00{text}".encode()).hexdigest()[:20]
    slug = re.sub(r"[^0-9A-Za-z]+", "-", voice).strip("-") or "voice"
    return TTS_CACHE / slug / f"{key}.mp3"


# edge-tts 的 voice id：`zh-CN-XiaoxiaoNeural` 这一类。系统 python3 没有 edge_tts，
# 合成走 MPT venv 的 python 跑 tools/edge_tts_once.py，本进程绝不 import edge_tts。
_EDGE_VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+Neural$")
EDGE_TTS_ONCE = Path(__file__).resolve().parent / "tools" / "edge_tts_once.py"


def _edge_voice(voice: str) -> str | None:
    """`edge:zh-CN-XiaoxiaoNeural` / 裸 `zh-CN-XiaoxiaoNeural` → voice id，否则 None。"""
    raw = str(voice or "").strip()
    name = raw[5:].strip() if raw.lower().startswith("edge:") else raw
    return name if _EDGE_VOICE_RE.match(name) else None


def _edge_rate(rate: float) -> str:
    """1.0 → +0%，0.92 → -8%；edge-tts 吃百分比字符串，夹在 ±30%。"""
    pct = max(-30.0, min(30.0, (float(rate) - 1.0) * 100.0))
    return f"{pct:+.0f}%"


def _words_path(mp3: Path) -> Path:
    """逐字 sidecar 与 mp3 同目录同 stem：export/lipsync.py 就按这个约定找。"""
    return mp3.with_suffix(".words.json")


def _words_ok(path: Path) -> bool:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    chars = blob.get("chars") if isinstance(blob, dict) else None
    return isinstance(chars, list) and bool(chars)


def _edge_tts(text: str, mp3: Path, voice: str, rate: float) -> None:
    """edge-tts 合成 + 逐字 sidecar；两者同进同出缓存，缺一个就重新合成。

    有 mp3 没 sidecar 时不能静默退回包络猜口型（那样口型与真声音只是「差不多」），
    所以缓存命中要求两者都在，缺 sidecar 就整句重合成。
    """
    name = _edge_voice(voice)
    if not name:
        raise SlidesError(f"not an edge voice: {voice!r}")
    python = MPT_HOME / ".venv/bin/python"
    if not python.exists():
        raise SlidesError(f"MPT venv missing at {python}; run deploy/bootstrap_mpt.sh")
    if not EDGE_TTS_ONCE.exists():
        raise SlidesError(f"edge_tts_once.py not found at {EDGE_TTS_ONCE}")

    cached = _tts_cache(text, f"edge:{name}", rate)
    cached_words = _words_path(cached)
    sidecar = _words_path(mp3)
    if cached.exists() and cached.stat().st_size >= 1000:
        if _words_ok(cached_words):
            shutil.copyfile(cached, mp3)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(cached_words, sidecar)
            return
        # 旧缓存只有 mp3、没有逐字时间：删掉重合成，别让这一屏悄悄降级
        try:
            cached.unlink()
        except OSError:
            pass

    # 整句中文塞 argv 会撞长度/编码，落成临时文件交给子进程读
    txt = mp3.with_suffix(".tts.txt")
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(
            [str(python), str(EDGE_TTS_ONCE), "--text", str(txt), "--voice", name,
             f"--rate={_edge_rate(rate)}", "--out", str(mp3),
             "--words", str(sidecar)],
            capture_output=True, text=True, cwd=str(MPT_HOME), timeout=180,
        )
        detail = (r.stderr or r.stdout or "no output").strip()[-400:]
        if not mp3.exists() or mp3.stat().st_size < 1000:
            raise SlidesError(f"edge tts failed for {name}: {detail}")
        if not _words_ok(sidecar):
            raise SlidesError(
                f"edge tts for {name} produced no word boundaries: {detail}")
    finally:
        try:
            txt.unlink()
        except OSError:
            pass

    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mp3, cached)
        shutil.copyfile(sidecar, cached_words)
        prune_tts_cache(cached)
    except OSError:
        pass


def _tts(text: str, mp3: Path, voice: str, rate: float = 1.0) -> None:
    """配音：`edge:` / `*Neural` 音色走 edge-tts，其余走 SAMI（默认，行为不变）。

    SAMI 分支：worker 跑在系统 python3 上，没有 MPT venv 的依赖
    （loguru/websockets），所以**不能在本进程 import sami_tts**，必须用子进程
    交给 MPT 的解释器跑。edge 分支同理，交给 tools/edge_tts_once.py。
    """
    edge_name = _edge_voice(voice)
    if edge_name:
        _edge_tts(text, mp3, voice, rate)
        return
    python = MPT_HOME / ".venv/bin/python"
    script = MPT_HOME / "app/services/sami_tts.py"
    if not python.exists():
        raise SlidesError(f"MPT venv missing at {python}; run deploy/bootstrap_mpt.sh")
    if not script.exists():
        raise SlidesError(f"sami_tts not found at {script}")

    code = (
        "import sys\n"
        "from app.services.sami_tts import sami_tts\n"
        "sami_tts(sys.argv[1], sys.argv[2], float(sys.argv[4]), sys.argv[3])\n"
    )
    env = dict(os.environ)
    env["MPT_HOME"] = str(MPT_HOME)
    env["PYTHONPATH"] = str(MPT_HOME)
    # 同音色同文本只合成一次：SAMI 流式音色每次采样的停顿、尾音都有细微差别，
    # 不缓存的话同一份稿子两次出片口型与时长两样，批量出片没法比母版。
    cached = _tts_cache(text, voice, rate)
    if cached is not None:
        if cached.exists() and cached.stat().st_size >= 1000:
            shutil.copyfile(cached, mp3)
            return
    r = subprocess.run(
        [str(python), "-c", code, text, voice, str(mp3), f"{rate:.3f}"],
        capture_output=True, text=True, cwd=str(MPT_HOME), env=env, timeout=180,
    )
    if not mp3.exists() or mp3.stat().st_size < 1000:
        raise SlidesError(
            f"tts failed for {voice}: {(r.stderr or r.stdout or 'no output').strip()[-400:]}"
        )
    if cached is not None:
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(mp3, cached)
            prune_tts_cache(cached)
        except OSError:
            pass


def _dur(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise SlidesError(f"ffprobe could not read duration: {p}") from None


def render(spec: dict[str, Any], job_dir: Path, progress: Progress | None = None) -> dict[str, Any]:
    """把分镜 spec 渲成一条 MP4，返回 {videoPath, duration, scenes}。"""
    say = progress or (lambda *_: None)

    scenes = spec.get("scenes") or []
    if not scenes:
        raise SlidesError("spec has no scenes")
    for i, sc in enumerate(scenes):
        for key in ("title", "caption"):
            if not str(sc.get(key) or "").strip():
                raise SlidesError(f"scene {i} missing {key}")

    g = {
        "chapters": spec.get("chapters") or [f"0{i + 1}" for i in range(len(scenes))],
        "logo_a": spec.get("logo_a", "KV"),
        "logo_b": spec.get("logo_b", "CACHE"),
    }
    voice = spec.get("voice") or DEFAULT_VOICE
    # 语速只有 edge 音色吃：SAMI 分支保持原样（rate 固定 1.0），现有成片不受影响
    rate = 1.0
    if _edge_voice(voice):
        try:
            rate = float(spec.get("rate") or 1.0)
        except (TypeError, ValueError):
            rate = 1.0

    job_dir = Path(job_dir)
    (job_dir / "pages").mkdir(parents=True, exist_ok=True)
    (job_dir / "audio").mkdir(parents=True, exist_ok=True)

    segs: list[tuple[Path, Path, float]] = []
    n = len(scenes)
    for i, sc in enumerate(scenes):
        say(int(5 + 70 * i / n), f"slides:page {i + 1}/{n}")
        page = job_dir / "pages" / f"s{i:02d}.html"
        png = job_dir / "pages" / f"s{i:02d}.png"
        mp3 = job_dir / "audio" / f"s{i:02d}.mp3"
        page.write_text(_page(sc, i, n, g), encoding="utf-8")
        _shot(page, png)
        _tts(sc["caption"], mp3, voice, rate)
        segs.append((png, mp3, _dur(mp3)))

    say(80, "slides:encoding")
    parts = []
    for i, (png, mp3, d) in enumerate(segs):
        seg = job_dir / f"seg{i:02d}.mp4"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-loop", "1", "-i", str(png), "-i", str(mp3),
             "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
             "-r", "30", "-t", f"{d:.3f}",
             "-c:a", "aac", "-b:a", "128k", "-shortest", str(seg)],
            capture_output=True, text=True, timeout=300,
        )
        if not seg.exists():
            raise SlidesError(f"ffmpeg segment {i} failed: {r.stderr.strip()[-300:]}")
        parts.append(seg)

    say(92, "slides:concat")
    lst = job_dir / "concat.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
    out = job_dir / "final.mp4"
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    if not out.exists() or out.stat().st_size < 10000:
        raise SlidesError(f"concat failed: {r.stderr.strip()[-300:]}")

    total = sum(d for _, _, d in segs)
    return {
        "videoPath": str(out),
        "duration": round(total, 3),
        "scenes": n,
        "voice": voice,
        "width": W,
        "height": H,
    }


def render_from_payload(payload: dict[str, Any], job_dir: Path,
                        progress: Progress | None = None) -> dict[str, Any]:
    """worker 入口：payload 里的 spec 可以是内联对象或文件路径。"""
    spec = payload.get("spec")
    if isinstance(spec, str):
        spec = json.loads(Path(spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise SlidesError("payload.spec must be an object or a path to a JSON file")
    return render(spec, Path(job_dir), progress)
