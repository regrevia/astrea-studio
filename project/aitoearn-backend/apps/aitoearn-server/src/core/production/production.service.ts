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
      spec: input.spec ?? {},
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
    const updated = await this.runRepo.updateById(run.id, {
      revision,
      brief: patch.brief ?? run.brief,
      spec: { ...run.spec, ...(patch.spec ?? {}) },
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
    capabilities: string[] = [ProductionJobKind.RENDER_MPT, ProductionJobKind.RENDER_FFMPEG, ProductionJobKind.SLIDES_RENDER, ProductionJobKind.MOTION_RENDER, ProductionJobKind.JIANYING_DRAFT, ProductionJobKind.PROBE],
  ) {
    if (lane === PUBLISH_LANE) {
      // one browser at a time, always
      const running = await this.jobRepo.listByStatus(PUBLISH_LANE, ProductionJobStatus.RUNNING)
      if (running.length) {
        return null
      }
    }
    const job = await this.jobRepo.claimNext(lane, workerId, LEASE_MS, capabilities)
    if (job) {
      await this.appendEvent(job.runId, {
        kind: 'job.claimed',
        jobId: job.id,
        stage: 'starting',
        message: `claimed by ${workerId} (attempt ${job.attempt})`,
      })
    }
    return job
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
    await this.runRepo.updateById(job.runId, {
      status: ProductionRunStatus.SUCCEEDED,
      currentStage: 'done',
      ...runPatch,
    } as never)
    await this.appendEvent(job.runId, {
      kind: 'job.succeeded',
      jobId,
      stage: 'done',
      progress: 100,
      data: result,
    })
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

  /** Requeue jobs whose worker stopped heart-beating. */
  async reapExpiredLeases() {
    const expired = await this.jobRepo.findExpired(new Date())
    for (const job of expired) {
      await this.jobRepo.updateById(job.id, {
        status: ProductionJobStatus.INTERRUPTED,
        message: 'lease expired',
        finishedAt: new Date(),
      } as never)
      await this.runRepo.updateById(job.runId, {
        status: ProductionRunStatus.WAITING_USER,
        currentStage: 'interrupted',
        lastError: 'worker lease expired; render can be retried safely',
      } as never)
      await this.appendEvent(job.runId, {
        kind: 'job.interrupted',
        jobId: job.id,
        stage: 'interrupted',
        message: 'lease expired, no side effects assumed',
      })
    }
    return expired.length
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
    if (run.status !== ProductionRunStatus.SUCCEEDED || !(run.output as Record<string, unknown> | undefined)?.['videoPath']) {
      throw new AppException(ResponseCode.ProductionInvalidState, 'Run has no verified output yet')
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
      content: input.content ?? {},
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
