# 后端 AI 能力面清点（对话 → 素材 → 草稿 → 配音 → 剪映 → 发布）

> 只读清点，未改动任何源码。范围：`apps/aitoearn-ai`、`apps/aitoearn-server`、`libs/*`（含 `libs/aitoearn-ai-client`、`libs/nest-mcp`），以及本地 mac worker（仅用于确认「能力实际在哪一侧」）。
>
> 行格式：`METHOD 路径 | 鉴权 | 入参要点 | 出参要点 | 证据 file:line`

## 0. 路径与鉴权约定（先读这一节，后面路径都按此拼）

- 全局前缀：本机部署 `globalPrefix = /api`（证据：`apps/aitoearn-server/src/core/production/production.controller.ts:194` 返回的 `statusUrl: '/api/production/runs/:id'`；前缀由 `libs/common/src/starter.ts:161-163` 应用）。
- URI 版本：`app.enableVersioning({ type: VersioningType.URI })` 且未设 `defaultVersion`（`libs/common/src/starter.ts:160`），因此只有显式声明 `version` 的 controller 才带版本段——`channels/publish/*` 是 `v2`（`.../publish/publish-flow.controller.ts:10`），其余（`ai/*`、`agent/*`、`material/*`、`media`、`production/*`、`unified/*`）= `/api/<path>`，**没有 v1**。
- 鉴权四种，全部由全局 `AitoearnAuthGuard`（`AitoearnAuthModule.forRootAsync` 注册为 `APP_GUARD`）统一判定：
  - 默认：`Authorization: Bearer <JWT>`，`@GetToken()` 取 `token.id`；
  - `x-api-key: ai_xxx` 头（API Key 走同一 guard）；
  - `@Public()`（`@yikart/aitoearn-auth`）：不需要用户身份；
  - `@Internal()`：`Authorization` 必须等于配置里的 `internalToken`，否则 401（服务间调用用）；
  - Worker 面：`@Public()` + `x-worker-token` 头，且 token 必须与 `register` 时下发的一次性 token 对应（`production.controller.ts:298-308`）。
- 两个服务：`aitoearn-ai`（AI 侧，:3000 类端口）与 `aitoearn-server`（站点侧）。站点侧调 AI 侧只有三条内部通道（`libs/aitoearn-ai-client/src/clients/ai.service.ts:14/23/32` → `POST /internal/ai/draft-generation/{v2,image-text,task}`）。

## 1. AI 对话（`apps/aitoearn-ai`）

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `GET /api/ai/models/chat` | `@Public` | query `channel`、`scene` | `ChatModelConfigVo[]`（只回 `ModelsConfigService.config.chat` 里匹配的条目） | `chat.controller.ts:20-21` |
| `POST /api/ai/chat` | JWT/API Key | `chatCompletionDtoSchema`：`messages[{role, content}]`（content 支持 `image_url`）、`model`、`temperature`、`maxTokens`、`maxCompletionTokens`、`modalities`、`topP`、`modelKwargs` | `ChatCompletionVo`（OpenAI 兼容）；**无 `tools`/function-calling 字段** | `chat.controller.ts:41`；`libs/aitoearn-ai-shared/src/dtos/chat.dto.ts:38` |
| `POST /api/ai/chat/stream` | JWT/API Key | `chatStreamProxyDtoSchema`（`z.looseObject`，未声明字段原样透传） | SSE 分片：`{type:'content'}` / `{type:'complete'}` | `chat.controller.ts:57`；`chat.dto.ts:35`；`vos/chat.vo.ts:41` |
| `POST /api/ai/chat/claude` | JWT/API Key | `claudeChatProxyDtoSchema`，`max_tokens` 默认 32000 | SSE（Claude 原始事件流） | `chat.controller.ts:74`；`chat.dto.ts:17` |
| `GET /api/ai/logs` | JWT | 分页 | AI 调用日志 | `ai/logs/logs.controller.ts:10-19` |

要点（会影响「统一面板」设计）：
- `chat.service.ts` 每次请求都用 `getChatModelConfig(channel, scene)` 校验 model，非法抛 `InvalidModel`（`chat.service.ts:186`、`:215`）。
- **没有任何会话/上下文持久化**：只有 `aiLogRepo.create({ type: AiLogType.Chat, ... })` 记账（`chat.service.ts:186` 起），历史必须客户端每次全量重发 `messages`。
- **没有工具调用**：DTO 无 `tools` 字段，唯一逃生口是 `modelKwargs` 透传。所以「对话里直接驱动整条流水线」靠 chat 端点做不到。

