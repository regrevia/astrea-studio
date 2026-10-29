import { Controller, Injectable } from '@nestjs/common'
import { AppException, getUser, ResponseCode, toTextResult, toYamlTextResult } from '@yikart/common'
import { PublicationDispatcher } from '@yikart/mongodb'
import { Tool } from '@yikart/nest-mcp'
import { z } from 'zod'
import { ProductionAssistantService } from './production-assistant.service'
import { normalizeRunSpec } from './production.run-spec'
import { ProductionService } from './production.service'
import { StudioJobKind } from './studio-jobs.client'

/**
 * Studio tools for Codex.
 *
 * Two contract points drive this file:
 *
 * 1. Creating a run must NOT wait for the render. A tool that blocked until the
 *    video existed would hold the agent's turn open for minutes, so
 *    `createProductionRun` returns a queued runId and the caller polls
 *    `getProductionRun`.
 * 2. Publishing is irreversible. `planPublication` only records intent; there is
 *    deliberately no MCP tool that submits, so an agent cannot publish by itself.
 */

const CreateProjectSchema = z.object({
  name: z.string().min(1).describe('项目名称'),
  sourceRepo: z.string().optional().describe('项目源码仓库或素材来源（仅记录）'),
  materialGroupId: z.string().optional().describe('绑定的草稿箱 ID'),
})

const CreateJianyingDraftSchema = z.object({
  runId: z.string().describe('任务 ID'),
  title: z.string().optional().describe('工程名，省略用任务标题'),
})

const StartJianyingExportSchema = z.object({
  runId: z.string().describe('任务 ID，必须已经落过剪映工程'),
  allowGui: z.boolean().default(false).describe('true 才让本机驱动剪映导出面板；用户正在用机器时会被推迟'),
  waitIdle: z.number().min(0).max(600).optional()
    .describe('等空闲多少秒再点导出，0=立刻；点完立刻把前台和光标还给用户'),
  claimPath: z.string().min(1).optional()
    .describe('用户已在自己剪映里点过导出：成片绝对路径。只做体检入库，不开面板不动鼠标'),
})

const CreateRunSchema = z.object({
  projectId: z.string().describe('项目 ID'),
  title: z.string().min(1).describe('任务标题'),
  brief: z.string().default('').describe('用户原始需求，用自然语言写'),
  engine: z.string().optional().describe('渲染通道：motion/slides/jianying/timeline，缺省走 MoneyPrinterTurbo'),
  template: z.string().optional().describe('版式模板，比如 factcard（竖屏干货卡）'),
  spec: z.record(z.string(), z.unknown()).optional().describe('结构化制作规格：mode="vertical_short" 时给 topic/points/script/materials/bgm；bgm={src, volume?, bpm?, offset?, beats?, pulse?{amount?,decay?}}，bpm 或 beats 决定拍点、pulse 决定拍点上是否推静帧镜头；进阶可直接给 canvas/video/overlay/audio/music，video 每段的 keyframes=[{at,zoom}] 是折线推镜关键帧'),
  facts: z.record(z.string(), z.unknown()).optional().describe('项目事实：产品名、卖点、禁止出现的说法'),
})

const BatchRunSchema = CreateRunSchema.extend({
  count: z.number().int().min(1).max(8).default(2).describe('排几条：同一份稿子出多个母版，每条独立入队'),
  variants: z.array(z.enum(['mirror', 'opening', 'hook'])).optional()
    .describe('逐条错开哪几维：mirror 换运镜方向、opening 换开场镜头、hook 换开头字幕。不传则三维全用；第 1 条永远是原稿'),
})

const RunIdSchema = z.object({
  runId: z.string().describe('任务 ID'),
})

const ListRunsSchema = z.object({
  pageNo: z.number().int().min(1).default(1).describe('页码'),
  pageSize: z.number().int().min(1).max(100).default(20).describe('每页条数'),
  status: z.string().optional().describe('按状态过滤：queued/running/succeeded/failed/canceled'),
  projectId: z.string().optional().describe('只看某个项目名下的任务'),
})

const ReviseRunSchema = z.object({
  runId: z.string().describe('要修改的任务 ID'),
  brief: z.string().optional().describe('修改后的需求'),
  spec: z.record(z.string(), z.unknown()).optional().describe('规格补丁，会与现有规格合并'),
  scenes: z.array(z.object({
    index: z.number().int().min(0).describe('第几屏，从 0 数'),
    text: z.string().optional().describe('这一屏的字幕'),
    duration: z.number().min(1).optional().describe('这一屏的时长（秒）'),
    src: z.string().optional().describe('这一屏的素材路径'),
  })).max(50).optional().describe('按场景逐条改，一次只改一项；适合只换一句字幕或一处素材'),
})

