# 成片按项目入库

## 问题

渲染任务只带了标题，没带项目 id；worker 拿不到 `libraryProject` 就回退到 `studio/`，于是所有项目的成片都进同一个目录：

```
/Users/apple/CreatorStudio/library/studio/2026-10/20261029-014833__studio__001__…__douyin__r1.mp4
```

## 改动

`production.service.ts` 的渲染任务 payload 补上 `libraryProject: run.projectId`（导出认领任务本来就有），worker 按它分目录。

## 验收

新建项目「端到端验收 · 一体化宣发工作台」（`6ae235e6aa561aad282987e0`），一次出 3 版，全部 succeeded：

| 版本 | 时长 | 画布 | 帧率 | 入库路径 |
| --- | --- | --- | --- | --- |
| 1/3 | 30 s | 1080×1920 | 30 | `library/6ae235e6…/2026-10/…015036__6ae235e6…__001__…r1.mp4` |
| 2/3 | 30 s | 1080×1920 | 30 | `library/6ae235e6…/2026-10/…015045__6ae235e6…__001__…r1.mp4` |
| 3/3 | 30 s | 1080×1920 | 30 | `library/6ae235e6…/2026-10/…015051__6ae235e6…__001__…r1.mp4` |

三个文件都存在（4.35–4.42 MB），ffprobe 实测 1080×1920 / 30 fps / 30.0 s。

工作区截图：两张批次卡（`批量结果 · 01:51:32`、`上一批 · 01:50:30`）与左栏任务列表同屏。

![工作区](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/library-project/e2e-workspace.png)
