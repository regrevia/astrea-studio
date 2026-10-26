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
  background:radial-gradient(96% 64% at 46% 8%,#111F2D 0%,#0B1621 44%,#070F18 74%,#050B12 100%)}}
.grain{{position:absolute;inset:0;pointer-events:none;opacity:.5;
  background-image:repeating-linear-gradient(104deg,rgba(150,186,210,.030) 0 1px,transparent 1px 3px),
    repeating-linear-gradient(9deg,rgba(150,186,210,.022) 0 1px,transparent 1px 4px)}}
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
        '<feTurbulence type="fractalNoise" baseFrequency=".021" numOctaves="2" seed="{seed}" result="n"/>'
        '<feDisplacementMap in="SourceGraphic" in2="n" scale="2.1" '
        'xChannelSelector="R" yChannelSelector="G"/></filter></defs>')

_SKETCHES = {
    # 一张链接 → 一份带定位的材料
    "link": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <g filter="url(#rgh-link)" stroke="#9CC0D2" stroke-width="2.4">
  <rect class="sk" pathLength="1" x="20" y="26" width="404" height="278" rx="11"/>
  <path class="sk" pathLength="1" d="M20 64 H424"/>
  <circle cx="44" cy="46" r="4.2" stroke-width="1.8"/><circle cx="60" cy="46" r="4.2" stroke-width="1.8"/>
  <rect class="sk" pathLength="1" x="82" y="38" width="300" height="17" rx="8.5"/>
  <path class="sk" pathLength="1" d="M98 46 h128" stroke-width="1.6" opacity=".85"/>
  <path class="sk" pathLength="1" d="M390 38 v17" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M44 96 h300" stroke-width="1.9"/><path class="sk" pathLength="1" d="M44 124 h352" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M44 152 h322" stroke-width="1.9"/><path class="sk" pathLength="1" d="M44 180 h246" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M44 208 h338" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M44 248 h170" stroke-width="1.6" opacity=".8"/>
  <path class="sk" pathLength="1" d="M410 80 v200" stroke-width="1.5" opacity=".7"/>
  <path class="sk" pathLength="1" d="M404 176 h12" stroke-width="1.3"/>
 </g>
 <!-- 一条链接搬过去 -->
 <g filter="url(#rgh-link)" stroke="#5ED3BE" stroke-width="2.4">
  <path class="sk" pathLength="1" d="M442 168 h52"/>
  <path class="sk" pathLength="1" d="M480 152 l20 16 l-20 16"/>
  <path class="sk" pathLength="1" d="M442 184 h34" stroke-width="1.5" opacity=".45"/>
 </g>
 <!-- 材料页：折角 + 定位标 -->
 <g filter="url(#rgh-link)" stroke="#9CC0D2" stroke-width="2.4">
  <path class="sk" pathLength="1" d="M540 18 h252 l78 78 v352 h-330 z"/>
  <path class="sk" pathLength="1" d="M792 18 v78 h78"/>
  <path class="sk" pathLength="1" d="M566 124 h228" stroke-width="1.9"/><path class="sk" pathLength="1" d="M566 158 h186" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M566 192 h214" stroke-width="1.9"/><path class="sk" pathLength="1" d="M566 258 h198" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M566 292 h234" stroke-width="1.9"/><path class="sk" pathLength="1" d="M566 326 h152" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M566 392 h246" stroke-width="1.9"/><path class="sk" pathLength="1" d="M566 426 h178" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M550 96 v356" stroke-width="1.4" opacity=".55"/>
 </g>
 <!-- 坐标：时间戳 / 页码 -->
 <g stroke="#5ED3BE" stroke-width="1.8" fill="none" filter="url(#rgh-link)">
  <rect class="sk" pathLength="1" x="812" y="212" width="72" height="28" rx="5"/>
  <rect class="sk" pathLength="1" x="812" y="252" width="72" height="28" rx="5"/>
  <circle class="sk" pathLength="1" cx="700" cy="300" r="12" stroke-width="1.6"/>
  <path class="sk" pathLength="1" d="M700 288 v-16" stroke-width="1.3" opacity=".75"/>
 </g>
 <g fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="16">
  <text x="819" y="231">04:18</text><text x="819" y="271">p.12</text>
 </g>
 <!-- 引线 + 手写标注 -->
 <g stroke="#7FA6BC" stroke-width="1.4" fill="none" opacity=".85">
  <path d="M690 292 q-46 -20 -78 -8"/>
  <path d="M744 172 h74" stroke-dasharray="5 6"/>
  <path d="M566 96 q-30 -14 -58 -10"/>
 </g>
 <g fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="18" opacity=".9">
  <text x="26" y="344">one link</text><text x="754" y="322">material id</text>
 </g>
 <!-- 影线 -->
 <g stroke="#6F97AE" stroke-width="1.1" opacity=".38">
  <path d="M26 300 l16 -12"/><path d="M48 306 l16 -12"/><path d="M70 312 l16 -12"/>
  <path d="M556 452 l20 -16"/><path d="M584 456 l20 -16"/><path d="M612 460 l20 -16"/>
 </g>
</svg>""",
    # 视频按时间点抽帧，结论带 [mm:ss]
    "timeline": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 胶片条 + 齿孔 -->
 <g filter="url(#rgh-tl)" stroke="#9CC0D2" stroke-width="2.4">
  <rect class="sk" pathLength="1" x="20" y="88" width="860" height="140" rx="6"/>
  <path class="sk" pathLength="1" d="M20 108 H880"/><path class="sk" pathLength="1" d="M20 208 H880"/>
  <g stroke-width="1.3" opacity=".82">
   <path class="sk" pathLength="1" d="M44 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M100 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M156 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M212 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M268 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M324 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M380 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M436 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M492 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M548 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M604 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M660 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M716 94 h10 v10 h-10 z"/><path class="sk" pathLength="1" d="M772 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M828 94 h10 v10 h-10 z"/>
   <path class="sk" pathLength="1" d="M44 212 h10 v-10 h-10 z"/><path class="sk" pathLength="1" d="M156 212 h10 v-10 h-10 z"/>
   <path class="sk" pathLength="1" d="M268 212 h10 v-10 h-10 z"/><path class="sk" pathLength="1" d="M380 212 h10 v-10 h-10 z"/>
   <path class="sk" pathLength="1" d="M492 212 h10 v-10 h-10 z"/><path class="sk" pathLength="1" d="M604 212 h10 v-10 h-10 z"/>
   <path class="sk" pathLength="1" d="M716 212 h10 v-10 h-10 z"/><path class="sk" pathLength="1" d="M828 212 h10 v-10 h-10 z"/>
  </g>
 </g>
 <!-- 音轨波形：满条铺开，不出条外 -->
 <g filter="url(#rgh-tl)" stroke="#9CC0D2" stroke-width="1.7" opacity=".95">
  <path class="sk" pathLength="1" d="M52 168 q8 -14 16 0 q8 -27 16 0 q8 -9 16 0 q8 -32 16 0 q8 -18 16 0 q8 -8 16 0 q8 -24 16 0 q8 -13 16 0 q8 -35 16 0 q8 -17 16 0 q8 -23 16 0 q8 -9 16 0 q8 -28 16 0 q8 -19 16 0 q8 -33 16 0 q8 -11 16 0 q8 -22 16 0 q8 -15 16 0 q8 -30 16 0 q8 -18 16 0 q8 -8 16 0 q8 -26 16 0 q8 -12 16 0 q8 -34 16 0 q8 -16 16 0 q8 -21 16 0 q8 -11 16 0 q8 -25 16 0 q8 -14 16 0 q8 -29 16 0 q8 -9 16 0 q8 -31 16 0 q8 -18 16 0 q8 -24 16 0 q8 -13 16 0 q8 -27 16 0 q8 -16 16 0 q8 -22 16 0 q8 -10 16 0 q8 -33 16 0 q8 -15 16 0 q8 -26 16 0 q8 -12 16 0 q8 -30 16 0 q8 -17 16 0 q8 -20 16 0 q8 -11 16 0 q8 -28 16 0 q8 -14 16 0 q8 -24 16 0 q8 -16 16 0 q8 -32 16 0"/>
 </g>
 <!-- 抽帧标尺 -->
 <g filter="url(#rgh-tl)" stroke="#9CC0D2" stroke-width="1.5" opacity=".85">
  <path class="sk" pathLength="1" d="M20 250 H880"/>
  <path class="sk" pathLength="1" d="M80 250 v8"/><path class="sk" pathLength="1" d="M164 250 v14"/>
  <path class="sk" pathLength="1" d="M248 250 v8"/><path class="sk" pathLength="1" d="M332 250 v14"/>
  <path class="sk" pathLength="1" d="M416 250 v8"/><path class="sk" pathLength="1" d="M500 250 v14"/>
  <path class="sk" pathLength="1" d="M584 250 v8"/><path class="sk" pathLength="1" d="M668 250 v14"/>
  <path class="sk" pathLength="1" d="M752 250 v8"/><path class="sk" pathLength="1" d="M836 250 v14"/>
 </g>
 <!-- 两个抽帧点：竖线 + 小旗 + 帧框 -->
 <g filter="url(#rgh-tl)" stroke="#5ED3BE" stroke-width="2.2">
  <path class="sk" pathLength="1" d="M254 96 V286"/>
  <path class="sk" pathLength="1" d="M254 96 V240"/>
  <path class="sk" pathLength="1" d="M604 96 V240"/>
  <path class="sk" pathLength="1" d="M248 96 h12 v12 h-12 z" stroke-width="1.6"/>
  <path class="sk" pathLength="1" d="M598 96 h12 v12 h-12 z" stroke-width="1.6"/>
  <rect class="sk" pathLength="1" x="232" y="120" width="42" height="26" rx="3" opacity=".8" stroke-width="1.5"/>
  <rect class="sk" pathLength="1" x="582" y="120" width="42" height="26" rx="3" opacity=".8" stroke-width="1.5"/>
  <path class="sk" pathLength="1" d="M232 139 l12 -8 l8 6 l9 -10" stroke-width="1.1" opacity=".85"/>
  <path class="sk" pathLength="1" d="M582 139 l12 -8 l8 6 l9 -10" stroke-width="1.1" opacity=".85"/>
 </g>
 <!-- 被抽中的两句转录 -->
 <g filter="url(#rgh-tl)" stroke="#9CC0D2" stroke-width="1.7">
  <path class="sk" pathLength="1" d="M254 320 h300"/><path class="sk" pathLength="1" d="M254 346 h216"/>
  <path class="sk" pathLength="1" d="M604 320 h214"/><path class="sk" pathLength="1" d="M604 346 h166"/>
  <path class="sk" pathLength="1" d="M240 310 v52" opacity=".45" stroke-width="1.1"/>
  <path class="sk" pathLength="1" d="M590 310 v52" opacity=".45" stroke-width="1.1"/>
 </g>
 <g fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="17">
  <text x="262" y="82">03:12</text><text x="612" y="82">07:02</text>
 </g>
 <g fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="17" opacity=".85">
  <text x="22" y="300">keyframe</text><text x="502" y="300">transcript</text>
  <text x="22" y="404">frame( t )</text>
 </g>
 <g stroke="#6F97AE" stroke-width="1.1" opacity=".32">
  <path d="M26 430 l22 -16"/><path d="M58 434 l22 -16"/><path d="M90 438 l22 -16"/>
 </g>
</svg>""",
    # 没读到的部分照实标出来
    "gap": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <!-- 材料页 -->
 <g filter="url(#rgh-gap)" stroke="#9CC0D2" stroke-width="2.4">
  <path class="sk" pathLength="1" d="M40 20 h398 l82 82 v328 h-480 z"/>
  <path class="sk" pathLength="1" d="M438 20 v82 h82"/>
  <path class="sk" pathLength="1" d="M80 112 h330" stroke-width="1.9"/><path class="sk" pathLength="1" d="M80 144 h268" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M80 176 h312" stroke-width="1.9"/><path class="sk" pathLength="1" d="M80 208 h236" stroke-width="1.9"/>
  <path class="sk" pathLength="1" d="M80 340 h318 v52 h-318 z"/>
  <path class="sk" pathLength="1" d="M94 366 l12 13 l25 -27"/>
  <path class="sk" pathLength="1" d="M172 370 h186"/>
 </g>
 <!-- 没读到的一段：虚线带 + 打叉 -->
 <g filter="url(#rgh-gap)" stroke="#8FAEBF" stroke-width="1.5" opacity=".7">
  <path d="M80 262 h330" stroke-dasharray="7 8"/>
  <path d="M80 288 h330" stroke-dasharray="7 8"/>
 </g>
 <g filter="url(#rgh-gap)" stroke="#E8B464" stroke-width="2.2">
  <rect class="sk" pathLength="1" x="196" y="240" width="104" height="70" rx="7" stroke-width="1.5" opacity=".6"/>
  <path class="sk" pathLength="1" d="M212 254 l72 44"/><path class="sk" pathLength="1" d="M284 254 l-72 44"/>
 </g>
 <!-- 右侧图例 -->
 <g stroke="#E8B464" stroke-width="1.6" fill="none" opacity=".9">
  <path d="M604 236 h44" stroke-dasharray="6 7"/>
  <path d="M560 236 h20"/>
 </g>
 <g fill="#E8B464" font-family="SF Mono, ui-monospace, monospace" font-size="19">
  <text x="662" y="242">未覆盖</text>
 </g>
 <g filter="url(#rgh-gap)" stroke="#5ED3BE" stroke-width="1.9">
  <circle class="sk" pathLength="1" cx="590" cy="306" r="10"/>
  <path class="sk" pathLength="1" d="M585 306 l4 5 l9 -12"/>
 </g>
 <g fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="19" opacity=".9">
  <text x="614" y="313">covered</text>
 </g>
 <g filter="url(#rgh-gap)" stroke="#5ED3BE" stroke-width="1.7" fill="none">
  <rect class="sk" pathLength="1" x="556" y="346" width="74" height="30" rx="5"/>
 </g>
 <g fill="#5ED3BE" font-family="SF Mono, ui-monospace, monospace" font-size="15">
  <text x="566" y="366">04:18</text>
 </g>
 <g fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="19" opacity=".9">
  <text x="632" y="368">带定位才算</text>
 </g>
 <g fill="#8FAEBF" font-family="SF Mono, ui-monospace, monospace" font-size="17" opacity=".8">
  <text x="560" y="416">只有结论不算读到了</text>
 </g>
 <g stroke="#6F97AE" stroke-width="1.1" opacity=".35">
  <path d="M60 452 l22 -16"/><path d="M88 456 l22 -16"/><path d="M116 460 l22 -16"/>
 </g>
</svg>""",
    # 三个客户端，同一套 skill + MCP
    "install": r"""
<svg viewBox="0 0 900 470" fill="none">__RGH__
 <g filter="url(#rgh-in)" stroke="#9CC0D2" stroke-width="2.4">
  <rect class="sk" pathLength="1" x="20" y="16" width="600" height="238" rx="9"/>
  <path class="sk" pathLength="1" d="M20 58 H620"/>
  <circle cx="44" cy="37" r="4.6" stroke-width="1.7"/><circle cx="62" cy="37" r="4.6" stroke-width="1.7"/>
  <circle cx="80" cy="37" r="4.6" stroke-width="1.7"/>
  <path class="sk" pathLength="1" d="M100 37 h118" stroke-width="1.4" opacity=".7"/>
  <path class="sk" pathLength="1" d="M70 100 l9 6 l-9 6" stroke-width="1.6"/>
  <path class="sk" pathLength="1" d="M70 152 l9 6 l-9 6" stroke-width="1.6"/>
  <path class="sk" pathLength="1" d="M70 204 l9 6 l-9 6" stroke-width="1.6"/>
 </g>
 <g font-family="SF Mono, ui-monospace, monospace" font-size="22">
  <text x="96" y="114" fill="#5ED3BE">$ pip install envoy-cli</text>
  <text x="96" y="166" fill="#9CC0D2">$ uv tool install envoy</text>
  <text x="96" y="218" fill="#8FAEBF" font-size="17" opacity=".85">envoy hook install --host codex</text>
 </g>
 <!-- 三个客户端 -->
 <g filter="url(#rgh-in)" stroke="#9CC0D2" stroke-width="2.2">
  <rect class="sk" pathLength="1" x="20" y="306" width="198" height="62" rx="31"/>
  <rect class="sk" pathLength="1" x="238" y="306" width="152" height="62" rx="31"/>
  <rect class="sk" pathLength="1" x="410" y="306" width="132" height="62" rx="31"/>
  <path d="M320 254 v24" stroke-width="1.5" opacity=".55"/>
  <path d="M119 284 h357" stroke-dasharray="6 7" stroke-width="1.3" opacity=".55"/>
  <path d="M119 284 v20" stroke-width="1.3" opacity=".55"/>
  <path d="M314 284 v20" stroke-width="1.3" opacity=".55"/>
  <path d="M476 284 v20" stroke-width="1.3" opacity=".55"/>
 </g>
 <g fill="#D6E8F5" font-family="PingFang SC, sans-serif" font-size="22">
  <text x="62" y="344">Claude Code</text><text x="264" y="344">Codex</text><text x="436" y="344">DSH</text>
 </g>
 <g fill="#7FA6BC" font-family="SF Mono, ui-monospace, monospace" font-size="17" opacity=".88">
  <text x="22" y="414">skill + MCP</text><text x="352" y="414">同一套接口</text>
 </g>
 <g stroke="#6F97AE" stroke-width="1.1" opacity=".35">
  <path d="M646 236 l24 -18"/><path d="M676 242 l24 -18"/><path d="M706 248 l24 -18"/>
 </g>
</svg>""",
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
