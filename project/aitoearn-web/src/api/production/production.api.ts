import type {
  AssistantAgentEndpoint,
  AssistantReply,
  AssistantSession,
  AssistantSessionDetail,
  AvatarModel,
  CharacterInfo,
  OutfitInfo,
  CreateFactCardRunInput,
  CreateRunResult,
  Inventory,
  PageResult,
  ProductionEvent,
  ProductionProject,
  ProductionRun,
  ProductionWorker,
  PublicationAttempt,
  RenderJob,
  RunDetail,
} from './production.types'
import { useUserStore } from '@/store/user'
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

/** 更新项目；`materialGroupId` 决定这个项目的成片进哪个草稿箱。 */
export function updateProject(id: string, data: { name?: string, sourceRepo?: string, materialGroupId?: string }) {
  return http.post<ProductionProject>(`production/projects/${id}`, data)
}

/** 任务列表。 */
export function getRuns(params: { pageNo?: number, pageSize?: number, status?: string, projectId?: string } = {}) {
  return http.get<PageResult<ProductionRun>>('production/runs', params)
}

/** 本机渲染队列最近任务：面板「进行中」用它对齐队列真实状态。 */
export function getRenderJobs(limit = 10) {
  return http.get<RenderJob[]>('production/assistant/render-jobs', { limit })
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

/** 同一份稿子排多条母版：一次出 2~3 条，挑一条发。 */
export function createFactCardRunBatch(data: CreateFactCardRunInput & { count: number, variants?: RunBatchVariant[] }) {
  return http.post<{ runIds: string[], count: number }>('production/runs/batch', data)
}

/** 同一份稿子排多条母版（口播通道）：一次入队 count 条，返回全部 runId。 */
export function createRunBatch(data: {
  projectId: string
  title: string
  brief?: string
  spec?: Record<string, unknown>
  facts?: Record<string, unknown>
  count: number
  /** 逐条错开哪几维；不传后端三维全用，第 1 条永远是原稿 */
  variants?: RunBatchVariant[]
}) {
  return http.post<{ runIds: string[], count: number }>('production/runs/batch', data)
}

export type RunBatchVariant = 'mirror' | 'opening' | 'hook'

/** 精确修改：合并 spec 补丁并重新渲染。 */
export function reviseRun(id: string, data: { brief?: string, spec?: Record<string, unknown> }) {
  return http.post<ProductionRun>(`production/runs/${id}/revise`, data)
}

/**
 * 从剪映工程收导出产物。
 * 默认只排队等人点导出；allowGui 让这台 Mac 的 worker 驱动剪映自己导出。
 */
export function startRunExport(id: string, data: { allowGui?: boolean, draftPath?: string } = {}) {
  return http.post<{ jobId: string, attempt: number, draftPath: string }>(`production/runs/${id}/export`, data)
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
export function sendAssistantMessage(
  id: string,
  message: string,
  agent?: AssistantAgentEndpoint,
) {
  return http.post<AssistantReply>(
    `production/assistant/sessions/${id}/messages`,
    agent ? { message, agent } : { message },
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
/**
 * 可用服装清单。同样来自宿主上的 capabilities server：
 * 素材目录里多一套，面板就多一个选项。
 */
export async function getOutfits(base = 'http://127.0.0.1:8899'): Promise<OutfitInfo[]> {
  try {
    const res = await fetch(`${base}/outfits`, { cache: 'no-store' })
    if (!res.ok) {
      return []
    }
    const payload = await res.json() as { outfits?: OutfitInfo[] }
    return Array.isArray(payload.outfits) ? payload.outfits : []
  }
  catch {
    // 服务没起来就按默认服装出片，不给表单加阻塞。
    return []
  }
}

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

/** 主角素材模型：动作全集、表情全集、每套服装的状态。 */
export async function getCharacterModel(
  id = 'whale',
  base = 'http://127.0.0.1:8899',
): Promise<AvatarModel | null> {
  try {
    const res = await fetch(`${base}/characters/${id}`, { cache: 'no-store' })
    if (!res.ok) {
      return null
    }
    return await res.json() as AvatarModel
  }
  catch {
    return null
  }
}

/** 装一套衣服：给了身体动作图就能上，表情沿用共用那一套。 */
export async function importCharacterOutfit(
  payload: { src: string, id: string, label?: string, force?: boolean },
  id = 'whale',
  base = 'http://127.0.0.1:8899',
): Promise<{ ok: boolean, message?: string }> {
  try {
    const res = await fetch(`${base}/characters/${id}/outfits`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    const body = await res.json().catch(() => ({})) as { error?: string }
    return res.ok ? { ok: true } : { ok: false, message: body.error || `${res.status}` }
  }
  catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : 'offline' }
  }
}

/** 换默认服装：只改一个字段，不动任何一张图。 */
export async function setCharacterOutfit(
  outfit: string,
  id = 'whale',
  base = 'http://127.0.0.1:8899',
): Promise<{ ok: boolean, message?: string }> {
  try {
    const res = await fetch(`${base}/characters/${id}/default-outfit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outfit }),
    })
    const body = await res.json().catch(() => ({})) as { error?: string }
    return res.ok ? { ok: true } : { ok: false, message: body.error || `${res.status}` }
  }
  catch (err) {
    return { ok: false, message: err instanceof Error ? err.message : 'offline' }
  }
}

/**
 * 素材原图：图片标签带不上登录头，所以取回成 blob URL，用完由调用方 revoke。
 * `rel` 是素材根目录内的相对路径。
 */
export async function fetchMaterialBlobUrl(rel: string): Promise<string | null> {
  const credential = useUserStore.getState().token
  if (!credential)
    return null
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}/production/materials/file?path=${encodeURIComponent(rel)}`,
    { headers: { Authorization: `Bearer ${credential}` } },
  )
  if (!res.ok)
    return null
  return URL.createObjectURL(await res.blob())
}
