# 宣发工作台：验收记录

一条真实链路：建项目 → 建任务（剪映草稿）→ 改稿出新版本 → 导出。下面每条都附可复跑的证据；记录只写跑过的，不写推测。

## 1. 改稿出片真的改内容

任务 `6ae009d498849e9680314e0b`（项目 `6adc2161656a13996166bbfc`）通过 `reviseProductionRun` 打 `{spec: {hook: "AI 读完一篇长文的真实成本", kicker: "Envoy 实测 · 改稿"}}`：

- 返回 `revision: 2`、`status: queued`；任务落 `succeeded / done`，草稿目录为 `studio_6ae13f0f791bd5bc23ed991a__r2`。
- 第 1 版与第 2 版的卡面不同（`s00.png` 哈希 `3d8fec50…` → `cdbae022…`）。
- OCR 第 2 版首屏卡面读到「AI 读完一篇长文的真实成本」「Envoy 实测 · 改稿」；第 1 版同位置是「给 AI 一个链接，它只读到标题」。字幕轨两版一致——改的是钩子与眉标，念白没动，符合补丁范围。

判据：改稿不是重跑同一份稿子，卡面文字必须换。

## 2. 导出不撒谎

同一任务连发导出，机器状态不同则结果不同，且都不产生假成片：

| 次 | 条件 | job 结果 | run 状态 |
| --- | --- | --- | --- |
| 1–2 | 不带 `allowGui` | `awaiting_manual_export` / `export_needs_manual_gui` | `waiting_user`，无 `videoPath` |
| 3 | 缺 `subprocess` 导入 | `failed`（worker 异常） | `failed` |
| 4 | 辅助工具在 `/tmp` 不存在 | `failed`（`FileNotFoundError`） | `failed` |
| 5 | 屏幕锁着，无自检 | 挂 100 秒后被终止 | `failed`（rc=-15） |
| 6 | 屏幕锁着，有自检 | `succeeded` / `screen_locked` | `waiting_user` |
| 7 | 打上原因归一 | `succeeded` / `screen_locked` | `waiting_user`，`output.manualExportReason = screen_locked` |

第 6 次是关键：同样的环境，修好之后 3 秒内返回，而不是等 1800 秒超时。

## 3. 界面跟着后端说话

`http://127.0.0.1:8080/zh-CN/studio?projectId=6adc2161656a13996166bbfc&runId=6ae009d498849e9680314e0b` 上，该任务显示「等你决定」「等你在剪映导出」「屏幕锁着，解锁后再点导出。」，动作是「让这台 Mac 导出」「提修改」。没有成片时不显示播放器，也不出现 `awaiting_manual_export`、`export_queued` 这类枚举原文。

## 4. 本机环境问题不再判死任务

`failJob` 收到缺模块 / 缺文件 / 缺权限这类报错时，把 run 放回 `queued` 并补一条同 kind 的 job（最多 3 次），不再让用户看到一个「渲染失败」去手动重试；已出片或被取消的 run 不会被迟到的失败结果拉回队列。`production.service.spec.ts` 23 项通过，其中 3 项专门盯这条分支。

## 5. 一条命令重跑验收

```
node scripts/verify/e2e-promo.mjs --project <项目 ID>
```

脚本只走平台公开路径：`POST /api/production/runs` 起一条 `timeline/vertical_short` 任务 → 轮询 `GET .../runs/:id` 到终态 → 从 `output.videoUrl` 取回母版 → ffprobe 核对。核对项是「能不能直接发」的硬指标：**1080×1920**、**26–36 秒（约 30 秒）**、**有音轨**、`videoUrl` 必须存在。任一项不达标就以非零码退出，并把失败证据写成 `docs/platform/evidence/e2e-<tag>-failed.json`；通过则写 `e2e-<tag>-<runId>.json`。

最近一次跑（tag `round7b`，run `6ae14a9c3f3f9145994a22d8`）：

| 项 | 实测 |
| --- | --- |
| 分辨率 | 1080×1920 |
| 帧率 | 30/1 |
| 时长 | 28.69 s |
| 音轨 | 有 |
| 母版 | `/oss/studio/runs/6ae14a9c3f3f9145994a22d8/r1/master.mp4`（8,410,987 B） |