## 2. Agent（长任务 + MCP 工具集，`apps/aitoearn-ai`）

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `POST /api/agent/tasks` | JWT | `CreateContentGenerationTaskSchema`：`prompt`（≤4000 或 ≤20 个 content block：text/image/video/document）、`model`（限 `config.agent.models`，默认 `defaultModel`）、`includePartialMessages`、`taskId`（续跑） | **SSE**，边跑边推，末尾 `outputTaskResult` 落结果 | `agent.controller.ts:66`；`agent.dto.ts`（`CreateContentGenerationTaskSchema` L88） |
| `GET /api/agent/tasks` | JWT | `keyword`、`favoriteOnly`、分页 | 任务列表 | `agent.controller.ts:88` |
| `GET /api/agent/tasks/:taskId` | JWT | — | 任务详情 | `agent.controller.ts:102` |
| `GET /api/agent/tasks/:taskId/messages` | JWT | `lastMessageId` 增量拉 | 消息流 | `agent.controller.ts:117` |
| `PATCH /api/agent/tasks/:taskId` | JWT | 标题等 | — | `agent.controller.ts:144` |
| `DELETE /api/agent/tasks/:taskId` | JWT | — | — | `agent.controller.ts:131` |
| `POST /api/agent/tasks/:taskId/abort` | JWT | — | 中断运行 | `agent.controller.ts:202` |
| `POST /api/agent/tasks/:taskId/rating` | JWT | 1–5 分 | — | `agent.controller.ts:158` |
| `POST /api/agent/tasks/:taskId/favorite` / `DELETE .../favorite` | JWT | — | 收藏开关 | `agent.controller.ts:218/230` |
| `POST /api/agent/tasks/:taskId/share` | JWT | `ttl` 60s–30d | 分享 token | `agent.controller.ts:173` |
| `GET /api/agent/tasks/shared/:token` | `@Public` | — | 只读任务 | `agent.controller.ts:190` |

运行时（`agent-runtime.service.ts`，1049 行）：Claude Code SDK 子进程，经本地 `claude-code-router`（`127.0.0.1:3456`），in-process MCP server 提供工具；`permissionMode:'default'`，`canUseTool` 全放行，`PostToolUse` hook 把 `[Resource link: Image…]` 文本重写成 image block；子 agent 有 `polling-task`、`skill-analyzer`。工具白名单：`generateAllowedTools` 把 MCP server 映射成 `mcp__<server>__<tool>`（`agent-runtime.service.ts:143`）。

Agent 已挂的 MCP 工具（这是「AI 已经能自己干到哪一步」的真实边界）：

- 素材/媒体：`generateImage`、`generateVideo`、`getVideoStatus`、`createSoraCharacter`、`getSoraCharacter`、`generateVideoWithGrok`、`getGrokVideoStatus`（`mcp/media.mcp.ts:57`）；`submitAideoTask`、`getAideoTaskStatus`（`mcp/volcengine/aideo.mcp.ts:20`）。
- 剪辑：`compositeImages`、`resizeImage`、`transformImage`、`adjustImage`、`getImageMetadata`（`mcp/image-edit.mcp.ts:108`）；`submitDirectEditTask`、`getVideoEditTaskStatus`（`mcp/volcengine/common.ts:207`）；`getVolcVideoInfo`、`uploadAndGetVid`、`probeVideoMetadata`、`extractThumbnail`、`generateSubtitle`（`mcp/video-utils.mcp.ts:46`）；`generateSubtitle`（`mcp/subtitle.mcp.ts:21`）；`drama-recap`（`mcp/drama-recap.mcp.ts:38`）、`style-transfer`（`mcp/style-transfer.mcp.ts:21`）。
- 发布（关键：**已打通**）：`createChannelPublishFlow`、`listChannelPublishRecords`、`getChannelPublishRecordByRecordId/ByTaskId/ByFlowId`、`publishChannelTaskNow`、`cancelChannelPublishTask`、`updateChannelPublishAt`、`requestChannelPublishUpdate`、`listChannelPlatforms`、`getChannelPlatform`、`listBilibili/YoutubeChannelPlatformCategories`、`getChannelWorkLinkInfo`、`getChannelWorkDetail`（`agent.constants.ts:22`）。
- 结果回传：`outputTaskResult`、`setTitle`、`getCurrentTime`、`wait`（`mcp/util.mcp.ts:17/22`）。

