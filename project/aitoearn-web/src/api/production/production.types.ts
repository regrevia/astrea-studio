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

/** 任务详情：任务本体在 run 里，其余是附带的时间线。 */
export interface RunDetail {
  run: ProductionRun
  jobs: Array<Record<string, unknown>>
  attempts: PublicationAttempt[]
  events: ProductionEvent[]
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

/** 能力快照。 */
export interface Inventory {
  platforms: PlatformInfo[]
  summary: { total: number, ready: number, accounts: number }
  voices: { jianying: VoiceInfo[], edge: VoiceInfo[] }
  tools: Record<string, { available: boolean, version: string }>
  generatedAt?: string
}