第一版稿子念下来 40.05 s，超出「约 30 秒」，把每屏口播压短后重跑才落到 28.69 s——说明时长是被真实配音驱动的，不是写死的。

## 5.1 一条命令跑完不碰 GUI 的自检

```bash
cd integrations/mac-worker
<venv-python> tools/check_all.py              # 全量：离线契约 + 剪映段 + 发布泳道真机探针 + 版本依据
<venv-python> tools/check_all.py --offline    # 只跑离线（不排真机作业，约 5 s）
<venv-python> tools/check_all.py --with-web   # 再加上后端单测、Web 类型检查与三套 lint（多约 5 s）
```

输出一表，任一失败退出码非 0；`--json /tmp/report.json` 落一份机器可读结果。最近一次结果（真机探针 + `--with-web`）：

| 项 | 耗时 | 结论 |
| --- | --- | --- |
| 发布段离线契约 | 0.1 s | PASS |
| 剪映段离线自检 | 4.7 s | PASS（10/10） |
| worker 分发单元测试 | 0.0 s | PASS（18/18：认领参数拼装/退出码归因/产物定位、入库证据/字段兜底/失败留痕不丢文件、归位收尾与中转清理） |
| 发布泳道真机探针 | 72.0 s | PASS（claim 3.3 / cancelled-run 3.3 / stale-revision 3.3 / lease-expired 58.8 / readback-retry 2.0 s） |
| 剪映版本依据 | — | 11.6.0 build 490 · 沿用模板（无同主版本实证，不猜版本号）· 参考草稿 11 份（版本戳 10.2.0/5.9.0） |
| 后端生产 spec（`--with-web`） | 1.9 s | PASS（79/79：发布状态机、租约回收归位、回读、认领成片落库、发布取库路径） |
| Web 类型检查（`--with-web`） | 2.0 s | PASS（`tsc --noEmit` 0 错误） |
| Web 文案 lint（`--with-web`） | 0.0 s | PASS（0 条错误 / 121 条提醒，基线 0） |
| Web 中文排印（`--with-web`） | 0.0 s | PASS（0 条错误 / 17 条提醒，基线 0） |
| Web 样式 lint（`--with-web`） | 0.0 s | PASS（0 条问题，基线 1） |

三套 lint 的判据是「不高于基线」：范围固定在 `src/app/[lng]/studio`，基线值写在
`tools/check_all.py` 的 `baseline` 字段里（文案 0 / 排印 0 / 样式 1），超过一条就 FAIL 并报出
条数。文案这条从 64 降到 0：原来的 64 条全部落在 `utils/material-timeline.ts` 与
`utils/timeline.ts`——两份「按渲染计划拼数据」的模块，接口字段和注释被当成界面键值扫了出来；
它们不是界面文案，已按职责挪到 `src/utils/studio/`，studio 目录只剩渲染界面的文件，于是恢复
零容忍。warning 不清零；样式那条 1 是脚本自报的「未检出颜色/字号字面值，需人工 + 截图核对」。

最后一行是版本适配层的诚实面：本机 11 份参考草稿的版本戳都是 10.2.0 / 5.9.0，没有一份是剪映
11.x 自己保存的 schema，所以适配层不盖章、沿用 VectCutAPI 模板（该模板在 11.6 实测能开）。
等这台机器上的剪映保存过一次 11.x 草稿，同一条命令的输出会变成「同主版本实证 schema …」，
不需要改代码——这就是「新版本靠同主版本实证自动采纳，不猜」的可验证形态。

## 6. 成片落到哪个草稿箱

制作台的项目选择器右侧新增「草稿箱」下拉：选中项目后可以直接把它绑到一个草稿箱（不选即后端默认箱），改动落到项目的 `materialGroupId` 上。后端 `attachMaterialForWorker` 早就按 `项目.materialGroupId → 用户默认分组` 的顺序解析，缺的是界面这一端——在此之前所有项目的 `materialGroupId` 都是空串。
## 7. 素材引用真的进了成片

`spec.materials` 写 `pack:envoy-demo/...` 两行，跑 `E2E_MATERIALS=$'pack:…/github-com-regrevia-envoy-1\npack:…/-2' E2E_FRAMES="5.6" node scripts/verify/e2e-promo.mjs --tag materials`：
1080×1920 / 28.69 s / 30 fps / 有音轨，`/oss/studio/runs/6ae14f923f3f9145994a3fa3/r1/master.mp4` 13,118,389 B。

