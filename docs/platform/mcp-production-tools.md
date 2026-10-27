# 宣发工作台 MCP 工具

端点：`POST /api/production/mcp`，需要 `Authorization: Bearer <token>`，并同时接受 `application/json` 与 `text/event-stream`。无状态：每个请求自带认证，不返回 session 头。

| 工具 | 做什么 |
| --- | --- |
| `createProductionProject` / `listProductionProjects` | 建项目、列项目。项目绑定素材分组，任务默认落到该分组。 |
| `createProductionRun` / `listProductionRuns` / `getProductionRun` | 建任务（立即返回 `runId`，不等渲染）、列任务、查任务详情。 |
| `reviseProductionRun` | 在既有任务上出新版本，`revision` 递增。 |
| `listProductionEvents` | 读任务事件流，按阶段回放。 |
| `createJianyingDraft` | 把已完成任务的成片落成剪映工程，进用户素材库默认分组；同一任务重复调用复用同一工程。任务没有成片时直接报错，不会凭空造工程。 |
| `submitRender` / `getRenderJob` / `cancelRenderJob` | 本机渲染队列下单、查进度、取消。`getRenderJob` 成功时给出成片路径、时长与画面尺寸。 |
| `planPublication` / `listPublications` | 只登记发布意向与查看登记结果；提交发布必须由用户在面板确认。 |

两个约束是刻意保留的：

- 建任务不等渲染。视频要跑几分钟，工具当场返回 `runId`，调用方用 `getProductionRun` 轮询。
- 没有提交发布的工具。`planPublication` 只写台账，发布是不可逆动作，必须人确认。
