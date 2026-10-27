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
#stage{position:relative;width:%(W)dpx;height:%(H)dpx;background:radial-gradient(126%% 84%% at 18%% 6%%,#F8F2E7 0%%,#F3ECE0 40%%,#EDE4D5 74%%,#E6DBC8 100%%)}
.sheet{position:absolute;inset:0;opacity:0;will-change:opacity,transform;background:radial-gradient(142%% 98%% at 24%% 3%%,#F9F4EA 0%%,#F4EDE1 38%%,#EDE3D3 72%%,#E4D9C4 100%%)}
.sheet::before{content:"";position:absolute;inset:0;pointer-events:none;background-image:url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNTYiIGhlaWdodD0iMjU2Ij48ZmlsdGVyIGlkPSJuIj48ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iMC44MiIgbnVtT2N0YXZlcz0iMyIgc3RpdGNoVGlsZXM9InN0aXRjaCIvPjxmZUNvbG9yTWF0cml4IHR5cGU9InNhdHVyYXRlIiB2YWx1ZXM9IjAiLz48L2ZpbHRlcj48cmVjdCB3aWR0aD0iMjU2IiBoZWlnaHQ9IjI1NiIgZmlsdGVyPSJ1cmwoI24pIiBvcGFjaXR5PSIwLjU1Ii8+PC9zdmc+");background-size:256px 256px;opacity:.085;mix-blend-mode:multiply}
.sheet::after{content:"";position:absolute;inset:0;pointer-events:none;
  background-image:radial-gradient(120%% 85%% at 50%% 8%%,rgba(255,255,255,.55),rgba(255,255,255,0) 62%%),
    radial-gradient(80%% 62%% at 86%% 97%%,rgba(150,134,112,.16),rgba(150,134,112,0) 58%%);
  mix-blend-mode:multiply;opacity:.9}
/* 纸纹：各向同性纤维噪点（不是条纹），乘算叠在纸面上，静止不动 */
.grain{position:absolute;inset:0;pointer-events:none;opacity:.062;mix-blend-mode:multiply;background-image:url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNTYiIGhlaWdodD0iMjU2Ij48ZmlsdGVyIGlkPSJuIj48ZmVUdXJidWxlbmNlIHR5cGU9ImZyYWN0YWxOb2lzZSIgYmFzZUZyZXF1ZW5jeT0iMC44MiIgbnVtT2N0YXZlcz0iMyIgc3RpdGNoVGlsZXM9InN0aXRjaCIvPjxmZUNvbG9yTWF0cml4IHR5cGU9InNhdHVyYXRlIiB2YWx1ZXM9IjAiLz48L2ZpbHRlcj48cmVjdCB3aWR0aD0iMjU2IiBoZWlnaHQ9IjI1NiIgZmlsdGVyPSJ1cmwoI24pIiBvcGFjaXR5PSIwLjU1Ii8+PC9zdmc+");background-size:384px 384px}
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
#bg{{position:absolute;inset:0;width:{WIDTH}px;height:{HEIGHT}px}}
/* 纸面：笔刷痕和四角磨损都只压暗、不补亮；原来的 104° 斜纹在 crf16 下会变成条纹噪点，删掉 */
.tone{{position:absolute;inset:0;pointer-events:none;background:rgba(2,5,9,.10)}}
#vign{{position:absolute;inset:0;pointer-events:none;background:
  radial-gradient(120% 90% at 46% 8%,rgba(0,0,0,0) 40%,rgba(0,0,0,.28) 100%),
  linear-gradient(180deg,rgba(0,0,0,.15) 0%,rgba(0,0,0,0) 13%,rgba(0,0,0,0) 84%,rgba(0,0,0,.22) 100%)}}
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
.pbody img{{width:calc(100% - 22px);height:calc(100% - 22px);margin:11px;object-fit:contain;object-position:center;display:block;border-radius:2px}}
.pbody::after{{content:"";position:absolute;inset:0;
  background:linear-gradient(90deg,rgba(6,13,21,0) 0 calc(100% - 56px),rgba(6,13,21,.06) calc(100% - 56px),rgba(6,13,21,.42) 100%),
  linear-gradient(180deg,rgba(6,13,21,0) 78%,rgba(6,13,21,.48) 100%)}}
.panel.shot::before,.panel.shot::after,.panel.shot .pbody::after{{display:none}}
.panel.shot .pbody img{{filter:brightness(.96) contrast(1.02)}}
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
    <!-- 墨面低频浓淡：各向同性，避免 1080 宽上出现拉丝 -->
    <filter id="pm" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency=".0016 .011" numOctaves="3" seed="29"/>
      <feColorMatrix type="saturate" values="0"/>
    </filter>
    <!-- 横向笔刷：位移打破直边，再糊一点，像笔扫过一遍 -->
    <filter id="pb" x="-12%" y="-12%" width="124%" height="124%">
      <feTurbulence type="fractalNoise" baseFrequency=".009 .06" numOctaves="2" seed="23" result="n"/>
      <feDisplacementMap in="SourceGraphic" in2="n" scale="20" xChannelSelector="R" yChannelSelector="G"/>
      <feGaussianBlur stdDeviation="2.6"/>
    </filter>
    <!-- 四角磨损：不规则块，只压暗 -->
    <filter id="pw" x="-12%" y="-12%" width="124%" height="124%">
      <feTurbulence type="fractalNoise" baseFrequency=".02 .05" numOctaves="3" seed="41" result="n"/>
      <feDisplacementMap in="SourceGraphic" in2="n" scale="30" xChannelSelector="R" yChannelSelector="G"/>
    </filter>
  </defs>
  <rect width="1080" height="1920" filter="url(#pm)" opacity=".03"/>
  <g filter="url(#pb)" stroke-linecap="round" fill="none">
    <path d="M-60 92 q320 -14 610 8 t520 -10" stroke="#16283A" stroke-width="44" opacity=".035"/>
    <path d="M-60 118 q360 10 640 -8 t480 12" stroke="#04090F" stroke-width="30" opacity=".05"/>
    <path d="M-60 1306 q300 12 560 -8 t520 8" stroke="#04090F" stroke-width="58" opacity=".055"/>
    <path d="M120 1402 q300 -10 520 8 t420 -8" stroke="#16283A" stroke-width="36" opacity=".04"/>
    <path d="M-60 1548 q340 10 600 -8 t520 6" stroke="#04090F" stroke-width="66" opacity=".05"/>
    <path d="M40 1796 q320 -10 580 8 t500 -6" stroke="#03080E" stroke-width="52" opacity=".075"/>
  </g>
  <g filter="url(#pw)" fill="#03070C">
    <path d="M0 0 h190 q-40 26 -96 30 q-58 4 -94 44 z" opacity=".30"/>
    <path d="M1080 0 h-150 q34 20 74 30 q52 12 76 46 z" opacity=".24"/>
    <path d="M0 1920 h168 q-34 -22 -82 -30 q-56 -8 -86 -46 z" opacity=".26"/>
    <path d="M1080 1920 h-176 q30 -20 76 -30 q54 -10 100 -44 z" opacity=".22"/>
  </g>
</svg>
<div class="tone"></div><div id="vign"></div>"""


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
 <!-- 左：一个仓库页，密到能看出是真页面 -->
 <g filter="url(#rgh-link)" stroke="#A8C9DA" stroke-linecap="round" stroke-linejoin="round">
  <rect class="sk" pathLength="1" x="18" y="22" width="412" height="334" rx="9" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M18 58 H430" stroke-width="1"/>
  <circle cx="36" cy="40" r="3.1" stroke-width=".9"/><circle cx="50" cy="40" r="3.1" stroke-width=".9"/>
  <circle cx="64" cy="40" r="3.1" stroke-width=".9"/>
  <path class="sk" pathLength="1" d="M80 28 H228 V54 H80 Z" stroke-width=".9" opacity=".75"/>
  <rect x="90" y="36" width="8" height="8" rx="1.4" stroke-width=".8" opacity=".6"/>
  <path d="M104 40 h52" stroke-width=".8" opacity=".5"/>
  <path class="sk" pathLength="1" d="M236 30 H406 V52 H236 Z" stroke-width=".7" opacity=".3"/>
  <path d="M246 41 h44" stroke-width=".7" opacity=".22"/>
  <rect class="sk" pathLength="1" x="28" y="68" width="392" height="22" rx="11" stroke-width=".9"/>
  <path d="M44 79 v-4 a3 3 0 0 1 6 0 v4" stroke-width=".8" opacity=".55"/>
  <text x="58" y="84" fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="13">github.com/regrevia/envoy</text>
  <text x="404" y="84" fill="#8FAEBF" font-family="SF Mono, ui-monospace, monospace" font-size="13" text-anchor="end">60 stars</text>
  <path d="M96 102 V348" stroke-width=".5" opacity=".22"/>
  <g stroke-width=".6" opacity=".45">
   <rect x="32" y="114" width="7" height="7" rx="1.2"/><path d="M46 118 h34"/>
   <rect x="32" y="140" width="7" height="7" rx="1.2"/><path d="M46 144 h28"/>
   <rect x="40" y="166" width="7" height="7" rx="1.2"/><path d="M54 170 h22"/>
   <rect x="40" y="192" width="7" height="7" rx="1.2"/><path d="M54 196 h26"/>
   <rect x="32" y="218" width="7" height="7" rx="1.2"/><path d="M46 222 h30"/>
   <rect x="40" y="244" width="7" height="7" rx="1.2"/><path d="M54 248 h20"/>
   <rect x="32" y="270" width="7" height="7" rx="1.2"/><path d="M46 274 h32"/>
   <rect x="40" y="296" width="7" height="7" rx="1.2"/><path d="M54 300 h24"/>
  </g>
  <path class="sk" pathLength="1" d="M110 116 h196" stroke-width="3"/>
  <text x="110" y="144" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".7">Python · Apache-2.0 · 236 commits</text>
  <path class="sk" pathLength="1" d="M110 162 h292" stroke-width=".8" opacity=".8"/>
  <path class="sk" pathLength="1" d="M110 182 h262" stroke-width=".8" opacity=".72"/>
  <path class="sk" pathLength="1" d="M110 202 h286" stroke-width=".8" opacity=".64"/>
  <path class="sk" pathLength="1" d="M110 222 h170" stroke-width=".8" opacity=".56"/>
  <path class="sk" pathLength="1" d="M110 242 h188" stroke-width=".8" opacity=".48"/>
  <rect class="sk" pathLength="1" x="110" y="258" width="184" height="56" rx="4" stroke-width=".8" opacity=".55"/>
  <path d="M122 276 h96" stroke="#5ED3BE" stroke-width=".7" opacity=".6"/>
  <path d="M122 292 h110" stroke-width=".6" opacity=".3"/>
  <rect class="sk" pathLength="1" x="306" y="258" width="104" height="56" rx="4" stroke-width=".8" opacity=".4"/>
  <g stroke-width=".5" opacity=".22">
   <path d="M312 308 l10 -44"/><path d="M324 308 l10 -44"/><path d="M336 308 l10 -44"/><path d="M348 308 l10 -44"/>
  </g>
  <path d="M416 110 V348" stroke-width=".5" opacity=".3"/>
  <rect x="414" y="110" width="3" height="72" rx="1.5" fill="#A8C9DA" opacity=".16" stroke="none"/>
  <path d="M110 340 h184" stroke-width=".5" opacity=".22"/>
  <path d="M110 356 h142" stroke-width=".5" opacity=".18"/>
  <text x="18" y="382" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".8">repo · 60 stars · Apache-2.0</text>
  <path d="M18 396 h140" stroke-width=".5" opacity=".2"/>
  <text x="18" y="420" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="18" opacity=".8">整页读完，再取素材</text>
 </g>
 <!-- 中间：取 -->
 <g filter="url(#rgh-link)" stroke="#5ED3BE" stroke-linecap="round" stroke-linejoin="round">
  <path class="sk" pathLength="1" d="M450 194 H506" stroke-width="1.4"/>
  <path class="sk" pathLength="1" d="M496 185 L512 200 L496 215" stroke-width="1.4"/>
  <path d="M450 214 H492" stroke-width=".6" stroke-dasharray="2 4" opacity=".4"/>
  <text x="452" y="240" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".85">fetch</text>
 </g>
 <!-- 右：材料卡，行号栏 + 定位 -->
 <g filter="url(#rgh-link)" stroke="#A8C9DA" stroke-linecap="round" stroke-linejoin="round">
  <path class="sk" pathLength="1" d="M556 18 H788 L862 92 V452 H556 Z" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M788 18 V92 H862" stroke-width=".9"/>
  <path d="M600 34 V436" stroke-width=".5" opacity=".22"/>
  <g stroke-width=".5" opacity=".26">
   <path d="M588 52 h12"/><path d="M588 74 h12"/><path d="M588 96 h12"/><path d="M588 118 h12"/>
   <path d="M588 140 h12"/><path d="M588 162 h12"/><path d="M588 184 h12"/><path d="M588 206 h12"/>
   <path d="M588 228 h12"/><path d="M588 250 h12"/><path d="M588 272 h12"/><path d="M588 294 h12"/>
   <path d="M588 316 h12"/><path d="M588 338 h12"/><path d="M588 360 h12"/><path d="M588 382 h12"/>
   <path d="M588 404 h12"/><path d="M588 426 h12"/>
  </g>
  <path class="sk" pathLength="1" d="M614 50 h148" stroke-width="2.8"/>
  <text x="614" y="76" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".7">material</text>
  <path class="sk" pathLength="1" d="M614 94 h212" stroke-width=".7" opacity=".8"/>
  <path class="sk" pathLength="1" d="M614 114 h188" stroke-width=".7" opacity=".72"/>
  <path class="sk" pathLength="1" d="M614 134 h204" stroke-width=".7" opacity=".64"/>
  <path class="sk" pathLength="1" d="M614 154 h142" stroke-width=".7" opacity=".56"/>
  <path d="M614 174 h96" stroke="#5ED3BE" stroke-width=".9" opacity=".7"/>
  <path class="sk" pathLength="1" d="M614 196 h208" stroke-width=".7" opacity=".66"/>
  <path class="sk" pathLength="1" d="M614 216 h174" stroke-width=".7" opacity=".58"/>
  <path class="sk" pathLength="1" d="M614 236 h196" stroke-width=".7" opacity=".5"/>
  <path class="sk" pathLength="1" d="M614 256 h128" stroke-width=".7" opacity=".42"/>
  <text x="614" y="288" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".75">material id</text>
  <rect class="sk" pathLength="1" x="614" y="298" width="80" height="26" rx="5" stroke="#5ED3BE" stroke-width=".9"/>
  <text x="624" y="316" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">04:18</text>
  <rect class="sk" pathLength="1" x="702" y="298" width="66" height="26" rx="5" stroke="#5ED3BE" stroke-width=".9"/>
  <text x="712" y="316" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">p.12</text>
  <circle class="sk" pathLength="1" cx="628" cy="360" r="11" stroke="#5ED3BE" stroke-width="1"/>
  <circle cx="628" cy="360" r="2.4" fill="#5ED3BE" stroke="none"/>
  <path d="M639 360 h34" stroke="#5ED3BE" stroke-width=".6" stroke-dasharray="3 4" opacity=".6"/>
  <text x="678" y="364" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".85">keyframe</text>
  <text x="678" y="382" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".5">04:18.2</text>
  <g stroke="#6F97AE" stroke-width=".5" opacity=".18">
   <path d="M614 426 l14 -10"/><path d="M632 428 l14 -10"/><path d="M650 430 l14 -10"/>
   <path d="M668 432 l14 -10"/><path d="M686 434 l14 -10"/>
  </g>
 </g>
</svg>
""",

    "timeline": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 刻度尺 -->
 <g filter="url(#rgh-tl)" stroke="#8FAEBF" stroke-linecap="round">
  <path d="M20 28 H880" stroke-width=".6" opacity=".35"/>
  <g stroke-width=".5" opacity=".3">
   <path d="M20 28 v5"/><path d="M63 28 v5"/><path d="M106 28 v5"/><path d="M149 28 v5"/>
   <path d="M192 28 v5"/><path d="M235 28 v5"/><path d="M278 28 v5"/><path d="M321 28 v5"/>
   <path d="M364 28 v5"/><path d="M407 28 v5"/><path d="M450 28 v5"/><path d="M493 28 v5"/>
   <path d="M536 28 v5"/><path d="M579 28 v5"/><path d="M622 28 v5"/><path d="M665 28 v5"/>
   <path d="M708 28 v5"/><path d="M751 28 v5"/><path d="M794 28 v5"/><path d="M837 28 v5"/>
  </g>
  <text x="20" y="52" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6">00:00</text>
  <text x="306" y="52" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6">03:12</text>
  <text x="656" y="52" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6">07:02</text>
  <text x="836" y="52" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6" text-anchor="end">10:20</text>
 </g>
 <!-- 胶片带：打孔 + 每格内容 -->
 <g filter="url(#rgh-tl)" stroke="#A8C9DA" stroke-linecap="round" stroke-linejoin="round">
  <rect class="sk" pathLength="1" x="20" y="64" width="860" height="132" rx="6" stroke-width="1.5"/>
  <g stroke-width=".5" opacity=".28">
   <rect x="30" y="70" width="8" height="5" rx="1"/><rect x="30" y="185" width="8" height="5" rx="1"/>
   <rect x="102" y="70" width="8" height="5" rx="1"/><rect x="102" y="185" width="8" height="5" rx="1"/>
   <rect x="174" y="70" width="8" height="5" rx="1"/><rect x="174" y="185" width="8" height="5" rx="1"/>
   <rect x="246" y="70" width="8" height="5" rx="1"/><rect x="246" y="185" width="8" height="5" rx="1"/>
   <rect x="318" y="70" width="8" height="5" rx="1"/><rect x="318" y="185" width="8" height="5" rx="1"/>
   <rect x="390" y="70" width="8" height="5" rx="1"/><rect x="390" y="185" width="8" height="5" rx="1"/>
   <rect x="462" y="70" width="8" height="5" rx="1"/><rect x="462" y="185" width="8" height="5" rx="1"/>
   <rect x="534" y="70" width="8" height="5" rx="1"/><rect x="534" y="185" width="8" height="5" rx="1"/>
   <rect x="606" y="70" width="8" height="5" rx="1"/><rect x="606" y="185" width="8" height="5" rx="1"/>
   <rect x="678" y="70" width="8" height="5" rx="1"/><rect x="678" y="185" width="8" height="5" rx="1"/>
   <rect x="750" y="70" width="8" height="5" rx="1"/><rect x="750" y="185" width="8" height="5" rx="1"/>
   <rect x="822" y="70" width="8" height="5" rx="1"/><rect x="822" y="185" width="8" height="5" rx="1"/>
  </g>
  <g stroke-width=".5" opacity=".18">
   <path d="M92 84 V176"/><path d="M164 84 V176"/><path d="M236 84 V176"/><path d="M308 84 V176"/>
   <path d="M380 84 V176"/><path d="M452 84 V176"/><path d="M524 84 V176"/><path d="M596 84 V176"/>
   <path d="M668 84 V176"/><path d="M740 84 V176"/><path d="M812 84 V176"/>
  </g>
  <g stroke-width=".6" opacity=".4">
   <rect x="34" y="96" width="26" height="18" rx="2"/><path d="M34 126 h30"/>
   <rect x="106" y="96" width="26" height="18" rx="2"/><path d="M106 126 h22"/>
   <path d="M178 100 h28"/><path d="M178 114 h18"/><path d="M178 128 h26"/>
   <rect x="250" y="96" width="26" height="18" rx="2"/><path d="M250 126 h24"/>
   <path d="M322 100 h28"/><path d="M322 114 h20"/><path d="M322 128 h24"/>
   <rect x="394" y="96" width="26" height="18" rx="2"/><path d="M394 126 h22"/>
   <rect x="466" y="96" width="26" height="18" rx="2"/><path d="M466 126 h28"/>
   <path d="M538 100 h28"/><path d="M538 114 h18"/><path d="M538 128 h26"/>
   <rect x="610" y="96" width="26" height="18" rx="2"/><path d="M610 126 h20"/>
   <path d="M682 100 h28"/><path d="M682 114 h20"/><path d="M682 128 h26"/>
   <rect x="754" y="96" width="26" height="18" rx="2"/><path d="M754 126 h24"/>
   <path d="M826 100 h24"/><path d="M826 114 h18"/><path d="M826 128 h20"/>
  </g>
  <g fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".3">
   <text x="36" y="174">01</text><text x="108" y="174">02</text><text x="180" y="174">03</text>
   <text x="252" y="174">04</text><text x="324" y="174">05</text><text x="396" y="174">06</text>
   <text x="468" y="174">07</text><text x="540" y="174">08</text><text x="612" y="174">09</text>
   <text x="684" y="174">10</text><text x="756" y="174">11</text><text x="828" y="174">12</text>
  </g>
  <path class="sk" pathLength="1" d="M20 148 H880" stroke-width=".5" opacity=".18"/>
 </g>
 <!-- 音轨：细柱波形 -->
 <g filter="url(#rgh-tl)" stroke="#A8C9DA" stroke-linecap="round">
  <path class="sk" pathLength="1" d="M20 212 H880" stroke-width=".7" opacity=".45"/>
  <path class="sk" pathLength="1" d="M28 216 v-5 M34 216 v-9 M40 216 v-5 M46 216 v-12 M52 216 v-18 M58 216 v-7 M64 216 v-13 M70 216 v-4 M76 216 v-16 M82 216 v-9 M88 216 v-12 M94 216 v-6 M100 216 v-15 M106 216 v-10 M112 216 v-5 M118 216 v-13 M124 216 v-7 M130 216 v-17 M136 216 v-4 M142 216 v-12 M148 216 v-9 M154 216 v-14 M160 216 v-6 M166 216 v-11 M172 216 v-16 M178 216 v-5 M184 216 v-13 M190 216 v-8 M196 216 v-15 M202 216 v-4 M208 216 v-12 M214 216 v-18 M220 216 v-7 M226 216 v-11 M232 216 v-5 M238 216 v-14 M244 216 v-9 M250 216 v-16 M256 216 v-6 M262 216 v-12 M268 216 v-4 M274 216 v-15 M280 216 v-8 M286 216 v-13 M292 216 v-5 M298 216 v-17 M304 216 v-10 M310 216 v-6 M316 216 v-14 M322 216 v-4 M328 216 v-12 M334 216 v-9 M340 216 v-16 M346 216 v-5 M352 216 v-13 M358 216 v-7 M364 216 v-15 M370 216 v-10 M376 216 v-4 M382 216 v-12 M388 216 v-18 M394 216 v-6 M400 216 v-14 M406 216 v-8 M412 216 v-11 M418 216 v-5 M424 216 v-16 M430 216 v-9 M436 216 v-13 M442 216 v-4 M448 216 v-15 M454 216 v-7 M460 216 v-12 M466 216 v-18 M472 216 v-6 M478 216 v-14 M484 216 v-10 M490 216 v-5 M496 216 v-13 M502 216 v-8 M508 216 v-16 M514 216 v-4 M520 216 v-12 M526 216 v-9 M532 216 v-15 M538 216 v-6 M544 216 v-11 M550 216 v-17 M556 216 v-5 M562 216 v-13 M568 216 v-7 M574 216 v-14 M580 216 v-4 M586 216 v-16 M592 216 v-9 M598 216 v-12 M604 216 v-6 M610 216 v-18 M616 216 v-5 M622 216 v-13 M628 216 v-8 M634 216 v-15 M640 216 v-4 M646 216 v-12 M652 216 v-10 M658 216 v-16 M664 216 v-6 M670 216 v-14 M676 216 v-5 M682 216 v-11 M688 216 v-17 M694 216 v-7 M700 216 v-13 M706 216 v-4 M712 216 v-15 M718 216 v-9 M724 216 v-12 M730 216 v-6 M736 216 v-16 M742 216 v-5 M748 216 v-13 M754 216 v-8 M760 216 v-14 M766 216 v-4 M772 216 v-12 M778 216 v-10 M784 216 v-17 M790 216 v-6 M796 216 v-14 M802 216 v-5 M808 216 v-11 M814 216 v-16 M820 216 v-7 M826 216 v-13 M832 216 v-4 M838 216 v-15 M844 216 v-9 M850 216 v-12 M856 216 v-6 M862 216 v-16 M868 216 v-8 M872 216 v-13" stroke-width=".6" opacity=".2"/>
  <path class="sk" pathLength="1" d="M20 224 H880" stroke-width=".5" opacity=".2"/>
 </g>
 <!-- 两个定位点 -->
 <g filter="url(#rgh-tl)" stroke="#5ED3BE" stroke-linecap="round">
  <path class="sk" pathLength="1" d="M254 58 V214" stroke-width="1.3"/>
  <path class="sk" pathLength="1" d="M604 58 V214" stroke-width="1.3"/>
  <circle cx="254" cy="58" r="3.6" fill="#5ED3BE" stroke="none"/>
  <circle cx="604" cy="58" r="3.6" fill="#5ED3BE" stroke="none"/>
  <path d="M254 214 v26" stroke-width=".7" stroke-dasharray="3 4" opacity=".55"/>
  <path d="M604 214 v26" stroke-width=".7" stroke-dasharray="3 4" opacity=".55"/>
 </g>
 <!-- 抽出来的证据 -->
 <g filter="url(#rgh-tl)" stroke="#A8C9DA" stroke-linecap="round">
  <rect class="sk" pathLength="1" x="212" y="240" width="84" height="26" rx="5" stroke="#5ED3BE" stroke-width=".9"/>
  <text x="222" y="258" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">03:12</text>
  <rect class="sk" pathLength="1" x="562" y="240" width="84" height="26" rx="5" stroke="#5ED3BE" stroke-width=".9"/>
  <text x="572" y="258" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">07:02</text>
  <g stroke-width=".6" opacity=".45">
   <path d="M212 292 h128"/><path d="M212 306 h96"/>
   <path d="M562 292 h128"/><path d="M562 306 h104"/>
  </g>
  <text x="212" y="332" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".65">frame grabs → 2 stills</text>
  <text x="562" y="332" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".65">transcript → 3 lines</text>
  <g stroke-width=".5" opacity=".22">
   <path d="M212 366 h150"/><path d="M562 366 h150"/>
  </g>
  <text x="212" y="392" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".55">12 cells · 0.5 s each · 30 fps</text>
  <text x="562" y="392" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".55">2 locators · 1 timeline</text>
  <path d="M212 414 h10" stroke="#5ED3BE" stroke-width="1.3" opacity=".85"/>
  <text x="238" y="430" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="18" opacity=".92">抽帧和转写，钉在同一根时间轴上</text>
 </g>
</svg>
""",

    "gap": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 左：一页材料，中间有一段没读到 -->
 <g filter="url(#rgh-gap)" stroke="#A8C9DA" stroke-linecap="round" stroke-linejoin="round">
  <path class="sk" pathLength="1" d="M20 26 H420 L470 76 V420 H20 Z" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M420 26 V76 H470" stroke-width=".9"/>
  <path d="M44 40 V404" stroke-width=".5" opacity=".2"/>
  <g stroke-width=".5" opacity=".24">
   <path d="M34 58 h10"/><path d="M34 80 h10"/><path d="M34 102 h10"/><path d="M34 124 h10"/>
   <path d="M34 146 h10"/><path d="M34 168 h10"/><path d="M34 190 h10"/><path d="M34 212 h10"/>
   <path d="M34 234 h10"/><path d="M34 256 h10"/><path d="M34 278 h10"/><path d="M34 300 h10"/>
   <path d="M34 322 h10"/><path d="M34 344 h10"/><path d="M34 366 h10"/><path d="M34 388 h10"/>
  </g>
  <path class="sk" pathLength="1" d="M56 58 h250" stroke-width="2.8"/>
  <text x="56" y="84" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6">page 12 · 04:18</text>
  <path class="sk" pathLength="1" d="M56 104 h330" stroke-width=".7" opacity=".78"/>
  <path class="sk" pathLength="1" d="M56 124 h296" stroke-width=".7" opacity=".7"/>
  <path class="sk" pathLength="1" d="M56 144 h318" stroke-width=".7" opacity=".62"/>
  <path class="sk" pathLength="1" d="M56 164 h206" stroke-width=".7" opacity=".54"/>
  <rect x="56" y="182" width="342" height="86" rx="4" stroke="#E8B464" stroke-width="1" stroke-dasharray="6 6" opacity=".85"/>
  <g stroke="#E8B464" stroke-width=".5" opacity=".16">
   <path d="M262 262 l22 -66"/><path d="M282 262 l22 -66"/><path d="M302 262 l22 -66"/>
   <path d="M322 262 l22 -66"/><path d="M342 262 l22 -66"/><path d="M362 262 l22 -66"/>
   <path d="M382 262 l14 -42"/>
  </g>
  <text x="70" y="218" fill="#E8B464" font-family="PingFang SC, sans-serif" font-size="18">这里没读到</text>
  <text x="70" y="242" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".6">403 / login / 无字幕</text>
  <path class="sk" pathLength="1" d="M56 296 h330" stroke-width=".7" opacity=".72"/>
  <path class="sk" pathLength="1" d="M56 316 h212" stroke-width=".7" opacity=".6"/>
  <path class="sk" pathLength="1" d="M56 336 h286" stroke-width=".7" opacity=".48"/>
  <path class="sk" pathLength="1" d="M56 356 h168" stroke-width=".7" opacity=".4"/>
  <rect class="sk" pathLength="1" x="56" y="374" width="78" height="26" rx="5" stroke="#5ED3BE" stroke-width=".9"/>
  <text x="65" y="392" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="13">04:18</text>
  <path d="M134 387 h74" stroke="#5ED3BE" stroke-width=".6" stroke-dasharray="3 4" opacity=".6"/>
  <text x="216" y="392" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".7">located</text>
 </g>
 <!-- 右：两条判定 -->
 <g filter="url(#rgh-gap)" stroke="#A8C9DA" stroke-linecap="round">
  <path d="M556 58 h324" stroke-width=".5" opacity=".25"/>
  <path d="M556 92 h44" stroke="#E8B464" stroke-width="1.3" stroke-dasharray="6 6"/>
  <text x="616" y="100" fill="#E8B464" font-family="PingFang SC, sans-serif" font-size="19">未覆盖</text>
  <text x="616" y="104" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="11" opacity="0"> </text>
  <text x="616" y="130" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="17" opacity=".9">没字幕、要登录、预算不够</text>
  <path d="M616 142 h150" stroke-width=".5" opacity=".22"/>
  <path d="M556 158 h324" stroke-width=".5" opacity=".18"/>
  <path class="sk" pathLength="1" d="M556 196 h44" stroke="#5ED3BE" stroke-width="1.3"/>
  <text x="616" y="204" fill="#5ED3BE" font-family="PingFang SC, sans-serif" font-size="19">带定位</text>
  <text x="616" y="234" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="17" opacity=".9">读到的每段都挂着时间和页码</text>
  <path d="M616 246 h208" stroke-width=".5" opacity=".22"/>
  <path d="M556 262 h324" stroke-width=".5" opacity=".18"/>
  <text x="556" y="296" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".55">uncovered</text>
  <g stroke="#E8B464" stroke-width=".6" opacity=".55">
   <circle cx="560" cy="314" r="2.2" fill="#E8B464" stroke="none"/>
   <circle cx="560" cy="336" r="2.2" fill="#E8B464" stroke="none"/>
   <circle cx="560" cy="358" r="2.2" fill="#E8B464" stroke="none"/>
   <path d="M570 314 h84"/><path d="M570 336 h64"/><path d="M570 358 h92"/>
  </g>
  <g stroke-width=".4" opacity=".18">
   <path d="M570 322 h74"/><path d="M570 344 h58"/><path d="M570 366 h80"/>
  </g>
  <text x="700" y="318" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".5">404</text>
  <text x="700" y="340" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".5">login</text>
  <text x="700" y="362" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".5">no-sub</text>
  <path d="M556 372 h324" stroke-width=".5" opacity=".28"/>
  <path d="M556 401 h8" stroke="#5ED3BE" stroke-width="1.4" opacity=".85"/>
  <text x="574" y="408" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="18" opacity=".92">没有定位，就不算读到</text>
 </g>
</svg>
""",

    "install": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 终端：装好 -->
 <g filter="url(#rgh-in)" stroke="#A8C9DA" stroke-linecap="round" stroke-linejoin="round">
  <rect class="sk" pathLength="1" x="140" y="34" width="620" height="248" rx="9" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M140 74 H760" stroke-width="1"/>
  <circle cx="162" cy="54" r="3.3" stroke-width=".9"/><circle cx="176" cy="54" r="3.3" stroke-width=".9"/>
  <circle cx="190" cy="54" r="3.3" stroke-width=".9"/>
  <path d="M212 54 h92" stroke-width=".7" opacity=".4"/>
  <text x="742" y="58" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".55" text-anchor="end">zsh · 80×24</text>
  <text x="166" y="106" fill="#6F97AE" font-family="PingFang SC, sans-serif" font-size="18" opacity=".5"># 三条命令，装完即用</text>
  <text x="166" y="138" fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="15">$ pip install envoy-cli</text>
  <text x="180" y="158" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".45">→ envoy 0.9.2 · 1 entry point</text>
  <text x="166" y="186" fill="#A8C9DA" font-family="SF Mono, ui-monospace, monospace" font-size="15" opacity=".85">$ uv tool install envoy</text>
  <text x="180" y="206" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".45">→ binary on PATH · no venv</text>
  <text x="166" y="234" fill="#A8C9DA" font-family="SF Mono, ui-monospace, monospace" font-size="15" opacity=".85">envoy hook install --host codex</text>
  <text x="180" y="254" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".45">✓ 42 files linked · codex, claude, dsh</text>
 </g>
 <!-- 三个客户端，同一套接口 -->
 <g filter="url(#rgh-in)" stroke="#A8C9DA" stroke-linecap="round">
  <path d="M450 282 v24" stroke-width=".9" opacity=".5"/>
  <path d="M279 306 h342" stroke-width=".7" stroke-dasharray="5 6" opacity=".45"/>
  <circle cx="279" cy="306" r="2.3" fill="#A8C9DA" stroke="none" opacity=".7"/>
  <circle cx="450" cy="306" r="2.3" fill="#A8C9DA" stroke="none" opacity=".7"/>
  <circle cx="621" cy="306" r="2.3" fill="#A8C9DA" stroke="none" opacity=".7"/>
  <path d="M279 306 v22" stroke-width=".9" opacity=".45"/>
  <path d="M450 306 v22" stroke-width=".9" opacity=".45"/>
  <path d="M621 306 v22" stroke-width=".9" opacity=".45"/>
  <rect class="sk" pathLength="1" x="186" y="328" width="186" height="60" rx="30" stroke-width="1.1"/>
  <rect class="sk" pathLength="1" x="380" y="328" width="140" height="60" rx="30" stroke-width="1.1"/>
  <rect class="sk" pathLength="1" x="555" y="328" width="132" height="60" rx="30" stroke-width="1.1"/>
  <text x="279" y="365" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="19" text-anchor="middle">Claude Code</text>
  <text x="450" y="365" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="19" text-anchor="middle">Codex</text>
  <text x="621" y="365" fill="#C6DCEC" font-family="PingFang SC, sans-serif" font-size="19" text-anchor="middle">DSH</text>
  <text x="186" y="412" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".75">skill + MCP</text>
  <text x="450" y="412" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="17" text-anchor="middle" opacity=".9">同一套接口</text>
  <text x="555" y="412" fill="#6F97AE" font-family="SF Mono, ui-monospace, monospace" font-size="13" opacity=".75">one binary</text>
  <path d="M176 432 h600" stroke-width=".4" opacity=".12"/>
  <text x="176" y="452" fill="#8FAEBF" font-family="PingFang SC, sans-serif" font-size="18" opacity=".75">装完就挂上，不用改配置</text>
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
        panel_html = (f'<div class="panel shot" style="{st}"><div class="pbody">'
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
            capture_output=True, text=True, timeout=90 + seconds * 40,
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