素材是否真的上屏，不看日志看帧：成片 t=5.6 s 抽帧 OCR 出 `regrevia / Envoy`、`Code`、`Sign in`——仓库页截图在片子里；
同一跑里 t=1.0 与 t=3.6 抽帧 OCR 全是封面文案「一条片子，从一句话到能发」，说明卡面在前、素材在后，
封面标题没被素材顶掉。成片工作目录 `spec.json` 的 `meta.materials[]` 记了两个文件路径、字节数、sha256 前缀。
证据：[e2e-materials-6ae14f923f3f9145994a3fa3.json](evidence/e2e-materials-6ae14f923f3f9145994a3fa3.json)。

素材缺失也不会白跑：`pack:envoy-demo/no-such-shot` 立刻报「素材不存在：…（包里是 … 这些）」，
离线自测 `integrations/mac-worker/export/test_vertical_materials.py` 7 项覆盖解析、后缀、坏包、坏图名。

## 8. 环境类失败的重排是活的，不只是单测

用真实 worker token 走一遍：停掉本机 worker → 建一条 `render_timeline` 任务 → 直接 claim 这条车道 →
经 `POST /api/production/worker/jobs/:id/fail` 上报 `ModuleNotFoundError` → 读回 run。

```
launchctl bootout gui/501/local.creatorstudio.worker
node scripts/verify/requeue-check.mjs
launchctl bootstrap gui/501 ~/Library/LaunchAgents/local.creatorstudio.worker.plist
```

结果（run `6ae17577affc0494af31fa2e`）：失败接口回 `{ok:true, retry:true}`；run 仍是 `revision=1`、
状态回到 `queued`；旧 job 记 `failed`（attempt 1），同时补发新 job `6ae17577affc0494af31fa4b`
（attempt 2、`queued`）；事件里有 `job.requeued`；`lastError` 写的是
「本机环境问题，已自动重排：ModuleNotFoundError…」而不是把任务判死。
证据：[requeue-6ae17577affc0494af31fa2e.json](evidence/requeue-6ae17577affc0494af31fa2e.json)。

## 9. 还没验收的

### 9.1 真剪映 allowGui 导出（唯一没闭环的一项）

**探到哪一步**：`allowGui:true` 的导出 job `6ae1ab69ed8f33a90f5c77a5`（run `6ae009d498849e9680314e0b`，
revision 2）在未锁屏状态下失败，`reasonCode=export_invalid_profile`，原文：

```
rc=2: 等待用户空闲 ≥30s 再操作（最多等 300s）…
用户已空闲 30.1s，开始
打不开导出面板（需要剪映在前台且草稿已打开）
```

**根因不是锁屏，是「草稿没打开」**：导出面板只存在于已打开的草稿里，而剪映没有「按路径打开草稿」的入口
（实测 `open -a VideoFusion-macOS.app <草稿目录>` 无效，仍停在首页），面板只能在首页草稿箱点进去。
所以整条链一直卡在等人工点草稿，而不是等人工点导出。

**本轮补上的三件事**
1. `export/open_draft.py`：读首页 AX 树，按「标题文字矩形落在卡片矩形内」定位缩略图卡（文字矩形是 98x21，
   点在文字上 Qt 只选中不打开），双击卡片进编辑器，再轮询导出按钮出现。
2. worker 导出分支前置调用它，失败带 `reasonCode=export_draft_not_open`（已加入原因码表），不再让人从
   `export_invalid_profile` 里猜。
3. **盲点保护**：AX 树能读到别的 Space 的窗口且坐标正常，无守卫时真实鼠标点会落在当前 Space 的**别的 App** 上
   （实测前台是 DeepSeek Harness）。现在先用 `ax_tool wins <pid>` 数当前桌面可见窗口，为 0 就拒绝点击。

**复测结果（未闭环，阻塞在桌面而非代码）**：

```
剪映窗口不在当前桌面（可见窗口数=0），拒绝盲点坐标
```

剪映窗口（`696,301 1168x780`）在另一个 Space；当前桌面有人在用（`ax_tool idle` = 0.68s），不抢前台不切 Space。
**收尾所需动作**：把剪映窗口拖到当前桌面（或切到它所在桌面）后执行