**没有的工具**：TTS/配音合成、剪映 draft 渲染、production run 创建/推进（这三块在站点侧，见 §7）。

## 3. 素材（AI 生成资产，`apps/aitoearn-ai`）

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `GET /api/ai/models/image/generation` | `@Public` | — | `ImageGenerationModelParamsVo[]` | `ai/image/image.controller.ts:17-18` |
| `GET /api/ai/models/image/edit` | `@Public` | — | 同上（编辑类模型） | `image.controller.ts:31-32` |
| `POST /api/ai/image/generate` | JWT | `imageGenerationSchema`：`prompt`(≤4000)、`model`、`n`(1–10)、`quality`、`response_format`(`url`/`b64_json`)、`size`、`style`、`user` | `ImageResponseVo` | `image.controller.ts:46`；`libs/aitoearn-ai-shared/src/dtos/image.dto.ts:5` |
| `POST /api/ai/image/edit` | JWT | 图片 + 编辑指令 | 同上 | `image.controller.ts:64` |
| `POST /api/ai/image/generate/async` | JWT | 同 generate | `AsyncTaskResponseVo`（异步任务） | `image.controller.ts:82` |
| `POST /api/ai/image/edit/async` | JWT | — | 同上 | `image.controller.ts:100` |
| `GET /api/ai/image/task/:logId` | JWT | — | `TaskStatusResponseVo`（生成图 url 落素材库） | `image.controller.ts:117` |
| `GET /api/ai/models/video/generation` | `@Public` | — | 视频模型 + 参数 | `ai/video/video.controller.ts:23-24` |
| `POST /api/ai/video/generations` | JWT | `videoGenerationRequestSchema`：`model`、`prompt`、`groupId`（成功后自动写进该素材组）、`image`/`image_tail`/`video_url`/`images`/`videos`/`audios`、`mode`、`size`、`resolution`、`ratio`、`duration`、`seed`、`watermark`、`tools[{type:'web_search'}]`、`metadata` | `VideoGenerationResponseVo` | `video.controller.ts:38`；`libs/aitoearn-ai-shared/src/dtos/video.dto.ts:9` |
| `GET /api/ai/video/generations/:taskId` | JWT | — | 单任务状态 | `video.controller.ts:55` |
| `GET /api/ai/video/generations` | JWT | 分页 | 列表 | `video.controller.ts:73` |
| `POST /api/ai/openai/videos`、`GET /api/ai/openai/videos/:videoId`、`POST .../remix` | JWT | Sora 原生形状 | — | `ai/video/openai/openai.controller.ts:19/36/50` |
| `POST /api/ai/volcengine/video`、`GET /api/ai/volcengine/video/:taskId` | JWT | 火山原生形状 | — | `ai/video/volcengine/volcengine.controller.ts:19/36` |
| `POST /api/ai/aideo/tasks`、`GET /api/ai/aideo/tasks/:taskId`、`GET /api/ai/aideo/tasks` | JWT | Aideo 任务 | 提交/状态/列表 | `ai/aideo/aideo.controller.ts:27/44/62` |
| `GET /api/ai/assets/` | JWT | `type` ∈ `AssetType`（AiImage/AiVideo/AiCard/AiChatImage/AideoOutput/VideoEdit/DramaRecap/StyleTransfer/ImageEdit）、`status` ∈ `Confirmed`/`Uploaded` | `AssetListVo` | `ai/assets/assets.controller.ts:12-21` |

结论：**「素材生产」这一层已经齐全**（文生图/图生图/异步、文生视频/图生视频/首尾帧/参考视频、Sora、火山、Aideo、通用素材检索），唯一没有的是「音频/配音素材」。

## 4. 内容草稿生成（文案+素材打包，`apps/aitoearn-ai`）

