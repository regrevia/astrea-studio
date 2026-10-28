import type { StudioAssistantOverride } from './studio-assistant.client'
import { randomUUID } from 'node:crypto'
import { Injectable } from '@nestjs/common'
import { AppException, ResponseCode } from '@yikart/common'
import {
  ProductionAssistantAction,
  ProductionAssistantActionStatus,
  ProductionAssistantActionType,
  ProductionAssistantMessage,
  ProductionAssistantRole,
  ProductionAssistantSessionRepository,
  ProductionRunStatus,
  PublicationDispatcher,
} from '@yikart/mongodb'
import { z } from 'zod'
import { config } from '../../config'
import {
  ASSISTANT_TOOLS,
  AssistantToolName,
  cancelRenderJobArgsSchema,
  createJianyingDraftArgsSchema,
  createProjectArgsSchema,
  createRunArgsSchema,
  createRunBatchArgsSchema,
  getRenderJobArgsSchema,
  getRunArgsSchema,
  listEventsArgsSchema,
  listPlatformsArgsSchema,
  listProjectsArgsSchema,
  listRunsArgsSchema,
  listStudioDeliverablesArgsSchema,
  planPublicationArgsSchema,
  researchWebArgsSchema,
  reviseRunArgsSchema,
  startJianyingExportArgsSchema,
  submitRenderArgsSchema,
} from './production-assistant.tools'
import { ProductionService } from './production.service'
import { ResearchFetcher } from './research-fetch'
import { AssistantChatMessage, AssistantToolCall, StudioAssistantClient } from './studio-assistant.client'
import { StudioJob, StudioJobCancelResult, StudioJobKind, StudioJobsClient } from './studio-jobs.client'

/** 工具循环上限：模型最多连续调 6 轮工具，防止一条消息把服务端拖死。 */
export const MAX_TOOL_ROUNDS = 6
/** 回灌给模型的单个工具结果上限，超出的部分对模型没有额外价值。 */
export const TOOL_RESULT_CHARS = 2000
/** 回灌给模型的历史消息条数上限。 */
const HISTORY_MESSAGES = 20
/** 能力清单服务在宿主机上，容器里连不上是常态，超时压到 4s。 */
const INVENTORY_TIMEOUT_MS = 10000
const DEFAULT_SESSION_TITLE = 'Studio 指挥台会话'

const SYSTEM_PROMPT = [
  '当前语境：用户说的项目都在他自己机器上。Envoy 指 ~/Desktop/2026/Envoy：把链接整篇读完、抽帧、记时间戳，再交给模型写作；不是 Lyft 开源、也不是 Istio 用的那个云原生代理。用户说 Envoy 时只讲本机这个项目，遇到同名产品不要引入，宁可说不确定也别换题。',
  '你是 Studio 的 AI 指挥台，帮用户把一条短视频从选题一路推进到发布。',
  '硬性规则：',
  '1. 只用简体中文回答，每次回复控制在 120 字以内。',
  '2. 一次只推进一个阶段（调研 → 素材 → 文案 → 渲染 → 发布），回复末尾要写清用户下一步需要确认或提供什么。',
  '3. 素材路径、账号、成片地址必须来自用户或工具返回，严禁编造。要么直接说「出一条讲 XX 的短视频」再自己写文案，要么用用户给的素材，量词不清时先问一句。',
  '4. 不确定就查工具：平台与音色用 list_platforms，任务状态用 get_run/list_runs，失败原因用 list_events。',
  '5. 发布只能登记意向（plan_publication），绝不能说已经发出，必须提醒用户在面板人工确认。',
  '6. 工具结果是唯一事实来源：拿到结果后直接引用里面的任务名、状态、链接作答，禁止说“没想好”“不知道”，也不要再问一遍已经查到的信息。',
  '7. 本工作台能凭空出片：卡面、字幕、配音、鲸鱼娘「阿斯特」的立绘都由流水线自己生成，create_run 只给 script 就能跑，materials 是可选项，只有用户明确要用图片/视频时才需要。想控制卡面就把 points 写成「标题::卡面正文::口播」三段，三段都要有字，卡面正文留空就只剩标题；卡面正文一行短句（可以放数字），口播写完整说法；hook 是首屏反差，cta 用最后一句口播收尾。要用现成素材时先 list_platforms 看素材包，spec 里写 pack:<包名>/<名字>；用户给了绝对路径就原样用，别自己拼路径。',
  '8. 「Envoy」指用户本机 ~/Desktop/2026/Envoy 这个项目：给 AI 一个链接，它把整篇读完、抽帧、记时间戳，再交给模型写作。envoyproxy.io 是同名的云原生代理，题材完全不同，research_web 返回它一律丢掉，别写 L7、服务网格、xDS、Wasm、mTLS 那套。能用的数字就这些：7.0 MB 原片 216 秒 109 段中文 ASR；690 秒长片 354 段；抓 25 个素材包只认 14 个，小红书 4 条全挂也照实标；每条结论都带时间戳能点回去核对。数字之外不编。',
  '9. 文案要像人说话：短句、有观点、有反差，不写说明书。结尾固定加一句「本期视频全程由大肥鱼制作完成，没有任何一个 token 受到伤害」，只放进最后一句口播，不占卡面。回应用户时说清这一步做了什么、下一步等他给什么。',
  '10. 任务排队和渲染是正常的：渲染要几分钟，最多查两次 get_run 就如实汇报阶段和进度，别说队列卡死，也不要让用户去重启服务。',
  '11. 用户要真出片就用 submit_render 下单：一次只下一单，kind 按需求选，specPath 用用户已有规格，没有规格就自己写 spec 再给。下单后如实报任务号，最多再查两次 get_render_job；成片路径来自 result.files，队列没返回就说出片没完成，禁止编造成片地址或说已经交付到某个平台。',
].join('\n')