```
PYTHONPATH=/Users/apple/CreatorStudio/runtime/lib \
  /Users/apple/CreatorStudio/runtime/integrations/mac-worker/.python3 \
  integrations/mac-worker/export/open_draft.py --name studio_6ae13f0f791bd5bc23ed991a__r2
```

返回 0 即草稿已开，再对 job `6ae1ab69ed8f33a90f5c77a5` 重发 `startJianyingExport{allowGui:true}`，
验收项是 `~/CreatorStudio/out/6ae1ab69ed8f33a90f5c77a5/r2/` 出现 mp4 且 `videoQa.ok=true`。

### 9.2 已顺带闭环的两项

- **辅助功能授权**：已具备 —— `ax_tool tree 1842` 能读到剪映 40 个控件（原来记的「尚未授予」已过期）。
- **`patch-backend-container.sh` 第 4 步假阴性**：已修。旧逻辑 30×2s 窗口太短且只认 nginx 200，
  容器刚重启时必判死。现为 180s 窗口 + `docker inspect` health 与 `/healthz` 双信号，实测
  `健康检查通过（healthy / nginx 200，6s）` EXIT=0。

### 9.3 导出不再抢前台（本轮新增约束）

剪映的 Qt/CEF 控件实测只认真实光标点击，且必须点在前台窗口上（`AXPress` 返回 err=0 但不生效、
`CGEvent.postToPid` 被忽略），所以「点导出」这一步没法做成纯后台。改成**低干扰**：

- `quiet_start(strict=True)`：只有连续空闲 ≥ `--wait-idle`（默认 30s）才开始，等不到就回 `rc=9`
  `export_user_busy`，不动鼠标、不激活剪映；
- 点导出前再查一次 `idle()<1.0` 就放弃这一轮（人刚回来敲键盘时不替人点）；
- 导出前记录前台 App pid，结束 `restore_front()` 把焦点还回去；
- `/scripts/dev/sync-mac-worker.sh`：仓库改动同步到 `~/CreatorStudio/runtime/integrations/mac-worker`
  并 `launchctl kickstart -k` 重启 worker —— 之前没有这一步，改完代码线上仍跑旧 worker
  （job `6ae1aece` 就是旧代码跑出来的同一句 rc=2）。

**真·无人值守**：`--wait-idle 3600` 挂后台等一个小时的连续空闲（夜间），或直接走不碰 GUI 的
`timeline` / `ffmpeg` 渲染通道出片；剪映导出只在人空着的窗口期做。

### 9.4 「暂无访问权限」不是 TCC，是 App Sandbox（已修）

现象：打开草稿，26 条媒体全红、弹「链接媒体」，进度 0/26，预览报「媒体格式不支持」，
而系统**不弹任何授权窗**。

根因（`codesign -d --entitlements -` 实测）：
`com.apple.security.app-sandbox = true`，只带 `assets.movies.read-write`。
剪映只能读 `~/Movies` 这类获准目录；我们的素材在 `~/CreatorStudio/...`，
沙盒里没有该路径的读取权——沙盒缺权不会弹 TCC 授权，所以等授权窗等不到，
在「链接媒体」里手点也只是一次性的 powerbox 授权，下次生成草稿又坏。

修法：`jianying_engine._relocate_media()` 在部署草稿时把每个外部媒体复制进
`<草稿目录>/assets/` 并把 JSON 里的绝对路径改指过去（草稿在 `~/Movies` 下，沙盒可读）。
实测该草稿 `copied=26 missing=0`，重启剪映重开草稿后 `ax_tool tree` 里
「暂无访问权限」计数 **0**，不再弹链接媒体。

## 9.1 剪映 GUI 导出实测（2026-10-28）

- 命令（直连驱动，等价于 worker 的 `jianying:export`）：
  `jianying_export.py --job-id direct17 --revision 2 --platform 抖音 --draft-path …/studio_6ae13f0f791bd5bc23ed991a__r2 --wait-idle 0 --return-pid <Chrome pid>`
