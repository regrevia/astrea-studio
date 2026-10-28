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