const RUN_STATUS_TEXT: Record<ProductionRunStatus, string> = {
  [ProductionRunStatus.DRAFT]: '草稿',
  [ProductionRunStatus.QUEUED]: '排队中',
  [ProductionRunStatus.RUNNING]: '渲染中',
  [ProductionRunStatus.SUCCEEDED]: '已完成',
  [ProductionRunStatus.FAILED]: '失败',
  [ProductionRunStatus.WAITING_USER]: '待确认',
  [ProductionRunStatus.CANCELLED]: '已取消',
}

const CANVAS_TEXT: Record<string, string> = {
  portrait: '竖屏 1080x1920',
  landscape: '横屏 1920x1080',
  square: '方形 1080x1080',
}

export interface AssistantSessionVo {
  id: string
  title: string
  createdAt: string
}

export interface AssistantSessionSummaryVo extends AssistantSessionVo {
  updatedAt: string
  messageCount: number
}

export interface AssistantSessionDetailVo {
  id: string
  title: string
  messages: AssistantMessageVo[]
}

export interface AssistantMessageVo {
  id: string
  role: ProductionAssistantRole
  content: string
  createdAt: string
  actions?: AssistantActionVo[]
}

export interface AssistantActionVo {
  id: string
  type: ProductionAssistantActionType
  status: ProductionAssistantActionStatus
  label: string
  detail?: string
  link?: string
}

/** 丢弃 undefined，避免把 undefined 字段写进 spec 覆盖掉上一版的值。 */
function compact(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined))
}

function describeError(error: unknown): string {
  if (error instanceof AppException) {
    return error.message || '操作失败'
  }
  if (error instanceof Error) {
    return error.message || '操作失败'
  }
  return String(error)
}

function action(input: {
  type: ProductionAssistantActionType
  status: ProductionAssistantActionStatus
  label: string
  detail?: string
  link?: string
}): AssistantActionVo {
  const result: AssistantActionVo = {
    id: randomUUID(),
    type: input.type,
    status: input.status,
    label: input.label.slice(0, 60),
  }
  if (input.detail) {
    result.detail = input.detail.slice(0, 120)
  }
  if (input.link) {
    result.link = input.link
  }
  return result
}

interface ToolResult { text: string, actions: AssistantActionVo[] }

/**
 * 「AI 指挥台」的会话与工具编排。
 *
 * 会话落库，推理走宿主机 Ollama，工具全部复用 production 已有能力：
 * 这里不复制任何渲染/发布逻辑，尤其不提供真实提交发布的工具——发布只登记意向，
 * 真正发出去永远由用户在面板确认。
 */
@Injectable()
export class ProductionAssistantService {
  private readonly jobsClient = new StudioJobsClient(config.studioAssistant.inventoryUrl)

  constructor(
    private readonly sessionRepo: ProductionAssistantSessionRepository,
    private readonly production: ProductionService,
    private readonly client: StudioAssistantClient,
    private readonly researchFetcher: ResearchFetcher,
  ) {}

  /** MCP 工具用：投一个本机渲染任务，立刻拿 jobId，不等出片。 */
  async submitQueueJob(kind: StudioJobKind, payload: Record<string, unknown>): Promise<StudioJob> {
    return await this.jobsClient.submit(kind, payload)
  }

  /** MCP 工具用：宿主能力清单（平台账号 / 音色 / 素材包），连不上返回 null 由工具层说人话。 */
  async capabilityInventory(): Promise<Awaited<ReturnType<ProductionAssistantService['readInventory']>>> {
    return await this.readInventory()
  }

  /** MCP 工具用：宿主机上的可渲规格、素材与已出成片（/assets）。 */
  async capabilityAssets(): Promise<Record<string, unknown>> {
    return await this.readCapabilityJson('/assets')
  }

  /** MCP 工具用：已交付成片清单（/deliverables），按修改时间新的在前。 */
  async capabilityDeliverables(limit: number): Promise<unknown[]> {
    const payload = await this.readCapabilityJson(`/deliverables?limit=${Math.max(1, Math.min(limit, 500))}`)
    const files = payload['files']
    return Array.isArray(files) ? files : []
  }