- 结果：`产物 27.10 MB`，`1080x1920 @ 30fps h264 + aac`，`30.833s`，6 项校验全 OK。
- 归位：`/Users/apple/CreatorStudio/out/direct17/r2/direct17__r2__抖音.mp4`（28,418,773 B，md5 `71d5abe966a97d983629ecb90f43d09d`）。
- 日志片段：`鼠标点击导出 1975,1322` → `已点导出，前台交还 pid=1313（ok）` → `产物: ~/Movies/studio_6ae13f0f791bd5bc23ed991a__r2.mp4`。
- 由此确认：**编码阶段不需要前台**。点击后驱动器只做 `poll_outputs(~/Movies)` + `ffprobe` 校验 + 归档。

## 9.5 导出的打扰边界（用户会感觉到什么）

| 阶段 | 占用前台 | 动鼠标 |
| --- | --- | --- |
| 打开草稿、读导出面板、选参数 | 约 10–20s（窗口需在最前） | 有，若干次真实点击 |
| 点「导出」那一下 | < 1s | 有，一次点击；随后 `cursor_restore()` 把光标挪回原位 |
| 编码 / 校验 / 归档 | 不需要 | 不需要 |

- 点完导出立刻 `restore_front(return_pid)`：前台还给用户原来的 App（实测 `pid=1313 Google Chrome`）。
- `waitIdle>0` 时，用户一直在用机器就整轮不动鼠标（退出码 9 `export_user_busy`）；`waitIdle=0` 才允许立刻开始。
- 想零打扰出片就走内置引擎（`TIMELINE_RENDER` / `RENDER_FFMPEG`），那一路完全不碰 GUI。
- 为什么不给「只属于剪映窗口的第二个光标」：实测 AXPress / `postToPid` 在剪映上返回成功但界面不动，只有 HID 生效，详见 [应用内点击的三级阶梯](gui-input-tiers.md)。

## 9. 任务书七项：当前状态与证据

