# 素材排片：从宿主机挑真实片段，按秒数排一条竖屏任务

## 之前的问题

素材清单只有素材包里的静图，没有视频片段的时长与画幅。想拿已经剪好的几段录屏拼一条竖屏任务，
只能手写 spec，段长全靠猜——写长了成片被裁，写短了留下黑帧。

## 现在

项目工作台右侧多一张「片子素材」卡：列出宿主机 `materials/shots` 下的视频片段，每张缩略图来自该片段第 1 秒的真实画面，
角标是探测出来的真实时长。勾几条、选目标时长（15 / 30 / 45 秒）、点「按素材排片」，就按每段真实秒数切段、
循环填满目标时长，提交成一条 timeline 任务；每段交替加一个 1.0 → 1.08 的推镜关键帧。

- 素材清单接口 `GET /api/production/materials/shots`：把能力服务 `/assets` 里 `shots.recent` 带路径的行转出来，
  视频附 `duration / width / height / fps`，探不到就只报体积，不编时长。
- 排片规则在 `studio/utils/material-timeline.ts`：跳过不足 1 秒的片段，每段取 `min(片段时长, 剩余时长)`，
  最后一段可以短于 1 秒收口，凑不出 1 秒以上片段就返回 null、前端报错不提交。

## 验的东西

**接口**（`result.json`）：`materials/shots` 回 27 条、其中 3 条视频，时长与 `ffprobe` 一致
（`seg-a` 7.13 / `seg-b` 10.9 / `seg-c` 6.6 秒，画幅 1600×1000，30fps）。

**界面**（截图 `ui-clips-before.png` → `ui-clips-picked.png` → `ui-clip-run-queued.png`）：
工作台里勾 3 条片段、目标时长 30 秒、点「按素材排片」→ 任务卡出现「素材排片验收 · 30 秒」并自动选中详情。

**成片**（run `6ae221c7dafe17ccf68123c3`）：

- 规格：30.000 秒 / 1080×1920 / 30fps，落到标准库
  `~/CreatorStudio/library/studio/2026-10/20261029-002445__studio__001__素材排片验收-30-秒__douyin__r1.mp4`。
- 切段：`seg-c 0–6.6`、`seg-b 0–10.9`、`seg-a 0–7.13`、`seg-c 0–5.37`（循环补齐最后 5.37 秒），段段 2 个关键帧。
- 画面（`frames-1-8-20s.png`）：1s / 8s / 20s 三帧分别来自不同片段，确认切的是素材本身，不是同一条反复填。
- 可复现：同一份 spec 排两次（run `6ae22122dafe17ccf6812125` 与本次），md5 都是 `39e0b7bff969d7a1a288d639b1bd3cfa`。
- 体检：`findings` 只有 `text_gap` / `character_gap` 两条 warn——这条规格没有字幕层，警告如实反映了没字的画面。

## 还差的

- 素材是 1600×1000 横屏录屏，竖屏 `cover` 只留中间一条，两侧被裁；还没有模糊背景填充（渲染层只支持 cover / contain）。
- 这一路只排画面，没有配乐与卡点、没有字幕；卡点与字幕仍走「排一条新任务」的 timeline 稿子模式。

## 复现

```bash
curl -s http://127.0.0.1:8080/api/production/materials/shots -H "Authorization: Bearer <token>" | python3 -m json.tool
cd project/aitoearn-web && npx playwright ...   # 见 /tmp/shot-run-ui.mjs
```

界面路径：`/zh-CN/studio/projects/<projectId>` → 右侧「片子素材」→ 勾片段 → 选时长 → 「按素材排片」。