  /** 读宿主能力服务的只读接口；网络/协议错误统一翻成「机器没连上」，别把原始异常甩给模型。 */
  private async readCapabilityJson(path: string): Promise<Record<string, unknown>> {
    const url = `${config.studioAssistant.inventoryUrl.replace(/\/+$/, '')}${path}`
    let response: Response
    try {
      response = await fetch(url, { signal: AbortSignal.timeout(INVENTORY_TIMEOUT_MS) })
    }
    catch (error) {
      throw new AppException(
        ResponseCode.ProductionRenderQueueUnavailable,
        `宿主机能力服务连不上：${error instanceof Error ? error.message : String(error)}`,
      )
    }
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw new AppException(
        ResponseCode.ProductionRenderQueueUnavailable,
        `宿主机能力服务返回 ${response.status}：${detail.slice(0, 200) || '无正文'}`,
      )
    }
    const payload = await response.json().catch(() => null)
    return payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  }

  /** MCP 工具用：查队列任务；队列返回 404 时给 null，由调用方说明任务不存在。 */
  async readQueueJob(jobId: string): Promise<StudioJob | null> {
    return await this.jobsClient.get(jobId)
  }

  /** MCP 工具用：取消队列任务；已经结束的任务返回 cancelled=false。 */
  async stopQueueJob(jobId: string): Promise<StudioJobCancelResult | null> {
    return await this.jobsClient.cancel(jobId)
  }

  /** 面板「进行中」直接读队列真实状态，避免页面上写着「没有正在渲染的任务」而队列在跑。 */
  async listRenderJobs(limit: number): Promise<StudioJob[]> {
    return await this.jobsClient.list(Math.min(Math.max(limit, 1), 50))
  }

  async createSession(userId: string, title?: string): Promise<AssistantSessionVo> {
    const session = await this.sessionRepo.create({
      userId,
      title: title?.trim() || DEFAULT_SESSION_TITLE,
      messages: [],
      research: [],
    } as never)
    return {
      id: session.id,
      title: session.title,
      createdAt: this.toIso(session.createdAt),
    }
  }

  async getSession(userId: string, sessionId: string): Promise<AssistantSessionDetailVo> {
    const session = await this.requireSession(userId, sessionId)
    return {
      id: session.id,
      title: session.title,
      messages: (session.messages ?? []).map(message => this.toMessageVo(message)),
    }
  }

  /** 最近会话：只列创建者自己的，按更新时间倒序，带上消息条数供面板排序。 */
  async listSessions(userId: string, limit: number): Promise<AssistantSessionSummaryVo[]> {
    const sessions = await this.sessionRepo.listByUser(userId, limit)
    return sessions.map(session => ({
      id: String(session._id),
      title: session.title,
      createdAt: this.toIso(session.createdAt),
      updatedAt: this.toIso(session.updatedAt ?? session.createdAt),
      messageCount: (session.messages ?? []).length,
    }))
  }

  /** 发一条消息：先落库用户消息，再跑工具循环，最后落库助手回复。 */
  async sendMessage(
    userId: string,
    sessionId: string,
    message: string,
    agent?: StudioAssistantOverride,
  ) {
    const session = await this.requireSession(userId, sessionId)
    const userMessage = {
      id: randomUUID(),
      role: ProductionAssistantRole.USER,
      content: message.trim(),
      createdAt: new Date(),
      actions: [],
    }
    const updated = await this.sessionRepo.appendMessage(session.id, userId, userMessage as never)
    const history = updated?.messages ?? [...(session.messages ?? []), userMessage]

    const actions: AssistantActionVo[] = []
    let content: string
    try {
      content = await this.runToolLoop(userId, session.id, history, userMessage.content, actions, agent)
    }
    catch (error) {
      // 推理服务挂了也要给出可读中文，而不是 500 让面板报「不可用」。
      // 模型名可能是面板里填的，所以取这次实际用的那个。
      const model = agent?.model?.trim() || config.studioAssistant.model
      content = `${describeError(error)}。检查请求地址和模型名（${model}）。`
    }

    const reply = {
      id: randomUUID(),
      role: ProductionAssistantRole.ASSISTANT,
      content: content.slice(0, 2000),
      createdAt: new Date(),
      actions,
    }
    await this.sessionRepo.appendMessage(session.id, userId, reply as never)
    if (session.title === DEFAULT_SESSION_TITLE) {
      // 用第一句话当标题，列表里更好认；失败不影响主流程
      await this.sessionRepo
        .updateById(session.id, { title: userMessage.content.slice(0, 20) } as never)
        .catch(() => undefined)
    }
    return { reply: this.toMessageVo(reply) }
  }

  private async requireSession(userId: string, sessionId: string) {
    if (!/^[a-f0-9]{24}$/i.test(sessionId)) {
      throw new AppException(ResponseCode.ProductionAssistantSessionNotFound, '会话不存在或已删除')
    }
    const session = await this.sessionRepo.getByIdForUser(sessionId, userId)
    if (!session) {
      throw new AppException(ResponseCode.ProductionAssistantSessionNotFound, '会话不存在或已删除')
    }
    return session
  }

  private async runToolLoop(
    userId: string,
    sessionId: string,
    history: ProductionAssistantMessage[],
    message: string,
    actions: AssistantActionVo[],
    agent?: StudioAssistantOverride,
  ): Promise<string> {
    const messages: AssistantChatMessage[] = [
      { role: 'system', content: SYSTEM_PROMPT },
      ...history
        .filter(item => item.role === ProductionAssistantRole.USER || item.role === ProductionAssistantRole.ASSISTANT)
        .slice(-HISTORY_MESSAGES)
        .map(item => ({ role: item.role, content: item.content.slice(0, TOOL_RESULT_CHARS) }) as AssistantChatMessage),
    ]
    if (messages[messages.length - 1]?.content !== message) {
      messages.push({ role: 'user', content: message })
    }

    for (let round = 0; round < MAX_TOOL_ROUNDS; round++) {
      const response = await this.client.chat(messages, ASSISTANT_TOOLS, agent)
      if (!response.toolCalls.length) {
        return response.content.trim() || '我没想好这一步该怎么做，你能再说具体一点吗？'
      }
      messages.push({
        role: 'assistant',
        content: response.content,
        tool_calls: response.toolCalls,
      })
      for (const call of response.toolCalls) {
        const result = await this.executeTool(userId, sessionId, call)
        actions.push(...result.actions)
        messages.push({
          role: 'tool',
          tool_call_id: call.id,
          content: result.text.slice(0, TOOL_RESULT_CHARS),
        })
      }
    }
    return `工具连调 ${MAX_TOOL_ROUNDS} 轮还没得出结论，先停在这里。请把要做的动作缩小到一件事，或直接在面板上操作。`
  }

  /** 工具执行永不抛异常：失败也要变成一句能读的话回灌给模型。 */
  private async executeTool(userId: string, sessionId: string, call: AssistantToolCall): Promise<ToolResult> {
    const name = call.function.name
    let raw: unknown = {}
    try {
      raw = call.function.arguments ? JSON.parse(call.function.arguments) : {}
    }
    catch {
      return this.fail(`工具 ${name} 的参数不是合法 JSON，请重新组织参数。`, '工具参数不合法')
    }

    try {
      switch (name) {
        case AssistantToolName.RESEARCH_WEB: {
          const args = researchWebArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.researchWeb(userId, sessionId, args.data.url)
        }
        case AssistantToolName.LIST_PLATFORMS: {
          const args = listPlatformsArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.listPlatforms(userId)
        }
        case AssistantToolName.LIST_STUDIO_ASSETS: {
          return await this.listStudioAssets()
        }
        case AssistantToolName.LIST_STUDIO_DELIVERABLES: {
          const args = listStudioDeliverablesArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.listStudioDeliverables(args.data.limit)
        }
        case AssistantToolName.LIST_PROJECTS: {
          const args = listProjectsArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.listProjects(userId)
        }
        case AssistantToolName.CREATE_PROJECT: {
          const args = createProjectArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.createProject(userId, args.data.name)
        }
        case AssistantToolName.CREATE_RUN: {
          const args = createRunArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.createRun(userId, args.data)
        }
        case AssistantToolName.CREATE_RUN_BATCH: {
          const args = createRunBatchArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.createRunBatch(userId, args.data)
        }
        case AssistantToolName.GET_RUN: {
          const args = getRunArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.getRun(userId, args.data.runId)
        }
        case AssistantToolName.LIST_RUNS: {
          const args = listRunsArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.listRuns(userId, args.data.limit, args.data.projectId)
        }
        case AssistantToolName.REVISE_RUN: {
          const args = reviseRunArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.reviseRun(userId, args.data.runId, args.data.patch)
        }
        case AssistantToolName.LIST_EVENTS: {
          const args = listEventsArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.listEvents(userId, args.data.runId)
        }
        case AssistantToolName.PLAN_PUBLICATION: {
          const args = planPublicationArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.planPublication(userId, args.data.runId, args.data.platforms)
        }
        case AssistantToolName.SUBMIT_RENDER: {
          const args = submitRenderArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.submitRender(args.data.kind, args.data.payload, args.data.label)
        }
        case AssistantToolName.GET_RENDER_JOB: {
          const args = getRenderJobArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.getRenderJob(args.data.jobId)
        }
        case AssistantToolName.CANCEL_RENDER_JOB: {
          const args = cancelRenderJobArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.cancelRenderJob(args.data.jobId)
        }
        case AssistantToolName.CREATE_JIANYING_DRAFT: {
          const args = createJianyingDraftArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.createJianyingDraft(userId, args.data.runId, args.data.title)
        }
        case AssistantToolName.START_JIANYING_EXPORT: {
          const args = startJianyingExportArgsSchema.safeParse(raw)
          if (!args.success)
            return this.invalidArgs(name, args.error)
          return await this.startJianyingExport(userId, args.data.runId, args.data.allowGui)
        }
        default:
          return this.fail(`没有名为 ${name} 的工具，请只用给定的工具。`, '未知工具')
      }
    }
    catch (error) {
      return this.fail(`工具 ${name} 执行失败：${describeError(error)}`, `${name} 执行失败`)
    }
  }

  // ------------------------------------------------------------------ tools

  private async researchWeb(userId: string, sessionId: string, url: string): Promise<ToolResult> {
    const page = await this.researchFetcher.fetch(url)
    await this.sessionRepo.appendResearch(sessionId, userId, {
      id: randomUUID(),
      url: page.url,
      title: page.title,
      chars: page.chars,
      text: page.text,
      createdAt: new Date(),
    } as never)
    return {
      text: `标题：${page.title}\n字数：${page.chars}\n正文节选：${page.excerpt}`,
      actions: [action({
        type: ProductionAssistantActionType.RESEARCH,
        status: ProductionAssistantActionStatus.OK,
        label: `已收集：${page.title}`,
        detail: `${page.chars} 字 · ${this.hostOf(page.url)}`,
        link: page.url,
      })],
    }
  }

  private async listPlatforms(userId: string): Promise<ToolResult> {
    const workers = await this.production.workerHealth(userId)
    const inventory = await this.readInventory()
    const platforms = inventory?.platforms ?? []
    const voices = inventory?.voices ?? { jianying: 0, edge: 0 }
    const materials = inventory?.materials ?? []
    const materialText = materials.length
      ? materials.map(item => `${item.slug}（${item.shots} 张${item.refs.length ? `：${item.refs.slice(0, 3).join('、')}` : ''}）`).join('；')
      : '无（可以给绝对路径，或先用抓取脚本收成素材包）'
    const platformText = platforms.length
      ? platforms.map((item) => {
          const name = item.label && item.label !== item.platform ? `${item.label}（${item.platform}）` : item.platform
          return `${name}${item.ready ? `（${item.accounts.length} 个账号）` : '（未就绪，缺 cookie）'}`
        }).join('、')
      : '未读取到'
    const workerText = workers.length
      ? workers.map(item => `${item.name}（${item.online ? '在线' : '离线'}）`).join('、')
      : '无'
    return {
      text: [
        `平台：${platformText}`,
        `音色：剪映 ${voices.jianying} 个、Edge ${voices.edge} 个`,
        `素材包：${materialText}`,
        `渲染 worker：${workerText}`,
        inventory ? '' : '能力清单服务不可达：需要先在宿主机启动 mac worker 的 capabilities server（--host 0.0.0.0）。',
      ].filter(Boolean).join('\n'),
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: platforms.length ? ProductionAssistantActionStatus.OK : ProductionAssistantActionStatus.FAILED,
        label: platforms.length ? `已读取 ${platforms.length} 个平台` : '平台清单不可达',
        detail: platforms.length
          ? `就绪 ${platforms.filter(item => item.ready).length} 个 · 剪映 ${voices.jianying} / Edge ${voices.edge} 音色 · ${materials.length} 个素材包`
          : `worker 在线 ${workers.filter(item => item.online).length} 个；需在宿主机启动 capabilities server`,
        link: '/studio',
      })],
    }
  }

  private async listStudioAssets(): Promise<ToolResult> {
    const assets = await this.capabilityAssets()
    const specs = Array.isArray(assets['specs']) ? assets['specs'] as Record<string, unknown>[] : []
    const packs = Array.isArray(assets['packs']) ? assets['packs'] as unknown[] : []
    const shots = (assets['shots'] ?? {}) as Record<string, unknown>
    const films = (assets['films'] ?? {}) as Record<string, unknown>
    const recentShots = Array.isArray(shots['recent']) ? shots['recent'] as Record<string, unknown>[] : []
    const root = typeof assets['root'] === 'string' ? assets['root'] : ''
    return {
      text: [
        root ? `素材根目录：${root}` : '',
        `规格：${specs.length} 份${specs.length ? `（${specs.slice(0, 4).map(item => String(item['title'] ?? item['path'])).join('、')}）` : ''}`,
        `镜头：${shots['count'] ?? 0} 个${recentShots.length ? `，最近 ${recentShots.slice(0, 3).map(item => String(item['path'] ?? '').split('/').pop()).join('、')}` : ''}`,
        `素材包：${packs.length ? packs.join('、') : '无'}`,
        `成片：${films['count'] ?? 0} 部`,
      ].filter(Boolean).join('\n'),
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.OK,
        label: `素材 ${shots['count'] ?? 0} 个 · 成片 ${films['count'] ?? 0} 部`,
        detail: packs.length ? `素材包：${packs.join('、')}` : '暂无素材包',
        link: '/studio',
      })],
    }
  }

  private async listStudioDeliverables(limit: number): Promise<ToolResult> {
    const files = await this.capabilityDeliverables(limit)
    const rows = files as Record<string, unknown>[]
    const text = rows.length
      ? rows.map((item, index) => {
          const bytes = Number(item['bytes'] ?? 0)
          const size = bytes > 0 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : '大小未知'
          return `${index + 1}. ${String(item['name'] ?? '')} · ${size} · ${String(item['path'] ?? '')}`
        }).join('\n')
      : '还没有交付过成片。'
    return {
      text,
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: rows.length ? ProductionAssistantActionStatus.OK : ProductionAssistantActionStatus.FAILED,
        label: rows.length ? `最近 ${rows.length} 部成片` : '暂无成片',
        detail: rows.length ? String(rows[0]?.['name'] ?? '') : '先把任务跑完再来看',
        link: '/studio',
      })],
    }
  }

  private async listProjects(userId: string): Promise<ToolResult> {
    const projects = await this.production.listProjects(userId)
    return {
      text: projects.map(item => `${item.id} ${item.name}${item.isDefault ? '（默认）' : ''}`).join('\n'),
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.OK,
        label: `已读取 ${projects.length} 个项目`,
        detail: projects.slice(0, 3).map(item => item.name).join('、'),
        link: '/studio',
      })],
    }
  }

  private async createProject(userId: string, name: string): Promise<ToolResult> {
    const project = await this.production.createProject(userId, { name, isDefault: false })
    return {
      text: `已创建项目：${project.name}（${project.id}）`,
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.OK,
        label: `已创建项目：${project.name}`,
        detail: '新项目',
        link: '/studio',
      })],
    }
  }

  /** 纯文案出片：把分句文案摊成竖屏脚本，交给 worker 自己的脚本通道渲染。 */
  private scriptSpec(args: z.infer<typeof createRunArgsSchema>): Record<string, unknown> {
    const lines = args.script.map(item => item.trim()).filter(Boolean)
    const hook = (args.hook ?? lines[0] ?? '').trim()
    const tail = lines.length > 1 ? lines[lines.length - 1] : ''
    const cta = (args.cta ?? tail).trim()
    const middle = lines.slice(1, lines.length > 1 ? lines.length - 1 : lines.length)
    const given = (args.points ?? []).map(item => item.trim()).filter(Boolean)
    // 兜底排序：把每条口播的第一个分句当卡面标题，正文留空——卡面只放能一眼读完的字，
    // 长句塞进卡面就会溢出，不如让口播去讲。要控制卡面就显式传 points。
    const points = given.length
      ? given
      : middle.map((line) => {
          const head = line.split(/[。！？；，,.!?;]/)[0]?.trim() ?? ''
          const title = head.length > 14 ? head.slice(0, 14) : head
          return `${title || line.slice(0, 14)}::`
        })
    const says = given.length
      ? points.map((item, _index) => {
          const parts = item.split('::')
          return parts.length >= 3 ? parts.slice(2).join('::') : item
        })
      : middle
    const voiced = says.length === points.length ? says : points
    return compact({
      engine: 'timeline',
      mode: 'vertical_short',
      topic: args.topic ?? args.title,
      hook,
      points,
      cta,
      narration: { intro: hook, points: voiced, outro: cta },
      template: 'factcard',
      character: true,
      voice: args.voice,
      platforms: args.platforms,
    }) as Record<string, unknown>
  }

  private async createRun(userId: string, args: z.infer<typeof createRunArgsSchema>): Promise<ToolResult> {
    const input = await this.runInput(userId, args.title, args)
    if (!input)
      return this.fail('找不到可用项目，先在面板创建一个项目。', '没有可用项目')
    const run = await this.production.createRun(userId, input)
    return {
      text: `已创建任务：${run.id}，状态 ${RUN_STATUS_TEXT[run.status] ?? run.status}，画幅 ${CANVAS_TEXT[args.canvas] ?? args.canvas}。`,
      actions: [action({
        type: ProductionAssistantActionType.RUN,
        status: ProductionAssistantActionStatus.RUNNING,
        label: `已创建任务：${run.title}`,
        detail: `${CANVAS_TEXT[args.canvas] ?? args.canvas} · 排队中`,
        link: '/studio',
      })],
    }
  }

  /** 同题多版：一条文案排 N 次，标题自动带 1/N 后缀，渲染完再挑。 */
  private async createRunBatch(userId: string, args: z.infer<typeof createRunBatchArgsSchema>): Promise<ToolResult> {
    const input = await this.runInput(userId, args.title, args)
    if (!input)
      return this.fail('找不到可用项目，先在面板创建一个项目。', '没有可用项目')
    const runs = await this.production.createRunBatch(userId, input, args.count, args.variants)
    return {
      text: `已排 ${runs.length} 条任务：${runs.map(item => `${item.id}（${item.title}）`).join('；')}。`,
      actions: [action({
        type: ProductionAssistantActionType.RUN,
        status: ProductionAssistantActionStatus.RUNNING,
        label: `已排 ${runs.length} 条：${args.title}`,
        detail: `${CANVAS_TEXT[args.canvas] ?? args.canvas} · ${runs.length} 条排队中`,
        link: '/studio',
      })],
    }
  }

  /** 任务都挂第一个项目：现在一个工作台只服务一个项目，库里也按项目分目录。 */
  private async runInput(userId: string, title: string, args: z.infer<typeof createRunArgsSchema>) {
    const projects = await this.production.listProjects(userId)
    const project = projects[0]
    if (!project)
      return null
    return {
      projectId: project.id,
      title,
      brief: args.title,
      spec: this.runSpec(args),
    }
  }

  /**
   * 友好输入展开成时间轴：没给素材就是「凭空出片」，走竖屏脚本通道，
   * 卡面/配音/立绘由 worker 现场生成；有素材才交给 buildTimelineSpec 展开。
   */
  private runSpec(args: z.infer<typeof createRunArgsSchema>): Record<string, unknown> {
    const materials = args.materials ?? []
    if (!materials.length)
      return this.scriptSpec(args)
    return compact({
      engine: 'timeline',
      script: args.script,
      materials,
      canvas: args.canvas,
      platforms: args.platforms,
      bgm: args.bgm,
      assetBase: args.assetBase,
      clipSeconds: args.clipSeconds,
    })
  }

  private async getRun(userId: string, runId: string): Promise<ToolResult> {
    const { run, jobs, events } = await this.production.runDetail(userId, runId)
    const job = jobs[jobs.length - 1]
    const output = (run.output ?? {}) as Record<string, unknown>
    const platforms = (output['platforms'] ?? {}) as Record<string, { url?: string, width?: number, height?: number, bytes?: number }>
    const videoUrl = typeof output['videoUrl'] === 'string' ? output['videoUrl'] : ''
    const detail = Object.entries(platforms)
      .map(([platform, info]) => {
        const size = Number(info?.bytes) > 0 ? ` · ${(Number(info.bytes) / 1024 / 1024).toFixed(1)} MB` : ''
        const shape = Number(info?.width) > 0 && Number(info?.height) > 0 ? ` ${info.width}x${info.height}` : ''
        return `${platform}${shape}${size}`
      })
      .join('；')
    const statusText = RUN_STATUS_TEXT[run.status] ?? run.status
    const actions: AssistantActionVo[] = []
    const renderLink = videoUrl || Object.values(platforms).find(item => item?.url)?.url || ''
    if (renderLink || typeof output['videoPath'] === 'string') {
      actions.push(action({
        type: ProductionAssistantActionType.RENDER,
        status: ProductionAssistantActionStatus.OK,
        label: `成片已就绪：${run.title}`,
        detail: detail || '母版已就绪',
        link: renderLink || '/studio',
      }))
    }
    actions.push(action({
      type: ProductionAssistantActionType.RUN,
      status: run.status === ProductionRunStatus.FAILED
        ? ProductionAssistantActionStatus.FAILED
        : run.status === ProductionRunStatus.SUCCEEDED
          ? ProductionAssistantActionStatus.OK
          : ProductionAssistantActionStatus.RUNNING,
      label: `任务${statusText}：${run.title}`,
      detail: `${job?.progress ?? 0}% · ${job?.stage || run.currentStage || 'queued'}${run.lastError ? ` · ${run.lastError}` : ''}`,
      link: '/studio',
    }))
    // 成片落在素材库里的绝对路径要原样交给模型：用户问「片子在哪」时它得能直接答，
    // 也能把这个路径拿去剪映/上传/二次剪辑，而不是只知道一个 runId。
    const libraryPath = typeof output['libraryPath'] === 'string' ? output['libraryPath'] : ''
    const duration = Number(output['duration']) > 0 ? `${Number(output['duration']).toFixed(1)}s` : ''
    const md5 = typeof output['md5'] === 'string' ? output['md5'] : ''
    const artifact = libraryPath || (typeof output['videoPath'] === 'string' ? output['videoPath'] : '')
    return {
      text: [
        `状态：${statusText}；阶段：${job?.stage || run.currentStage}；进度：${job?.progress ?? 0}%`,
        `错误：${run.lastError || job?.lastError || '无'}`,
        `产物：${videoUrl || artifact || '无'}${detail ? `；平台成片：${detail}` : ''}`,
        `本地文件：${artifact ? `${artifact}${duration ? ` · ${duration}` : ''}${md5 ? ` · md5 ${md5}` : ''}` : '未生成'}`,
        `最近事件：${events.slice(-3).map(item => item.message).join(' | ') || '无'}`,
      ].join('\n'),
      actions,
    }
  }

  private async listRuns(userId: string, limit: number, projectId?: string): Promise<ToolResult> {
    const { list, total } = await this.production.listRuns(userId, 1, limit, undefined, projectId)
    return {
      text: list.length
        ? `共 ${total} 条，最近 ${list.length} 条：\n${list.map(item => `${item.id} ${item.title} ${RUN_STATUS_TEXT[item.status] ?? item.status}`).join('\n')}`
        : (projectId ? '这个项目还没有任务。' : '还没有任何任务。'),
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.OK,
        label: `最近 ${list.length} 条任务`,
        detail: list.slice(0, 2).map(item => `${item.title}：${RUN_STATUS_TEXT[item.status] ?? item.status}`).join('、') || '暂无任务',
        link: '/studio',
      })],
    }
  }

  private async reviseRun(
    userId: string,
    runId: string,
    patch: z.infer<typeof reviseRunArgsSchema>['patch'],
  ): Promise<ToolResult> {
    const { brief, ...specPatch } = patch
    const spec = compact(specPatch)
    if (!brief && !Object.keys(spec).length) {
      return this.fail('没有要改的字段，请说明改文案还是改画幅。', '没有可应用的修改')
    }
    const run = await this.production.reviseRun(userId, runId, { brief, spec })
    if (!run) {
      return this.fail('任务不存在或已删除，请重新创建。', '任务未找到')
    }
    return {
      text: `已提交第 ${run.revision} 版（任务 ${run.id}），正在重新排队渲染。`,
      actions: [action({
        type: ProductionAssistantActionType.RUN,
        status: ProductionAssistantActionStatus.RUNNING,
        label: `已提交第 ${run.revision} 版`,
        detail: '重新排队渲染',
        link: '/studio',
      })],
    }
  }

  private async listEvents(userId: string, runId: string): Promise<ToolResult> {
    const events = await this.production.listEvents(userId, runId)
    const recent = events.slice(-8)
    const last = recent[recent.length - 1]
    return {
      text: recent.length
        ? recent.map(item => `#${item.seq} ${item.kind} ${item.message}`).join('\n')
        : '这条任务还没有事件。',
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.OK,
        label: `已读取 ${recent.length} 条事件`,
        detail: last?.message?.slice(0, 80) || '暂无事件',
        link: '/studio',
      })],
    }
  }

  /** 只登记发布意向，绝不触发真实提交——真正发布要用户在面板确认。 */
  private async planPublication(userId: string, runId: string, platforms: string[]): Promise<ToolResult> {
    const planned: string[] = []
    const failed: string[] = []
    for (const platform of platforms) {
      try {
        await this.production.planPublication(userId, {
          runId,
          platform,
          dispatcher: PublicationDispatcher.MANUAL,
          accountRef: 'panel-confirm',
        })
        planned.push(platform)
      }
      catch (error) {
        failed.push(`${platform}：${describeError(error)}`)
      }
    }
    const actions: AssistantActionVo[] = []
    if (planned.length) {
      actions.push(action({
        type: ProductionAssistantActionType.PUBLISH,
        status: ProductionAssistantActionStatus.OK,
        label: `已记录 ${planned.join('、')} 的发布意向`,
        detail: '需在面板人工确认后才会真正发布',
        link: '/studio',
      }))
    }
    if (failed.length) {
      actions.push(action({
        type: ProductionAssistantActionType.PUBLISH,
        status: ProductionAssistantActionStatus.FAILED,
        label: '部分平台没能记录发布意向',
        detail: failed.join('；'),
        link: '/studio',
      }))
    }
    return {
      text: `已登记发布意向：${planned.join('、') || '无'}；失败：${failed.join('；') || '无'}。请注意：这只记录了意向，需用户在面板人工确认，指挥台不会真正提交发布。`,
      actions,
    }
  }

  // ------------------------------------------------------------------ 渲染队列

  private async submitRender(kind: StudioJobKind, payload: Record<string, unknown>, label?: string): Promise<ToolResult> {
    const job = await this.jobsClient.submit(kind, payload)
    return {
      text: `已下单 ${kind} 任务 ${job.id}，当前 ${job.status}（${job.progress}%）。任务在用户机器上异步执行，用 get_render_job 查进度；成片路径要等任务成功后才在 result.files 里出现。`,
      actions: [action({
        type: ProductionAssistantActionType.RENDER,
        status: ProductionAssistantActionStatus.RUNNING,
        label: label ?? `${kind} 出片`,
        detail: job.id,
        link: '/studio',
      })],
    }
  }

  private async getRenderJob(jobId: string): Promise<ToolResult> {
    const job = await this.jobsClient.get(jobId)
    if (!job)
      return this.fail(`队列里没有任务 ${jobId}，先确认任务号有没有抄错。`, '出片任务不存在')
    if (job.status === 'succeeded') {
      const files = job.result?.files ?? []
      const duration = job.result?.duration ? `，时长 ${job.result.duration.toFixed(1)} 秒` : ''
      const frame = job.result?.width && job.result?.height ? `，画面 ${job.result.width}×${job.result.height}` : ''
      return {
        text: `任务 ${job.id} 已出片${duration}${frame}：${files.join('、') || '队列没返回产物路径'}。`,
        actions: [action({
          type: ProductionAssistantActionType.RENDER,
          status: ProductionAssistantActionStatus.OK,
          label: '已出片',
          detail: job.id,
          link: '/studio',
        })],
      }
    }
    if (job.status === 'failed') {
      return {
        text: `任务 ${job.id} 失败：${job.error ?? '队列没给错误原文'}。改完规格重新下单，别重复查同一个失败任务。`,
        actions: [action({
          type: ProductionAssistantActionType.RENDER,
          status: ProductionAssistantActionStatus.FAILED,
          label: '出片失败',
          detail: job.id,
          link: '/studio',
        })],
      }
    }
    return {
      text: `任务 ${job.id} 还在跑：${job.status}，进度 ${job.progress}%（${job.stage || '无阶段'}）。渲染要几分钟，先别取消，也别让用户重启服务。`,
      actions: [action({
        type: ProductionAssistantActionType.RENDER,
        status: ProductionAssistantActionStatus.RUNNING,
        label: '出片中',
        detail: job.id,
        link: '/studio',
      })],
    }
  }

  private async cancelRenderJob(jobId: string): Promise<ToolResult> {
    const job = await this.jobsClient.cancel(jobId)
    if (!job)
      return this.fail(`队列里没有任务 ${jobId}，没法取消。`, '出片任务不存在')
    return {
      text: job.cancelled
        ? `任务 ${job.id} 已取消。`
        : `任务 ${job.id} 已经是 ${job.status}，队列没有取消它（已结束的任务不能回滚）。`,
      actions: [action({
        type: ProductionAssistantActionType.RENDER,
        status: job.cancelled ? ProductionAssistantActionStatus.OK : ProductionAssistantActionStatus.FAILED,
        label: job.cancelled ? '已取消出片' : '没取消成',
        detail: job.id,
        link: '/studio',
      })],
    }
  }

  /**
   * 剪映工程落地：把任务成片登记进素材库默认分组，用户就能在本机剪映里打开接着改。
   * 成片没出来就直接说清楚，不能凭 runId 臆造一个工程。
   */
  private async createJianyingDraft(userId: string, runId: string, title?: string): Promise<ToolResult> {
    const run = await this.production.getRun(userId, runId)
    const videoKey = run.output['videoPath'] as string | undefined
    if (!videoKey)
      return this.fail(`任务 ${runId} 还没有成片，没法落剪映工程。先用 submit_render 出片，成功后再落工程。`, '任务未出片')
    const material = await this.production.attachMaterialForWorker(userId, runId, {
      videoKey,
      title: title || run.title,
    })
    return {
      text: `任务 ${runId} 的剪映工程已落地：素材 ${material.materialId}${material.deduped ? '（之前已落过，直接复用）' : ''}。工程在用户素材库的默认分组里，打开本机剪映就能继续精修；精修完再导出用 submit_render 的 jianying 引擎。`,
      actions: [action({
        type: ProductionAssistantActionType.RENDER,
        status: ProductionAssistantActionStatus.OK,
        label: '剪映工程已落地',
        detail: material.materialId,
        link: '/studio',
      })],
    }
  }

  /**
   * 收导出产物：草稿落好了不等于成片存在。这一步单独记账，并如实说明这一轮
   * 在等人的动作，不假装已经出了视频。
   */
  private async startJianyingExport(userId: string, runId: string, allowGui?: boolean): Promise<ToolResult> {
    const job = await this.production.startJianyingExport(userId, runId, { allowGui: allowGui === true })
    const text = allowGui === true
      ? `任务 ${runId} 的导出已排队（${job.jobId}），本机会尝试驱动剪映导出面板；机器正被占用时会自动退避。`
      : `任务 ${runId} 的导出已排队（${job.jobId}）。macOS 上剪映要人点导出，这一轮到「等人导出」就停，等你在剪映里点完再收一次。`
    return {
      text,
      actions: [action({
        type: ProductionAssistantActionType.RENDER,
        status: ProductionAssistantActionStatus.OK,
        label: allowGui === true ? '导出已排队' : '等待人工导出',
        detail: job.jobId,
        link: '/studio',
      })],
    }
  }

  // ------------------------------------------------------------------ helper

  private async readInventory(): Promise<{
    platforms: Array<{ platform: string, label: string, ready: boolean, accounts: string[] }>
    voices: { jianying: number, edge: number }
    materials: Array<{ slug: string, shots: number, refs: string[] }>
  } | null> {
    const url = `${config.studioAssistant.inventoryUrl.replace(/\/+$/, '')}/inventory`
    try {
      // 宿主机渲染满载时清单接口会慢一拍，先给一次重试，别让平台/音色直接报不可达。
      let response: Response | null = null
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const hit = await fetch(url, { signal: AbortSignal.timeout(INVENTORY_TIMEOUT_MS) })
          if (hit.ok) {
            response = hit
            break
          }
        }
        catch {
          if (attempt === 1)
            return null
        }
      }
      if (!response) {
        return null
      }
      const payload = await response.json() as {
        platforms?: unknown
        voices?: Record<string, unknown>
        materials?: { packs?: unknown }
      } | null
      const rows = Array.isArray(payload?.platforms) ? payload.platforms : []
      const platforms = rows.flatMap((row) => {
        if (!row || typeof row !== 'object')
          return []
        const item = row as Record<string, unknown>
        const platform = typeof item['platform'] === 'string' ? item['platform'] : ''
        if (!platform)
          return []
        const accounts = Array.isArray(item['accounts'])
          ? item['accounts'].flatMap((account) => {
              if (!account || typeof account !== 'object')
                return []
              const name = (account as Record<string, unknown>)['account']
              return typeof name === 'string' ? [name] : []
            })
          : []
        return [{
          platform,
          label: typeof item['label'] === 'string' ? item['label'] : platform,
          ready: item['ready'] === true,
          accounts,
        }]
      })
      const voiceCount = (key: string) => Array.isArray(payload?.voices?.[key]) ? (payload.voices[key] as unknown[]).length : 0
      const materialRows = Array.isArray(payload?.materials?.packs) ? payload.materials.packs : []
      const materials = materialRows.flatMap((row) => {
        if (!row || typeof row !== 'object')
          return []
        const item = row as Record<string, unknown>
        const slug = typeof item['slug'] === 'string' ? item['slug'] : ''
        if (!slug)
          return []
        const refs = Array.isArray(item['refs']) ? item['refs'].filter((ref): ref is string => typeof ref === 'string') : []
        return [{ slug, shots: Number(item['shots']) || refs.length, refs }]
      })
      return {
        platforms,
        voices: { jianying: voiceCount('jianying'), edge: voiceCount('edge') },
        materials,
      }
    }
    catch {
      return null
    }
  }

  private hostOf(url: string): string {
    try {
      return new URL(url).hostname
    }
    catch {
      return url
    }
  }

  private invalidArgs(name: string, error: z.ZodError): ToolResult {
    const issue = error.issues[0]
    const detail = issue ? `${issue.path.join('.') || '参数'}：${issue.message}` : '参数不合法'
    return {
      text: `工具 ${name} 参数不合法（${detail}），请修正后重试。`,
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.FAILED,
        label: '参数不合法',
        detail,
        link: '/studio',
      })],
    }
  }

  private fail(text: string, label: string): ToolResult {
    return {
      text,
      actions: [action({
        type: ProductionAssistantActionType.INFO,
        status: ProductionAssistantActionStatus.FAILED,
        label,
        detail: text,
        link: '/studio',
      })],
    }
  }

  private toMessageVo(message: ProductionAssistantMessage | {
    id: string
    role: ProductionAssistantRole
    content: string
    createdAt: Date
    actions?: ProductionAssistantAction[]
  }): AssistantMessageVo {
    const vo: AssistantMessageVo = {
      id: message.id,
      role: message.role,
      content: message.content,
      createdAt: this.toIso(message.createdAt),
    }
    const actions = (message.actions ?? []).map(item => ({
      id: item.id,
      type: item.type,
      status: item.status,
      label: item.label,
      detail: item.detail,
      link: item.link,
    }))
    if (actions.length) {
      vo.actions = actions
    }
    return vo
  }

  private toIso(value: Date | string | undefined): string {
    if (!value)
      return new Date().toISOString()
    return value instanceof Date ? value.toISOString() : new Date(value).toISOString()
  }
}
