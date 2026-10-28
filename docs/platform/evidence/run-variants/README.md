# 同题多版：一条出过的片子直接再排几版

## 之前的问题

批量出片只有「新写一份稿子」那一条路。片子已经出好、想再要两个不同运镜和开场的版本时，
只能把稿子和规格重新填一遍，前端也没有从成片出发的入口。

## 现在

成片任务的详情底部多一个「再出几版」：稿子、规格、事实卡整份沿用母版，只错开运镜方向、
开场镜头与开头字幕。一次可选 2–4 版，三个维度默认全勾，第 1 版永远按原稿出。

## 验的东西

**接口**：对母版 run `6ae21a6b99092e973cd685b8` 排 2 版（`variants: ["mirror","opening"]`）：

- `6ae21bb2e0c568eb7f80ef87`（1/2）成片 md5 `a694a029628b0749707f0d59f863a797`，与母版逐字节一致——第 1 版就是原稿。
- `6ae21bb2e0c568eb7f80ef90`（2/2）镜头顺序轮转一位（`seg-a/seg-b/seg-c/seg-a` → `seg-b/seg-c/seg-a/seg-b`），字幕逐句相同，
  与母版画面 SSIM `0.924738`；两条都是 30.00 秒 1080×1920，成片体检 `findings: []`。

**界面**（截图 `ui-variants-dialog.png`、`ui-variants-done.png`）：项目工作台点开母版（右侧详情标题就是母版名）→
「再出几版」→ 弹窗里三个维度全勾、出几版选 2 → 「开始排队」→ 弹窗回「已排 2 版」，列表随即多出两条任务：

- 1/2 成片 md5 `a694a029628b0749707f0d59f863a797`，与母版逐字节一致——界面这条路用的确实是母版规格。
- 2/2 成片 md5 `f2d7e10db88bcd5e2e3d505e671ae30e`，与母版 SSIM `0.924738`，成片体检 `findings: []`。
- 「开头字幕」这一维对这份稿子没有可改的钩子字段，勾上后成片与上个版本相同；运镜与开场两维生效。

## 复现

```bash
curl -X POST http://127.0.0.1:8080/api/production/runs/<母版runId>/variants \
  -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
  -d '{"count":2,"variants":["mirror","opening"]}'
```

界面路径：`/zh-CN/studio/projects/<projectId>` → 点开已出片的任务 → 「再出几版」。
