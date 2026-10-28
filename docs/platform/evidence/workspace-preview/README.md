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
