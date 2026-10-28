/**
 * 制作任务（production）相关类型。
 *
 * 字段与后端 apps/aitoearn-server/src/core/production 的返回保持一致；
 * 只保留 studio 面板实际读取的字段，避免整条 ledger 结构泄漏到前端。
 */

/**
 * 任务状态。与后端 ProductionRunStatus 枚举一一对应：
 * draft 是还没排期的草稿，waiting_user 是流水线停下来等人决定，
 * 两者都不能算进「已完成」。取消的英文是 cancelled（不是 canceled）。
 */
export type ProductionRunStatus
  = | 'draft'
    | 'queued'
    | 'running'
    | 'succeeded'
    | 'failed'
    | 'waiting_user'
    | 'cancelled'

/** 制作项目。 */
export interface ProductionProject {
  id: string
  name: string
  sourceRepo?: string
  materialGroupId?: string
  createdAt?: string
  updatedAt?: string
}

/** 单个平台的成片产物。canvas 是母版画幅，这里是按平台重新交付后的成片。 */
export interface ProductionPlatformVideo {
  /** 对象存储里的产物 key，发布阶段直接拿它投递。 */
  key: string
  url: string
  width: number
  height: number
  bytes: number
}

/**
 * 任务产物。后端 completeJob 会把 worker 回传的 deliveries 归一化到这里：
 * 母版走 videoUrl/videoPath，各平台成片走 platforms[platform]。
 */
/** 母版画幅。后端存的是对象而不是 'portrait' 之类的预设名，展示时要能直接读宽高。 */
export interface ProductionCanvas {
  width: number
  height: number
  fps?: number
}

export interface ProductionRunOutput {
  videoPath?: string
  videoUrl?: string
  /** 成片是用户自己在剪映里导出、平台认领入库的：没有母版渲染，也没有待办导出。 */
  claimed?: boolean
  /** 剪映草稿目录；认领成片按它的时长/画布/帧率做体检。 */
  draftPath?: string
  /** 需要人工在剪映点导出时为 true；此时没有 videoPath。 */
  requiresManualExport?: boolean
  /** 停下的原因码，例如 screen_locked / export_user_busy。 */
  manualExportReason?: string
  duration?: number
  canvas?: ProductionCanvas
  platforms?: Record<string, ProductionPlatformVideo>
  copyCheck?: ProductionCopyCheck
  videoQa?: ProductionVideoQa
  /** 成片入库后的文件落点（本机绝对路径），库里按规范命名，发布与归档都认这条路径。 */
  libraryPath?: string
  /** 库里的资产 id（文件名去后缀），用于跨任务引用同一条成片。 */
  libraryAssetId?: string
  /** 入库时算的 md5，校验成片没被替换。 */
  md5?: string
  /** 母版 + 各平台规格的入库清单。 */
  libraryEntries?: ProductionLibraryEntry[]
}

/** 一条入库记录：kind=master 是母版，kind=platform 是某个平台的规格。 */
export interface ProductionLibraryEntry {
  kind?: string
  platform?: string
  path?: string
}

/** 文案体检里的一条提醒。level 为 warn 才值得上屏，info 是可选建议。 */
export interface ProductionCopyFinding {
  code?: string
  level?: string
  where?: string
  /** 出问题的秒数；有它就能直接跳到母版那一帧。 */
  at?: number
  detail?: string
}

/** worker 出片时顺手跑的文案体检；ok=false 表示有值得看一眼的地方。 */
export interface ProductionCopyCheck {
  ok?: boolean
  findings?: ProductionCopyFinding[]
  metrics?: {
    sentences?: number
    avg_len?: number
    min_len?: number
    max_len?: number
    spread?: number
  }
}

/** 出片体检：时长、黑场、卡面缺字、人物缺席。ok=false 表示画面有硬伤。 */
export interface ProductionVideoQa {
  ok?: boolean
  duration?: number
  findings?: ProductionCopyFinding[]
}

