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
  background:linear-gradient(180deg,#0A151F 0%,#08121C 46%,#060E17 76%,#050B13 100%)}}
.grain{{position:absolute;inset:0;pointer-events:none;opacity:.16;
  background-image:repeating-linear-gradient(104deg,rgba(150,186,210,.013) 0 1px,transparent 1px 3px)}}
#bg{{position:absolute;inset:0;width:{WIDTH}px;height:{HEIGHT}px}}
.sheet{{position:absolute;inset:0;opacity:0;will-change:opacity}}
.pad{{position:absolute;left:92px;right:92px;top:128px;bottom:104px}}
.kicker{{display:none}}
.head{{font:600 61px/1.28 "PingFang SC","Hiragino Sans GB",sans-serif;
  letter-spacing:0;color:#ECF3FA;max-width:858px;opacity:0}}
.head .em{{color:#5ED3BE}}
.accent{{display:none}}
.ul{{margin:14px 0 0;width:228px;height:16px}}
.ul svg{{width:100%;height:100%;overflow:visible}}
.ul path{{stroke:#5ED3BE;stroke-width:3;opacity:.92}}
.body{{margin-top:24px;font:400 31px/1.62 "PingFang SC","Hiragino Sans GB",sans-serif;
  color:#A2B8CB;letter-spacing:.015em;max-width:846px;opacity:0}}
.body b{{color:#EAF2FA;font-weight:600}}
.panel{{position:absolute;left:58px;right:58px;top:396px;height:838px;display:block}}
.pbody{{position:absolute;inset:0;overflow:hidden;border-radius:3px;background:#0C1826;
  box-shadow:0 40px 92px -46px rgba(0,0,0,.96),0 0 0 1px rgba(133,176,201,.22);
  transform:rotate(-.85deg);transform-origin:50% 0}}
.panel::before{{content:"";position:absolute;left:44px;top:-14px;width:166px;height:30px;z-index:3;
  background:rgba(206,226,240,.12);transform:rotate(-2.4deg);box-shadow:0 3px 12px rgba(0,0,0,.30)}}
.panel::after{{content:"";position:absolute;right:30px;bottom:-12px;width:110px;height:24px;z-index:3;
  background:rgba(206,226,240,.10);transform:rotate(3.2deg);box-shadow:0 3px 12px rgba(0,0,0,.26)}}
.pbody img{{width:100%;height:100%;object-fit:contain;object-position:center;display:block}}
.pbody::after{{content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,rgba(6,13,21,0) 0 calc(100% - 74px),rgba(6,13,21,.10) calc(100% - 74px),rgba(6,13,21,.88) 100%),
  linear-gradient(180deg,rgba(6,13,21,0) 74%,rgba(6,13,21,.62) 100%)}}
.panel .cap{{position:absolute;left:2px;bottom:calc(100% + 14px);z-index:2;
  font:500 23px/1.3 "SF Mono",ui-monospace,monospace;letter-spacing:.08em;color:#7FA8C4}}
.sketch{{position:absolute;left:18px;right:18px;top:430px;height:545px;
  display:flex;align-items:center;justify-content:center}}
.sketch svg{{width:100%;height:100%;overflow:visible}}
.sk{{stroke-dasharray:1;stroke-dashoffset:1;stroke-linecap:round;stroke-linejoin:round;fill:none}}
.skf{{opacity:0}}
.foot{{position:absolute;left:84px;right:84px;bottom:52px;display:flex;
  justify-content:space-between;align-items:baseline;
  font:400 22px/1.2 "SF Mono",ui-monospace,monospace;letter-spacing:.18em;color:#6C8BA8}}
"""

_SVG_STAGE = """
<svg id="bg" viewBox="0 0 1080 1920" preserveAspectRatio="none">
  <defs>
    <filter id="paper" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency=".78" numOctaves="3" seed="5" result="n"/>
      <feColorMatrix in="n" type="saturate" values="0"/>
    </filter>
    <radialGradient id="vg" cx="46%" cy="8%" r="86%">
      <stop offset="0" stop-color="#000" stop-opacity="0"/>
      <stop offset="1" stop-color="#000" stop-opacity=".45"/>
    </radialGradient>
  </defs>
  <rect width="1080" height="1920" filter="url(#paper)" opacity=".05"/>
  <rect width="1080" height="1920" fill="url(#vg)"/>
</svg>"""


# ---------------------------------------------------------------- 手绘面板
# 真截图解释「东西长什么样」，手绘图解释「它怎么运作」——两种面板交替出现，
# 观众不会连着看六张同一风格的图，也就不会觉得这是幻灯片。
# 线条用手绘感：feTurbulence 位移 + 圆头笔触 + pathLength=1 的描边生长动画。
_RGH = ('<defs><filter id="rgh-{k}" x="-8%%" y="-8%%" width="116%%" height="116%%">'
        '<feTurbulence type="fractalNoise" baseFrequency=".012" numOctaves="2" seed="{seed}" result="n"/>'
        '<feDisplacementMap in="SourceGraphic" in2="n" scale="1.05" '
        'xChannelSelector="R" yChannelSelector="G"/></filter></defs>')

_SKETCHES = {
    "link": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <g filter="url(#rgh-link)" stroke="#A8C9DA">
  <rect class="sk" pathLength="1" x="20" y="24" width="406" height="280" rx="10" stroke-width="2"/>
  <path class="sk" pathLength="1" d="M20 62 H426" stroke-width="1.6"/>
  <circle cx="40" cy="43" r="3.4" stroke-width="1.4"/><circle cx="55" cy="43" r="3.4" stroke-width="1.4"/>
  <circle cx="70" cy="43" r="3.4" stroke-width="1.4"/>
  <rect class="sk" pathLength="1" x="86" y="31" width="152" height="24" rx="6" stroke-width="1.3"/>
  <rect class="sk" pathLength="1" x="95" y="39" width="9" height="9" rx="1.5" stroke-width="1.1"/>
  <path class="sk" pathLength="1" d="M112 43 h84" stroke-width="1.4" opacity=".8"/>
  <rect class="sk" pathLength="1" x="252" y="31" width="152" height="24" rx="6" stroke-width="1.1" opacity=".45"/>
  <path class="sk" pathLength="1" d="M262 43 h74" stroke-width="1.1" opacity=".38"/>
  <rect class="sk" pathLength="1" x="34" y="74" width="378" height="22" rx="11" stroke-width="1.3"/>
  <text x="50" y="89" fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="12" opacity=".9">github.com/regrevia/envoy</text>
  <path class="sk" pathLength="1" d="M398 79 h9" stroke-width="1.1" opacity=".5"/>
  <path class="sk" pathLength="1" d="M403 79 v11" stroke-width="1.1" opacity=".5"/>
  <path class="sk" pathLength="1" d="M36 122 h214" stroke-width="4.2"/>
  <path class="sk" pathLength="1" d="M36 148 h300" stroke-width="1.15" opacity=".9"/>
  <path class="sk" pathLength="1" d="M36 168 h268" stroke-width="1.15" opacity=".8"/>
  <path class="sk" pathLength="1" d="M36 188 h292" stroke-width="1.15" opacity=".7"/>
  <path class="sk" pathLength="1" d="M36 208 h176" stroke-width="1.15" opacity=".6"/>
  <rect class="sk" pathLength="1" x="36" y="230" width="240" height="58" rx="5" stroke-width="1.1" opacity=".7"/>
  <path class="sk" pathLength="1" d="M48 250 h132" stroke="#5ED3BE" stroke-width=".9" opacity=".6"/>
  <path class="sk" pathLength="1" d="M48 266 h96" stroke-width=".9" opacity=".45"/>
  <path class="sk" pathLength="1" d="M48 282 h150" stroke-width=".9" opacity=".35"/>
  <rect class="sk" pathLength="1" x="292" y="230" width="120" height="58" rx="5" stroke-width="1.1" opacity=".5"/>
  <path d="M296 234 L408 284" stroke-width=".8" opacity=".28"/>
  <path d="M408 234 L296 284" stroke-width=".8" opacity=".28"/>
  <path d="M414 74 v206" stroke-width=".9" opacity=".2"/>
  <rect x="411" y="86" width="6" height="54" rx="3" fill="#5ED3BE" opacity=".2"/>
 </g>
 <g filter="url(#rgh-link)" stroke="#5ED3BE" stroke-linecap="round" stroke-linejoin="round">
  <path class="sk" pathLength="1" d="M446 198 H508" stroke-width="2.1"/>
  <path class="sk" pathLength="1" d="M446 209 H500" stroke-width="1.1" opacity=".5"/>
  <path class="sk" pathLength="1" d="M496 188 L512 203 L496 218" stroke-width="2.1"/>
 </g>
 <g filter="url(#rgh-link)" stroke="#A8C9DA">
  <path class="sk" pathLength="1" d="M560 20 H788 L860 92 V450 H560 Z" stroke-width="2"/>
  <path class="sk" pathLength="1" d="M788 20 V92 H860" stroke-width="1.3"/>
  <path d="M600 40 V432" stroke-width=".9" opacity=".18"/>
  <text x="620" y="74" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="12" opacity=".75">material</text>
  <path class="sk" pathLength="1" d="M620 96 h182" stroke-width="3.4"/>
  <path class="sk" pathLength="1" d="M620 128 h216" stroke-width="1.1" opacity=".85"/>
  <path class="sk" pathLength="1" d="M620 150 h190" stroke-width="1.1" opacity=".75"/>
  <path class="sk" pathLength="1" d="M620 172 h206" stroke-width="1.1" opacity=".65"/>
  <path class="sk" pathLength="1" d="M620 194 h146" stroke-width="1.1" opacity=".55"/>
  <rect class="sk" pathLength="1" x="620" y="216" width="224" height="54" rx="4" stroke-width="1" opacity=".5"/>
  <path class="sk" pathLength="1" d="M632 236 h150" stroke-width=".85" opacity=".45"/>
  <path class="sk" pathLength="1" d="M632 254 h108" stroke-width=".85" opacity=".35"/>
  <text x="620" y="300" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="11.5" opacity=".8">material id</text>
  <rect class="sk" pathLength="1" x="620" y="310" width="84" height="26" rx="5" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="631" y="328" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="12.5">04:18</text>
  <rect class="sk" pathLength="1" x="714" y="310" width="70" height="26" rx="5" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="725" y="328" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="12.5">p.12</text>
  <circle class="sk" pathLength="1" cx="660" cy="382" r="13" stroke="#5ED3BE" stroke-width="1.5"/>
  <circle cx="660" cy="382" r="2.6" fill="#5ED3BE"/>
  <path d="M673 382 h44" stroke="#5ED3BE" stroke-width=".9" stroke-dasharray="4 4" opacity=".7"/>
  <text x="724" y="386" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="11.5" opacity=".85">keyframe</text>
  <g stroke="#6F97AE" stroke-width="1" opacity=".28">
   <path d="M620 424 l16 -11"/><path d="M640 428 l16 -11"/><path d="M660 432 l16 -11"/>
  </g>
 </g>
</svg>
""",

    "timeline": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 胶片带：每格一帧 -->
 <g filter="url(#rgh-tl)" stroke="#A8C9DA">
  <rect class="sk" pathLength="1" x="20" y="60" width="860" height="132" rx="6" stroke-width="1.9"/>
  <path d="M29 100 h28" stroke-width="1.1" opacity=".55"/><rect x="29" y="112" width="28" height="20" rx="2" stroke-width="1" opacity=".5"/>
  <rect x="101" y="96" width="30" height="22" rx="2" stroke-width="1" opacity=".5"/><path d="M101 128 h24" stroke-width="1.1" opacity=".45"/>
  <path d="M172 98 h32" stroke-width="1.1" opacity=".6"/><path d="M172 112 h22" stroke-width="1.1" opacity=".45"/><path d="M172 128 h30" stroke-width="1" opacity=".3"/>
  <path d="M244 100 h28" stroke-width="1.1" opacity=".55"/><rect x="244" y="112" width="28" height="20" rx="2" stroke-width="1" opacity=".5"/>
  <rect x="315" y="96" width="30" height="22" rx="2" stroke-width="1" opacity=".5"/><path d="M315 128 h24" stroke-width="1.1" opacity=".45"/>
  <path d="M387 98 h32" stroke-width="1.1" opacity=".6"/><path d="M387 112 h22" stroke-width="1.1" opacity=".45"/><path d="M387 128 h30" stroke-width="1" opacity=".3"/>
  <path d="M459 100 h28" stroke-width="1.1" opacity=".55"/><rect x="459" y="112" width="28" height="20" rx="2" stroke-width="1" opacity=".5"/>
  <rect x="530" y="96" width="30" height="22" rx="2" stroke-width="1" opacity=".5"/><path d="M530 128 h24" stroke-width="1.1" opacity=".45"/>
  <path d="M602 98 h32" stroke-width="1.1" opacity=".6"/><path d="M602 112 h22" stroke-width="1.1" opacity=".45"/><path d="M602 128 h30" stroke-width="1" opacity=".3"/>
  <path d="M673 100 h28" stroke-width="1.1" opacity=".55"/><rect x="673" y="112" width="28" height="20" rx="2" stroke-width="1" opacity=".5"/>
  <rect x="745" y="96" width="30" height="22" rx="2" stroke-width="1" opacity=".5"/><path d="M745 128 h24" stroke-width="1.1" opacity=".45"/>
  <path d="M817 98 h32" stroke-width="1.1" opacity=".6"/><path d="M817 112 h22" stroke-width="1.1" opacity=".45"/><path d="M817 128 h30" stroke-width="1" opacity=".3"/>
  <path d="M92 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M163 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M235 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M306 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M378 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M450 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M521 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M593 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M664 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M736 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M808 78 V172" stroke-width=".8" opacity=".22"/>
  <path d="M20 76 H880" stroke-width=".8" opacity=".2"/>
  <path d="M20 146 H880" stroke-width=".7" opacity=".13"/>
  <path class="sk" pathLength="1" d="M34 176 H866" stroke-width="1" opacity=".4"/>
  <path class="sk" pathLength="1" d="M34 176 h12 q6 -12 12 0 q6 -18 12 0 q6 -9 12 0 q6 -15 12 0 q6 -10 12 0 q6 -19 12 0 q6 -8 12 0 q6 -14 12 0 q6 -11 12 0 q6 -17 12 0 q6 -9 12 0 q6 -13 12 0 q6 -16 12 0 q6 -8 12 0 q6 -18 12 0 q6 -10 12 0 q6 -14 12 0 q6 -7 12 0 q6 -16 12 0 q6 -11 12 0 q6 -13 12 0 q6 -9 12 0 q6 -17 12 0 q6 -10 12 0 q6 -15 12 0 q6 -8 12 0 q6 -14 12 0 q6 -12 12 0 q6 -18 12 0 q6 -9 12 0 q6 -13 12 0 q6 -11 12 0 q6 -16 12 0 q6 -8 12 0 q6 -15 12 0 q6 -10 12 0 q6 -17 12 0 q6 -9 12 0 q6 -13 12 0 q6 -12 12 0 q6 -16 12 0 q6 -8 12 0 q6 -14 12 0 q6 -10 12 0 q6 -18 12 0 q6 -9 12 0 q6 -13 12 0 q6 -11 12 0 q6 -15 12 0 q6 -8 12 0 q6 -14 12 0 q6 -10 12 0 q6 -16 12 0 q6 -9 12 0 q6 -13 12 0 q6 -11 12 0 q6 -15 12 0 q6 -8 12 0 q6 -12 12 0 q6 -10 12 0" stroke-width="1.15" opacity=".6"/>
  <path class="sk" pathLength="1" d="M20 214 H880" stroke-width="1.1" opacity=".7"/>
  <g stroke-width=".9" opacity=".4">
   <path d="M92 214 v-7"/><path d="M164 214 v-7"/><path d="M236 214 v-7"/><path d="M308 214 v-7"/>
   <path d="M380 214 v-7"/><path d="M452 214 v-7"/><path d="M524 214 v-7"/><path d="M596 214 v-7"/>
   <path d="M668 214 v-7"/><path d="M740 214 v-7"/><path d="M812 214 v-7"/>
  </g>
  <g stroke-width="1.1" opacity=".55"><path d="M254 214 v-11"/><path d="M604 214 v-11"/></g>
 </g>
 <g filter="url(#rgh-tl)" stroke="#5ED3BE" stroke-linecap="round">
  <path class="sk" pathLength="1" d="M254 54 V212" stroke-width="2"/>
  <path class="sk" pathLength="1" d="M604 54 V212" stroke-width="2"/>
  <circle cx="254" cy="54" r="4.2" fill="#5ED3BE" stroke="none"/>
  <circle cx="604" cy="54" r="4.2" fill="#5ED3BE" stroke="none"/>
  <text x="232" y="40" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">03:12</text>
  <text x="582" y="40" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">07:02</text>
  <path d="M254 212 v28" stroke-width=".9" stroke-dasharray="4 4" opacity=".6"/>
  <path d="M604 212 v28" stroke-width=".9" stroke-dasharray="4 4" opacity=".6"/>
 </g>
 <g filter="url(#rgh-tl)" stroke="#A8C9DA">
  <rect class="sk" pathLength="1" x="212" y="240" width="84" height="26" rx="5" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="223" y="258" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="12.5">03:12</text>
  <rect class="sk" pathLength="1" x="562" y="240" width="84" height="26" rx="5" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="573" y="258" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="12.5">07:02</text>
  <path class="sk" pathLength="1" d="M212 300 h258" stroke-width="1.15" opacity=".8"/>
  <path class="sk" pathLength="1" d="M212 322 h222" stroke-width="1.15" opacity=".6"/>
  <path class="sk" pathLength="1" d="M212 344 h244" stroke-width="1.15" opacity=".45"/>
  <path class="sk" pathLength="1" d="M562 300 h256" stroke-width="1.15" opacity=".8"/>
  <path class="sk" pathLength="1" d="M562 322 h198" stroke-width="1.15" opacity=".6"/>
  <path class="sk" pathLength="1" d="M562 344 h230" stroke-width="1.15" opacity=".45"/>
  <path d="M212 396 h14" stroke="#5ED3BE" stroke-width="1.6"/>
  <text x="238" y="402" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="18" opacity=".92">抽帧和转写，钉在同一根时间轴上</text>
 </g>
</svg>
""",

    "gap": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 左：一页材料，中间有一段没读到 -->
 <g filter="url(#rgh-gap)" stroke="#A8C9DA">
  <path class="sk" pathLength="1" d="M20 26 H420 L470 76 V420 H20 Z" stroke-width="2"/>
  <path class="sk" pathLength="1" d="M420 26 V76 H470" stroke-width="1.3"/>
  <path class="sk" pathLength="1" d="M46 88 h250" stroke-width="3.4"/>
  <path class="sk" pathLength="1" d="M46 116 h330" stroke-width="1.15" opacity=".85"/>
  <path class="sk" pathLength="1" d="M46 138 h296" stroke-width="1.15" opacity=".75"/>
  <path class="sk" pathLength="1" d="M46 160 h318" stroke-width="1.15" opacity=".65"/>
  <rect x="46" y="180" width="342" height="86" rx="4" stroke="#E8B464" stroke-width="1.3" stroke-dasharray="7 6" opacity=".85"/>
  <g stroke="#E8B464" stroke-width="1" opacity=".2">
   <path d="M268 260 l24 -72"/><path d="M292 260 l24 -72"/><path d="M316 260 l24 -72"/>
   <path d="M340 260 l24 -72"/><path d="M364 260 l22 -66"/>
  </g>
  <text x="62" y="228" fill="#E8B464" font-family="PingFang SC, sans-serif" font-size="19">这里没读到</text>
  <path class="sk" pathLength="1" d="M46 296 h330" stroke-width="1.15" opacity=".8"/>
  <path class="sk" pathLength="1" d="M46 318 h212" stroke-width="1.15" opacity=".65"/>
  <path class="sk" pathLength="1" d="M46 392 h300" stroke-width="1.15" opacity=".4"/>
  <rect class="sk" pathLength="1" x="46" y="340" width="78" height="26" rx="5" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="55" y="358" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="12.5">04:18</text>
  <path d="M124 353 h84" stroke="#5ED3BE" stroke-width=".9" stroke-dasharray="4 4" opacity=".65"/>
  <text x="216" y="358" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="11.5" opacity=".8">带定位</text>
 </g>
 <!-- 右：两条判定 -->
 <g filter="url(#rgh-gap)" stroke="#A8C9DA">
  <path d="M560 62 h320" stroke-width=".9" opacity=".28"/>
  <path d="M560 96 h44" stroke="#E8B464" stroke-width="2" stroke-dasharray="7 6"/>
  <text x="620" y="105" fill="#E8B464" font-family="PingFang SC, sans-serif" font-size="21">未覆盖</text>
  <text x="620" y="134" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="16" opacity=".9">没字幕、要登录、预算不够</text>
  <path d="M560 162 h320" stroke-width=".9" opacity=".22"/>
  <path class="sk" pathLength="1" d="M560 196 h44" stroke="#5ED3BE" stroke-width="2"/>
  <text x="620" y="205" fill="#5ED3BE" font-family="PingFang SC, sans-serif" font-size="21">带定位</text>
  <text x="620" y="234" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="16" opacity=".9">读到的每段都挂着时间和页码</text>
  <path d="M560 262 h320" stroke-width=".9" opacity=".22"/>
  <path d="M560 310 h320" stroke-width=".9" opacity=".3"/>
  <path d="M560 340 h3" stroke="#5ED3BE" stroke-width="1.6"/>
  <text x="578" y="346" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="18" opacity=".92">没有定位，就不算读到</text>
 </g>
</svg>
""",

    "install": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 终端：装好 -->
 <g filter="url(#rgh-in)" stroke="#A8C9DA">
  <rect class="sk" pathLength="1" x="140" y="34" width="620" height="248" rx="10" stroke-width="2"/>
  <path class="sk" pathLength="1" d="M140 76 H760" stroke-width="1.5"/>
  <circle cx="164" cy="55" r="3.6" stroke-width="1.3"/><circle cx="180" cy="55" r="3.6" stroke-width="1.3"/>
  <circle cx="196" cy="55" r="3.6" stroke-width="1.3"/>
  <path class="sk" pathLength="1" d="M220 55 h104" stroke-width="1.3" opacity=".7"/>
  <text x="168" y="124" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="15.5">$ pip install envoy-cli</text>
  <text x="168" y="178" fill="#A8C9DA" font-family="SF Mono, ui-monospace, monospace" font-size="15.5" opacity=".85">$ uv tool install envoy</text>
  <text x="168" y="232" fill="#A8C9DA" font-family="SF Mono, ui-monospace, monospace" font-size="15.5" opacity=".85">envoy hook install --host codex</text>
  
 </g>
 <!-- 三个客户端，同一套接口 -->
 <g filter="url(#rgh-in)" stroke="#A8C9DA">
  <path d="M465 282 v26" stroke-width="1.2" opacity=".55"/>
  <path d="M279 310 h372" stroke-width="1.2" stroke-dasharray="6 7" opacity=".5"/>
  <circle cx="279" cy="310" r="2.6" fill="#A8C9DA" stroke="none" opacity=".75"/>
  <circle cx="480" cy="310" r="2.6" fill="#A8C9DA" stroke="none" opacity=".75"/>
  <circle cx="651" cy="310" r="2.6" fill="#A8C9DA" stroke="none" opacity=".75"/>
  <path d="M279 310 v22" stroke-width="1.2" opacity=".5"/>
  <path d="M480 310 v22" stroke-width="1.2" opacity=".5"/>
  <path d="M651 310 v22" stroke-width="1.2" opacity=".5"/>
  <rect class="sk" pathLength="1" x="174" y="332" width="210" height="62" rx="31" stroke-width="1.7"/>
  <rect class="sk" pathLength="1" x="400" y="332" width="160" height="62" rx="31" stroke-width="1.7"/>
  <rect class="sk" pathLength="1" x="576" y="332" width="150" height="62" rx="31" stroke-width="1.7"/>
  <text x="279" y="370" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="20" text-anchor="middle">Claude Code</text>
  <text x="480" y="370" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="20" text-anchor="middle">Codex</text>
  <text x="651" y="370" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="20" text-anchor="middle">DSH</text>
  <text x="176" y="438" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".85">skill + MCP</text>
  <text x="465" y="438" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="17" text-anchor="middle" opacity=".9">同一套接口</text>
 </g>
</svg>
""",

}

def _sketch_svg(name: str) -> str:
    tpl = _SKETCHES.get(name) or ""
    key = name
    seed = {"link": 11, "timeline": 23, "gap": 37, "install": 51}.get(name, 7)
    return tpl.replace("__RGH__", _RGH.replace("{k}", key).replace("{seed}", str(seed)))


def _sheet_schematic(scene: dict, i: int, total: int) -> str:
    headline = _esc(scene.get("headline") or "")
    body = _esc(scene.get("body") or "")
    shot = str(scene.get("shot") or "")
    sketch = str(scene.get("sketch") or "")
    cap = _esc(scene.get("cap") or "")
    panel_html = ""
    if shot:
        src = shot if shot.startswith("file:") else "file://" + shot
        pt = float(scene.get("pt") or 396)
        ph = float(scene.get("ph") or 838)
        fit = str(scene.get("fit") or "cover")
        pos = str(scene.get("pos") or "center")
        st = f"top:{pt:g}px;height:{ph:g}px"
        panel_html = (f'<div class="panel" style="{st}"><div class="pbody">'
                      f'<img src="{src}" alt="" style="object-fit:{fit};object-position:{pos}">'
                      f'</div><div class="cap">{cap}</div></div>')
    elif sketch:
        panel_html = f'<div class="sketch">{_sketch_svg(sketch)}</div>'

    return f"""<section class="sheet" data-i="{i}">
  <div class="pad">
    <div class="head rise">{headline}</div>
    <div class="ul"><svg viewBox="0 0 232 16" fill="none">
      <path class="sk" pathLength="1" d="M3 11 q13 -8 26 0 t26 0 t26 0 t26 0 t26 0 t26 0 t26 0 t26 0"/></svg></div>
    <div class="body rise">{body}</div>
  </div>
  {panel_html}
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
const EASE = (x) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);
window.renderFrame = function (t) {
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
    const body = el.querySelector('.body');
    const shot = el.querySelector('.panel');
    const sk = el.querySelectorAll('.sk');
    const pHead = EASE((local - 0.04) / 0.52);
    head.style.opacity = (fading ? 1 : pHead).toFixed(3);
    head.style.transform = `translateY(${((1 - pHead) * 20).toFixed(2)}px)`;
    body.style.opacity = (fading ? 1 : EASE((local - 0.16) / 0.5)).toFixed(3);
    body.style.transform = `translateY(${((1 - EASE((local - 0.16) / 0.5)) * 22).toFixed(2)}px)`;
    if (shot) {
      const p = EASE((local - 0.22) / 0.6);
      shot.style.opacity = (fading ? 1 : p).toFixed(3);
      shot.style.transform = `translateY(${((1 - p) * 46).toFixed(2)}px) scale(${(1.02 - 0.02 * p).toFixed(4)})`;
      const img = shot.querySelector('img');
      if (img) img.style.transform = `scale(${(1 + 0.04 * Math.min(1, local / 8)).toFixed(4)})`;
    }
    // 手绘面板：笔画逐条长出来（pathLength=1 归一化的描边生长）
    if (sk.length) {
      const gapSk = sk.length > 12 ? 0.86 / sk.length : 0.030;
      sk.forEach((el2, k) => {
        const q = EASE((local - 0.14 - k * gapSk) / 0.46);
        el2.style.strokeDashoffset = (1 - q).toFixed(3);
      });
    }
  }
};
// 版式落位：面板顶边跟着正文字块走，正文长一截也不会压到字上
document.querySelectorAll('.sheet').forEach(function (s) {
  var pad = s.querySelector('.pad'), pn = s.querySelector('.panel');
  if (!pad || !pn) return;
  var bottom = 0;
  pad.querySelectorAll(':scope > *').forEach(function (n) {
    var r = n.getBoundingClientRect();
    if (r.bottom > bottom) bottom = r.bottom;
  });
  pn.style.top = Math.max(parseFloat(pn.style.top) || 0, bottom + 34) + 'px';
});
window.renderFrame(0);
""" % {"P": payload}
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>schematic</title><style>{_CSS_SCHEMATIC}</style></head>
<body><div id="stage"><div class="grain"></div>{_SVG_STAGE}{sheets}</div>
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
// 版式落位：面板顶边跟着正文字块走，正文长一截也不会压到字上
document.querySelectorAll('.sheet').forEach(function (s) {
  var pad = s.querySelector('.pad'), pn = s.querySelector('.panel');
  if (!pad || !pn) return;
  var bottom = 0;
  pad.querySelectorAll(':scope > *').forEach(function (n) {
    var r = n.getBoundingClientRect();
    if (r.bottom > bottom) bottom = r.bottom;
  });
  pn.style.top = Math.max(parseFloat(pn.style.top) || 0, bottom + 34) + 'px';
});
window.renderFrame(0);
""" % {"P": payload}
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>paper</title><style>{_CSS}</style></head>
<body><div id="stage"><div class="grain"></div>{sheets}<div class="grain"></div></div>
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