| 项 | 状态 | 可复跑的凭据 |
| --- | --- | --- |
| P0 生产状态（revision/attempt/取消/租约/reaper/worker 用户范围） | 完成 | `production.service.spec.ts` 45 项通过，覆盖过期租约不覆盖 run、连续丢 3 次租约判失败、reaper 续约后不动 run、取消推进 leaseVersion、按项目过滤带 userId；实机取消一条排队任务：`{"cancelled":1}`，run 变 `cancelled / cancelled` |
| project/run 绑定 + 前端工作区 | 完成 | 工作区按项目列任务、批量卡片带批次时间、素材与工具面板；截图 [e2e-workspace.png](evidence/library-project/e2e-workspace.png) |
| 真实素材选择 | 完成 | 素材选择器只列已入库素材，缩略图走 `/oss/…`；[project-workspace-materials.png](evidence/project-workspace-materials.png) |
| 类型化工具 | 完成 | MCP 18 个工具带 schema（[mcp-production-tools.md](mcp-production-tools.md)、[mcp-studio-capabilities.json](evidence/mcp-studio-capabilities.json)） |
| 可验证 scene patch | 完成 | 补丁落新 revision、可连续叠加、底稿仍可审计；[scene-patch-accumulate.json](evidence/scene-patch-accumulate.json)、[scene-patch-ui.png](evidence/scene-patch-ui.png) |
| 剪映工程 + 独立导出任务 | 完成 | 导出与渲染拆成两条 job，认领只排任务不驱动面板，`claimPath`/`allowGui` 互斥；[claim-export-branches](evidence/claim-export-branches) |
| 认领成片入库 | 完成 | 实机 job `6ae2a8298f01834bace113fc`：`claimPath` 指向 1080×1920/30fps/6.0s 的 mp4，job `succeeded`、`claimed=true`，落 `library/端到端验收-一体化宣发工作台__6ae237a7/2026-10/…__douyin__r1.mp4`（188 KB）并写进 `library/index.jsonl`；素材指纹不符时 job `6ae2a8228f01834bace113b3` 失败，`reasonCode=draft_media_changed`（`认领失败 rc=12：草稿素材换了：media 1b0c4d60a2f4 != deadbeef0000`） |
| 发布契约（离线） | 完成 | `publish_sau` 载荷→命令行、四类拒绝、状态判读（`submitted_unverified` / `submission_unknown` / `PublishError`）全部有测试：`tools/check_publish.py` 16/16，worker 测试 81 项；见 [publish-contract.md](publish-contract.md)；`--cookie yefen` 实测无头预检通过；`tools/check_publish_lane.py` 用**故意缺字段**的 `publish_sau` 探针跑通四条队列路径（`--all`，各 2.8 s 全 PASS）：`claim` 被 `publish` 泳道领取、`job.claimed` 带作业号、在载荷校验处失败（`publish payload has no accountRef`）、不查 cookie 不上传；`cancelled-run` 与 `stale-revision` 在领取阶段被取消，`job.cancelled` 带作业号且写明 `run cancelled` / `revision 1 superseded by 2`，worker 从未收到这条活；`lease-expired` 造一条租约过期的 running 作业和一条 `submitting` 提交，回收器把作业判 `lease expired`、提交落回 `submission_unknown`（不再无声卡在进行中）、run 保持 `succeeded`不加任务；`readback-retry` 造一条回执不明的记录，调 `POST publications/:id/readback-job` 排出一条 `publish_readback`（payload 绑 `attemptId`）、记录翻成 `reconciling`，再点一次与无链接两种都被拒——回读入口不再靠手点验证，每次 `--all` 都验一遍 |
| 认领 → 发布台账 | 完成 | 认领过的 run 登记发布，`content.assetKey` 指向入库那一份而非产物目录临时路径；同载荷重复登记 `deduped:true`。attempt `6ae2ab4666b2fe676bafe43a` |
| 发布状态机 | 完成 | 越级转移 400；`planned→submitting→submission_unknown→not_submitted` 真机走通；重复回写不加证据（attempt `6ae2ab4666b2fe676bafe43a`） |
| 链接回读收尾 | 完成 | 无头渲染回读作品链接，只给 `published` / `not_found` / `unknown` 三档；`published` 才带作品 ID 回写台账，`unknown` 不动（离线 30/30、单测 94 条；真机 `yefen` 读到 4 条作品，真实作品 `7602658672556153993` → `published`，不存在的 ID → `unknown` 未写库；worker 作业 `publish_readback` 与后端 `POST production/worker/publications/:id/readback` 打通，后端只收 `published`（带 ID）/`not_submitted`，越级 12853）；界面入口 `POST publications/:id/readback-job` 已通（作业被 `publish` 泳道领取、21s 跑完，`unknown` 时记录退回 `submission_unknown` 并记 `readback_no_conclusion`，run 不动；重复点击、非 `submission_unknown`、无链接三类都拒；界面真机点过：`回执不明` 行有「回读链接」，点一下提示「已排队回读，稍后回来看结论」、该行转「核对中」，作业 21s 后跑完、无结论退回 `提交结果不明`） |
| 提交中断巡捡 | 完成 | `POST publications/sweep` 把超时 `submitting` 降级为 `submission_unknown`；幂等，撞上真实回写不覆盖（离线 4 例，真机 attempt `6ae2ae2b6d97de3d41203a1d`：首次 recovery，二次 empty） |
| 约 30 秒 1080×1920 端到端 | 完成 | 新项目 `6ae237a72a5e71e140145c45` 跑 3 版：全部 succeeded，ffprobe 1080×1920 / 30 fps / 30.0 s / 4.4 MB，落点在 `library/端到端验收-一体化宣发工作台__6ae237a7/`（[result.json](evidence/library-project/result.json) 的 `finalCheck`） |

本轮还补齐了成片库的目录规范：目录与文件名统一 `<项目名slug>__<id8>`，历史 `studio/` 与纯 id 目录用 `library.py migrate` 一次搬完 121 个文件，`library.py verify` 对账干净（见 [asset-library.md](asset-library.md)、[library-project/README.md](evidence/library-project/README.md)）。

复核命令：

```
bash scripts/dev/patch-backend-container.sh          # 后端热补丁
cd integrations/mac-worker && PYTHONPATH=$PWD <venv-python> -m unittest discover -s tests -p 'test_*.py'
cd integrations/mac-worker && <venv-python> tools/check_publish.py      # 发布契约离线体检
cd integrations/mac-worker && <venv-python> tools/check_publish_lane.py --apply --all  # 发布泳道五条探针（只造必败作业 / 只验回读入口，不发布）
cd project/aitoearn-backend && ./node_modules/.bin/tsc -p apps/aitoearn-server/tsconfig.app.json --noEmit
cd project/aitoearn-web && npx tsc --noEmit
```