const OpenJianyingDraftSchema = z.object({
  runId: z.string().describe('任务 ID'),
  draftPath: z.string().min(1).optional().describe('草稿目录；省略就用任务上次落的草稿'),
  check: z.boolean().optional().describe('true 只体检（草稿目录和剪映都在不在），不打开、不抢前台'),
})

const SubmitRenderSchema = z.object({
  kind: z.enum(StudioJobKind).describe('本机渲染通道：timeline 时间线成片 / motion 动效 / slides 图文 / jianying 剪映草稿 / paper 长图'),
  payload: z.record(z.string(), z.unknown()).describe('通道入参；timeline 要 canvas+video+text+audio，轴内 src 用绝对路径'),
})

const RenderJobIdSchema = z.object({
  jobId: z.string().describe('渲染队列任务 ID'),
})

const StudioDeliverablesSchema = z.object({
  limit: z.number().int().min(1).max(200).default(20).describe('最多返回多少条，新的在前'),
})

const PlanPublicationSchema = z.object({
  runId: z.string().describe('任务 ID（必须有已验证的成片）'),
  platform: z.string().default('douyin').describe('目标平台'),
  accountRef: z.string().describe('账号标识；抖音请用 SAU 中已登录的账号名'),
  accountName: z.string().optional().describe('账号显示名'),
  plannedAt: z.string().optional().describe('计划发布时间（ISO 8601）；不传表示手动触发'),
  caption: z.string().optional().describe('发布正文'),
  tags: z.array(z.string()).optional().describe('话题标签'),
})

@Controller()
@Injectable()
export class ProductionMcpController {
  constructor(
    private readonly production: ProductionService,
    private readonly assistant: ProductionAssistantService,
  ) {}

  /**
   * Mongoose documents carry a raw `_id`, which YAML-serialises into a byte
   * buffer ({"0":106,"1":217,...}) — unreadable for the agent and pure noise in
   * its context. Every domain object already has a string `id`, so drop `_id`
   * and return plain JSON-shaped data instead.
   */
  private normalize<T>(input: T): unknown {
    return JSON.parse(
      JSON.stringify(input, (key, value) => (key === '_id' ? undefined : value)),
    )
  }

  @Tool({
    name: 'createProductionProject',
    description: 'Create a production project. A project groups tasks and binds a draft box; create one before creating runs.',
    parameters: CreateProjectSchema,
  })
  async createProductionProject(params: z.infer<typeof CreateProjectSchema>) {
    const user = getUser()
    const project = await this.production.createProject(user.id, params)
    return toYamlTextResult(this.normalize(project))
  }

  @Tool({
    name: 'listProductionProjects',
    description: 'List this user\'s production projects with their bound draft boxes.',
    parameters: z.object({}),
  })
  async listProductionProjects() {
    const user = getUser()
    const projects = await this.production.listProjects(user.id)
    return toYamlTextResult(this.normalize(projects))
  }

  @Tool({
    name: 'createProductionRun',
    description: 'Queue a video production task and return its runId immediately — this does NOT wait for the render. Poll getProductionRun to follow progress; the worker renders, uploads, and attaches the draft.',
    parameters: CreateRunSchema,
  })
  async createProductionRun(params: z.infer<typeof CreateRunSchema>) {
    const user = getUser()
    const run = await this.production.createRun(user.id, {
      projectId: params.projectId,
      title: params.title,
      brief: params.brief ?? '',
      spec: normalizeRunSpec(params),
      facts: params.facts,
    })
    return toTextResult(
      `Run queued: ${run.id} (status=${run.status}). `
      + `Do not block waiting for the video; poll getProductionRun with runId=${run.id}. `
      + 'The draft appears in the bound draft box once the worker finishes.',
    )
  }

  @Tool({
    name: 'createProductionBatch',
    description: 'Queue the same script N times (1-8) and return every runId at once — use this for batch generation of short videos, not N createProductionRun calls. Each run renders independently and its title carries an index. Poll each runId with getProductionRun.',
    parameters: BatchRunSchema,
  })
  async createProductionBatch(params: z.infer<typeof BatchRunSchema>) {
    const user = getUser()
    const { count, variants, ...one } = params
    const runs = await this.production.createRunBatch(user.id, {
      projectId: one.projectId,
      title: one.title,
      brief: one.brief ?? '',
      spec: normalizeRunSpec(one),
      facts: one.facts,
    }, count, variants)
    return toTextResult(
      `Queued ${runs.length} run(s): ${runs.map(r => r.id).join(', ')}. `
      + 'Do not block; poll getProductionRun per runId.',
    )
  }

