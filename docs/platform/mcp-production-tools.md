# 宣发工作台 MCP 工具

端点：`POST /api/production/mcp`，需要 `Authorization: Bearer <token>`，并同时接受 `application/json` 与 `text/event-stream`。无状态：每个请求自带认证，不返回 session 头。

| 工具 | 做什么 |
| --- | --- |
| `createProductionProject` / `listProductionProjects` | 建项目、列项目。项目绑定素材分组，任务默认落到该分组。 |
| `createProductionRun` / `listProductionRuns` / `getProductionRun` | 建任务（立即返回 `runId`，不等渲染）、列任务、查任务详情。 |
| `createProductionBatch` | 同一份稿子一次排 1–8 条，返回全部 `runId`。每条独立入队、标题带 `i/N` 序号，用于批量出母版；逐条轮询 `getProductionRun`。 |
| `reviseProductionRun` | 在既有任务上出新版本，`revision` 递增。 |
| `listProductionEvents` | 读任务事件流，按阶段回放。 |
| `createJianyingDraft` | 把已完成任务的成片落成剪映工程，进用户素材库默认分组；同一任务重复调用复用同一工程。任务没有成片时直接报错，不会凭空造工程。 |
| `startJianyingExport` | 给已有剪映工程的任务排一条独立导出任务。默认只排队：macOS 上剪映要人点导出，任务停到 `awaiting_manual_export`、任务状态转 `waiting_user`，不写假成片；带 `allowGui: true` 时本机驱动导出面板，机器被占用则退避。 |
| `submitRender` / `getRenderJob` / `cancelRenderJob` | 本机渲染队列下单、查进度、取消。`getRenderJob` 成功时给出成片路径、时长与画面尺寸。 |
| `planPublication` / `listPublications` | 只登记发布意向与查看登记结果；提交发布必须由用户在面板确认。 |

两个约束是刻意保留的：

- 建任务不等渲染。视频要跑几分钟，工具当场返回 `runId`，调用方用 `getProductionRun` 轮询；批量同理，`createProductionBatch` 也不等待。
- 缺 `engine` 时按 `spec.mode` 反推（`vertical_short` → `timeline`），不会静默落到 MoneyPrinterTurbo 报一句和真实原因无关的缺素材错。
- 没有提交发布的工具。`planPublication` 只写台账，发布是不可逆动作，必须人确认。

## `startJianyingExport` 的契约

剪映的导出面板只能由人点，或由这台 Mac 上的 GUI 自动化代点；机器不空闲时强行代点只会空转。所以这个工具分成两档，行为可预期：

- 默认（不带 `allowGui`）：立刻返回，job 记 `succeeded`，`result.stage = awaiting_manual_export`、`reasonCode = export_needs_manual_gui`，任务转 `waiting_user`。不产生 `videoPath`。
- `allowGui: true`：worker 先自检。屏幕锁着直接返回 `reasonCode = screen_locked`（不开剪映、不等 1800 秒）；工具链缺失现场编译；面板可用才代点导出，返回 `stage = exported` 与真实 `videoPath`，或按 rc 映射成 `export_panel_unavailable` / `export_timeout` / `export_aspect_mismatch` 等原因码。

`reasonCode` 写在结果顶层，任务详情把它归一进 `run.output.manualExportReason`，界面据此给不同的下一步。一个不变量：**没有真实产物就不写 `videoPath`**，也不会把这类任务记成 `succeeded`。