最后一次全绿：spec 45/45、worker 31/31、两个 tsc 退出 0、`web=200 api=200`。已分批推 `astrea/studio/integration`，最新提交见 `git log --oneline -5`。

## 11. 打开工程改为可体检（2026-10-29）

打开草稿原来只有「真开」一条路，验证时必须动鼠标。现在多一条只读通道：

- `export/open_draft.py --check` 输出一行 JSON：`draftPath/appPath/draftExists/appInstalled/appRunning/editorOpen/canOpen/blockers`，退出码 0 可开、2 不可开；不启动、不点、不抢前台。
- 体检还读草稿内部（`export/draft_inspect.py`，只读）：`canvas/durationSec/fps/trackCount/media/missingMedia/schema`；素材引用逐个核对是否还在，缺了报 `missingMedia` 并给阻塞项 `media_missing`。剪映 11.6 保存后 `draft_info.json` 是密文，这时报 `encrypted/infoReadable=false`，不当失败（草稿照样能开）。
- 体检还跟这次任务对账：读 `studio_export_meta.json` 的 `expectedCanvas/expectedDuration/fps/tracks/mediaDigest`，跟草稿实况比，不一致列进 `mismatches` 并给阻塞项 `spec_mismatch`（拿错草稿在「真开」之前就能发现）；`mediaDigest` 是素材的短指纹（相对路径 + 字节数，排序后 sha1 前 12 位），同一路径换了内容也能在体检阶段看出来；版本戳分三态 `present/missing/unreadable`，旧草稿没戳和戳写坏不再混成一句 `null`。
- 实机负例：把 sidecar 写成期望 30s/2 轨、草稿实况 11s/1 轨，job `6ae2a4f58f01834bace1089d` 失败并给出「草稿和这次任务对不上：duration 11.0 != 30.0s；tracks 1 != 2」；素材指纹对不上的负例 job `6ae2a6878f01834bace10e4e` 给出「media b858506f887c != deadbeef0000」。
- 导出/认领前还有一道素材闸：草稿建好后素材被换掉，`jianying_export.py` 以 rc 12 拒绝并报 `draft_media_changed`（实测 `草稿素材换了：media 1b0c4d60a2f4 != deadbeef0000`），不会把贴错素材的成片认领进来。
- 实机正例（引擎 v4 语义的手写 sidecar）：job `6ae2a7748f01834bace11166` 的 `expected` 带上 `draftId/sourceSpecHash/mediaDigest=6f35d8b668ac`，与草稿实况一致，`mismatches=[]` 仍可开。
- worker 的 `jianying_open` 收 `check: true` 走体检分支，返回 `stage=draft_checked`；体检不看屏幕锁（只读），真开仍被 `screen_locked` 拦住。
- REST `POST /production/runs/:id/open-draft {check:true}`、MCP `openJianyingDraft {check:true}` 均可触发。
- 顺带修掉一个真问题：`claimJob` 的「run 已终态就不再跑」原来把 `jianying_open`/`jianying_export` 一起丢掉，对已交付的 run 点「在剪映中打开」会被判 `dropped: run already succeeded`。现在这两类收尾动作只拒「run 已取消」，渲染类任务行为不变（`production.service.spec.ts` 两条新断言覆盖）。

实测（2026-10-29 09:34，run `6ae237a72a5e71e140145c49`，草稿 `studio_6ae20a3cb2886cd834c8b67e__r1`）：
job `6ae2a2b58f01834bace100de` 返回 `stage=draft_checked`、`output.canOpen=true`，剪映进程未被拉起、前台未变；
09:46 同一 run 的 job `6ae2a5288f01834bace10964` 带 sidecar 对账：`expected=runId/1080×1920/11.234s/30fps/7轨`、`mismatches=[]`、`stampState=missing`（旧草稿没版本戳）；
09:43 同一 run 的 job `6ae2a4338f01834bace105e9` 带上内部信息：`canvas=1080×1920`、`durationSec=11.234`、`fps=30`、`trackCount=7`、`media={videos:15,audios:4,images:0}`、`missingMedia=[]`。

