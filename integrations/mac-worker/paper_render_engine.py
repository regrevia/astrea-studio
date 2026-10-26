#!/usr/bin/env python3
"""paper 引擎：整条片子按 HTML/CSS 排版，逐帧截成视频。

为什么不用现成的卡片渲染（slides_render / render_ffmpeg）：那两条是
PIL 画图 + 位移淡入，字号行距靠手算，做出来的东西一眼是「模板 PPT」。
纸面这条走真排版：字体度量、断行、字距、网格都由浏览器算，1080×1920
版心固定，动效用 clip-path + transform 表达，所以能做出印刷品的呼吸感。

链路：scene 计划 → HTML（window.renderFrame(t) 是纯函数）→ 无头 Chrome
逐帧 JPEG → ffmpeg 编码 → 可选配音合流。不依赖 playwright，只用 Node 24
自带的 WebSocket 走 CDP。
"""

from __future__ import annotations

import html as _html
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

WIDTH = 1080
HEIGHT = 1920
FPS = 30
HERE = Path(__file__).resolve().parent
CDP_DRIVER = HERE / "paper" / "cdp_frames.mjs"

_CHROME_CANDIDATES = [
    os.environ.get("CHROME_BIN") or "",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def _node() -> str:
    for cand in (shutil.which("node"), "/Users/apple/.local/opt/node-current/bin/node"):
        if cand and Path(cand).exists():
            return cand
    raise RuntimeError("找不到 node")


def _chrome() -> str:
    for cand in _CHROME_CANDIDATES:
        if cand and Path(cand).exists():
            return cand
    for base in (Path.home() / "Library/Caches/ms-playwright").glob("chromium_headless_shell-*"):
        for p in base.glob("chrome-mac*/**/headless_shell"):
            if p.exists():
                return str(p)
    raise RuntimeError("找不到 Chrome / headless_shell")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _esc(text: str) -> str:
    return _html.escape(str(text or "")).replace("\n", "<br>")


def _sheet(scene: dict, i: int, total: int) -> str:
    kicker = scene.get("kicker") or f"{i + 1:02d} / {total:02d}"
    headline = _esc(scene.get("headline") or "")
    body = _esc(scene.get("body") or "")
    foot = _esc(scene.get("foot") or "")
    rule = bool(scene.get("rule", True))
    return f"""<section class="sheet" data-i="{i}">
  <div class="frame"></div>
  <div class="pad">
    <div class="kicker rise">{_esc(kicker)}</div>
    <div class="block">
      <div class="head rise">{headline}</div>
      {'<div class="rule"></div>' if rule else ''}
      <div class="body rise">{body}</div>
    </div>
    <div class="foot rise">{foot}</div>
  </div>
</section>"""


_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:%(W)dpx;height:%(H)dpx;overflow:hidden;background:#12100E}
body{font-kerning:normal;text-rendering:geometricPrecision;
  -webkit-font-smoothing:antialiased}
#stage{position:relative;width:%(W)dpx;height:%(H)dpx;background:#F2ECE1}
.sheet{position:absolute;inset:0;background:#F2ECE1;opacity:0;will-change:opacity,transform}
.sheet::after{content:"";position:absolute;inset:0;pointer-events:none;
  background-image:radial-gradient(120%% 85%% at 50%% 8%%,rgba(255,255,255,.55),rgba(255,255,255,0) 62%%),
    radial-gradient(80%% 62%% at 86%% 97%%,rgba(150,134,112,.16),rgba(150,134,112,0) 58%%);
  mix-blend-mode:multiply;opacity:.9}
/* 纸纹：极细的斜纹 + 颗粒，静止不动，出现颗粒感而不是塑料感 */
.grain{position:absolute;inset:0;pointer-events:none;opacity:.5;mix-blend-mode:multiply;
  background-image:repeating-linear-gradient(102deg,rgba(120,104,84,.045) 0 1px,transparent 1px 3px),
    repeating-linear-gradient(8deg,rgba(120,104,84,.035) 0 1px,transparent 1px 4px)}
.frame{position:absolute;inset:44px;border:1px solid rgba(88,74,58,.22)}
.pad{position:absolute;inset:44px}
.kicker{position:absolute;left:84px;top:96px;font:600 25px/1.2 "PingFang SC","Hiragino Sans GB",sans-serif;
  letter-spacing:.42em;color:#8C6A4F;text-transform:uppercase}
/* 版心固定在 28 个百分点处：每一屏标题基线一致，切场景时字不会上下跳 */
.block{position:absolute;left:84px;right:84px;top:28%%;max-width:900px}
.head{font:600 92px/1.22 "Songti SC","STSongti-SC-Regular","Source Han Serif SC",serif;
  letter-spacing:.02em;color:#231E19}
.head em{font-style:normal;color:#A34B34}
.rule{width:0;height:3px;background:#A34B34;margin:34px 0 30px}
.body{font:400 41px/1.72 "PingFang SC","Hiragino Sans GB",sans-serif;color:#3E3831;
  letter-spacing:.01em;max-width:880px}
.foot{position:absolute;left:84px;right:84px;bottom:88px;display:flex;justify-content:space-between;align-items:baseline;
  font:400 23px/1.2 "PingFang SC",sans-serif;color:#8A7C69;letter-spacing:.16em}
.rise{opacity:0;transform:translateY(26px)}
""" % {"W": WIDTH, "H": HEIGHT}


# ---------------------------------------------------------------- schematic
# 纸面版是「印刷品」；缩略图版是「仪表盘」——整条片只有一个连续运动的 SVG 背景，
# 文字和真实截图浮在它上面。背景一直在动，字换得再快也不会有翻页感。

_CSS_SCHEMATIC = f"""
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden;background:#07101A}}
body{{font-kerning:normal;text-rendering:geometricPrecision;-webkit-font-smoothing:antialiased}}
#stage{{position:relative;width:{WIDTH}px;height:{HEIGHT}px;overflow:hidden;
  background:radial-gradient(120% 78% at 50% 12%,#101E2C 0%,#0A141F 46%,#060D15 100%)}}
#bg{{position:absolute;inset:0;width:{WIDTH}px;height:{HEIGHT}px}}
.sheet{{position:absolute;inset:0;opacity:0;will-change:opacity}}
.pad{{position:absolute;left:84px;right:84px;top:104px;bottom:104px}}
.kicker{{font:500 26px/1 "SF Mono","JetBrains Mono",ui-monospace,monospace;
  letter-spacing:.30em;color:#5ED3BE;text-transform:uppercase;opacity:0}}
.head{{margin-top:26px;font:700 90px/1.14 "PingFang SC","Hiragino Sans GB",sans-serif;
  letter-spacing:-.01em;color:#EAF2FA;opacity:0}}
.head .em{{color:#5ED3BE}}
.accent{{height:2px;background:linear-gradient(90deg,#5ED3BE,rgba(94,211,190,0));margin:38px 0 34px;width:0}}
.body{{font:400 40px/1.66 "PingFang SC","Hiragino Sans GB",sans-serif;color:#A9BDD0;
  letter-spacing:.01em;max-width:900px;opacity:0}}
.body b{{color:#EAF2FA;font-weight:600}}
.shot{{position:absolute;left:84px;right:84px;bottom:112px;height:560px;border-radius:16px;
  overflow:hidden;border:1px solid rgba(94,211,190,.30);
  box-shadow:0 26px 60px -30px rgba(0,0,0,.85);transform-origin:50% 100%}}
.shot img{{width:100%;height:100%;object-fit:cover;object-position:top center;display:block}}
.shot::after{{content:"";position:absolute;inset:0;
  background:linear-gradient(180deg,rgba(6,13,21,0) 62%,rgba(6,13,21,.55) 100%)}}
.shot .cap{{position:absolute;left:22px;bottom:18px;z-index:2;
  font:500 22px/1.3 "SF Mono",ui-monospace,monospace;letter-spacing:.06em;color:#CFE3F2}}
.foot{{position:absolute;left:84px;right:84px;bottom:52px;display:flex;
  justify-content:space-between;align-items:baseline;
  font:400 22px/1.2 "SF Mono",ui-monospace,monospace;letter-spacing:.18em;color:#6C8BA8}}
"""

_SVG_STAGE = """
<svg id="bg" viewBox="0 0 1080 1920" preserveAspectRatio="none">
  <defs>
    <pattern id="dots" width="56" height="56" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.6" fill="#3C6E80" opacity=".55"/>
    </pattern>
    <linearGradient id="edge" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#7FA6C0" stop-opacity="0"/>
      <stop offset=".5" stop-color="#7FA6C0" stop-opacity=".45"/>
      <stop offset="1" stop-color="#7FA6C0" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <g id="grid"><rect x="-400" y="-400" width="1900" height="2800" fill="url(#dots)"/></g>
  <g id="rings" fill="none" stroke="#4E93A8" stroke-opacity=".30">
    <circle id="r0" cx="540" cy="770" r="282" stroke-dasharray="3 17"/>
    <circle id="r1" cx="540" cy="770" r="430" stroke-dasharray="44 30" stroke-opacity=".20"/>
    <circle id="r2" cx="540" cy="770" r="600" stroke-dasharray="2 26" stroke-opacity=".14"/>
  </g>
  <polyline id="signal" points="0,1318 150,1306 268,1330 402,1272 548,1296 690,1246 812,1274 940,1228 1080,1252"
    fill="none" stroke="#5ED3BE" stroke-opacity=".55" stroke-width="2"
    stroke-dasharray="46 26"/>
  <g id="nodes" fill="#5ED3BE">
    <circle id="n0" cx="150" cy="1306" r="5"/><circle id="n1" cx="402" cy="1272" r="5"/>
    <circle id="n2" cx="690" cy="1246" r="5"/><circle id="n3" cx="940" cy="1228" r="5"/>
  </g>
  <line x1="0" y1="1318" x2="1080" y2="1318" stroke="url(#edge)" stroke-width="1"/>
</svg>"""


def _sheet_schematic(scene: dict, i: int, total: int) -> str:
    kicker = _esc(scene.get("kicker") or f"{i + 1:02d} / {total:02d}")
    headline = _esc(scene.get("headline") or "")
    body = _esc(scene.get("body") or "")
    shot = str(scene.get("shot") or "")
    cap = _esc(scene.get("cap") or "")
    shot_html = ""
    if shot:
        src = shot if shot.startswith("file:") else "file://" + shot
        shot_html = (f'<div class="shot rise"><img src="{src}" alt="">'
                     f'<div class="cap">{cap}</div></div>')
    return f"""<section class="sheet" data-i="{i}">
  <div class="pad">
    <div class="kicker rise">{kicker}</div>
    <div class="head rise">{headline}</div>
    <div class="accent"></div>
    <div class="body rise">{body}</div>
  </div>
  {shot_html}
</section>"""


def _html_doc_schematic(scenes: list[dict], total: float) -> str:
    payload = json.dumps(
        [{"start": float(s["start"]), "end": float(s["end"])} for s in scenes],
        ensure_ascii=False,
    )
    sheets = "\n".join(_sheet_schematic(s, i, len(scenes)) for i, s in enumerate(scenes))
    js = """
const SC = %(P)s;
const sheets = [...document.querySelectorAll('.sheet')];
const grid = document.getElementById('grid');
const rings = [0, 1, 2].map((k) => document.getElementById('r' + k));
const signal = document.getElementById('signal');
const nodes = [0, 1, 2, 3].map((k) => document.getElementById('n' + k));
const EASE = (x) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);
const LEN = signal.getTotalLength();
window.renderFrame = function (t) {
  // 背景是**一条连续的运动**：格子漂、环转、信号线跑、扫描带走，全程不停
  grid.setAttribute('transform', `translate(${(t * 13 %% 56).toFixed(2)},${(t * 7 %% 56).toFixed(2)})`);
  rings.forEach((r, k) => {
    const cx = r.getAttribute('cx'), cy = r.getAttribute('cy');
    r.setAttribute('transform', `rotate(${(t * (5.5 - k * 1.6) * (k %% 2 ? -1 : 1)).toFixed(3)} ${cx} ${cy})`);
  });
  signal.setAttribute('stroke-dashoffset', (-t * 130).toFixed(2));
  nodes.forEach((n, k) => {
    const ph = k * 1.7;
    // 点位固定不动：半径/透明度呼吸在成片里就是「一闪一闪的灯」
  });
  for (let i = 0; i < sheets.length; i++) {
    const s = SC[i];
    const el = sheets[i];
    if (t < s.start - 0.01 || t > s.end + 0.01) { el.style.opacity = 0; continue; }
    const fadeOut = 0.34, fadeIn = 0.40;
    const out = EASE((s.end - t) / fadeOut);
    const inn = EASE((t - s.start) / fadeIn);
    const fading = t > s.end - fadeOut;
    el.style.zIndex = fading ? 900 : 100 + i;
    el.style.opacity = fading ? out.toFixed(4) : '1';
    const local = t - s.start;
    // 字不整块平移，只各走各的；幅度小到看不出「入场动画」，只觉得画面在呼吸
    const head = el.querySelector('.head');
    const kick = el.querySelector('.kicker');
    const body = el.querySelector('.body');
    const acc = el.querySelector('.accent');
    const shot = el.querySelector('.shot');
    const pHead = EASE((local - 0.04) / 0.52);
    head.style.opacity = (fading ? 1 : pHead).toFixed(3);
    head.style.transform = `translateY(${((1 - pHead) * 20).toFixed(2)}px)`;
    kick.style.opacity = (fading ? 1 : EASE((local - 0.0) / 0.4)).toFixed(3);
    body.style.opacity = (fading ? 1 : EASE((local - 0.16) / 0.5)).toFixed(3);
    body.style.transform = `translateY(${((1 - EASE((local - 0.16) / 0.5)) * 22).toFixed(2)}px)`;
    acc.style.width = (EASE((local - 0.10) / 0.5) * 100).toFixed(2) + '%%';
    if (shot) {
      const p = EASE((local - 0.22) / 0.6);
      shot.style.opacity = (fading ? 1 : p).toFixed(3);
      shot.style.transform = `translateY(${((1 - p) * 46).toFixed(2)}px) scale(${(1.02 - 0.02 * p).toFixed(4)})`;
      const img = shot.querySelector('img');
      if (img) img.style.transform = `scale(${(1 + 0.04 * Math.min(1, local / 8)).toFixed(4)})`;
    }
  }
};
window.renderFrame(0);
""" % {"P": payload}
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>schematic</title><style>{_CSS_SCHEMATIC}</style></head>
<body><div id="stage">{_SVG_STAGE}{sheets}</div>
<script>{js}</script></body></html>"""


def _html_doc(scenes: list[dict], total: float) -> str:
    payload = json.dumps(
        [{"start": float(s["start"]), "end": float(s["end"])} for s in scenes],
        ensure_ascii=False,
    )
    sheets = "\n".join(_sheet(s, i, len(scenes)) for i, s in enumerate(scenes))
    js = """
const SC = %(P)s;
const sheets = [...document.querySelectorAll('.sheet')];
const rises = sheets.map((el) => [...el.querySelectorAll('.rise')]);
const rules = sheets.map((el) => el.querySelector('.rule'));
const EASE = (x) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);
function clamp01(x){ return Math.min(1, Math.max(0, x)); }
// renderFrame(t) 是纯函数：第 t 秒的画面只由 t 决定，逐帧截图才能复现同一条片
window.renderFrame = function (t) {
  for (let i = 0; i < sheets.length; i++) {
    const s = SC[i];
    const el = sheets[i];
    if (t < s.start - 0.01 || t > s.end + 0.01) { el.style.opacity = 0; continue; }
    const out = EASE((s.end - t) / 0.42);
    const inn = EASE((t - s.start) / 0.42);
    const fading = t > s.end - 0.42;
    // 交叉淡化的两层不要同时半透明（会糊成一片灰）；让走的那层压在上面褪掉
    el.style.zIndex = fading ? 900 : 100 + i;
    el.style.opacity = fading ? out.toFixed(4) : '1';
    // 整张纸极慢的推近：1.2%% / 10s，静止画面看着不「卡住」
    const local = t - s.start;
    const y = fading ? -18 * (1 - out) : 18 * (1 - inn);
    el.style.transform = `translateY(${y.toFixed(2)}px) scale(${(1 + 0.012 * Math.min(1, local / 10)).toFixed(5)})`;
    const head = rises[i][0];
    rises[i].forEach((node, k) => {
      if (node === head) return;
      // 上一页褪下去的时候这页的字必须已经在纸上，否则交叉淡化的中间
      // 那几帧是空纸，体检报「卡面没字」，眼睛看也像闪了一下白。
      const d = k === 1 ? 0.06 : k === 2 ? 0.14 : k === 3 ? 0.22 : 0.2;
      const p = EASE((local - d) / 0.42);
      node.style.opacity = p.toFixed(3);
      node.style.transform = `translateY(${(1 - p) * 26}px)`;
    });
    // 标题逐字上浮：中文按字断，一行行错 55ms 出来
    const p0 = EASE((local - 0.02) / 0.48);
    head.style.opacity = p0.toFixed(3);
    head.style.transform = `translateY(${(1 - p0) * 34}px)`;
    if (rules[i]) {
      const pr = EASE((local - 0.12) / 0.46);
      rules[i].style.width = (pr * 100).toFixed(2) + '%%';
    }
  }
};
window.renderFrame(0);
""" % {"P": payload}
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>paper</title><style>{_CSS}</style></head>
<body><div id="stage">{sheets}<div class="grain"></div></div>
<script>{js}</script></body></html>"""


def render_frames(html_path: Path, frames_dir: Path, seconds: float, *, fps: int = FPS,
                  log=print) -> int:
    port = _free_port()
    tmp = Path(tempfile.mkdtemp(prefix="paper-chrome-"))
    chrome = _chrome()
    args = [
        chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
        "--no-first-run", "--no-default-browser-check", "--use-mock-keychain",
        "--allow-file-access-from-files", "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
        f"--window-size={WIDTH},{HEIGHT}", f"--user-data-dir={tmp}",
        f"--remote-debugging-port={port}", "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        out = subprocess.run(
            [_node(), str(CDP_DRIVER), str(port), html_path.resolve().as_uri(),
             str(fps), f"{seconds:.3f}", str(frames_dir)],
            capture_output=True, text=True, timeout=60 + seconds * 6,
        )
        if out.returncode != 0:
            raise RuntimeError(f"逐帧渲染失败: {out.stderr[-800:]}")
        info = json.loads(out.stdout.strip().splitlines()[-1])
        log(f"[paper] frames={info['frames']} -> {frames_dir}")
        return int(info["frames"])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def encode(frames_dir: Path, out_mp4: Path, *, fps: int = FPS, audio: str | None = None,
           log=print) -> Path:
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
           "-i", str(frames_dir / "f%05d.jpg")]
    if audio:
        cmd += ["-i", audio]
    cmd += ["-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-movflags", "+faststart"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += [str(out_mp4)]
    subprocess.run(cmd, check=True, timeout=900)
    log(f"[paper] encoded {out_mp4} ({out_mp4.stat().st_size // 1024} KB)")
    return out_mp4


def render(plan: list[dict], out_mp4: Path, *, audio: str | None = None, fps: int = FPS,
           workdir: Path | None = None, style: str = "paper", log=print) -> Path:
    """plan: [{start,end,kicker,headline,body,foot,shot,cap}]，时间单位秒，覆盖整条片。

    style: "paper" 印刷纸面 / "schematic" 深色仪表盘（一条连续 SVG 背景 + 真实截图）。
    """
    if not plan:
        raise ValueError("paper 计划为空")
    total = max(float(s["end"]) for s in plan)
    work = Path(workdir or tempfile.mkdtemp(prefix="paper-render-"))
    work.mkdir(parents=True, exist_ok=True)
    html_path = work / "sheet.html"
    html_path.write_text(_html_doc_schematic(plan, total) if style == "schematic"
                         else _html_doc(plan, total), encoding="utf-8")
    frames = work / "frames"
    t0 = time.time()
    n = render_frames(html_path, frames, total, fps=fps, log=log)
    if n < int(total * fps) - 2:
        raise RuntimeError(f"帧数不足: {n} < {int(total * fps)}")
    encode(frames, out_mp4, fps=fps, audio=audio, log=log)
    log(f"[paper] {total:.2f}s / {n} 帧 / {time.time() - t0:.1f}s 总耗时")
    if workdir is None:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4
