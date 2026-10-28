# 真实素材 + 配乐卡点验收

一条 30 秒 1080×1920 竖屏成片，素材来自宿主机 `~/CreatorStudio/materials/shots/`，配乐来自
`~/CreatorStudio/materials/bgm/demo-120.m4a`，切点按实测拍点每两拍一刀。全程在网页工作区里点出来，
没有开剪映、没有动鼠标。

## 走的路

1. 工作区「片子素材」卡加载 `GET /production/materials/shots`，选 3 条真实片段。
2. 配乐下拉加载 `GET /production/materials/music`，选 `demo-120.m4a`。
3. 勾「按鼓点切」→ 前端请求 `GET /production/materials/music/beats?path=bgm%2Fdemo-120.m4a`，
   拿到 `bpm 60.09 / offset 0 / 31 个拍点`，按两拍一刀生成 15 段，切点落在 `0, 2, 4, … 28` 秒。
4. 提交 `POST /production/runs`，spec 里带 `music {src, volume 0.32, loop true, bpm, offset}`。
5. 渲染器把配乐循环铺满 30 秒，按拍点网格出片，落库到标准目录。

## 结果

| 项 | 实测 |
| --- | --- |
| runId | `6ae2269123fdf91423383f2c` |
| 状态 | `succeeded` |
| 时长 | `30.00 s`（ffprobe format.duration） |
| 画布 | `1080×1920 @ 30/1` |
| 段数 | 15，合计 30.00 s |
| 音轨 | `aac 48000 Hz 2ch`，30.04 s |
| 配乐实测 | 60.09 BPM，offset 0.04，31 个拍点 |
| 切点偏差 | 平均 26 ms，最大 125 ms（单帧 33 ms） |
| md5 | `1eb47212f8b00666123e5f8b9f1771cd` |
| 库文件 | `~/CreatorStudio/library/studio/2026-10/20261029-004514__studio__001__卡点成片验收-30-秒__douyin__r1.mp4` |
| videoQa | 1 条 warn：`text_gap`（这条时间线没有字幕层，卡面没字属预期） |

## 复现

```bash
# 拍点
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8080/api/production/materials/music/beats?path=bgm%2Fdemo-120.m4a'

# 切点对齐：抽音轨再估一次拍点，跟 spec 里的切点相减
ffmpeg -i master.mp4 -vn -ac 1 -ar 22050 /tmp/master.wav
python3 -c 'import beats; print(beats.estimate("/tmp/master.wav"))'
```

截图：`ui-beat-before.png`（素材墙）、`ui-beat-picked.png`（3 条已选 + 配乐 + 勾上按鼓点切）、
`ui-beat-queued.png`（任务已进队列）。

## 这一轮修掉的坑

- 素材只剩不到 1 秒的尾巴时，旧逻辑直接结束整条时间线（30 秒只出 6 秒）；现在跳过尾巴换下一条，
  末尾不足 1 秒的零头并入最后一段，15 段严格铺满 30 秒。
- 提交回调少声明 `pickedTrack` / `beatOn` 依赖，闭包拿到的是首次渲染的空值，选了配乐也不生效；
  现在依赖补全，选什么就按什么排。
