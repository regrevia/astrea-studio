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
import { buildTimelineSpec } from './timeline-spec'

export const LEASE_MS = 60_000
export const RENDER_LANE = 'render'
export const PUBLISH_LANE = 'publish'

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
  if (!videoKey && !Object.keys(platforms).length && !copyCheck && !videoQa) {
    return null
  }
  const output: Record<string, unknown> = { ...existing }
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

    await this.enqueueRender(userId, run, 1)
    await this.appendEvent(run.id, {
      kind: 'run.created',
      stage: 'queued',
      message: `run ${run.title} queued (revision 1)`,
    })
    return run
  }

  /**
   * Create a new revision and a render job for it. Prior publication approvals
   * do not carry over: any attempt bound to an older revision stays as-is and a
   * fresh intent must be created before publishing again.
   */
  async reviseRun(userId: string, runId: string, patch: { brief?: string, spec?: Record<string, unknown> }) {
    const run = await this.getRun(userId, runId)
    if (run.status === ProductionRunStatus.RUNNING) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run is still rendering; wait or cancel first')
    }
    const revision = run.revision + 1
    const patchSpec = patch.spec ?? {}
    // 改 spec 时以友好输入的底稿为基线重新展开，避免把上一次的展开结果再展开一遍
    const baseSpec = patchSpec['timeline'] === undefined && run.spec['input']
      ? run.spec['input'] as Record<string, unknown>
      : run.spec
    const updated = await this.runRepo.updateById(run.id, {
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

    await this.enqueueRender(userId, updated as unknown as ProductionRun, revision)
    await this.appendEvent(run.id, {
      kind: 'run.revised',
      stage: 'queued',
      message: `revision ${revision} queued`,
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
    const attempt = await this.jobRepo.nextAttempt(runId, ProductionJobKind.RENDER_MPT)
    await this.runRepo.updateById(runId, {
      status: ProductionRunStatus.QUEUED,
      currentStage: 'queued',
      lastError: '',
    } as never)
    await this.enqueueRender(userId, run, attempt)
    await this.appendEvent(runId, { kind: 'run.retried', stage: 'queued', message: `attempt ${attempt}` })
    return { runId, attempt }
  }

  async cancelRun(userId: string, runId: string) {
    await this.getRun(userId, runId)
    const jobs = await this.jobRepo.listByRun(runId)
    const pending = jobs.filter((j: ProductionJob) =>
      j.status === ProductionJobStatus.QUEUED || j.status === ProductionJobStatus.RUNNING,
    )
    for (const job of pending) {
      await this.jobRepo.updateById(job.id, {
        status: ProductionJobStatus.CANCELLED,
        finishedAt: new Date(),
        message: 'cancelled by user',
      } as never)
      await this.appendEvent(runId, {
        kind: 'job.cancelled',
        jobId: job.id,
        stage: job.stage,
        message: 'cancelled by user',
      })
    }
    await this.runRepo.updateById(runId, {
      status: ProductionRunStatus.CANCELLED,
      currentStage: 'cancelled',
    } as never)
    return { cancelled: pending.length }
  }

  private async enqueueRender(userId: string, run: ProductionRun, attempt: number) {
    // 同一条 run 可以交给多种渲染引擎：
    //   engine=slides   → 知识幻灯片（HTML 信息图 + 口播），逐屏静态；
    //   engine=motion   → GSAP 时间轴逐帧渲染，有真正的动效语言；
    //   engine=jianying → 只出剪映草稿，把转场/特效/文字动画交给剪映原生资源
    //                     （macOS 无自动导出，交付物是草稿目录）；
    //   engine=timeline → 我们自己的 timeline spec 直接渲染母版，纯 ffmpeg、
    //                     不启剪映、不抢前台，可后台排队也可搬进容器；
    //   默认            → MoneyPrinterTurbo 素材拼贴。
    // 队列、租约、草稿投递完全复用，只有 worker 侧的分派不同。
    const spec = (run.spec ?? {}) as Record<string, unknown>
    const engine = spec['engine']
    const kind = engine === 'motion'
      ? ProductionJobKind.MOTION_RENDER
      : engine === 'slides'
        ? ProductionJobKind.SLIDES_RENDER
        : engine === 'jianying'
          ? ProductionJobKind.JIANYING_DRAFT
          : engine === 'timeline'
            ? ProductionJobKind.TIMELINE_RENDER
            : ProductionJobKind.RENDER_MPT
    const job = await this.jobRepo.create({
      userId,
      runId: run.id,
      kind,
      attempt,
      status: ProductionJobStatus.QUEUED,
      lane: RENDER_LANE,
      payload: { runId: run.id, revision: run.revision, spec: run.spec, brief: run.brief },
    } as unknown as Partial<ProductionJob>)
    return job
  }

  // ------------------------------------------------------------------- jobs

  async claimJob(
    workerId: string,
    lane: string,
    capabilities: string[] = [ProductionJobKind.RENDER_MPT, ProductionJobKind.RENDER_FFMPEG, ProductionJobKind.SLIDES_RENDER, ProductionJobKind.MOTION_RENDER, ProductionJobKind.TIMELINE_RENDER, ProductionJobKind.JIANYING_DRAFT, ProductionJobKind.PROBE],
  ) {
    if (lane === PUBLISH_LANE) {
      // one browser at a time, always
      const running = await this.jobRepo.listByStatus(PUBLISH_LANE, ProductionJobStatus.RUNNING)
      if (running.length) {
        return null
      }
    }
    for (let i = 0; i < 3; i++) {
      const job = await this.jobRepo.claimNext(lane, workerId, LEASE_MS, capabilities)
      if (!job) {
        return null
      }
      // 一条 run 只该有一个活着的渲染：心跳超时补出来的新 job 可能在旧 job
      // 完成之后才被领取，此时 run 已经 succeeded，再渲一遍纯属白烧机器。
      const run = await this.runRepo.getById(job.runId)
      if (run && (run.status === ProductionRunStatus.SUCCEEDED
        || run.status === ProductionRunStatus.CANCELLED
        || run.status === ProductionRunStatus.FAILED)) {
        await this.jobRepo.updateById(job.id, {
          status: ProductionJobStatus.CANCELLED,
          finishedAt: new Date(),
          message: `run already ${run.status}`,
        } as never)
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
        message: `claimed by ${workerId} (attempt ${job.attempt})`,
      })
      // 领取即开工：run 要从「排队中」翻到「渲染中」，否则指挥台和列表里
      // 一直在说排队，实际 worker 已经在跑，用户会以为队列卡死。
      if (run && run.status === ProductionRunStatus.QUEUED) {
        await this.runRepo.updateById(job.runId, {
          status: ProductionRunStatus.RUNNING,
          currentStage: 'starting',
        } as never)
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
    const job = await this.jobRepo.renewLease(jobId, workerId, leaseVersion, LEASE_MS)
    if (!job) {
      return { ok: false, reason: 'stale-lease' }
    }
    const update: Record<string, unknown> = {}
    if (progress !== undefined) {
      update['progress'] = Math.max(0, Math.min(100, progress))
    }
    if (stage) {
      update['stage'] = stage
    }
    if (message) {
      update['message'] = message
    }
    if (Object.keys(update).length) {
      await this.jobRepo.updateById(jobId, update as never)
      // 进度同步回 run：任务详情与指挥台都读 run，只更新 job 的话界面永远停在 0%
      await this.runRepo.updateById(job.runId, {
        currentStage: stage ?? job.stage,
        progress: progress ?? job.progress,
        status: ProductionRunStatus.RUNNING,
      } as never)
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
    const done = await this.jobRepo.finish(jobId, workerId, leaseVersion, {
      status: ProductionJobStatus.SUCCEEDED,
      progress: 100,
      result,
    })
    if (!done) {
      return { ok: false, reason: 'stale-lease' }
    }
    const update: Record<string, unknown> = {
      status: ProductionRunStatus.SUCCEEDED,
      currentStage: 'done',
      progress: 100,
      ...runPatch,
    }
    const run = await this.runRepo.getById(job.runId)
    const output = run ? normalizeRunOutput(run.output ?? {}, result) : null
    if (output) {
      update['output'] = output
    }
    await this.runRepo.updateById(job.runId, update as never)
    await this.appendEvent(job.runId, {
      kind: 'job.succeeded',
      jobId,
      stage: 'done',
      progress: 100,
      data: result,
    })
    // 同一 run 可能因为租约过期补出第二个 job；这条已经出片，剩下的必须作废，
    // 否则 worker 会紧接着把同一条视频再渲一遍。
    const leftovers = (await this.jobRepo.listByRun(job.runId)).filter(
      (item: ProductionJob) => item.id !== jobId
        && (item.status === ProductionJobStatus.QUEUED || item.status === ProductionJobStatus.RUNNING),
    )
    for (const item of leftovers) {
      await this.jobRepo.updateById(item.id, {
        status: ProductionJobStatus.CANCELLED,
        finishedAt: new Date(),
        message: 'superseded by successful attempt',
      } as never)
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
    const done = await this.jobRepo.finish(jobId, workerId, leaseVersion, {
      status,
      lastError: error,
    })
    if (!done) {
      return { ok: false, reason: 'stale-lease' }
    }
    const runStatus = status === ProductionJobStatus.WAITING_USER
      ? ProductionRunStatus.WAITING_USER
      : ProductionRunStatus.FAILED
    await this.runRepo.updateById(job.runId, {
      status: runStatus,
      currentStage: status,
      lastError: error,
    } as never)
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
      // 用租约版本做守卫，而不是普通 updateById：扫描与写入之间 worker 可能刚续约，
      // 直接覆盖会把一个健康任务的租约改坏
      const failed = await this.jobRepo.finish(job.id, job.leaseOwner, job.leaseVersion, {
        status: ProductionJobStatus.FAILED,
        message: 'lease expired',
        lastError: 'worker stopped heart-beating',
      })
      if (!failed) {
        continue
      }
      await this.appendEvent(job.runId, {
        kind: 'job.failed',
        jobId: job.id,
        stage: 'failed',
        message: 'lease expired',
      })
      const run = await this.runRepo.getById(job.runId)
      // run 已经结束（另一条路径完成或用户取消）就不要把它拉回 queued
      if (!run || run.status === ProductionRunStatus.SUCCEEDED || run.status === ProductionRunStatus.CANCELLED) {
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
        await this.runRepo.updateById(job.runId, {
          status: ProductionRunStatus.FAILED,
          currentStage: 'failed',
          lastError: 'worker 连续 3 次心跳超时，已停止自动重试',
        } as never)
        continue
      }
      await this.runRepo.updateById(job.runId, {
        status: ProductionRunStatus.QUEUED,
        currentStage: 'queued',
        lastError: 'worker 心跳超时，已自动重新排队',
      } as never)
      await this.enqueueRender(run.userId, run, await this.jobRepo.nextAttempt(job.runId, job.kind))
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