> ⚠️ 命名陷阱：这里的「draft」是**平台内容草稿**（标题/正文/话题/视频，落到站点素材组），**不是剪映工程文件**。剪映 draft 见 §7 的 `jianying_draft` job。

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `POST /api/ai/draft-generation/v2` | JWT | 校验 model → 解析素材组 → AiLog → 入队（`version=v2`）；Gemini Flash 出 prompt+metadata → 生成视频 → 抽帧做封面 → 存素材 | `CreateDraftGenerationVo { taskIds }` | `draft-generation.controller.ts:166` |
| `POST /api/ai/draft-generation/image-text` | JWT | 图文版 | 同上 | `draft-generation.controller.ts:~178` |
| `GET /api/ai/draft-generation` | JWT | 分页 | 任务列表 | `draft-generation.controller.ts:60` |
| `POST /api/ai/draft-generation/query` | JWT | 复合筛选 | 列表 | `draft-generation.controller.ts:75` |
| `GET /api/ai/draft-generation/stats` | JWT | — | 统计 | `draft-generation.controller.ts:46` |
| `GET /api/ai/draft-generation/:id` | JWT | — | 单任务（`generating`/`success`/`failed`） | `draft-generation.controller.ts:144` |
| `GET /api/ai/draft-generation/pricing` | `@Public` | — | 计费表 | `draft-generation.controller.ts:90` |
| `GET /api/ai/draft-generation/memory`、`DELETE /memory/items/:itemId`、`POST /memory/regenerate` | JWT | 记忆库 | — | `draft-generation.controller.ts:101/117/126` |
| `POST /api/internal/ai/draft-generation/v2` | `@Internal` | 同上 + body 里带 `userId`/`userType` | 同上 | `core/internal/draft-generation.controller.ts:29` |
| `POST /api/internal/ai/draft-generation/image-text` | `@Internal` | 同上 | 同上 | `core/internal/draft-generation.controller.ts:40` |
| `POST /api/internal/ai/draft-generation/task` | `@Internal` | `taskId` | 任务状态 | `core/internal/draft-generation.controller.ts:51` |

用户的另一个入口是站点侧 MCP 工具 `createVideoDraft` / `createImageTextDraft` / `getDraftTaskStatus`（`apps/aitoearn-server/src/core/unified-mcp/draft-generation.mcp.controller.ts:15/30/45`，内部转成上面三条 `@Internal` 路径）。

## 5. TTS / 配音 —— **后端没有合成能力**

清点结果（这是本次最重要的缺口）：

- 后端全仓没有 TTS：`grep -rniE '\btts\b|edge-tts|text_to_speech|textToSpeech|配音'` 只命中 agent 的技能名文案（`agent.constants.ts:112`、`:428` 的 `translating-videos` 技能描述，属提示词，不是接口）。
- `inventory.voices`（「剪映原生 14 / Edge TTS 9」）**来自本地 mac worker，不是后端**：`integrations/mac-worker/capabilities.py:157 jianying_voices()`（正则解析 `sami_tts.py` 的 `SPEAKER_ALIASES`）、`:178 edge_voices()`（9 个硬编码 zh-CN 音色）、`:221` 组装 `payload["voices"] = {"jianying":…, "edge":…}`。
- 暴露方式只有 `GET /inventory`（别名 `/capabilities`）和 `GET /healthz`——`capabilities_server.py:57-66` 的 `do_GET`，**没有 POST，没有 `/tts` 合成路由**（`do_OPTIONS` 在 `:54`，只回 CORS 预检）。
- 前端直连读它：`project/aitoearn-web/src/api/production/production.api.ts:89-95` 注释写死「这不是后端 API，而是宿主上 `capabilities_server.py` 的 `GET /inventory`」，`fetch(`${base}/inventory`)`，`base = http://127.0.0.1:8899`；类型 `voices:{jianying:VoiceInfo[], edge:VoiceInfo[]}` 在 `production.types.ts:175-209`。
- 真正出声的地方在渲染管线内部：`integrations/mac-worker/slides_render.py:233 _tts()` 用子进程调 MPT venv 里的 `app/services/sami_tts.py`（`sami_tts(text, voice, 1.0, mp3)`），失败抛 `SlidesError`。也就是**音色只能在提交渲染任务时传参，不能单独试听/单独出音频**。

