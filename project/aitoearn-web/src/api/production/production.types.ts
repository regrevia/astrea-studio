/**
 * 制作任务（production）相关类型。
 *
 * 字段与后端 apps/aitoearn-server/src/core/production 的返回保持一致；
 * 只保留 studio 面板实际读取的字段，避免整条 ledger 结构泄漏到前端。
 */

/** 任务状态。 */
export type ProductionRunStatus
  = | 'queued'
    | 'running'
    | 'succeeded'
    | 'failed'
    | 'canceled'

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
  duration?: number
  canvas?: ProductionCanvas
  platforms?: Record<string, ProductionPlatformVideo>
  copyCheck?: ProductionCopyCheck
}

/** 文案体检里的一条提醒。level 为 warn 才值得上屏，info 是可选建议。 */
export interface ProductionCopyFinding {
  code?: string
  level?: string
  where?: string
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
  /** 渲染完成后的产物；排队/渲染中为 undefined。 */
  output?: ProductionRunOutput
  lastError?: string
  createdAt?: string
  updatedAt?: string
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

/** 发布台账状态。submission_unknown 表示回执不明，不可自动重投。 */
export type PublicationStatus
  = | 'planned'
    | 'submitting'
    | 'submitted_unverified'
    | 'published'
    | 'submission_unknown'
    | 'failed'
    | 'canceled'

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

/** 能力快照。 */
export interface Inventory {
  platforms: PlatformInfo[]
  summary: { total: number, ready: number, accounts: number }
  voices: { jianying: VoiceInfo[], edge: VoiceInfo[] }
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

/** 发一条消息的返回。 */
export interface AssistantReply {
  reply: AssistantMessage
}
