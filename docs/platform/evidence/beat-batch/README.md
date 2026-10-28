# 卡点片子批量出 3 版

一条卡点 spec 走 `POST /api/production/runs/batch` 提交 3 版：入队串行、各自渲染、各自落库。
每版都带同一条配乐与同一套拍点网格，画面靠错开素材入点和翻转推拉来区分。

## 结果

| 版本 | runId | 状态 | 时长 | md5 | 库文件 |
| --- | --- | --- | --- | --- | --- |
| 1 | `6ae226e623fdf914233840cb` | succeeded | 30.000 s | `1eb47212f8b00666123e5f8b9f1771cd` | `20261029-004638__studio__001__卡点批量-30-秒-1-3__douyin__r1.mp4` |
| 2 | `6ae226e623fdf914233840d4` | succeeded | 30.000 s | `aceb316b9f1750a6447d1769ba72943b` | `20261029-004644__studio__001__卡点批量-30-秒-2-3__douyin__r1.mp4` |
| 3 | `6ae226e623fdf914233840dd` | succeeded | 30.000 s | `12f72b389bf6c53461a70ce04694e3e4` | `20261029-004651__studio__001__卡点批量-30-秒-3-3__douyin__r1.mp4` |

- 三条都是 `h264 + aac`、`30.000 s`、`1080×1920 @ 30`，段数各 15。库目录
  `~/CreatorStudio/library/studio/2026-10/`。
- 同一份 spec 出来的第 1 版与单独提交的那条 md5 相同（`1eb47212…`），说明批量没有偷偷改参数。
- 第 2、3 版分别把素材入点错开 +2 s、+4 s 并翻转推拉方向，SSIM 对第 1 版为 `0.964785` / `0.923282`：
  画面确有差别，但没有跑出同一套节奏。
- 三版都保留 `music {bpm 60.09, offset 0, volume 0.32, loop true}`，即拍点网格一致。

## 复现

```bash
curl -s -X POST "http://127.0.0.1:8080/api/production/runs/batch" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"projectId":"<id>","title":"卡点批量 30 秒","brief":"批量出 3 版，配乐按鼓点切","spec":<单条 run 的 spec>,"count":3}'
```

逐版验收：

```bash
for id in <runId...>; do
  curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8080/api/production/runs/$id" \
    | python3 -c 'import sys,json;r=json.load(sys.stdin)["data"]["run"];o=r["output"];print(r["status"],o["duration"],o["md5"])'
done
```
