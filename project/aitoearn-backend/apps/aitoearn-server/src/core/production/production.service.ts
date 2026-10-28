import { createHash, randomUUID } from 'node:crypto'
import { Injectable, Logger } from '@nestjs/common'
import { AppException, ResponseCode, UserType } from '@yikart/common'
import {
  MaterialStatus,
  MaterialType,
  MediaType,
  ProductionEventRepository,
  ProductionJob,
  ProductionJobKind,
  ProductionJobRepository,
  ProductionJobStatus,
  ProductionProject,
  ProductionProjectRepository,
  ProductionRun,
  ProductionRunRepository,
  ProductionRunStatus,
  ProductionWorkerRepository,
  PublicationAttempt,
  PublicationAttemptRepository,
  PublicationDispatcher,
  PublicationStatus,
} from '@yikart/mongodb'
import { MaterialGroupService } from '../content/material-group.service'
import { MaterialService } from '../content/material.service'
import { buildTimelineSpec, mirrorKeyframes, rotateOpening } from './timeline-spec'

export const LEASE_MS = 60_000
export const RENDER_LANE = 'render'
export const PUBLISH_LANE = 'publish'

/** 旧 job 没有 revision 字段（上线前入队），一律按 revision 1 处理。 */
function revisionOf(job: Pick<ProductionJob, 'revision'>): number {
  return job.revision || 1
}

/**
 * 渲染 spec → job kind。创建、改稿重排、超时补任务都走这一处，否则补出来的
 * 任务会退回默认引擎，和原始 spec 不符。
 */
/**
 * 环境类失败（缺依赖、缺文件、缺权限）不是稿子的问题，重排一次往往就好了。
 * 这类错误以前直接把 run 记成 failed，用户看到的是「渲染失败」，其实是本机没装东西。
 */
const ENV_FAILURE_MARKS = [
  'ModuleNotFoundError',
  'ImportError',
  'FileNotFoundError',
  'PermissionError',
  'No module named',
  'is not defined',
]

function isEnvironmentFailure(error: string): boolean {
  return ENV_FAILURE_MARKS.some(mark => error.includes(mark))
}

function renderKindOf(spec: Record<string, unknown>): ProductionJobKind {
  const engine = spec['engine']
  if (engine === 'motion') {
    return ProductionJobKind.MOTION_RENDER
  }
  if (engine === 'slides') {
    return ProductionJobKind.SLIDES_RENDER
  }
  if (engine === 'jianying') {
    return ProductionJobKind.JIANYING_DRAFT
  }
  if (engine === 'timeline') {
    return ProductionJobKind.TIMELINE_RENDER
  }
  return ProductionJobKind.RENDER_MPT
}

export interface CreateRunInput {
  projectId: string
  title: string
  brief: string
  spec?: Record<string, unknown>
  facts?: Record<string, unknown>
}

/**
 * 把 worker 回传的 result 归一成 run.output，前端只读 output，不再自己解析 deliveries。
 *
 * 没有产物的完成（例如 publish_sau 只回传发布证据）返回 null，调用方保持原 output，
 * 否则会把渲染阶段写进去的 videoPath/materialId 清空。
 */
function normalizeRunOutput(
  existing: Record<string, unknown>,
  result: Record<string, unknown>,
): Record<string, unknown> | null {
  const deliveries = Array.isArray(result['deliveries']) ? result['deliveries'] : []
  const platforms: Record<string, Record<string, unknown>> = {}
  for (const row of deliveries) {
    if (row === null || typeof row !== 'object') {
      continue
    }
    const delivery = row as Record<string, unknown>
    const platform = typeof delivery['platform'] === 'string' ? delivery['platform'] : ''
    const key = typeof delivery['key'] === 'string' ? delivery['key'] : ''
    // 平台行上传失败时只有 uploadError、没有 key，不能算成可发布产物
    if (!platform || !key) {
      continue
    }
    const probe = delivery['probe'] !== null && typeof delivery['probe'] === 'object'
      ? delivery['probe'] as Record<string, unknown>
      : {}
    platforms[platform] = {
      key,
      url: typeof delivery['url'] === 'string' ? delivery['url'] : '',
      width: Number(probe['width']) || 0,
      height: Number(probe['height']) || 0,
      bytes: Number(delivery['bytes']) || 0,
    }
  }

  // 剪映草稿不出视频文件，但文案体检对写稿的人有用，跟着 output 一起回去。
  const copyCheck = result['copyCheck'] !== null && typeof result['copyCheck'] === 'object'
    ? result['copyCheck'] as Record<string, unknown>
    : null

  const videoQa = result['videoQa'] !== null && typeof result['videoQa'] === 'object'
    ? result['videoQa'] as Record<string, unknown>
    : null

  const videoKey = typeof result['videoKey'] === 'string' ? result['videoKey'] : ''
  const draftPath = typeof result['draftPath'] === 'string' ? result['draftPath'] : ''
  // worker 把「要人点导出」放在 result.output 里，老结果也可能只在 stage 上体现
  const outputMeta = result['output'] !== null && typeof result['output'] === 'object'
    ? result['output'] as Record<string, unknown>
    : {}
  const requiresManualExport = result['requiresManualExport'] === true
    || outputMeta['requiresManualExport'] === true
    || result['stage'] === 'awaiting_manual_export'
  if (!videoKey && !draftPath && !Object.keys(platforms).length && !copyCheck && !videoQa) {
    return null
  }
  const output: Record<string, unknown> = { ...existing }
  // 草稿目录是收导出产物的唯一入口：出草稿的 job 不产视频，这条路径不写进
  // output 的话，后面的 jianying_export 只能靠猜最新草稿。
  if (draftPath) {
    output['draftPath'] = draftPath
  }
  if (requiresManualExport || typeof result['requiresManualExport'] === 'boolean') {
    output['requiresManualExport'] = requiresManualExport
  }
  // 为什么停下要能传到界面上：锁屏和「用户忙」给的动作不一样。
  const manualReason = typeof result['reasonCode'] === 'string' ? result['reasonCode'] : ''
  if (requiresManualExport && manualReason) {
    output['manualExportReason'] = manualReason
  }
  if (videoKey) {
    output['videoPath'] = videoKey
  }
  if (typeof result['videoUrl'] === 'string' && result['videoUrl']) {
    output['videoUrl'] = result['videoUrl']
  }
  if (typeof result['duration'] === 'number' && result['duration'] > 0) {
    output['duration'] = result['duration']
  }
  if (result['canvas'] !== null && typeof result['canvas'] === 'object') {
    output['canvas'] = result['canvas']
  }
  if (Object.keys(platforms).length) {
    output['platforms'] = platforms
  }
  if (copyCheck) {
    output['copyCheck'] = copyCheck
  }
  if (videoQa) {
    output['videoQa'] = videoQa
  }
  // 成片入库的落点要跟着 output 回前端：库里按规范命名，界面和发布都认这一条路径
  for (const key of ['libraryPath', 'libraryAssetId', 'md5'] as const) {
    const value = result[key]
    if (typeof value === 'string' && value) {
      output[key] = value
    }
  }
  if (Array.isArray(result['libraryEntries'])) {
    output['libraryEntries'] = result['libraryEntries']
  }
  return output
}

