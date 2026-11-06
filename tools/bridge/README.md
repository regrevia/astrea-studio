# tools/bridge — 外部写作台的成稿，接进我们的出片链路

`from_media_desk.py` 把 [dsh-media-desk](https://github.com/woniuniuniu/dsh-media-desk) 的一条成稿
（标题 + 口播 + 分镜表）变成 `timeline` spec，交给 `timeline_render.py` 出 1080×1920 的预览片。

它不是插件，也不 import 对方的代码：两边各自完整，接缝只有一份数据。

## 为什么只做单向

| | media-desk | 我们 |
|---|---|---|
| 强项 | 选题、标题、封面文案、分镜表、复盘 | 配音、卡面版式、时间轴、响度、交付 |
| 没有 | 配音、视频渲染（成稿只导出 Markdown） | 浏览器里的写作台 |

所以 `写作台 → 我们` 单向就够了；反过来的「把我们的稿子送回它的编辑器」会变成双向同步，
两边状态各有一份，冲突处理没有收益。它的口播规则和我们 `tools/promo/script_gen.py` 的
`COPY_RULES` 已经是同一套主张（每句推进论证、不写免责声明），再抄一份必然漂移——要改规则
就改 `COPY_RULES` 那一处。

## 数据契约

读它 `src/produce.js` 里 `normalizePlan()` 产出的形状（字段多了会被忽略，少了会报错）：

```json
{
  "title": "…", "coverText": "…", "article": "…",
  "images": [{"no": 1, "caption": "…", "src": "media-desk/files/x.jpg"}],
  "shots":  [{"no": 1, "shot": "近景", "visual": "画面描述", "voice": "口播一句",
              "seconds": 6, "src": "media-desk/files/frame-1.jpg", "prompt": "生图提示词"}]
}
```

- `shots[].voice` 必填：没口播的镜头直接拒绝（没旁白就不是这条片子的镜头）。
- `shots[].seconds` 是它的时长建议；我们取 `max(seconds, 配音时长 + 0.35s)`，不会为凑它的数
  把一句话切掉。
- `shots[].src` 找不到时用卡面兜底（复用 `promo_envoy.card` 的版式），不静默留黑屏。
- 输入可以是单个 production，也可以是它的 `$DSH_HOME/media-desk/state.json`，用 `--id`/`--title` 挑。

## 用法

```bash
PY=~/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python

# 1) 只看时间轴：生成 spec.json + plan.md（镜头表，和它的「分镜脚本.md」对得上）
"$PY" tools/bridge/from_media_desk.py tools/bridge/tests/production.json --out /tmp/desk

# 2) 直接渲一版看画面（不排队、不碰交付盘）
"$PY" tools/bridge/from_media_desk.py tools/bridge/tests/production.json --out /tmp/desk --render

# 3) 正式出片：走能力服务排队，产物落交付目录
"$PY" tools/bridge/from_media_desk.py ~/.dsh/media-desk/state.json --id 3f2a --out /tmp/desk --submit
```

产物：`audio/aNN.mp3`（每镜一条，缓存键 = 文本+音色+语速）、`cards/NN.jpg`、
`spec.json`、`plan.md`、以及 `--render` 的 `raw.mp4` / `preview.mp4`。
没有 `--render` 时 `--af` 不生效。

环境变量：`BRIDGE_VOICE`/`BRIDGE_RATE`（默认同宣传片 `zh-CN-XiaoxiaoNeural` `+14%`）、
`BRIDGE_SHOT_SLACK`（默认 0.35s）、`BRIDGE_XFADE`（默认 0.18s，转场重叠）、
`BRIDGE_COVER_SECONDS`（片头标题卡，`--no-cover` 关掉）、
`BRIDGE_AF`（默认 `volume=1.5dB,alimiter=limit=0.80:…`，限幅 0.80 是响度基线，别动）。

## 已知边界

- 分镜图是它生成的线稿/配图，我们只做 `cover` 裁切 + 1.0→1.045 的缓推，不做重绘。
- 字幕按 13 字/行硬折（`timeline_render` 的 `text` 只认 `\n`），一行超过 39 字就说明该拆镜，
  不是靠缩小字号塞进去。
- 它导出的 `脚本.md`/`分镜脚本.md` 不读——那是给人看的交付物；`spec.json` 才是机器契约。
- 成稿若还没跑出 shots（写作台的「标题与画面规划」没完成），这里直接报错，不猜。
- 没有立绘层：卡面兜底只借版式，人物层要 `promo_envoy.py` 那条链路（`char_layer` 需要 outfit 资产与口型音频）。