⇒ 面板要做「选音色 → 试听 → 再合成」，后端目前一行都不支持；要么加一个转发到 worker 的合成端点，要么把「试听」也交给渲染任务（成本高、慢）。

## 6. 发布（两条并存的路，别重复造）

### 6.1 站点侧正规发布（推荐，`v2`）

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `POST /api/v2/channels/publish/flows` | JWT | `CreatePublishFlowSchema`：`flowId?`、`content: {title, body, media[], cover}`、`publishAt`（coerce Date）、`context?{taskId, materialGroupId, materialId, source}`、`items[]{accountId, platform: AccountType, option, overrides}`（≥1） | 一次请求生成多个单平台任务 | `publish/publish-flow.controller.ts:20`；`publish-flow.dto.ts:15/22/31`；`schemas/publish-content.schema.ts` |
| `GET /api/v2/channels/publish/flows/:flowId` | JWT | — | flow 及子任务状态 | `publish-flow.controller.ts:34` |
| `POST /api/v2/channels/publish/tasks/:taskId/publish-now` | JWT | — | 立即发 | `publish-task.controller.ts:19` |
| `POST /api/v2/channels/publish/tasks/:taskId/retry` | JWT | — | 重试 | `:33` |
| `PATCH /api/v2/channels/publish/tasks/:taskId/publish-at` | JWT | 新时间 | 改定时 | `:62` |
| `POST /api/v2/channels/publish/tasks/:taskId/update` | JWT | 内容覆盖 | 改内容 | `:78` |
| `DELETE /api/v2/channels/publish/tasks/:taskId` | JWT | — | 取消 | `:47` |
| `GET /api/v2/channels/publish/records`、`/queued`、`/published`、`/:recordId` | JWT | 分页/筛选 | 发布记录 | `publish-record.controller.ts:20/40/59/132` |
| `GET /api/v2/channels/publish/records/public/:recordId` | `@Public` | — | 公开分享页数据 | `:77-78` |
| `DELETE /api/v2/channels/publish/records/:recordId`、`PATCH .../work-link`、`GET .../user-action` | JWT | — | 删记录/回填作品链接/查用户待办 | `:90/104/119` |
| `POST /api/ai/...` 侧对应 MCP 工具 | — | — | `createChannelPublishFlow` 等 15 个，见 §2 | `channels/mcp/channels.mcp.controller.ts:37-245` |

配套的草稿箱（发布内容源的来源）：

| 端点 | 鉴权 | 入参要点 | 证据 |
| --- | --- | --- | --- |
| `POST /api/material` | JWT | `CreateMaterialSchema`：`groupId`、`coverUrl`、`mediaList[]`、`title`、`desc`、`topics[]`、`type: MaterialType`、`maxUseCount`、`accountTypes[]` | `content/material.controller.ts:57-69`；`material.dto.ts:19` |
| `POST /api/material/transfer`、`DELETE /api/material/{filter,list,use-count,:id}`、`PUT /api/material/info/:id` | JWT | 移动/批量删/改 | `material.controller.ts:93/117/131/145/157/175` |
| `GET /api/material/info/:id`、`GET /api/material/optimal`、`GET /api/material/list/:pageNo/:pageSize` | `@Public`/JWT | 详情/最优草稿/分页 | `material.controller.ts:194/206/218` |
| `/api/material/group`（POST、DELETE `:id`、POST/GET `info/:id`、GET `list/...`） | JWT | 素材组 CRUD | `material-group.controller.ts:26/37/52/72/89/100` |
| `/api/media`（POST、`/transfer`、DELETE `ids`/`filter`/`:id`、GET `info/:id`/`list/...`、PUT `addUseCountOfList`） | JWT | 媒体库 CRUD | `media.controller.ts:35/48/62/86/97/107/121/127/150` |
| MCP 等价工具 | — | `createDraft`、`listDrafts`、`getDraftDetail`、`deleteDraft`、`createMedia`、`listMedia`、`listDraftGroups`、`listMediaGroups`、`getDraftGroupInfoByName`、`getMediaGroupInfoByName` | `content/content.mcp.controller.ts:82-257` |

