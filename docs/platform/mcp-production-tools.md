# 宣发工作台 MCP 工具

端点：`POST /api/production/mcp`，需要 `Authorization: Bearer <token>`，并同时接受 `application/json` 与 `text/event-stream`。无状态：每个请求自带认证，不返回 session 头。

| 工具 | 做什么 |
| --- | --- |
| `createProductionProject` / `listProductionProjects` | 建项目、列项目。项目绑定素材分组，任务默认落到该分组。 |
| `createProductionRun` / `listProductionRuns` / `getProductionRun` | 建任务（立即返回 `runId`，不等渲染）、列任务、查任务详情。 |
| `createProductionBatch` | 同一份稿子一次排 1–8 条，返回全部 `runId`。每条独立入队、标题带 `i/N` 序号；逐条轮询 `getProductionRun`。**多条不是多份副本**：第 1 条按原稿，第 2 条起把每段关键帧的 `zoom` 值倒序（推镜变拉镜、时间点不动），第 3 条起再把首段素材挪到最后换开场；`text[0]` 若带 `hookVariants: string[]`，还会按条轮换开头字幕。传 `variants`（取值 `mirror`、`opening`、`hook`） 可以只错开其中几维；只勾 `mirror` 时第 2 条镜像、第 3 条回原稿，避免第 2、3 条渲成同一个文件。竖屏口播稿（`mode=vertical_short`，没有 `timeline`）走另一套：`opening` 轮转 `points`/`materials` 换开场那屏，`hook` 按 `hook_variants` 取一条，`mirror` 控制 worker 的表演种子（勾上每条手势/运镜按 `runId` 各演各的，取消则几条表演完全一致）。实测 count=3 三条 30s 1080×1920 成片 md5 两两不同、时长均为 30.000000 s。 |
| `reviseProductionRun` | 在既有任务上出新版本，`revision` 递增。 |
| `listProductionEvents` | 读任务事件流，按阶段回放。 |
| `createJianyingDraft` | 把已完成任务的成片落成剪映工程，进用户素材库默认分组；同一任务重复调用复用同一工程。任务没有成片时直接报错，不会凭空造工程。 |
| `startJianyingExport` | 给已有剪映工程的任务排一条独立导出任务。默认只排队：macOS 上剪映要人点导出，任务停到 `awaiting_manual_export`、任务状态转 `waiting_user`，不写假成片；带 `allowGui: true` 时本机驱动导出面板，机器被占用则退避。 |
| `openJianyingDraft` | 把任务已落的剪映工程在这台 Mac 的剪映里打开，让人接着改。只打开：不导出、不改任务状态、不写 `videoPath`；剪映会到前台，只在用户明确要看工程时调。 |
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

## `openJianyingDraft` 的契约

参数 `draftPath` 可省略（用任务上次落的草稿）；`check=true` 只体检：确认草稿目录与剪映安装
都在、报当前是不是编辑器态，**不启动剪映、不点鼠标、不抢前台**，人正在用机器时也能跑。
返回 `stage=draft_checked`、`output.opened=false`：`output.canOpen` 是结论，`output.blockers`
说明卡在哪（`draft_missing` / `app_missing` / `media_missing`），`output.canvas/durationSec/fps/
missingMedia/schema` 是草稿内部实况；密文草稿给 `encrypted=true` 而不是失败。
和这次任务对账的结果在 `output.expected`（画布/时长/帧率/轨道/`mediaDigest` 素材指纹，含
`draftId`/`sourceSpecHash` 身份）与 `output.mismatches`；对不上时阻塞项是 `spec_mismatch`。

打开工程是给人看的动作，不是流水线的一步，所以它和导出彻底分开：

- 排一条 `jianying_open` 任务，worker 调 `export/open_draft.py --name <草稿目录>`；成功后 job 落 `stage = draft_opened`，任务的 `status` / `output` 一个字都不改。
- 省略 `draftPath` 就用任务记录里的草稿；没有就直接拒绝，不猜「最新草稿」，也不去目录里挑文件。
- 屏幕锁着判 `screen_locked`，剪映正被别人占用判 `export_user_busy`，两种都不硬点面板。
- 平台侧同一动作走 `POST /production/runs/:id/open-draft`（body `{draftPath?, waitIdle?}`），界面上的「在剪映中打开」调的就是它。

## `submitRender` 的 timeline 契约

`kind: "timeline"` 走本机 ffmpeg 时间轴渲染，不启剪映、不抢前台，可后台排队。`payload` 的形状固定：

```
payload: { spec: { canvas, video[], overlay[], audio[], text[], music } }
```

- `spec.canvas` = `{width, height, fps}`；`video[]` 每段要 `src / in / out`（`in`、`out` 是秒，不是时长），`fit` 决定裁切。
- `video[].keyframes = [{at, zoom}]` 是折线推镜：`at` 是段内 0–1 的相对时间点，给两帧以上且缩放不同才会真的推拉，纯静帧照样可推。
- `music.pulse.amount` 上限 `0.4`（超过直接判 spec 不合法），`decay` 是回弹长度；`beats` 用 `beats.py` 从音频里测出来，`bpm`/`offset`/`beats` 三者给其一即可。
- `createProductionRun` 要传完整时间轴时把上面整段放进 `spec.timeline`，否则后端会按「素材 + 文案」友好输入重新展开，缺 `materials` 会报「时间轴规格缺少素材」。
- 有 `runId` 的任务渲染完自动入库：`output.libraryPath` / `libraryAssetId` / `md5`，`libraryEntries` 里连各平台规格一起列。