  @Tool({
    name: 'getProductionRun',
    description: 'Get one production run: status, stage, progress, and the attached draft ID. Use this to poll instead of waiting on createProductionRun.',
    parameters: RunIdSchema,
  })
  async getProductionRun(params: z.infer<typeof RunIdSchema>) {
    const user = getUser()
    const run = await this.production.runDetail(user.id, params.runId)
    return toYamlTextResult(this.normalize(run))
  }

  @Tool({
    name: 'listProductionRuns',
    description: 'List recent production runs, newest first, optionally filtered by status.',
    parameters: ListRunsSchema,
  })
  async listProductionRuns(params: z.infer<typeof ListRunsSchema>) {
    const user = getUser()
    const page = await this.production.listRuns(
      user.id,
      params.pageNo,
      params.pageSize,
      params.status,
      params.projectId,
    )
    return toYamlTextResult(this.normalize(page))
  }

  @Tool({
    name: 'reviseProductionRun',
    description: 'Request a precise revision of a task (e.g. change one subtitle, swap one segment). Merges the patch into the existing spec and starts a new render.',
    parameters: ReviseRunSchema,
  })
  async reviseProductionRun(params: z.infer<typeof ReviseRunSchema>) {
    const user = getUser()
    const run = await this.production.reviseRun(user.id, params.runId, {
      brief: params.brief,
      spec: params.spec,
      scenes: params.scenes,
    })
    if (!run) {
      return toTextResult(`Run ${params.runId} could not be revised: it no longer exists.`)
    }
    return toTextResult(
      `Revision queued as run ${run.id} (status=${run.status}). Poll getProductionRun to follow it.`,
    )
  }

  @Tool({
    name: 'listProductionEvents',
    description: 'Read the stage/timing timeline of a run: what the worker did, when, and any error. Use this to explain a failure instead of guessing.',
    parameters: RunIdSchema,
  })
  async listProductionEvents(params: z.infer<typeof RunIdSchema>) {
    const user = getUser()
    const events = await this.production.listEvents(user.id, params.runId)
    return toYamlTextResult(this.normalize(events))
  }

  @Tool({
    name: 'createJianyingDraft',
    description: 'Turn a finished run into a 剪映 draft in the user\'s default material group, so the user can keep editing it in the desktop app. Fails when the run has no rendered output yet; repeat calls reuse the same draft.',
    parameters: CreateJianyingDraftSchema,
  })
  async createJianyingDraft(params: z.infer<typeof CreateJianyingDraftSchema>) {
    const user = getUser()
    const run = await this.production.getRun(user.id, params.runId)
    const videoKey = run.output['videoPath'] as string | undefined
    if (!videoKey)
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run has no rendered output yet; render it first')
    const material = await this.production.attachMaterialForWorker(user.id, params.runId, {
      videoKey,
      title: params.title || run.title,
    })
    return toYamlTextResult({ materialId: material.materialId, deduped: material.deduped })
  }

  @Tool({
    name: 'startJianyingExport',
    description: 'Queue a separate export job that turns the run\'s 剪映 draft into a video file. On macOS 剪映 needs a human to press export: without allowGui the job only records that the draft is ready and the run waits for the user; with allowGui the local worker drives the export panel and backs off when the machine is busy. Never claims a video before the file exists.',
    parameters: StartJianyingExportSchema,
  })
  async startJianyingExport(params: z.infer<typeof StartJianyingExportSchema>) {
    const user = getUser()
    const job = await this.production.startJianyingExport(user.id, params.runId, {
      allowGui: params.allowGui,
      ...(typeof params.waitIdle === 'number' ? { waitIdle: params.waitIdle } : {}),
      ...(params.claimPath ? { claimPath: params.claimPath } : {}),
    })
    return toYamlTextResult(job)
  }

  @Tool({
    name: 'openJianyingDraft',
    description: 'Open an existing 剪映 draft for this run in the desktop app so the user can keep editing. It only opens — nothing is exported, the run status does not change, and 剪映 takes the foreground, so call it only when the user asked to see or edit the draft.',
    parameters: OpenJianyingDraftSchema,
  })
  async openJianyingDraft(params: z.infer<typeof OpenJianyingDraftSchema>) {
    const user = getUser()
    const opened = await this.production.openDraft(user.id, params.runId, {
      ...(params.draftPath ? { draftPath: params.draftPath } : {}),
      ...(params.check ? { check: true } : {}),
    })
    return toYamlTextResult(opened)
  }

