# 工作区里直接切版本看片

出完 3 版不用回任务列表翻：工作区「批量结果」卡上三个按钮，点哪版播放器就播哪版。

## 改了什么

- `projects/[id]/ProjectWorkspace.tsx`：`ShotRunCard` 一次交回多个 runId 时记住这批 id，渲染一排版本按钮，
  点一下把选中的 run 交给下面的详情面板；`RunDetailPanel` 本来就是按 run 播成片的，不用另做播放器。
- 文案 `project.batch` / `project.batchVersion`（中英同步）。

## 验收

项目「版本切换验收」，工作区填好三段钩子点「出 3 版」：

| 版本 | runId | 状态 |
| --- | --- | --- |
| 1 | `6ae22f4ad4961ac32cc57abb` | succeeded |
| 2 | `6ae22f4ad4961ac32cc57ac4` | succeeded |
| 3 | `6ae22f4ad4961ac32cc57acd` | succeeded |

- 按钮实际渲染出 `第 1 版 / 第 2 版 / 第 3 版`。
- 点「第 2 版」后播放器 `<video src>` 变成第 2 条任务的成片（`/oss/studio/runs/6ae22f4ad4961ac32cc57ac4/r1/master.mp4`，HTTP 200）。
- 截图：[workspace-versions.png](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/workspace-preview/workspace-versions.png)。

## 刷新后还在

版本按钮不再只活在内存里：出多版时后端把这一批 runId 记到项目上（`lastBatchRunIds`），工作区先看刚出的这批，没有就回落到项目上那份。

项目「刷新留存验收」出 3 版（全 succeeded / 30 s），刷新两次页面后：

- 按钮仍是 `第 1 版 / 第 2 版 / 第 3 版`；
- 点「第 3 版」播放器源是第 3 条任务（`/oss/studio/runs/6ae231ce38be775d59714096/r1/master.mp4`）。

| 版本 | runId | 状态 |
| --- | --- | --- |
| 1 | `6ae231ce38be775d59714084` | succeeded |
| 2 | `6ae231ce38be775d5971408d` | succeeded |
| 3 | `6ae231ce38be775d59714096` | succeeded |

截图：[versions-after-reload.png](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/workspace-preview/versions-after-reload.png)

## 同屏并排比三条

「批量结果」卡从一排按钮换成三列缩略：每列播一条成片的首帧（静音、`preload=metadata`、加载完跳到 1 秒），下面写「第 N 版 + 时长」。
点哪列，下方详情面板就播哪条。

实测项目「刷新留存验收」三条全 succeeded / 30 s：

| 列 | 缩略图 src | 点第一列后详情面板 src |
| --- | --- | --- |
| 第 1 版 | `…714084/r1/master.mp4` | `…714084/r1/master.mp4` |
| 第 2 版 | `…71408d/r1/master.mp4` | — |
| 第 3 版 | `…714096/r1/master.mp4` | 点击前是 `…714096` |

截图：[batch-compare.png](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/workspace-preview/batch-compare.png)

## 最近两批都留

`lastBatchRunIds` 换成 `recentBatches: [{ runIds, at }]`，新的在前，只留两批。工作区一次最多出两张并排卡：「批量结果」是刚出的这批，「上一批」是上一次的。

实测项目「刷新留存验收」连出两批（各 3 版、全 succeeded / 30 s）：

- 两张卡标题分别是 `批量结果`、`上一批`；
- 两卡共 6 个可播首帧，src 分别指向两批的 master；
- 点「上一批」第 1 版后详情面板 src 切到 `…a43c62/r1/master.mp4`。

截图：[two-batches.png](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/workspace-preview/two-batches.png)

## 首帧落在钩子上

固定取第 1 秒时几版缩略图几乎一样。现在按时间线里起得最早的那句开场文字算：落在它开头往后 1.5 秒（不越过片尾 0.3 秒）。

实测两条批次的 6 个缩略图 `currentTime` 都是 `1.7`，三列能看出运镜/版式差异；批次卡标题变成 `批量结果 · 01:40:50` / `上一批 · 01:40:50`，时间到秒，同分钟连出两批也分得清（时间取自项目 `recentBatches[].at`）。

截图：[hook-frame.png](/Users/apple/Desktop/work/Studio/creator-studio/docs/platform/evidence/workspace-preview/hook-frame.png)