### 6.2 站点制作台账的发布意图（`production/publications`）

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `POST /api/production/publications` | JWT | `PlanPublicationSchema`：`runId`、`platform`、`dispatcher`(`aitoearn`/`sau`/`manual`)、`accountRef`、`accountName`、`plannedAt`、`content` | `{attemptId, status, deduped}` | `production.controller.ts:244`；`libs/mongodb/src/schemas/production.schema.ts:6-10` |
| `GET /api/production/publications` | JWT | `runId?` | 尝试列表 | `production.controller.ts:251` |
| `POST /api/production/publications/:id/submit` | JWT | — | 先落库 `submitting` 再动手 | `production.controller.ts:257`；`production.service.ts:740`（只允许 `planned`/`waiting_user` 进入） |
| `POST /api/production/publications/:id/reconcile` | JWT | `RecordSubmissionSchema`：`status`(∈`PublicationStatus`, 12 态)、`remotePostId`、`remoteUrl`、`evidence`、`error` | 回写终态 | `production.controller.ts:264`；`production.schema.ts:39-51` |

**谁执行 submit？** 服务端没有任何执行器：`grep PublicationStatus.PLANNED` 只命中 `production.service.ts:717/740` 与 schema 默认值，无定时任务、无 worker 消费。实际由本地 SAU（`integrations/vendor/social-auto-upload`，`integrations/mac-worker/batch_qr.py:103` 走 `sau_cli.py`）跑浏览器，再回写 `reconcile`。前端目前只**读**台账（`production.api.ts:78`、`StudioOverview.tsx:327/439`），没有调用 submit/reconcile。

⇒ 这意味着「AI 说一句就自动发」在今天可以走 6.1（服务端真发），也可以走 6.2（人工/SAU 代发 + 台账）。**别新造第三条。**

## 7. 站点侧制作（production，`apps/aitoearn-server/src/core/production`）

用户面（`@Controller('production')`，`production.controller.ts:149`）：

| 端点 | 鉴权 | 入参要点 | 出参要点 | 证据 |
| --- | --- | --- | --- | --- |
| `GET /api/production/projects` | JWT | — | 项目列表 | `:154` |
| `POST /api/production/projects`、`POST /api/production/projects/:id` | JWT | `CreateProjectSchema`/`UpdateProjectSchema` | 项目 | `:160/166` |
| `GET /api/production/runs` | JWT | `pageNo`、`pageSize`、`status` | 分页 | `:176` |
| `POST /api/production/runs` | JWT | `CreateRunSchema`：`projectId`、`title`、`brief`、`spec`、`facts` | **只回 `{runId, status, revision, statusUrl}`**（不等渲染） | `:187`、`:191`；`production.service.ts:156-179` |
| `GET /api/production/runs/:id` | JWT | — | `{run, jobs, attempts, events}`（本体在 `.run`） | `:199` |
| `GET /api/production/runs/:id/events` | JWT | `afterSeq` | 事件增量（轮询用，非 SSE） | `:205` |
| `POST /api/production/runs/:id/revise` | JWT | `ReviseRunSchema`（`brief`/`spec`）→ 新 `revision` + 新 job | `{runId, revision, status}` | `:215`；`production.service.ts:186-201` |
| `POST /api/production/runs/:id/retry` / `cancel` | JWT | — | — | `:222/228` |
| `POST /api/production/runs/:id/material` | JWT | `AttachMaterialSchema`：`groupId`、`title`、`desc`、`topics`、`videoUrl`、`coverUrl`、`videoPath`、`duration` | `{materialId, deduped}`，成片写入草稿箱；幂等（有 `materialId` 直接返回） | `:234`；`production.service.ts:526-580` |
| `GET /api/production/health` | JWT | — | `{workers, serverTime}` | `:274` |
| `POST /api/production/workers/register` | JWT | `RegisterWorkerSchema` | 一次性 worker token | `:283` |

Worker 面（`@Controller('production/worker')`，`@Public` + `x-worker-token`，`auth()` 在 `:298`）：`POST claim`（`:311`，`lane` 默认 `render`）、`POST jobs/:id/heartbeat`（`:320`）、`POST jobs/:id/events`（`:339`）、`POST jobs/:id/complete`（`:363`）、`POST jobs/:id/fail`（`:375`）、`POST runs/:id/material`（`:393`，`WorkerMaterialSchema`，`videoKey`）、`POST capabilities`（`:413`）、`POST workers/heartbeat`（`:424`）。