  @Tool({
    name: 'planPublication',
    description: 'Record intent to publish a run. This creates a ledger row only — nothing is uploaded, and there is no tool here that submits. Publishing must be done by the user in the workbench or explicitly via the API.',
    parameters: PlanPublicationSchema,
  })
  async planPublication(params: z.infer<typeof PlanPublicationSchema>) {
    const user = getUser()
    const { attempt, deduped } = await this.production.planPublication(user.id, {
      runId: params.runId,
      platform: params.platform ?? 'douyin',
      // The local stack drives Douyin through the SAU browser worker; anything
      // else would need a server-side dispatcher this build does not ship.
      dispatcher: PublicationDispatcher.SAU,
      accountRef: params.accountRef,
      accountName: params.accountName,
      plannedAt: params.plannedAt,
      content: {
        caption: params.caption,
        tags: params.tags,
      },
    })
    return toYamlTextResult({ attemptId: attempt.id, status: attempt.status, deduped })
  }

  @Tool({
    name: 'submitRender',
    description: 'Queue a render on the local machine and return a jobId immediately — it does not wait for the video. Poll getRenderJob until status is succeeded/failed. Use this only when the user asked for a video now; a production run (createProductionRun) is the higher-level path with ledger and publishing.',
    parameters: SubmitRenderSchema,
  })
  async submitRender(params: z.infer<typeof SubmitRenderSchema>) {
    getUser()
    const job = await this.assistant.submitQueueJob(params.kind, params.payload)
    return toYamlTextResult(this.normalize(job))
  }

  @Tool({
    name: 'getRenderJob',
    description: 'Read one render job: status, progress, stage, output files and error. A missing jobId returns null, which means the queue has no such job — say that instead of inventing an output path.',
    parameters: RenderJobIdSchema,
  })
  async getRenderJob(params: z.infer<typeof RenderJobIdSchema>) {
    getUser()
    const job = await this.assistant.readQueueJob(params.jobId)
    if (!job)
      return toTextResult(`队列里没有任务 ${params.jobId}`)
    return toYamlTextResult(this.normalize(job))
  }

  @Tool({
    name: 'cancelRenderJob',
    description: 'Cancel a queued or running render job. `cancelled: false` means the job had already finished or the id is unknown; re-read it with getRenderJob before reporting.',
    parameters: RenderJobIdSchema,
  })
  async cancelRenderJob(params: z.infer<typeof RenderJobIdSchema>) {
    getUser()
    const job = await this.assistant.stopQueueJob(params.jobId)
    if (!job)
      return toTextResult(`队列里没有任务 ${params.jobId}`)
    return toYamlTextResult(this.normalize(job))
  }

  @Tool({
    name: 'listStudioInventory',
    description: 'Read the render host\'s capability inventory: platform accounts with readiness, voice counts, and material packs usable as run materials. Call this before choosing materials or before telling the user a platform is unavailable.',
    parameters: z.object({}),
  })
  async listStudioInventory() {
    getUser()
    const inventory = await this.assistant.capabilityInventory()
    if (!inventory)
      return toTextResult('宿主机能力服务连不上：素材包、音色与平台账号都读不到。先在宿主机启动 mac worker 的 capabilities server，再重试。')
    return toYamlTextResult(this.normalize(inventory))
  }

  @Tool({
    name: 'listStudioAssets',
    description: 'List what this render host can actually use: deployable specs, material shot roots, and recent films, with absolute paths. Material pack refs look like pack:<slug>/<name> and can be passed straight into a run spec.',
    parameters: z.object({}),
  })
  async listStudioAssets() {
    getUser()
    const assets = await this.assistant.capabilityAssets()
    return toYamlTextResult(this.normalize(assets))
  }

  @Tool({
    name: 'listStudioDeliverables',
    description: 'List finished deliverables already landed on this host (absolute path, size, mtime). Use it to confirm a render produced a file instead of trusting a run status alone.',
    parameters: StudioDeliverablesSchema,
  })
  async listStudioDeliverables(params: z.infer<typeof StudioDeliverablesSchema>) {
    getUser()
    const files = await this.assistant.capabilityDeliverables(params.limit)
    return toYamlTextResult(this.normalize(files))
  }

  @Tool({
    name: 'listPublications',
    description: 'List publication ledger rows. `submission_unknown` means the platform response was ambiguous: the work may or may not be live, and it is never resubmitted automatically — the user must reconcile it.',
    parameters: z.object({
      runId: z.string().optional().describe('只看某个任务的发布记录'),
    }),
  })
  async listPublications(params: { runId?: string }) {
    const user = getUser()
    const rows = await this.production.listAttempts(user.id, params.runId)
    return toYamlTextResult(this.normalize(rows))
  }
}