/** 制作任务。 */
export interface ProductionRun {
  id: string
  projectId: string
  title: string
  brief?: string
  status: ProductionRunStatus
  currentStage?: string
  progress?: number
  revision?: number
  materialId?: string
  videoUrl?: string
  duration?: number
  /**
   * 渲染通道参数。`engine` 决定出片形态：`jianying` 进剪映草稿箱，
   * `timeline` 直接出 mp4。后端按它派切渲染 job，前端据它区分产物类型。
   */
  spec?: ProductionRunSpec
  /** 阶段留痕：改稿、重试、人工干预都在这里追加一条。 */
  checkpoints?: ProductionCheckpoint[]
  /** 渲染完成后的产物；排队/渲染中为 undefined。 */
  output?: ProductionRunOutput
  lastError?: string
  createdAt?: string
  updatedAt?: string
}

/** 本机渲染队列（8899）的一条任务，字段和队列返回一致，别在这里改名。 */
export interface RenderJob {
  id: string
  kind: string
  status: string
  progress: number
  stage: string
  error?: string
  result?: {
    files?: string[]
    duration?: number
    width?: number
    height?: number
  }
}

/** 渲染通道参数（后端 spec 只透传，形状由各表单决定）。 */
export interface ProductionRunSpec {
  engine?: string
  template?: string
  mode?: string
  [key: string]: unknown
}

/** 一次阶段留痕。改稿会带 note，例如 `revision 2`。 */
export interface ProductionCheckpoint {
  at?: string
  stage?: string
  note?: string
}

/** 任务事件（阶段 / 计时 / 错误）。 */
export interface ProductionEvent {
  seq?: number
  kind: string
  stage?: string
  message?: string
  at?: string
}

/** 分页结果。 */
export interface PageResult<T> {
  list: T[]
  total: number
  pageNo: number
  pageSize: number
}

/**
 * 发布台账状态。与后端 PublicationStatus 枚举一一对应：
 * submission_unknown 表示回执不明，不可自动重投；waiting_user 是等人补齐账号或确认。
 */
export type PublicationStatus
  = | 'planned'
    | 'submitting'
    | 'submitted_unverified'
    | 'submission_unknown'
    | 'reconciling'
    | 'scheduled_remote'
    | 'reviewing'
    | 'published'
    | 'rejected'
    | 'waiting_user'
    | 'not_submitted'

/** 发布尝试记录。 */
export interface PublicationAttempt {
  id: string
  runId: string
  platform: string
  accountRef?: string
  accountName?: string
  status: PublicationStatus
  workUrl?: string
  workId?: string
  plannedAt?: string
  createdAt?: string
}

/** 健康检查里的 worker 行。 */
export interface ProductionWorker {
  name: string
  online: boolean
  capabilities?: string[]
  lastHeartbeatAt?: string
}

/**
 * 渲染作业。失败重试会新增一条；result 里是 worker 回传的原始产物
 * （videoKey/videoUrl/duration/canvas/deliveries），与 run.output 的归一化结果对应。
 */
export interface ProductionJob {
  id?: string
  runId?: string
  kind?: string
  status?: string
  attempt?: number
  stage?: string
  result?: Record<string, unknown>
  createdAt?: string
  updatedAt?: string
  [key: string]: unknown
}

/** 任务详情：任务本体在 run 里，其余是附带的时间线。 */
export interface RunDetail {
  run: ProductionRun
  jobs: ProductionJob[]
  attempts: PublicationAttempt[]
  events: ProductionEvent[]
}

/* ------------------------------------------------------------------ 时间轴输入
 * POST production/runs 的 spec 用「友好输入」，前端不拼 timeline 内部细节：
 * 画布、切片、字幕时序、BGM 轨道都由后端 timeline-spec builder 展开。
 */

/** 画幅。portrait=1080x1920，landscape=1920x1080，square=1080x1080。 */
export type TimelineCanvas = 'portrait' | 'landscape' | 'square'

/** 时间轴任务的友好输入 spec。script 每项一句，materials 是相对 assetBase 的文件名。 */
export interface TimelineSpecInput {
  engine: 'timeline'
  mode: 'script'
  /** worker 本机可读的素材根目录（绝对路径）。 */
  assetBase: string
  materials: string[]
  script: string[]
  canvas: TimelineCanvas
  /** 每句文案的时长（秒）。 */
  clipSeconds?: number
  platforms: string[]
  /** 素材与画布的适配方式，默认 blur。 */
  fit?: string
  bgm?: { src: string, volume?: number }
}

