# 配乐按钩子分段切音量

一条 30 秒竖屏，文字分三段钩子（0–10 / 10–20 / 20–30 秒）。音乐也跟着分段：
开场 0.34、中段 0.24、收尾 0.40，让中段卖点露出来、收尾把情绪推上去。

## 在哪儿改的

- `project/aitoearn-web/src/app/[lng]/studio/utils/material-timeline.ts`：有钩子时给 `music.volume_steps` 写三档。
- `integrations/mac-worker/export/timeline_render.py`：`volume_steps` 校验后拼成 `volume='if(gte(t,20),0.4,if(gte(t,10),0.24,0.34))':eval=frame`。
  有分段音量时把 `loudnorm` 换成 `alimiter`——单遍 loudnorm 会按时间追着改增益，正好把这条曲线抹平。
- `integrations/mac-worker/tests/test_music_rows.py`：锁住表达式的嵌套顺序（越靠后的时间点套外层）。

## 验收

项目「音量三段验收」，工作区点「出 3 版」，三条全 succeeded、30.000 s：

| 版本 | runId | md5 |
| --- | --- | --- |
| 1 | `6ae22e08d4961ac32cc5752e` | `d1ab550857fbc24d882a433006dd75af` |
| 2 | `6ae22e08d4961ac32cc57537` | `c76ba7d0560474a902cc38c289a5eac5` |
| 3 | `6ae22e08d4961ac32cc57540` | `afd2977f47ffba88f8b6a5b5e8feacda` |

`spec.timeline.music.volume_steps` 三条都是 `[{at:0,volume:0.34},{at:10,volume:0.24},{at:20,volume:0.4}]`。

成片分窗测响度（`ffmpeg -af volumedetect`）：

| 窗口 | mean_volume |
| --- | --- |
| 0.5–9.5 s | -25.0 dB |
| 10.5–19.5 s | -28.1 dB |
| 20.5–29.5 s | -23.6 dB |

中段比开场低 3.1 dB、收尾比开场高 1.4 dB，和 0.34/0.24/0.40 的比例对得上。
三条的 `videoQa.findings` 全空。

复现：工作区填好三段钩子后点「出 3 版」，成片在 `~/CreatorStudio/library/studio/<月>/…__音量三段验收-30-秒-N-3__douyin__r1.mp4`。