渲染规格与产物：
- `createRun` 入库前把「友好输入」展开成渲染器规格（`buildTimelineSpec`，`production.service.ts:166`），job kind 由 `spec.engine` 决定：`motion`→`motion_render`、`slides`→`slides_render`、`jianying`→`jianying_draft`、`timeline`→`render_timeline`，其它 `render_mpt`。
- 产物落在 `run.output`：`videoPath`、`videoUrl`、`duration`、`canvas`、`platforms{key,url,width,height,bytes}`（`production.service.ts:80-95`）。
- 剪映：worker `worker.py:227-230` 收 `jianying_draft` → `do_jianying`（`:347`）→ `jianying_engine.render_jianying(payload, STUDIO_HOME/jianying/<jobId>)`（`jianying_engine.py:401`），内部 `build_timeline` → `POST /save_draft` → `_deploy(folder, name)`，返回 `draftPath/draftName/duration`。**站点侧只有「提交 job + 轮询事件」，没有独立的「导出剪映 draft」HTTP 端点。**
- 僵尸租约兜底：`production.scheduler.ts`（每分钟 `reapExpiredLeases`，上一轮已补）。

production MCP（`production-mcp.module.ts`，`McpModule.forRoot({name:'production', apiPrefix:'production'})`）的工具：`createProductionProject`、`listProductionProjects`、`createProductionRun`、`getProductionRun`、`listProductionRuns`、`reviseProductionRun`、`listProductionEvents`、`planPublication`、`listPublications`（`production.mcp.controller.ts:79/90/101/122/133/149/168/179/202`）。设计上**故意不给「提交发布」工具**（文件头注释 `:8-19`：「an agent cannot publish by itself」）。

## 8. MCP 面（统一面板最省力的接入点）

传输层（`libs/nest-mcp`，默认端点 `sse`/`messages`/`mcp`，`mcp.module.ts:178-180`）：

| 端点 | 鉴权 | 说明 | 证据 |
| --- | --- | --- | --- |
| `POST /api/<apiPrefix>/mcp` | JWT/API Key（继承全局 guard） | Streamable HTTP，无状态，JSON 响应 | `transport/streamable-http.controller.factory.ts:43` |
| `GET /api/<apiPrefix>/mcp` / `DELETE` | 同上 | 会话探测/释放 | `:56/65` |
| `GET /api/<apiPrefix>/sse` + `POST /api/<apiPrefix>/messages` | 同上 | 旧版 SSE 传输 | `transport/sse.controller.factory.ts:54/68` |

已注册的 MCP server（`apiPrefix`）：`unified`（= `aitoearn`，`unified-mcp.module.ts`：内容 + 草稿生成，共 13 个工具）、`content`（`content-mcp.module.ts:13`）、`channels`（`channels/mcp/channels.mcp.module.ts:13`）、`production`（`production-mcp.module.ts`）。注意 `McpRegistryService` 只扫「导入了 `McpModule` 的模块子树」，漏了 import 工具会静默消失（`production-mcp.module.ts` 注释）。

## 9. 缺口（按对「一句话跑完整条流水线」的阻塞程度排序）