## 10. 剪映打开工程 + 版本适配层（2026-10-29）
同主版本实证这条现在验到草稿级：离线自检把本机版本模拟成剪映 11.9、草稿箱里放一份 11.9 自己保存的
草稿，然后走真实的 `_deploy` 落盘，断言 `draft_content.json` 的 `new_version`/`platform`、
`draft_version.json` 的 `stamp.action=applied`、sidecar 的 `schema.source` 全部换成 11.9——即
「新版本出现后不需要改代码，下一份生成的草稿会自动采纳」不是说法而是可复跑的一条用例（`t_adopt_at_deploy`）。
「认领入库」这一步也补了离线用例（`t_claim`）：人工在剪映里导出的成片必须显式给路径，按草稿 sidecar
的时长/画布体检，过了才归位到 `jobId/rN` 落点并留 accepted 证据；时长对不上就拒绝、原文件不动、
留 rejected 证据；同一修订版重复认领返回 8 而不是覆盖——「不猜导出目录里最新文件」因此可离线复跑。
认领在 worker 侧的分发（拼命令行、退出码→原因码、找归位后的成片）已抽成纯标准库的
`integrations/mac-worker/claim_dispatch.py`，由 `tests/test_claim_dispatch.py` 覆盖并在验收里作为独立一行跑，
不再只靠真机探针间接验证；worker 只负责起进程、落库、把原因码回给后端。
成片入库的收口同样抽成 `integrations/mac-worker/export_landing.py`：按候选根目录读导出体检证据、
把项目/标题/平台字段按优先级兜底、交给 `library.land`；入库抛错时返回 None 并留痕，成片仍在原处，
调用方不会拿到伪造的库路径。`tests/test_export_landing.py` 8 项覆盖（含真实 `library.land` 的同版本二次入库不覆盖）。
认领/导出两条成功路径共用同一段收尾（`claim_dispatch.finish`）：入库成功才清 `out/<jobId>/rN` 中转目录，
入库失败则 `videoPath` 保留原文件、不伪造库路径；若自定义库目录恰好落在这个中转目录里，清理会跳过，
不会把刚入库的成片一起删掉。`tests/test_claim_dispatch.py` 3 项盯这条。
后端侧补了一条对应断言：认领结果只给 `libraryPath` 而没给 `videoPath` 时，按同一份成片认，
run 不会停在「等待人工导出」或写出空路径。发布侧同样按「平台重交付 → 库路径 → 母版」取文件：
只有库路径时台账记的就是库里的那份；已经入库的 run 即使输出里残留「等待人工导出」也不会被挡在发布之外。
`production.service.spec.ts` 79 项通过。
认领入库到发布的字段、清理规则与失败码见 [claim-to-publish-contract.md](claim-to-publish-contract.md)。



「在剪映中打开」与「导出」分成两条任务记：`jianying_open` 只把工程开到剪映里，job 落 `stage=draft_opened`，
任务的 `status` / `output` 不变；`jianying_export` 才碰成片。屏幕锁定判 `screen_locked`，剪映被占用判 `export_user_busy`，
都不硬点面板。入口三处同源：界面「在剪映中打开」→ REST `POST /production/runs/:id/open-draft`；
MCP `openJianyingDraft`；worker `do_jianying_open`。省 `draftPath` 时用任务记录里的工程，没有就拒绝，不猜最新目录。

版本适配层同时落地：从 `.app` 读版本（本机 11.6.0 / build 490）→ `resolve_schema` 决定 `reference-same-major` 还是
`keep-template` → 草稿里写 `draft_version.json`（`stamp.action ∈ {applied, kept}`）并在导出侧车带同一份 `schema`
（`source` 必须与 stamp 一致）。没有同主版本实证时保持模板不动，不猜 schema。详见
[jianying-version-support.md](../shorts/jianying-version-support.md)。

复核（本轮全绿）：

```
python integrations/mac-worker/tools/check_jianying.py --draft <草稿目录>     # 7/7 通过
cd integrations/mac-worker && ... -m unittest discover -s tests -p 'test_*.py'  # 49 tests OK
cd project/aitoearn-backend && pnpm exec vitest run apps/aitoearn-server/src/core/production/production.service.spec.ts   # 47 passed
                                              ... production-assistant.service.spec.ts   # 21 passed
curl -s -X POST :8080/api/production/mcp -d '{"method":"tools/call","params":{"name":"openJianyingDraft",...}}'  # 工具已注册
```

未做：12.x 无安装包、无同主版本实证，所以自动采纳路径只有单测覆盖（`test_jianying_versions.py`），没有真机样本。
