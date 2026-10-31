# 认领入库 → 发布：字段与失败码契约

这份文档把「人在剪映里点导出 → 工作机认领成片 → 入库 → 平台发布」这一段上双方约定的字段、
取值优先级和失败码固定下来。它对应的代码只有四处，改动任一处都必须让本页与验收表同步：

| 位置 | 职责 |
| --- | --- |
| `integrations/mac-worker/claim_dispatch.py` | 认领命令行拼装、退出码→原因码、归位产物定位、成功收尾（清中转 / 回填库字段） |
| `integrations/mac-worker/export_landing.py` | 读导出体检证据、项目/标题/平台字段兜底、调 `library.land`、失败留痕不丢文件 |
| `integrations/mac-worker/worker.py` | 起进程、调上面两个模块、把结果写回后端 |
| `apps/aitoearn-server/src/core/production/production.service.ts` | `completeJob` 归一化产物字段、`planPublication` 取发布文件 |

## 1. 认领成功时工作机回什么

`completeJob` 收到的结果形如（顶层与 `output` 内层都可以承载，后端两处都认）：

```json
{
  "stage": "exported",
  "videoPath": "<入库后的绝对路径>",
  "output": {
    "videoPath": "<入库后的绝对路径>",
    "libraryPath": "<同一份成片的库内路径>",
    "libraryAssetId": "<台账 id>",
    "md5": "<成片 md5>",
    "claimed": true,
    "requiresManualExport": false
  }
}
```

规则：

- `claimed` 为真表示「这是人工导出后被认领的成片」，后端据此清掉 `manualExportReason`。
- `platform` 与 `draftPath` 必须由载荷显式给出：缺 `platform` 在起进程前就失败（`worker` 不再
  默认 `douyin`，`claim.build_argv` 同样拒空白），`plan_output` 也不会再给出 `__unknown` 落点——
  产物名里的平台段是发布取文件的依据，猜错等于把成片归错档。
- `videoPath` 与 `libraryPath` 指同一份文件；**发布取文件顺序是 平台重交付 key → `libraryPath` → `videoPath`**，
  因为库内路径是唯一保证在本机存在、且过过指纹闸的那份。
- 入库失败（`library.land` 抛错）时工作机返回 `claimed: true` 但**只有**原始 `videoPath`，
  不写 `libraryPath`/`md5`，也不清 `out/<jobId>/rN`：成片仍在，可人工处理，但不假装已入库。
- 归一化只在有产物（或草稿、平台、文案体检、视频体检任一）时才更新 run，避免把渲染阶段的字段清空。

真机证据：[`cli-claim-platform-named.json`](evidence/claim-export-branches/cli-claim-platform-named.json)——同一份手动
导出成片带 `--platform douyin` 重新认领，rc 0，产物从 `__unknown` 归位成 `__r1__douyin.mp4`，7 项体检全 OK。

## 2. 中转目录清理规则

入库成功才清 `out/<jobId>/rN`；且成片落点在 `rN` 之内时跳过——自定义 `STUDIO_LIBRARY` 指到中转目录里时，
无条件清理会把刚入库的成片一起删掉。`claim_dispatch.finish` 是唯一实现，两条成功路径共用。

## 3. 失败码

认领进程退出码 → 原因码（`claim_dispatch.FAIL_REASONS`，只收「用户能自己处理」的四类）：

| 退出码 | 原因码 | 界面动作 |
| --- | --- | --- |
| 6 | `export_probe_failed` | 成片体检没过，重新导出 |
| 8 | `export_output_exists` | 这一版已经有成片，去库里看或升版 |
| 11 | `export_draft_revision_mismatch` | 成片与草稿修订版不一致，重新导出 |
| 12 | `draft_media_changed` | 草稿里的素材换过了，重新导出 |

其余退出码统一 `jianying_export_failed`（原因文案带 `rc` 与输出末尾便于排查）。参数缺失走 `rc=2`
（`claim_export` 的 `bad input`），后端把它当任务失败，不排重试。

导出路径的退出码（`worker.do_jianying_export` 内 `codes`）另有一套，含 `export_invalid_profile`、
`export_param_not_applied`、`export_panel_unavailable`、`export_timeout`、`export_aspect_mismatch`、
`export_ambiguous_output`、`export_user_busy`（用户在用机器，排队而非失败）。

## 4. 发布侧拒绝条件

`planPublication` 只在 run 处于 `succeeded` 且能取到文件时排提交；成片已入库的 run 即使输出里残留
`requiresManualExport: true` 也不拦（那是等待期的标记，不是发布闸）。真正发不发生由 `PublishSpec`
在 worker 侧二次校验（账号引用、路径存在、cookie），载荷不全时 2 秒内失败，不会碰浏览器。

## 5. 目前只有真机才能证的清单

| 项 | 现状 |
| --- | --- |
| `allowGui=true` 真实导出（含导出面板操作） | 未跑过全自动一轮；步骤与判据在 [导出面板实测矩阵](../probe/export-panel/platform-matrix.md) |
| 真实 `jianying_open` 打开草稿 | 能力已注册，未在真机确认窗口被拉起 |
| 带 `remotePostId` 的 `published` 回读 | 只有 `state: unknown` 的失败样本，无成功样本 |
| 真实抖音发布 | 不可逆，需人工明确授权素材/标题/标签后才做 |
| 剪映 11.x 同主版本版本戳 | 本机 11.6.0，参考草稿为 10.2.0/5.9.0，线上如实报「沿用模板」 |

## 6. 对应验收

`integrations/mac-worker/tools/check_all.py` 里相关行：`worker 分发单元测试`（认领拼装/退出码/收尾 + 入库证据/兜底/失败不丢文件）、
`剪映段离线自检`（含 `t_claim` 用真文件走 `claim_export`）、`后端生产 spec`（归一化与发布取文件）、
`发布泳道真机探针`（队列路径：领取/取消/过期租约/回读）。真机导出与发布成功仍见第 5 节。