/**
 * Owns the production ledger: runs, render jobs, worker leases and publication
 * intents. Every state transition that matters for correctness is a conditionally
 * guarded Mongo update rather than a read-then-write.
 */
@Injectable()
export class ProductionService {
  private readonly logger = new Logger(ProductionService.name)

  constructor(
    private readonly projectRepo: ProductionProjectRepository,
    private readonly runRepo: ProductionRunRepository,
    private readonly jobRepo: ProductionJobRepository,
    private readonly attemptRepo: PublicationAttemptRepository,
    private readonly workerRepo: ProductionWorkerRepository,
    private readonly eventRepo: ProductionEventRepository,
    private readonly materialService: MaterialService,
    private readonly materialGroupService: MaterialGroupService,
  ) {}

  // ---------------------------------------------------------------- projects

  async listProjects(userId: string) {
    const projects = await this.projectRepo.listByUser(userId)
    if (projects.length) {
      return projects
    }
    // First use: a sane default project so the workbench is never empty.
    const created = await this.projectRepo.create({
      userId,
      name: '默认项目',
      isDefault: true,
      accountBindings: [],
      brand: {},
      schedulePolicy: {},
    } as Partial<ProductionProject>)
    return [created]
  }

  async getProject(userId: string, projectId: string) {
    // 传进来的不是 ObjectId 时 Mongo 会抛 CastError，落到接口上就是 500。
    // 这种输入本来就是「找不到这个项目」，先挡掉再查库。
    if (!/^[a-f\d]{24}$/i.test(String(projectId || ''))) {
      throw new AppException(ResponseCode.ProductionProjectNotFound)
    }
    const project = await this.projectRepo.getByIdForUser(projectId, userId)
    if (!project) {
      throw new AppException(ResponseCode.ProductionProjectNotFound)
    }
    return project
  }

  async createProject(userId: string, data: Partial<ProductionProject>) {
    return await this.projectRepo.create({ ...data, userId } as Partial<ProductionProject>)
  }

  async updateProject(userId: string, projectId: string, data: Partial<ProductionProject>) {
    await this.getProject(userId, projectId)
    return await this.projectRepo.updateById(projectId, { ...data, userId } as never)
  }

  // -------------------------------------------------------------------- runs

  async createRun(userId: string, input: CreateRunInput): Promise<ProductionRun> {
    const project = await this.getProject(userId, input.projectId)
    const run = await this.runRepo.create({
      userId,
      projectId: project.id,
      title: input.title,
      brief: input.brief,
      revision: 1,
      facts: input.facts ?? {},
      // 友好输入在入库前就展开成渲染器规格，worker 拿到的 job payload 永远是可渲染的
      spec: buildTimelineSpec(input.spec ?? {}),
      status: ProductionRunStatus.QUEUED,
      currentStage: 'queued',
      checkpoints: [{ at: new Date(), stage: 'queued', note: 'run created' }],
    } as unknown as Partial<ProductionRun>)

    await this.enqueueRender(userId, run)
    await this.appendEvent(run.id, {
      kind: 'run.created',
      stage: 'queued',
      message: `run ${run.title} queued (revision 1)`,
    })
    return run
  }

  /**
   * 批量出片：同一份稿子排 count 条。每条仍是独立的 run/job，worker 按队列逐条
   * 渲，互不覆盖；标题带序号，列表里能直接分清是哪一条。
   */
  async createRunBatch(userId: string, input: CreateRunInput, count: number): Promise<ProductionRun[]> {
    const runs: ProductionRun[] = []
    for (let i = 0; i < count; i += 1) {
      // 第 1 条按原稿；第 2 条只镜像推拉；第 3 条起再换开场镜头，
      // 三条两两不同才叫多版，否则批到第三条又撞回第一条。
      let spec = input.spec
      const timeline = input.spec?.['timeline']
      if (i > 0 && timeline) {
        let variant = mirrorKeyframes(timeline as Record<string, unknown>)
        if (i > 1) variant = rotateOpening(variant)
        spec = { ...input.spec, timeline: variant }
      }
      runs.push(await this.createRun(userId, {
        ...input,
        spec,
        title: count > 1 ? `${input.title} · ${i + 1}/${count}` : input.title,
      }))
    }
    return runs
  }

