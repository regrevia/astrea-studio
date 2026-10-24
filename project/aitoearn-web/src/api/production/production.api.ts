import type {
  AssistantReply,
  AssistantSession,
  AssistantSessionDetail,
  CharacterInfo,
  CreateFactCardRunInput,
  CreateRunResult,
  Inventory,
  PageResult,
  ProductionEvent,
  ProductionProject,
  ProductionRun,
  ProductionWorker,
  PublicationAttempt,
  RunDetail,
} from './production.types'
import http from '@/utils/request'

/**
 * 制作任务 API。
 *
 * 路径都挂在 `production/` 下（production.controller.ts）。两处返回形状容易踩：
 *
 * - `createRun` 只回 `{ runId, status, revision, statusUrl }`，不是完整 run；
 * - `getRun` 回 `{ run, jobs, attempts, events }`，真正的任务字段在 `.run` 里。
 */

/** 项目列表。 */
export function getProjects() {
  return http.get<ProductionProject[]>('production/projects')
}

/** 创建项目。 */
export function createProject(data: { name: string, sourceRepo?: string, materialGroupId?: string }) {
  return http.post<ProductionProject>('production/projects', data)
}

/** 任务列表。 */
export function getRuns(params: { pageNo?: number, pageSize?: number, status?: string } = {}) {
  return http.get<PageResult<ProductionRun>>('production/runs', params)
}

/** 任务详情。注意任务本体在 `.run`，jobs/attempts/events 是附带的时间线。 */
export function getRun(id: string) {
  return http.get<RunDetail>(`production/runs/${id}`)
}

/** 任务事件时间线。 */
export function getRunEvents(id: string) {
  return http.get<ProductionEvent[]>(`production/runs/${id}/events`)
}

/** 创建任务：只返回 runId，渲染在 worker 上异步进行，必须轮询 getRun。 */
export function createRun(data: {
  projectId: string
  title: string
  brief?: string
  spec?: Record<string, unknown>
  facts?: Record<string, unknown>
}) {
  return http.post<CreateRunResult>(
    'production/runs',
    data,
  )
}

/**
 * 干货卡一键出片：factcard 模板，worker 在宿主机逐屏出卡并配音，
 * 交付物是剪映草稿目录，直接出现在剪映草稿箱里。
 */
export function createFactCardRun(data: CreateFactCardRunInput) {
  return http.post<CreateRunResult>('production/runs', data)
}

/** 精确修改：合并 spec 补丁并重新渲染。 */
export function reviseRun(id: string, data: { brief?: string, spec?: Record<string, unknown> }) {
  return http.post<ProductionRun>(`production/runs/${id}/revise`, data)
}

/** 重试失败的任务。返回的是新 attempt 序号，不是 run。 */
export function retryRun(id: string) {
  return http.post<{ runId: string, attempt: number }>(`production/runs/${id}/retry`)
}

/** 取消任务。 */
export function cancelRun(id: string) {
  return http.post<{ runId: string }>(`production/runs/${id}/cancel`)
}

/** 发布台账列表。 */
export function getPublications(runId?: string) {
  return http.get<PublicationAttempt[]>('production/publications', runId ? { runId } : undefined)
}

/** worker 与运行时健康。 */
export function getHealth() {
  return http.get<{ workers: ProductionWorker[], serverTime: string }>('production/health')
}

/**
 * 宿主机能力快照（平台登录态 / 配音音色 / 工具链）。
 *
 * 注意：这不是后端 API，而是宿主上 capabilities_server.py 的 GET /inventory。
 * 数据本来就在宿主机上（cookie 文件、ffmpeg 二进制），进不了容器，所以直连。
 * 用原生 fetch 而非 http 实例，避免带上后端的 token/拦截器。
 */
/**
 * 新建指挥台会话。
 *
 * 这三个方法都传 silent：指挥台要把后端 message 直接贴在对话里（后端没上线时
 * 是「指挥台后端未就绪」），而不是弹一条全局网络错误提示。
 * 返回 null 表示没拿到 JSON（接口 404 / 服务没起），由调用方降级处理。
 */
export function createAssistantSession(data: { title?: string } = {}) {
  return http.post<AssistantSession>('production/assistant/sessions', data, true)
}

/** 读取会话历史（刷新后恢复上下文）。 */
export function getAssistantSession(id: string) {
  return http.get<AssistantSessionDetail>(`production/assistant/sessions/${id}`, undefined, true)
}

/** 发一条用户消息，返回指挥台的回复。 */
export function sendAssistantMessage(id: string, message: string) {
  return http.post<AssistantReply>(
    `production/assistant/sessions/${id}/messages`,
    { message },
    true,
  )
}

export async function getInventory(base = 'http://127.0.0.1:8899'): Promise<Inventory | null> {
  try {
    const res = await fetch(`${base}/inventory`, { cache: 'no-store' })
    if (!res.ok) {
      return null
    }
    return await res.json() as Inventory
  }
  catch {
    // 服务没起来不是致命错误：面板照常显示渲染与发布，只是没有能力区。
    return null
  }
}

/**
 * 可用主角清单。宿主上的 capabilities server 顺带读了
 * ~/CreatorStudio/state/characters，面板不需要知道目录结构。
 */
export async function getCharacters(base = 'http://127.0.0.1:8899'): Promise<CharacterInfo[]> {
  try {
    const res = await fetch(`${base}/characters`, { cache: 'no-store' })
    if (!res.ok) {
      return []
    }
    const payload = await res.json() as { characters?: CharacterInfo[] }
    return Array.isArray(payload.characters) ? payload.characters : []
  }
  catch {
    // 服务没起来就当没有主角可选，面板其余部分照常。
    return []
  }
}