1. **对话层无法触发流水线**：`POST /api/ai/chat` 无 `tools`/function-calling（`chat.dto.ts:38` 无该字段），chat 与 production/agent 之间没有任何桥。要走「对话即执行」，必须用 `POST /api/agent/tasks`（SSE）或让前端把 chat 的意图转成 MCP 调用——但这需要模型侧支持工具调用，chat 端不支持。
2. **对话无服务端会话**：`chat.service.ts` 不存 messages，多轮上下文全靠客户端重发；统一面板要「连续聊」只能自己拼历史，且无法回溯（只有 `ai/logs` 的计费流水）。
3. **TTS 完全没有后端面**：无合成端点、无试听端点；音色清单只能从 `127.0.0.1:8899/inventory` 跨域直读（GET-only，`capabilities_server.py:57`），且只能在渲染任务里用。
4. **`production` 缺三个读端点**：面板要「选平台/选账号/看产物」时，`inventory`（平台就绪度+账号+cookie）在 worker、`platforms` 只有 channels MCP 工具（`listChannelPlatforms`）、`artifact`（成片地址）只能从 `GET /production/runs/:id` 的 `run.output` 里刨。controller 里确实没有这三条路由（`production.controller.ts:149-290` 全文）。
5. **发布执行器不在服务端**：`publications/:id/submit` 之后没人跑 SAU；`PublicationStatus` 12 态里 `submitting`/`reconciling`/`submission_unknown` 需要外部回写（`StudioOverview.tsx:439` 已在展示 `submission_unknown`，说明账是虚的）。
6. **剪映 draft 只能靠 job 副作用拿**：没有「给定 spec 直接出一份 draft 文件并下载」的端点，产物只在 worker 本机 `STUDIO_HOME/jianying/<jobId>`，且 `save_draft` 之后靠 `_deploy` 落到剪映目录（`jianying_engine.py:427-441`）。
7. **两条发布路并存**：`v2/channels/publish/flows`（服务端真发）与 `production/publications`（台账+SAU 代发），语义重叠、状态机不同（一个 `ChannelPublishTaskStatus`，一个 `PublicationStatus` 12 态），面板上容易做出两套历史。

## 10. 建议的最小新增面（3–6 条，其余复用）

原则：**只补「AI 侧拿不到、前端又必须知道」的三类东西——音色、平台就绪度、产物定位**；对话编排交给已有的 `POST /agent/tasks`，发布交给已有的 `v2/channels/publish/flows`，素材/草稿/剪映都别碰。

1. `POST /api/ai/tts/preview`（JWT）——`{ text ≤200字, voice }` → `{ audioUrl, durationMs, voice }`。
   理由：唯一完全缺失的能力；包一层 worker 侧 `sami_tts`/Edge 合成即可（worker 已有 `_tts()`，`slides_render.py:233`），不动渲染管线。面板「选音色即听」必须靠它。
2. `GET /api/ai/tts/voices`（JWT）——代理 worker `GET /inventory` 的 `voices` 段并缓存。
   理由：现在前端直连 `127.0.0.1:8899`（`production.api.ts:89-95`），跨域、无鉴权、只能本地用；挪到后端一次，顺手把「音色 id → 引擎」的映射收敛。
3. `GET /api/production/inventory`（JWT）——`{ platforms[], voices, tools }`，服务端代取 worker `/inventory` + `listChannelPlatforms` 合并。
   理由：面板首页要「这家机器现在能发哪些平台、哪些账号活着」，目前全靠浏览器本地 fetch；聚合后 agent 也能通过 MCP 看到同样的就绪度（现在 `planPublication` 的 `accountRef` 靠猜，见 `production.mcp.controller.ts:54` 注释）。
4. `GET /api/production/runs/:id/artifact`（JWT）——`{ videoPath, videoUrl, duration, canvas, platforms }`，从 `run.output` 直出（`production.service.ts:80-95`）。
   理由：面板/Agent 现在都得拿整包 `runDetail`（含 jobs+attempts+events）再自己找字段，浪费且容易踩「本体在 `.run` 里」的坑（`production.api.ts:8-13` 注释已记录该坑）。
5. （可选，只在要「对话 → 一键成片」时才做）`POST /api/production/runs/from-chat`（JWT）——`{ brief | messages[], projectId, preset }` → `{runId, statusUrl}`，内部复用 `createRun` + `buildTimelineSpec`，并由服务端用 `aiClientService.chat`（或 gemini）把对话压成 `spec`。
   理由：把「AI 理解 → 渲染规格」这段留在服务端，前端就不必懂 `spec` 结构；但它只是 `createRun` 的糖，若前端已能拼 `spec`，这条可省。
6. （不建议做）`POST /api/production/publications/:id/execute`。执行器属于 SAU/worker，放服务端会破坏「agent 不能自己发布」的既有约束（`production.mcp.controller.ts:8-19`）；需要自动化就把 SAU 包成 worker capability，走 `production/worker/*`。

不做清单（已存在，别重建）：素材生产（§3）、内容草稿（§4）、草稿箱/媒体库（§6.1）、多平台发布（§6.1）、剪映渲染（§7 `jianying_draft`）、对话缓存（用 `agent/tasks` 的 messages 增量接口）、MCP 传输（§8）。