  /**
   * Create a new revision and a render job for it. Prior publication approvals
   * do not carry over: any attempt bound to an older revision stays as-is and a
   * fresh intent must be created before publishing again.
   */
  async reviseRun(userId: string, runId: string, patch: { brief?: string, spec?: Record<string, unknown> }) {
    const run = await this.getRun(userId, runId)
    // 只有已结束的 run 才能改稿：排队中/渲染中/待用户的 run 改稿会和正在跑的
    // job 抢同一个 runId，旧 revision 的结果回来时没人知道该写哪一版。
    const revisable: ProductionRunStatus[] = [
      ProductionRunStatus.SUCCEEDED,
      ProductionRunStatus.FAILED,
      ProductionRunStatus.CANCELLED,
      ProductionRunStatus.WAITING_USER,
    ]
    if (!revisable.includes(run.status)) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run is still rendering; wait or cancel first')
    }
    const revision = run.revision + 1
    const patchSpec = patch.spec ?? {}
    // 改 spec 时以友好输入的底稿为基线重新展开，避免把上一次的展开结果再展开一遍
    const baseSpec = patchSpec['timeline'] === undefined && run.spec['input']
      ? run.spec['input'] as Record<string, unknown>
      : run.spec
    // CAS：读到 revision N 就只能把 run 从 N 推到 N+1。并发改稿时后到的那次
    // 拿不到 CAS，不会把 revision 覆盖回去，也不会为同一 revision 建两条 job。
    const updated = await this.runRepo.updateByRevision(run.id, run.revision, {
      revision,
      brief: patch.brief ?? run.brief,
      spec: buildTimelineSpec({ ...baseSpec, ...patchSpec }),
      status: ProductionRunStatus.QUEUED,
      currentStage: 'queued',
      lastError: '',
      checkpoints: [
        ...(run.checkpoints ?? []),
        { at: new Date(), stage: 'revise', note: `revision ${revision}` },
      ],
    } as never)
    if (!updated) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run changed concurrently; reload and retry')
    }

    // 旧 revision 还排在队列里（或正被 worker 持有）的 job 全部作废，并推进
    // fencing token：即使 worker 还握着旧租约，之后的写回也会落空。
    const staleJobs = await this.jobRepo.listByRunBeforeRevision(run.id, revision)
    for (const job of staleJobs) {
      const cancelled = await this.jobRepo.updateByStatusIn(
        job.id,
        [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING],
        {
          $set: { status: ProductionJobStatus.CANCELLED, finishedAt: new Date(), message: `superseded by revision ${revision}` },
          $inc: { leaseVersion: 1 },
        },
      )
      if (!cancelled) {
        continue
      }
      await this.appendEvent(run.id, {
        kind: 'job.cancelled',
        jobId: job.id,
        stage: job.stage,
        message: `superseded by revision ${revision}`,
      })
    }

    const job = await this.enqueueRender(userId, updated as unknown as ProductionRun)
    await this.appendEvent(run.id, {
      kind: 'run.revised',
      stage: 'queued',
      message: `revision ${revision} queued (attempt ${job.attempt})`,
    })
    return updated
  }

  async getRun(userId: string, runId: string): Promise<ProductionRun> {
    const run = await this.runRepo.getByIdForUser(runId, userId)
    if (!run) {
      throw new AppException(ResponseCode.ProductionRunNotFound)
    }
    return run
  }

  async listRuns(userId: string, pageNo = 1, pageSize = 20, status?: string | undefined) {
    const [list, total] = await this.runRepo.listByUser(userId, pageNo, pageSize, status ? { status } : {})
    return { list, total, pageNo, pageSize }
  }

  async runDetail(userId: string, runId: string) {
    const run = await this.getRun(userId, runId)
    const [jobs, attempts, events] = await Promise.all([
      this.jobRepo.listByRun(runId),
      this.attemptRepo.listByRun(runId),
      this.eventRepo.listByRun(runId),
    ])
    return { run, jobs, attempts, events }
  }

  async retryRun(userId: string, runId: string) {
    const run = await this.getRun(userId, runId)
    // 重试沿用这条 run 真正的渲染引擎；之前写死 RENDER_MPT，slides/motion/timeline
    // 的 run 重试会被排到素材拼贴引擎上，attempt 也按错的 kind 算。
    const updated = await this.runRepo.updateByRevision(
      runId,
      run.revision,
      {
        status: ProductionRunStatus.QUEUED,
        currentStage: 'queued',
        lastError: '',
      } as never,
      { status: { $ne: ProductionRunStatus.RUNNING } },
    )
    if (!updated) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run is running; cancel or wait first')
    }
    // 用改状态前的 run 取引擎：CAS 返回值只保证状态，spec 以读到的这条为准。
    const kind = renderKindOf((run.spec ?? {}) as Record<string, unknown>)
    const job = await this.enqueueRender(userId, run, kind)
    await this.appendEvent(runId, { kind: 'run.retried', stage: 'queued', message: `attempt ${job.attempt}` })
    return { runId, attempt: job.attempt }
  }

  async cancelRun(userId: string, runId: string) {
    const run = await this.getRun(userId, runId)
    const jobs = await this.jobRepo.listByRun(runId)
    const pending = jobs.filter((j: ProductionJob) =>
      j.status === ProductionJobStatus.QUEUED || j.status === ProductionJobStatus.RUNNING,
    )
    let cancelled = 0
    for (const job of pending) {
      // 条件更新 + fencing token：worker 已经拿着租约在跑时，取消也会把
      // leaseVersion 推进一格，让它随后的 heartbeat/complete/fail 全部落空。
      const done = await this.jobRepo.updateByStatusIn(
        job.id,
        [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING],
        {
          $set: { status: ProductionJobStatus.CANCELLED, finishedAt: new Date(), message: 'cancelled by user' },
          $inc: { leaseVersion: 1 },
        },
      )
      if (!done) {
        continue
      }
      cancelled += 1
      await this.appendEvent(runId, {
        kind: 'job.cancelled',
        jobId: job.id,
        stage: job.stage,
        message: 'cancelled by user',
      })
    }
    await this.runRepo.updateByRevision(runId, run.revision, {
      status: ProductionRunStatus.CANCELLED,
      currentStage: 'cancelled',
    } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
    return { cancelled }
  }

  /**
   * 起一个独立的剪映导出任务：草稿 → 成片。
   *
   * 默认不动 GUI（allowGui=false），只把「该导出了」记进台账，worker 回报
   * awaiting_manual_export 后 run 停在 waiting_user；带 allowGui=true 才让
   * worker 驱动导出面板，导出端自己会等用户空闲，等不到就退 9 继续排队。
   */
  async startJianyingExport(
    userId: string,
    runId: string,
    input: { allowGui?: boolean, draftPath?: string, waitIdle?: number },
  ) {
    const run = await this.getRun(userId, runId)
    if (run.status === ProductionRunStatus.CANCELLED) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run is cancelled; cannot export')
    }
    const draftPath = input.draftPath || (run.output['draftPath'] as string | undefined) || ''
    if (input.allowGui && !draftPath) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        'Run has no jianying draft path to export; build the draft first',
      )
    }
    const attempt = await this.jobRepo.nextAttempt(run.id, ProductionJobKind.JIANYING_EXPORT)
    const job = await this.jobRepo.create({
      userId,
      runId: run.id,
      revision: run.revision,
      kind: ProductionJobKind.JIANYING_EXPORT,
      attempt,
      status: ProductionJobStatus.QUEUED,
      lane: RENDER_LANE,
      payload: {
        runId: run.id,
        revision: run.revision,
        draftPath,
        allowGui: input.allowGui === true,
        libraryProject: run.projectId,
        libraryTitle: run.title,
        ...(typeof input.waitIdle === 'number' ? { waitIdle: input.waitIdle } : {}),
        platform: 'douyin',
      },
    } as unknown as Partial<ProductionJob>)
    // 正在跑的渲染不抢，等它释放 lane 之后 worker 自然领这条导出任务
    await this.runRepo.updateByRevision(run.id, run.revision, {
      status: ProductionRunStatus.QUEUED,
      currentStage: 'export_queued',
      lastError: '',
    } as never, { status: { $nin: [ProductionRunStatus.CANCELLED, ProductionRunStatus.RUNNING] } })
    await this.appendEvent(run.id, {
      kind: 'job.queued',
      jobId: job.id,
      stage: 'export_queued',
      message: `jianying export attempt ${job.attempt}`,
    })
    return { jobId: job.id, attempt: job.attempt, draftPath }
  }

  private async enqueueRender(
    userId: string,
    run: ProductionRun,
    kind: ProductionJobKind = renderKindOf((run.spec ?? {}) as Record<string, unknown>),
  ) {
    // 同一条 run 可以交给多种渲染引擎：
    //   engine=slides   → 知识幻灯片（HTML 信息图 + 口播），逐屏静态；
    //   engine=motion   → GSAP 时间轴逐帧渲染，有真正的动效语言；
    //   engine=jianying → 只出剪映草稿，把转场/特效/文字动画交给剪映原生资源
    //                     （macOS 无自动导出，交付物是草稿目录）；
    //   engine=timeline → 我们自己的 timeline spec 直接渲染母版，纯 ffmpeg、
    //                     不启剪映、不抢前台，可后台排队也可搬进容器；
    //   默认            → MoneyPrinterTurbo 素材拼贴。
    // 队列、租约、草稿投递完全复用，只有 worker 侧的分派不同。
    // attempt 按 run+kind 全局递增（不按 revision 重置），旧库上残留的
    // (runId, kind, attempt) 唯一索引也不会和新 revision 的任务撞车。
    const attempt = await this.jobRepo.nextAttempt(run.id, kind)
    const job = await this.jobRepo.create({
      userId,
      runId: run.id,
      revision: run.revision,
      kind,
      attempt,
      status: ProductionJobStatus.QUEUED,
      lane: RENDER_LANE,
      payload: {
        runId: run.id,
        revision: run.revision,
        spec: run.spec,
        brief: run.brief,
        // 入库命名要用人看得懂的标题，worker 回退到 runId 只会得到一串十六进制
        libraryTitle: run.title,
      },
    } as unknown as Partial<ProductionJob>)
    return job
  }

  // ------------------------------------------------------------------- jobs

  async claimJob(worker: { id: string, userId: string, capabilities: string[] }, lane: string) {
    // 空 capabilities 的 worker 不该领到任何东西：之前形参默认值是「全部渲染
    // 种类」，等于没配能力的 worker 能领所有任务。
    const capabilities = worker.capabilities ?? []
    if (!capabilities.length) {
      return null
    }
    if (lane === PUBLISH_LANE) {
      // one browser at a time per user, always
      const running = await this.jobRepo.listByStatus(PUBLISH_LANE, ProductionJobStatus.RUNNING, worker.userId)
      if (running.length) {
        return null
      }
    }
    for (let i = 0; i < 3; i++) {
      const job = await this.jobRepo.claimNext(lane, worker.id, LEASE_MS, capabilities, worker.userId)
      if (!job) {
        return null
      }
      // 一条 run 只该有一个活着的渲染：心跳超时补出来的新 job 可能在旧 job
      // 完成之后才被领取，此时 run 已经 succeeded，再渲一遍纯属白烧机器。
      // revision 对不上说明这条 job 属于旧稿，渲染结果不能写回当前 run。
      const run = await this.runRepo.getById(job.runId)
      if (run && revisionOf(job) !== (run.revision || 1)) {
        await this.jobRepo.updateByStatusIn(
          job.id,
          [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING],
          {
            $set: { status: ProductionJobStatus.CANCELLED, finishedAt: new Date(), message: `superseded by revision ${run.revision}` },
            $inc: { leaseVersion: 1 },
          },
        )
        await this.appendEvent(job.runId, {
          kind: 'job.cancelled',
          jobId: job.id,
          stage: job.stage,
          message: `dropped: revision ${revisionOf(job)} superseded by ${run.revision}`,
        })
        continue
      }
      if (run && (run.status === ProductionRunStatus.SUCCEEDED
        || run.status === ProductionRunStatus.CANCELLED
        || run.status === ProductionRunStatus.FAILED)) {
        await this.jobRepo.updateByStatusIn(
          job.id,
          [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING],
          {
            $set: { status: ProductionJobStatus.CANCELLED, finishedAt: new Date(), message: `run already ${run.status}` },
            $inc: { leaseVersion: 1 },
          },
        )
        await this.appendEvent(job.runId, {
          kind: 'job.cancelled',
          jobId: job.id,
          stage: job.stage,
          message: `dropped: run already ${run.status}`,
        })
        continue
      }
      await this.appendEvent(job.runId, {
        kind: 'job.claimed',
        jobId: job.id,
        stage: 'starting',
        message: `claimed by ${worker.id} (attempt ${job.attempt})`,
      })
      // 领取即开工：run 要从「排队中」翻到「渲染中」，否则指挥台和列表里
      // 一直在说排队，实际 worker 已经在跑，用户会以为队列卡死。
      if (run && run.status === ProductionRunStatus.QUEUED) {
        await this.runRepo.updateByRevision(job.runId, run.revision, {
          status: ProductionRunStatus.RUNNING,
          currentStage: 'starting',
        } as never, { status: ProductionRunStatus.QUEUED })
      }
      return job
    }
    return null
  }

  async heartbeatJob(
    workerId: string,
    jobId: string,
    leaseVersion: number,
    progress?: number,
    stage?: string,
    message?: string,
  ) {
    // 续约和进度写回合成一次原子更新：分两步做的话，中间被 reaper 判死时
    // 进度会写到一个已经 failed 的 job 上。
    const patch: Record<string, unknown> = {}
    if (progress !== undefined) {
      patch['progress'] = Math.max(0, Math.min(100, progress))
    }
    if (stage) {
      patch['stage'] = stage
    }
    if (message) {
      patch['message'] = message
    }
    const job = await this.jobRepo.renewLease(jobId, workerId, leaseVersion, LEASE_MS, patch)
    if (!job) {
      return { ok: false, reason: 'stale-lease' }
    }
    if (Object.keys(patch).length) {
      // 进度同步回 run：任务详情与指挥台都读 run，只更新 job 的话界面永远停在 0%。
      // CAS 在 job 自己的 revision 上，且已取消的 run 不再被心跳拉回 running。
      await this.runRepo.updateByRevision(job.runId, revisionOf(job), {
        currentStage: stage ?? job.stage,
        progress: progress ?? job.progress,
        status: ProductionRunStatus.RUNNING,
      } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
      await this.appendEvent(job.runId, {
        kind: 'job.progress',
        jobId,
        stage: stage ?? job.stage,
        progress: progress ?? job.progress,
        message: message ?? '',
      })
    }
    return { ok: true, leaseExpiresAt: job.leaseExpiresAt }
  }

  async completeJob(
    workerId: string,
    jobId: string,
    leaseVersion: number,
    result: Record<string, unknown>,
    runPatch: Record<string, unknown> = {},
  ) {
    const job = await this.jobRepo.getById(jobId)
    if (!job) {
      throw new AppException(ResponseCode.ProductionJobNotFound)
    }
    const revision = revisionOf(job)
    const run = await this.runRepo.getById(job.runId)
    // 旧 revision 的任务晚到：它干了活也不能把结果写进新稿，直接作废这一条。
    if (run && revision !== (run.revision || 1)) {
      await this.jobRepo.finish(jobId, workerId, leaseVersion, revision, {
        status: ProductionJobStatus.CANCELLED,
        message: `stale revision ${revision}; run is at ${run.revision}`,
      })
      await this.appendEvent(job.runId, {
        kind: 'job.cancelled',
        jobId,
        stage: job.stage,
        message: `stale revision ${revision}; run is at ${run.revision}`,
      })
      return { ok: false, reason: 'stale-revision' }
    }
    const done = await this.jobRepo.finish(jobId, workerId, leaseVersion, revision, {
      status: ProductionJobStatus.SUCCEEDED,
      progress: 100,
      result,
    })
    if (!done) {
      return { ok: false, reason: 'stale-lease' }
    }
    // 剪映导出在 macOS 上要人点导出面板：worker 干完了这轮也只能回报
    // 「等人导出」，这时把 run 记成 succeeded 会凭空造出一条做不到的成片。
    const awaitingManualExport = result['requiresManualExport'] === true
      || result['stage'] === 'awaiting_manual_export'
    const update: Record<string, unknown> = {
      status: awaitingManualExport ? ProductionRunStatus.WAITING_USER : ProductionRunStatus.SUCCEEDED,
      currentStage: awaitingManualExport ? 'awaiting_manual_export' : 'done',
      progress: awaitingManualExport ? job.progress : 100,
      ...runPatch,
    }
    const output = run ? normalizeRunOutput(run.output ?? {}, result) : null
    if (output) {
      update['output'] = output
    }
    // 已取消的 run 不能被一条迟到的完成改回 succeeded。
    const updatedRun = await this.runRepo.updateByRevision(job.runId, revision, update as never, {
      status: { $ne: ProductionRunStatus.CANCELLED },
    })
    if (!updatedRun) {
      return { ok: false, reason: 'run-cancelled' }
    }
    await this.appendEvent(job.runId, {
      kind: 'job.succeeded',
      jobId,
      stage: awaitingManualExport ? 'awaiting_manual_export' : 'done',
      progress: awaitingManualExport ? job.progress : 100,
      data: result,
    })
    // 同一 run 可能因为租约过期补出第二个 job；这条已经出片，剩下的必须作废，
    // 否则 worker 会紧接着把同一条视频再渲一遍。
    const leftovers = (await this.jobRepo.listByRun(job.runId)).filter(
      (item: ProductionJob) => item.id !== jobId
        && (item.status === ProductionJobStatus.QUEUED || item.status === ProductionJobStatus.RUNNING),
    )
    for (const item of leftovers) {
      await this.jobRepo.updateByStatusIn(
        item.id,
        [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING],
        {
          $set: { status: ProductionJobStatus.CANCELLED, finishedAt: new Date(), message: 'superseded by successful attempt' },
          $inc: { leaseVersion: 1 },
        },
      )
      await this.appendEvent(job.runId, {
        kind: 'job.cancelled',
        jobId: item.id,
        stage: item.stage,
        message: 'superseded by successful attempt',
      })
    }
    return { ok: true }
  }

  async failJob(
    workerId: string,
    jobId: string,
    leaseVersion: number,
    error: string,
    status: ProductionJobStatus = ProductionJobStatus.FAILED,
  ) {
    const job = await this.jobRepo.getById(jobId)
    if (!job) {
      throw new AppException(ResponseCode.ProductionJobNotFound)
    }
    const revision = revisionOf(job)
    const run = await this.runRepo.getById(job.runId)
    if (run && revision !== (run.revision || 1)) {
      await this.jobRepo.finish(jobId, workerId, leaseVersion, revision, {
        status: ProductionJobStatus.CANCELLED,
        message: `stale revision ${revision}; run is at ${run.revision}`,
      })
      await this.appendEvent(job.runId, {
        kind: 'job.cancelled',
        jobId,
        stage: job.stage,
        message: `stale revision ${revision}; run is at ${run.revision}`,
      })
      return { ok: false, reason: 'stale-revision' }
    }
    const done = await this.jobRepo.finish(jobId, workerId, leaseVersion, revision, {
      status,
      lastError: error,
    })
    if (!done) {
      return { ok: false, reason: 'stale-lease' }
    }
    const retryable = status === ProductionJobStatus.FAILED && isEnvironmentFailure(error)
    // failJob 开头读到的 run 是失败前的状态，重排判断要用最新的那份。
    const currentRun = retryable ? await this.runRepo.getById(job.runId) : null
    // 只重排还在跑或排队的 run：已出片或被取消的 run 不因一条迟到的失败结果复活。
    const runActive = currentRun
      && currentRun.status !== ProductionRunStatus.CANCELLED
      && currentRun.status !== ProductionRunStatus.SUCCEEDED
      && (currentRun.revision || 1) === revision
    if (currentRun && runActive && (job.attempt || 1) < 3) {
      // 环境类失败：先把 run 放回队列再补一条同 kind 的 job，别让用户去手动重试。
      await this.runRepo.updateByRevision(job.runId, revision, {
        status: ProductionRunStatus.QUEUED,
        currentStage: 'queued',
        lastError: `本机环境问题，已自动重排：${error.slice(0, 200)}`,
      } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
      await this.enqueueRender(currentRun.userId, currentRun, job.kind)
      await this.appendEvent(job.runId, {
        kind: 'job.requeued',
        jobId,
        stage: 'queued',
        message: 'environment failure, render requeued',
      })
      return { ok: true, retry: true }
    }
    const runStatus = status === ProductionJobStatus.WAITING_USER
      ? ProductionRunStatus.WAITING_USER
      : ProductionRunStatus.FAILED
    // 已取消的 run 保持 cancelled，失败结果只记在 job 上。
    await this.runRepo.updateByRevision(job.runId, revision, {
      status: runStatus,
      currentStage: status,
      lastError: error,
    } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
    await this.appendEvent(job.runId, {
      kind: 'job.failed',
      jobId,
      stage: status,
      message: error,
    })
    return { ok: true }
  }

  /**
   * 回收心跳超时的渲染任务，返回真正重新排队的 run 数量。
   *
   * worker 被 kill -9 之后不会再续约，也没有人替它把 job 从 running 里拿出来。
   * 之前这里只把 run 标成 waiting_user，等于把 run 永久挂起：job 永远 running、
   * run 永远进行中，前端就一直显示一条僵尸任务。
   *
   * 现在按「过期租约 = 这一次尝试作废」处理：job 判 failed、run 回到 queued，
   * 并且补一条新的队列 job，否则 queued 的 run 没有 job 可 claim，仍然是僵尸。
   * 连续 3 次丢租约就判 run 失败，避免必然崩溃的渲染被每分钟无限重启。
   */
  async reapExpiredLeases() {
    const expired = await this.jobRepo.findExpired(new Date())
    let requeued = 0
    for (const job of expired) {
      // 没有 leaseExpiresAt 的脏数据无法做「续约即失配」的条件更新，跳过不动。
      if (!job.leaseExpiresAt) {
        continue
      }
      // 以扫描时看到的 leaseExpiresAt 做条件更新，而不是普通 updateById：
      // 扫描与写入之间 worker 可能刚续约，那时租约时间已经变了，这次过期更新
      // 匹配不到任何文档，健康任务不会被误判失败（续约赢）。
      const voided = await this.jobRepo.updateByLeaseExpiry(
        job.id,
        job.leaseOwner,
        job.leaseVersion,
        job.leaseExpiresAt,
        {
          status: ProductionJobStatus.FAILED,
          message: 'lease expired',
          lastError: 'worker stopped heart-beating',
        },
      )
      if (!voided) {
        continue
      }
      await this.appendEvent(job.runId, {
        kind: 'job.failed',
        jobId: job.id,
        stage: 'failed',
        message: 'lease expired',
      })
      const run = await this.runRepo.getById(job.runId)
      // run 已经结束（另一条路径完成或用户取消）就不要把它拉回 queued；
      // 旧 revision 的任务超时也不该复活当前稿。
      if (!run || run.status === ProductionRunStatus.SUCCEEDED || run.status === ProductionRunStatus.CANCELLED) {
        continue
      }
      if (revisionOf(job) !== (run.revision || 1)) {
        continue
      }
      // 同一条 run 已经有后续 job 在排队/执行（例如用户手动重试过），不要重复补任务
      const jobs = await this.jobRepo.listByRun(job.runId)
      const pending = jobs.some(item =>
        item.id !== job.id
        && (item.status === ProductionJobStatus.QUEUED || item.status === ProductionJobStatus.RUNNING),
      )
      if (pending) {
        continue
      }
      if (job.attempt >= 3) {
        await this.runRepo.updateByRevision(job.runId, run.revision, {
          status: ProductionRunStatus.FAILED,
          currentStage: 'failed',
          lastError: 'worker 连续 3 次心跳超时，已停止自动重试',
        } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
        continue
      }
      await this.runRepo.updateByRevision(job.runId, run.revision, {
        status: ProductionRunStatus.QUEUED,
        currentStage: 'queued',
        lastError: 'worker 心跳超时，已自动重新排队',
      } as never, { status: { $ne: ProductionRunStatus.CANCELLED } })
      // 补的必须是原来那条 job 的 kind，否则 timeline/motion 的 run 会被排到默认引擎
      await this.enqueueRender(run.userId, run, job.kind)
      await this.appendEvent(job.runId, {
        kind: 'job.requeued',
        jobId: job.id,
        stage: 'queued',
        message: 'lease expired, render requeued',
      })
      requeued += 1
    }
    return requeued
  }

  // --------------------------------------------------------------- material

  /**
   * Create the draft for a finished render. Idempotent on (runId, revision): a
   * retry returns the existing material instead of creating a second draft.
   */
  async attachMaterial(
    userId: string,
    runId: string,
    input: {
      groupId: string
      title: string
      desc?: string
      topics?: string[]
      videoUrl: string
      coverUrl?: string
      videoPath?: string
      duration?: number
    },
  ) {
    const run = await this.getRun(userId, runId)
    if (run.materialId) {
      // Backfill: runs finished before output was recorded still have only a
      // materialId, so this idempotent early return would leave them
      // un-publishable forever. Repair the record once, then return as before.
      if (!(run.output as Record<string, unknown> | undefined)?.['videoPath']) {
        await this.runRepo.updateById(runId, {
          output: {
            ...((run.output as Record<string, unknown> | undefined) ?? {}),
            videoPath: input.videoUrl,
            materialId: run.materialId,
          },
        } as never)
      }
      return { materialId: run.materialId, deduped: true }
    }
    const material = await this.materialService.create({
      userId,
      userType: UserType.User,
      groupId: input.groupId,
      type: MaterialType.VIDEO,
      title: input.title,
      desc: input.desc ?? '',
      topics: input.topics ?? [],
      coverUrl: input.coverUrl,
      mediaList: [{ url: input.videoUrl, type: MediaType.VIDEO, thumbUrl: input.coverUrl }],
      autoDeleteMedia: false,
      status: MaterialStatus.SUCCESS,
      accountTypes: [],
    } as never)

    await this.runRepo.updateById(runId, {
      materialId: material.id,
      // planPublication gates on output.videoPath ("Run has no verified output
      // yet"), but nothing ever wrote output: the worker only reported a
      // materialId, so every real P1 run was rejected when planning a
      // publication. Record the verified artifact here, at the one place where
      // a run provably owns a finished file.
      output: {
        ...((run.output as Record<string, unknown> | undefined) ?? {}),
        videoPath: input.videoUrl,
        materialId: material.id,
      },
      checkpoints: [
        ...(run.checkpoints ?? []),
        { at: new Date(), stage: 'draft', note: `material ${material.id}` },
      ],
    } as never)
    await this.appendEvent(runId, {
      kind: 'material.attached',
      stage: 'draft',
      message: `draft ${material.id} created in group ${input.groupId}`,
    })
    return { materialId: material.id, deduped: false }
  }

  /**
   * Worker-scoped draft attach: the worker knows the finished file, the ledger
   * knows the owner. Resolving the draft box here keeps the groupId off the wire
   * and makes a retry land on the same draft instead of creating a second one.
   */
  async attachMaterialForWorker(
    userId: string,
    runId: string,
    input: {
      title?: string
      desc?: string
      topics?: string[]
      videoKey: string
      duration?: number
      groupId?: string
    },
  ) {
    const run = await this.getRun(userId, runId)
    if (run.materialId) {
      // Backfill: runs finished before output was recorded still have only a
      // materialId, so this idempotent early return would leave them
      // un-publishable forever (planPublication gates on output.videoPath).
      if (!(run.output as Record<string, unknown> | undefined)?.['videoPath']) {
        await this.runRepo.updateById(runId, {
          output: {
            ...((run.output as Record<string, unknown> | undefined) ?? {}),
            videoPath: input.videoKey,
            materialId: run.materialId,
          },
        } as never)
      }
      return { materialId: run.materialId, groupId: '', deduped: true }
    }
    const project = await this.projectRepo.getByIdForUser(run.projectId, userId)
    let groupId = input.groupId || project?.materialGroupId || ''
    if (!groupId) {
      let group = await this.materialGroupService.getDefaultGroup(userId)
      if (!group) {
        // ensureDefaultGroup only reports success (returns a boolean); it does
        // not hand back the created group. Re-read it, otherwise groupId stays
        // '' and Material rejects the draft with "groupId is required" — which
        // reads like a worker bug but is really this discarded id.
        await this.materialGroupService.ensureDefaultGroup(userId)
        group = await this.materialGroupService.getDefaultGroup(userId)
      }
      groupId = group && typeof group !== 'boolean' ? group.id : ''
    }
    if (!groupId) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        'Could not resolve a draft group for this run',
      )
    }
    const result = await this.attachMaterial(userId, runId, {
      groupId,
      title: input.title || run.title,
      desc: input.desc ?? '',
      topics: input.topics ?? [],
      videoUrl: input.videoKey,
      duration: input.duration,
    })
    return { ...result, groupId }
  }

  // ------------------------------------------------------------ publication

  static idempotencyKey(parts: {
    runId: string
    revision: number
    platform: string
    accountRef: string
    slot: string
  }) {
    return createHash('sha256')
      .update(`${parts.runId}|${parts.revision}|${parts.platform}|${parts.accountRef}|${parts.slot}`)
      .digest('hex')
  }

  async planPublication(
    userId: string,
    input: {
      runId: string
      platform: string
      dispatcher: PublicationDispatcher
      accountRef: string
      accountName?: string
      plannedAt?: string
      content?: Record<string, unknown>
    },
  ) {
    const run = await this.getRun(userId, input.runId)
    const output = (run.output ?? {}) as Record<string, unknown>
    const platforms = (output['platforms'] ?? {}) as Record<string, { key?: string }>
    // 发布投递哪个文件：该平台重新交付过的成片优先，否则退回母版 key
    const assetKey = platforms[input.platform]?.key || (typeof output['videoPath'] === 'string' ? output['videoPath'] : '')
    if (run.status !== ProductionRunStatus.SUCCEEDED || !assetKey) {
      throw new AppException(ResponseCode.ProductionInvalidState, '运行还没有可发布的成片产物')
    }
    const slot = input.plannedAt ?? 'now'
    const key = ProductionService.idempotencyKey({
      runId: run.id,
      revision: run.revision,
      platform: input.platform,
      accountRef: input.accountRef,
      slot,
    })
    const existing = await this.attemptRepo.getByIdempotencyKey(key)
    if (existing) {
      return { attempt: existing, deduped: true }
    }
    const attempt = await this.attemptRepo.create({
      userId,
      runId: run.id,
      projectId: run.projectId,
      platform: input.platform,
      dispatcher: input.dispatcher,
      accountRef: input.accountRef,
      accountName: input.accountName ?? '',
      revision: run.revision,
      idempotencyKey: key,
      plannedAt: input.plannedAt ? new Date(input.plannedAt) : null,
      status: PublicationStatus.PLANNED,
      // 记下这次计划实际投递的文件，事后能回答「发出去的是哪个平台的成片」
      content: { ...(input.content ?? {}), assetKey },
      evidence: [],
    } as unknown as Partial<PublicationAttempt>)
    await this.appendEvent(run.id, {
      kind: 'publication.planned',
      stage: 'planned',
      message: `${input.platform} via ${input.dispatcher} (${input.accountRef})`,
    })
    return { attempt, deduped: false }
  }

  /**
   * Mark an attempt as submitting *before* the browser touches the platform, so
   * a crash mid-submit is recoverable as `submission_unknown` rather than being
   * silently retried.
   */
  async beginSubmission(userId: string, attemptId: string) {
    const attempt = await this.attemptRepo.getById(attemptId) as PublicationAttempt | null
    if (!attempt || attempt.userId !== userId) {
      throw new AppException(ResponseCode.PublicationAttemptNotFound)
    }
    if (attempt.status !== PublicationStatus.PLANNED && attempt.status !== PublicationStatus.WAITING_USER) {
      throw new AppException(ResponseCode.ProductionInvalidState, `Cannot submit from status ${attempt.status}`)
    }
    const updated = await this.attemptRepo.updateById(attemptId, {
      status: PublicationStatus.SUBMITTING,
    } as never)
    return updated
  }

  async recordSubmission(
    userId: string,
    attemptId: string,
    input: {
      status: PublicationStatus
      remotePostId?: string
      remoteUrl?: string
      evidence?: Record<string, unknown>
      error?: string
    },
  ) {
    const attempt = await this.attemptRepo.getById(attemptId) as PublicationAttempt | null
    if (!attempt || attempt.userId !== userId) {
      throw new AppException(ResponseCode.PublicationAttemptNotFound)
    }
    const updated = await this.attemptRepo.updateById(attemptId, {
      status: input.status,
      remotePostId: input.remotePostId ?? attempt.remotePostId,
      remoteUrl: input.remoteUrl ?? attempt.remoteUrl,
      evidence: [...(attempt.evidence ?? []), { at: new Date(), ...(input.evidence ?? {}) }],
      lastError: input.error ?? '',
    } as never)
    await this.appendEvent(attempt.runId, {
      kind: 'publication.updated',
      stage: input.status,
      message: input.error || `status -> ${input.status}`,
      data: { attemptId, remotePostId: input.remotePostId },
    })
    return updated
  }

  async listAttempts(userId: string, runId?: string) {
    if (runId) {
      await this.getRun(userId, runId)
      return await this.attemptRepo.listByRun(runId)
    }
    return await this.attemptRepo.listByUser(userId)
  }

  async getJobRaw(jobId: string) {
    return await this.jobRepo.getById(jobId)
  }

  /**
   * Worker 上报过程事件。事件本身不改文档，但必须证明「你是这条 job 当前
   * 的持有者」：旧 worker 或旧 revision 的 job 不能继续往 run 时间线里写。
   */
  async appendJobEvent(
    workerId: string,
    jobId: string,
    event: { kind: string, stage?: string, progress?: number, message?: string, data?: Record<string, unknown> },
  ) {
    const job = await this.jobRepo.getById(jobId)
    if (!job) {
      throw new AppException(ResponseCode.ProductionJobNotFound)
    }
    const held = job.status === ProductionJobStatus.RUNNING
      && job.leaseOwner === workerId
      && !!job.leaseExpiresAt
      && new Date(job.leaseExpiresAt).getTime() > Date.now()
    if (!held) {
      throw new AppException(ResponseCode.ProductionWorkerUnauthorized, 'Job lease is not held by this worker')
    }
    await this.appendEvent(job.runId, { jobId, ...event })
    return { ok: true }
  }

  // ----------------------------------------------------------------- events

  async appendEvent(
    runId: string,
    event: { jobId?: string, kind: string, stage?: string, progress?: number, message?: string, data?: Record<string, unknown> },
  ) {
    const seq = (await this.eventRepo.lastSeq(runId)) + 1
    return await this.eventRepo.append({
      runId,
      jobId: event.jobId ?? '',
      seq,
      kind: event.kind,
      stage: event.stage ?? '',
      progress: event.progress ?? 0,
      message: event.message ?? '',
      data: event.data ?? {},
    })
  }

  async listEvents(userId: string, runId: string, afterSeq = 0) {
    await this.getRun(userId, runId)
    return await this.eventRepo.listByRun(runId, afterSeq)
  }

  // ---------------------------------------------------------------- workers

  async registerWorker(userId: string, name: string, capabilities: string[], version: string) {
    const token = `wkr_${randomUUID().replace(/-/g, '')}`
    const tokenHash = createHash('sha256').update(token).digest('hex')
    const worker = await this.workerRepo.create({
      userId,
      name,
      tokenHash,
      capabilities,
      version,
      enabled: true,
      lastSeenAt: new Date(),
    } as never)
    return { workerId: worker.id, token }
  }

  /**
   * worker 启动时把本进程真实支持的能力推回服务端。
   * claim 的过滤条件读的是库里的 worker 记录而不是进程配置，以前新增 job kind
   * 必须手改数据库，漏改的表现是任务永远排队、worker 却看起来完全健康。
   */
  async refreshWorkerCapabilities(workerId: string, capabilities: string[]) {
    const normalized = [...new Set(capabilities.map(item => item.trim()).filter(item => item.length > 0))]
    const updated = await this.workerRepo.updateById(workerId, { capabilities: normalized })
    return { ok: true, capabilities: updated?.capabilities ?? normalized }
  }

  async resolveWorker(token: string) {
    const tokenHash = createHash('sha256').update(token).digest('hex')
    const worker = await this.workerRepo.getByTokenHash(tokenHash)
    if (!worker) {
      return null
    }
    await this.workerRepo.touch(worker.id)
    return worker
  }

  async workerHealth(userId: string) {
    const workers = await this.workerRepo.listByUser(userId)
    const now = Date.now()
    return workers.map((w: Awaited<ReturnType<typeof this.workerRepo.listByUser>>[number]) => ({
      id: w.id,
      name: w.name,
      version: w.version,
      capabilities: w.capabilities,
      lastSeenAt: w.lastSeenAt,
      online: w.lastSeenAt ? now - new Date(w.lastSeenAt).getTime() < 60_000 : false,
    }))
  }
}