/* ------------------------------------------------------------------ 口播出片输入
 * 同一条 timeline 通道的竖屏脚本分支：worker 按脚本文本现场出卡面、立绘和逐屏配音，
 * 前端只发稿子，不用先把时间轴算好。
 */

/** 口播出片 spec。points 每项「标题::镜头注解::口播句」，第三段就是这一屏念的句子。 */
export interface VerticalShortSpecInput {
  engine: 'timeline'
  mode: 'vertical_short'
  template: 'factcard'
  /** 顶栏主题；任务标题通常也用它。 */
  topic: string
  /** 开场钩子，写一句反差；缺省时 worker 用通用面试题文案。 */
  hook?: string
  /** 每屏一条「标题::镜头注解::口播句」。 */
  points: string[]
  /** 收尾一句。 */
  cta?: string
  /** 音色：`edge:zh-CN-XiaoxiaoNeural@rate=0.98` 或 `sami:BV411_streaming`。 */
  voice: string
  /** 系列名，显示在卡面左上角。 */
  kicker?: string
  /** 逐屏口播；points 第三段与之对应，缺省时 worker 按第三段念。 */
  narration?: { intro?: string, points?: string[], outro?: string }
  /** 素材：绝对路径或 `pack:包名/文件名`，原样交给渲染进程。 */
  materials?: string[]
  /** 目标平台标识，渲染后按平台各交付一份。 */
  platforms?: string[]
}

/* ------------------------------------------------------------------ 干货卡输入
 * 「干货卡一键出片」用 factcard 模板：选题 + 开场钩子 + 「标题::说明」要点 + 音色。
 * 卡面出图、逐屏字幕、转场和配音全部由 worker 的 factcard 模块展开，
 * 前端只负责把用户填的内容按这个结构提交。
 */

/** 干货卡出片通道：剪映草稿，或后台直接渲染 mp4。 */
export type FactCardEngine = 'jianying' | 'timeline'

/** 干货卡可用音色。BV405 甜美解说（女声），BV411 解说小帅（男声）。 */
export type FactCardVoice = 'sami:BV405_streaming' | 'sami:BV411_streaming'

/** 干货卡 spec。points 每项是「标题::说明」，建议 2-4 条。 */
export interface FactCardSpec {
  /**
   * 渲染通道按 `run.spec.engine` 派切：jianying 出剪映草稿，timeline 直接出 mp4。
   * 顶层 engine 会被接口 DTO 丢掉，所以 spec 里必须再带一份。
   */
  engine: FactCardEngine
  template: 'factcard'
  /** timeline 通道：worker 按脚本现场出卡面与配音，不用前端算时间轴。 */
  mode?: 'vertical_short'
  /** 选题，同时作为开场字幕。 */
  topic: string
  /** 开场钩子，留空由 worker 用默认钩子。 */
  hook?: string
  /** 要点，每项「标题::说明」（英文双冒号）。 */
  points: string[]
  /** 收尾口播。 */
  cta: string
  voice: FactCardVoice
  /** 系列名，显示在卡面左上角。 */
  kicker: string
  /** 开场口播，留空由 worker 用「选题。钩子」。要点口播随 points 第三段下发。 */
  narration?: {
    intro?: string
  }
  /** 主角服装：不传就用默认那套动作最全的。 */
  character?: {
    outfit?: string
  }
}

/** 创建干货卡任务的请求体。 */
export interface CreateFactCardRunInput {
  engine: FactCardEngine
  template: 'factcard'
  projectId: string
  title: string
  brief: string
  spec: FactCardSpec
}

/** POST production/runs 的返回：只回任务句柄，渲染异步进行。 */
export interface CreateRunResult {
  runId: string
  status: string
  revision: number
  statusUrl: string
}

/* ------------------------------------------------------------------ 能力清单
 * 来自宿主机的 capabilities_server.py（GET /inventory），不是后端 API。
 * 这些是宿主事实：cookie 落盘状态、可用的 TTS 音色、工具链版本。
 * 面板跨域直读 http://127.0.0.1:8899/inventory。
 */

