# 卡点成片加标题层

上一条卡点成片 `videoQa` 报 `text_gap`（整条片子卡面没字）。这一轮给「片子素材」卡加了标题/副标题
两个输入，成片带上常驻标题层，画质检查里 `text_gap` 消失。

## 走的路

1. 工作区「片子素材」卡填标题「一杯好咖啡」、副标题「三分钟送到家」。
2. 选 3 条真实素材 + 配乐 `demo-120.m4a`，勾「按鼓点切」，按素材排片。
3. 时间线 spec 里多出两条 `text` 行：标题 `0.3–29.6 s` 在 `y 0.2`，副标题 `0.9–29.5 s` 在 `y 0.29`，
   都带 2 px 描边、0.3 s 淡入。
4. 渲染器按 Pillow 画 PNG 再叠，出片落库。

## 结果

| 项 | 实测 |
| --- | --- |
| runId | `6ae2286723fdf914233846b7` |
| 状态 | `succeeded` |
| 时长 / 画布 | `30.000 s`，`1080×1920 @ 30/1`，h264 + aac |
| 段数 | 15（每两拍一刀，60.09 BPM，offset 0） |
| 文字 | 2 条：标题 0.3–29.6 s，副标题 0.9–29.5 s |
| md5 | `ed590190446dde00381f6ff34542759a` |
| 大小 | 4248268 B |
| videoQa | `findings: []`（上一条同样素材无文字时是 1 条 `text_gap`） |
| 库文件 | `~/CreatorStudio/library/studio/2026-10/20261029-005308__studio__001__标题卡点验收-30-秒__douyin__r1.mp4` |

三帧抽检（`frames-text-30s.png`，0.5 s / 12 s / 29.3 s）：标题三段都在，副标题在 12 s 与 29.3 s 可见，
0.5 s 那帧还没进场（0.9 s 起），与 spec 一致。

## 复现

```bash
# 跑一遍界面
PROJECT_ID=<项目 id> OUT_DIR=docs/platform/evidence/beat-text node /tmp/beat-text-ui.mjs

# 画质检查
python3 -c 'import video_qa,pathlib;print(video_qa.check(pathlib.Path("<master>"), pathlib.Path("<spec json>")))'
```
