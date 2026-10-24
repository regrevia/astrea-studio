# 主角（主播）素材目录

每个主角一个子目录，放 `host.json` + 素材。面板的「主角」下拉框会自动列出这里所有合法目录。

```
~/CreatorStudio/state/characters/
  whale-girl/
    host.json
    idle.webm        # 带 alpha 的 webm（VP9 alpha_mode=1），或
    poster.png       # 单张透明 PNG，会自动转成静帧动画
```

最小 `host.json`：

```json
{
  "label": "鲸鱼娘",
  "kind": "avatar",
  "voice": "sami:BV411_streaming",
  "clips": ["idle.webm", "wave.webm"],
  "placement": { "x": 1046, "y": 470, "width": 872 },
  "motion": { "type": "bob", "amplitude": 9, "period": 2.6 }
}
```

- `kind`: `avatar`（虚拟形象，可多段动作轮播）/ `person`（真人实拍）/ `digital_human`（数字人口型视频）
- `alpha`: 默认 `true`；`person` 或不透明 mp4 设 `false`，会自动加圆角相框
- `voice`: 决定「谁来解说」，写 studio 支持的音色（剪映 `sami:*` 或 Edge `edge:*`）
- 只写 `poster`/单张 PNG 也行，渲染时转成带 alpha 的静帧视频