/** 一个已登录的账号。 */
export interface PlatformAccountInfo {
  account: string
  cookieBytes: number
  hasNote: boolean
  mtime: string
  cookieFile: string
}

/** 一个平台的可发布状态。 */
export interface PlatformInfo {
  platform: string
  label: string
  ready: boolean
  accounts: PlatformAccountInfo[]
  supportsNote: boolean
}

/** 配音音色。 */
export interface VoiceInfo {
  id: string
  name: string
  voice?: string
}

/** 成片的主角：虚拟形象 / 真人素材 / 数字人。 */
export interface CharacterInfo {
  id: string
  label: string
  kind: 'avatar' | 'person' | 'digital_human'
  voice: string
  alpha: boolean
  clips: number
  source: 'builtin' | 'custom'
}

/** 主角能换的服装：动作齐全的先把动作表用满，只有主图的照样能选。 */
export interface OutfitInfo {
  id: string
  label: string
  complete: boolean
}

/** 一套衣服的素材状态：动作和口型齐不齐、要不要另叠表情层。 */
export interface AvatarOutfit {
  id: string
  label: string
  mode: 'baked' | 'split'
  poses: string[]
  mouths: string[]
  has_expression_layer: boolean
  anchor_count: number
}

/** 主角素材模型。动作与表情共用一套，服装只提供身体。 */
export interface AvatarModel {
  id: string
  name: string
  default_outfit: string
  poses: string[]
  expressions: string[]
  outfits: AvatarOutfit[]
  root?: string
}

/** 素材包：本地目录里按主题收好的一批图，spec 里用 pack:<slug>/<名字> 引用。 */
export interface MaterialPack {
  slug: string
  shots: number
  refs: string[]
}

/** 能力快照。 */
export interface Inventory {
  platforms: PlatformInfo[]
  summary: { total: number, ready: number, accounts: number }
  voices: { jianying: VoiceInfo[], edge: VoiceInfo[] }
  /** 素材包清单；老版本能力服务没有这一段。 */
  materials?: { root: string, packs: MaterialPack[] }
  tools: Record<string, { available: boolean, version: string }>
  generatedAt?: string
}

/* ------------------------------------------------------------------ AI 指挥台
 * 指挥台是「一句话跑完整条流水线」的会话接口：用户说人话，后端拆步骤并逐步回报。
 * 步骤用 AssistantAction 表示，前端只负责渲染成状态卡片，不参与编排。
 */

/** 会话里一条消息的作者。 */
export type AssistantRole = 'user' | 'assistant'

/** 步骤类型：与流水线阶段一一对应。 */
export type AssistantActionType
  = | 'research'
    | 'material'
    | 'run'
    | 'render'
    | 'draft'
    | 'publish'
    | 'info'

/** 步骤状态。running 表示还在跑，前端展示转圈。 */
export type AssistantActionStatus = 'running' | 'ok' | 'failed'

/** 指挥台回报的一个步骤。link 是产物页地址（成片、草稿、发布回执）。 */
export interface AssistantAction {
  id: string
  type: AssistantActionType
  status: AssistantActionStatus
  label: string
  detail?: string
  link?: string
}

/** 一条会话消息。assistant 消息可以带若干步骤卡片。 */
export interface AssistantMessage {
  id: string
  role: AssistantRole
  content: string
  createdAt: string
  actions?: AssistantAction[]
}

/** 新建会话的返回。 */
export interface AssistantSession {
  id: string
  title: string
  createdAt: string
}

/** 读取会话的返回：历史消息按时间正序。 */
export interface AssistantSessionDetail {
  id: string
  title: string
  messages: AssistantMessage[]
}

/** 面板上自己填的推理端点，只在本机保存，随每次发送带给后端。 */
export interface AssistantAgentEndpoint {
  baseUrl?: string
  apiKey?: string
  model?: string
  wire?: 'openai' | 'anthropic'
}

/** 发一条消息的返回。 */
export interface AssistantReply {
  reply: AssistantMessage
}
