import { describe, expect, it, vi } from 'vitest'

import { ProductionService } from './production.service'

vi.mock('../content/material.service', () => ({ MaterialService: class {} }))

vi.mock('../content/material-group.service', () => ({ MaterialGroupService: class {} }))

vi.mock('@yikart/mongodb', () => ({
  ProductionProjectRepository: class {},
  ProductionRunRepository: class {},
  ProductionJobRepository: class {},
  PublicationAttemptRepository: class {},
  ProductionWorkerRepository: class {},
  ProductionEventRepository: class {},
  ProductionJobKind: {
    RENDER_MPT: 'render_mpt',
    RENDER_FFMPEG: 'render_ffmpeg',
    SLIDES_RENDER: 'slides_render',
    MOTION_RENDER: 'motion_render',
    TIMELINE_RENDER: 'render_timeline',
    JIANYING_DRAFT: 'jianying_draft',
    JIANYING_EXPORT: 'jianying_export',
    PUBLISH_SAU: 'publish_sau',
    PROBE: 'probe',
  },
  ProductionJobStatus: {
    QUEUED: 'queued',
    RUNNING: 'running',
    SUCCEEDED: 'succeeded',
    FAILED: 'failed',
    WAITING_USER: 'waiting_user',
    INTERRUPTED: 'interrupted',
    CANCELLED: 'cancelled',
  },
  ProductionRunStatus: {
    DRAFT: 'draft',
    QUEUED: 'queued',
    RUNNING: 'running',
    SUCCEEDED: 'succeeded',
    FAILED: 'failed',
    WAITING_USER: 'waiting_user',
    CANCELLED: 'cancelled',
  },
  PublicationDispatcher: {
    AITOEARN: 'aitoearn',
    SAU: 'sau',
    MANUAL: 'manual',
  },
  PublicationStatus: {
    PLANNED: 'planned',
    SUBMITTING: 'submitting',
    SUBMITTED_UNVERIFIED: 'submitted_unverified',
    SUBMISSION_UNKNOWN: 'submission_unknown',
    RECONCILING: 'reconciling',
    SCHEDULED_REMOTE: 'scheduled_remote',
    REVIEWING: 'reviewing',
    PUBLISHED: 'published',
    REJECTED: 'rejected',
    WAITING_USER: 'waiting_user',
    NOT_SUBMITTED: 'not_submitted',
  },
  MaterialStatus: {},
  MaterialType: { VIDEO: 'video' },
  MediaType: {},
}))

const FRIENDLY_INPUT = {
  engine: 'timeline',
  mode: 'script',
  assetBase: '/tmp/demo_c/asset',
  materials: ['a.mp4'],
  script: ['第一句'],
  canvas: 'portrait',
  clipSeconds: 3,
  platforms: ['douyin'],
  bgm: { src: 'bgm.m4a', volume: 0.35 },
}

function createService() {
  type Deps = ConstructorParameters<typeof ProductionService>
  const projectRepo = {
    getByIdForUser: vi.fn(),
  }
  const runRepo = {
    create: vi.fn().mockImplementation(async (data: Record<string, unknown>) => ({ id: 'run-1', ...data })),
    getById: vi.fn(),
    getByIdForUser: vi.fn(),
    updateById: vi.fn().mockImplementation(async (id: string, update: Record<string, unknown>) => ({ id, ...update })),
    updateByRevision: vi.fn().mockImplementation(async (id: string, revision: number, update: Record<string, unknown>) => ({
      id,
      revision: (update['revision'] as number | undefined) ?? revision,
      ...update,
    })),
  }
  const jobRepo = {
    create: vi.fn().mockImplementation(async (data: Record<string, unknown>) => ({ id: 'job-1', ...data })),
    getById: vi.fn(),
    finish: vi.fn().mockResolvedValue(true),
    updateById: vi.fn(),
    listByStatus: vi.fn().mockResolvedValue([]),
    claimNext: vi.fn().mockResolvedValue(null),
    renewLease: vi.fn().mockResolvedValue(null),
    findExpired: vi.fn().mockResolvedValue([]),
    listByRun: vi.fn().mockResolvedValue([]),
    listByRunBeforeRevision: vi.fn().mockResolvedValue([]),
    updateByStatusIn: vi.fn().mockResolvedValue(true),
    updateByLeaseExpiry: vi.fn().mockResolvedValue(true),
    nextAttempt: vi.fn().mockResolvedValue(2),
  }
  const attemptRepo = {
    getByIdempotencyKey: vi.fn().mockResolvedValue(null),
    create: vi.fn().mockImplementation(async (data: Record<string, unknown>) => ({ id: 'attempt-1', ...data })),
  }
  const workerRepo = {
    updateById: vi.fn().mockImplementation(async (id: string, update: Record<string, unknown>) => ({ id, ...update })),
  }
  const eventRepo = {
    lastSeq: vi.fn().mockResolvedValue(0),
    append: vi.fn().mockImplementation(async (event: Record<string, unknown>) => ({ id: 'event-1', ...event })),
  }
  const service = new ProductionService(
    projectRepo as Deps[0],
    runRepo as Deps[1],
    jobRepo as Deps[2],
    attemptRepo as Deps[3],
    workerRepo as Deps[4],
    eventRepo as Deps[5],
    {} as Deps[6],
    {} as Deps[7],
  )
  return { service, projectRepo, runRepo, jobRepo, attemptRepo, workerRepo, eventRepo }
}

describe('createRun', () => {
  it('入库前把友好输入展开成时间轴规格', async () => {
    const { service, projectRepo, runRepo } = createService()
    projectRepo.getByIdForUser.mockResolvedValue({ id: '6adc2161656a13996166bbfc' })

    await service.createRun('user-1', {
      projectId: '6adc2161656a13996166bbfc',
      title: '演示',
      brief: '三秒一句',
      spec: { ...FRIENDLY_INPUT },
    })

    const spec = runRepo.create.mock.calls[0][0].spec as Record<string, unknown>
    expect(spec.engine).toBe('timeline')
    expect(spec.input).toEqual(FRIENDLY_INPUT)
    expect((spec.timeline as Record<string, unknown>).canvas).toEqual({ width: 1080, height: 1920, fps: 30 })
    expect((spec.timeline as Record<string, unknown>).video).toEqual([{ src: 'a.mp4', in: 0, out: 3 }])
    expect((spec.timeline as Record<string, unknown>).text).toHaveLength(1)
  })
})

describe('createRunBatch', () => {
  it('同一份稿子排 N 条：每条独立 run，标题带序号', async () => {
    const { service, projectRepo, runRepo } = createService()
    projectRepo.getByIdForUser.mockResolvedValue({ id: '6adc2161656a13996166bbfc' })
    runRepo.create.mockImplementation(async (data: Record<string, unknown>) => ({ id: `run-${runRepo.create.mock.calls.length}`, ...data }))

    const runs = await service.createRunBatch('user-1', {
      projectId: '6adc2161656a13996166bbfc',
      title: '咖啡机',
      brief: '三秒一句',
      spec: { ...FRIENDLY_INPUT },
    }, 3)

    expect(runs).toHaveLength(3)
    expect(runRepo.create).toHaveBeenCalledTimes(3)
    expect(runs.map(r => r.title)).toEqual(['咖啡机 · 1/3', '咖啡机 · 2/3', '咖啡机 · 3/3'])
    expect(runRepo.create.mock.calls.map(c => c[0].status)).toEqual(['queued', 'queued', 'queued'])
  })

  it('timeline 稿按条错开：原稿、镜像推拉、换开场，engine 一直保留', async () => {
    const { service, projectRepo, runRepo } = createService()
    projectRepo.getByIdForUser.mockResolvedValue({ id: '6adc2161656a13996166bbfc' })
    runRepo.create.mockImplementation(async (data: Record<string, unknown>) => ({ id: `run-${runRepo.create.mock.calls.length}`, ...data }))
    const timeline = {
      canvas: { width: 1080, height: 1920, fps: 30 },
      video: [
        { src: 'a.mp4', in: 0, out: 3, keyframes: [{ at: 0, zoom: 1 }, { at: 3, zoom: 1.12 }] },
        { src: 'b.mp4', in: 0, out: 4, keyframes: [{ at: 0, zoom: 1.1 }, { at: 4, zoom: 1 }] },
      ],
    }

    await service.createRunBatch('user-1', {
      projectId: '6adc2161656a13996166bbfc',
      title: '咖啡机',
      brief: '三秒一句',
      spec: { engine: 'timeline', timeline },
    }, 3)

    const specs = runRepo.create.mock.calls.map(c => c[0].spec as Record<string, unknown>)
    const first = specs[0]['timeline'] as Record<string, unknown>
    const second = specs[1]['timeline'] as Record<string, unknown>
    const third = specs[2]['timeline'] as Record<string, unknown>
    expect(specs.map(s => s.engine)).toEqual(['timeline', 'timeline', 'timeline'])
    // 第 1 条原稿逐字不动
    expect(first.video).toEqual(timeline.video)
    // 第 2 条只把 zoom 值倒过来，时间点和镜头顺序都不变
    expect((second.video as typeof timeline.video)[0].keyframes).toEqual([{ at: 0, zoom: 1.12 }, { at: 3, zoom: 1 }])
    expect((second.video as typeof timeline.video).map(v => v.src)).toEqual(['a.mp4', 'b.mp4'])
    // 第 3 条再换开场
    expect((third.video as typeof timeline.video).map(v => v.src)).toEqual(['b.mp4', 'a.mp4'])
  })
})

describe('reviseRun', () => {
  it('以审计底稿为基线重新展开，不对已展开规格二次展开', async () => {
    const { service, runRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue({
      id: 'run-1',
      revision: 1,
      brief: '旧需求',
      spec: {
        engine: 'timeline',
        input: { ...FRIENDLY_INPUT },
        timeline: { canvas: { width: 1080, height: 1920, fps: 30 }, video: [], text: [], audio: [] },
      },
      status: 'succeeded',
      checkpoints: [],
    })

    await service.reviseRun('user-1', 'run-1', { spec: { script: ['改过的文案'] } })

    const spec = runRepo.updateByRevision.mock.calls[0][2].spec as Record<string, unknown>
    expect(spec.input).toEqual({ ...FRIENDLY_INPUT, script: ['改过的文案'] })
    const timeline = spec.timeline as Record<string, unknown>
    expect((timeline.text as Array<Record<string, unknown>>).map(row => row.text)).toEqual(['改过的文案'])
    expect(timeline).not.toHaveProperty('input')
  })
})

describe('completeJob', () => {
  it('把 worker 结果归一成 output.videoPath/videoUrl/duration/canvas/platforms', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue({ id: 'job-1', runId: 'run-1', attempt: 1 })
    runRepo.getById.mockResolvedValue({ id: 'run-1', output: { materialId: 'mat-1' } })

    const result = await service.completeJob('worker-1', 'job-1', 1, {
      engine: 'timeline',
      videoKey: 'studio/runs/run-1/r1/out.mp4',
      videoUrl: '/oss/studio/runs/run-1/r1/out.mp4',
      duration: 5.2,
      canvas: { width: 1080, height: 1920, fps: 30 },
      deliveries: [
        {
          platform: 'douyin',
          status: 'ok',
          ok: true,
          key: 'studio/runs/run-1/r1/platforms/douyin/douyin.mp4',
          url: '/oss/studio/runs/run-1/r1/platforms/douyin/douyin.mp4',
          bytes: 12345,
          probe: { width: 1080, height: 1920 },
        },
        {
          platform: 'bilibili',
          status: 'failed',
          ok: false,
          uploadError: 'upload failed',
        },
      ],
    })

    expect(result).toEqual({ ok: true })
    const update = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(update.status).toBe('succeeded')
    expect(update.currentStage).toBe('done')
    expect(update.output).toEqual({
      materialId: 'mat-1',
      videoPath: 'studio/runs/run-1/r1/out.mp4',
      videoUrl: '/oss/studio/runs/run-1/r1/out.mp4',
      duration: 5.2,
      canvas: { width: 1080, height: 1920, fps: 30 },
      platforms: {
        douyin: {
          key: 'studio/runs/run-1/r1/platforms/douyin/douyin.mp4',
          url: '/oss/studio/runs/run-1/r1/platforms/douyin/douyin.mp4',
          width: 1080,
          height: 1920,
          bytes: 12345,
        },
      },
    })
  })

  it('成片入库落点跟着 output 回前端，不能只活在 worker 日志里', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue({ id: 'job-1', runId: 'run-1', attempt: 1 })
    runRepo.getById.mockResolvedValue({ id: 'run-1', output: {} })

    await service.completeJob('worker-1', 'job-1', 1, {
      engine: 'timeline',
      videoKey: 'studio/runs/run-1/r1/master.mp4',
      videoUrl: '/oss/studio/runs/run-1/r1/master.mp4',
      duration: 30,
      libraryPath: '/Users/apple/CreatorStudio/library/studio/2026-10/20261028-183224__studio__001__片名__douyin__r1.mp4',
      libraryAssetId: '20261028-183224__studio__001__片名__douyin__r1',
      md5: '7f250578fec55e68cce604f0cdb156ba',
      libraryEntries: [
        { kind: 'master', path: '/Users/apple/CreatorStudio/library/studio/2026-10/master.mp4' },
      ],
      // 库里没有落点时 worker 会带 None，这种不能把已有 output 写坏
      unknownField: 'ignored',
    })

    const update = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    const output = update.output as Record<string, unknown>
    expect(output['libraryPath']).toBe('/Users/apple/CreatorStudio/library/studio/2026-10/20261028-183224__studio__001__片名__douyin__r1.mp4')
    expect(output['libraryAssetId']).toBe('20261028-183224__studio__001__片名__douyin__r1')
    expect(output['md5']).toBe('7f250578fec55e68cce604f0cdb156ba')
    expect(output['libraryEntries']).toEqual([
      { kind: 'master', path: '/Users/apple/CreatorStudio/library/studio/2026-10/master.mp4' },
    ])
    expect(output['unknownField']).toBeUndefined()
  })

  it('剪映导出还要人点面板时把 run 记成等人，不造假成片', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue({ id: 'job-1', runId: 'run-1', attempt: 1, progress: 90 })
    runRepo.getById.mockResolvedValue({ id: 'run-1', output: { draftPath: '/draft/studio_1' } })

    // worker 把「要人导出」埋在 result.output 里，老结果也可能只写在 stage 上。
    const result = await service.completeJob('worker-1', 'job-1', 1, {
      stage: 'awaiting_manual_export',
      reasonCode: 'screen_locked',
      draftPath: '/draft/studio_1',
      output: { videoPath: null, requiresManualExport: true },
    })

    expect(result).toEqual({ ok: true })
    const update = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(update.status).toBe('waiting_user')
    expect(update.currentStage).toBe('awaiting_manual_export')
    expect(update.progress).toBe(90)
    expect(update.output).toEqual({
      draftPath: '/draft/studio_1',
      requiresManualExport: true,
      manualExportReason: 'screen_locked',
    })
  })

  it('发布作业完成时没有渲染产物，保留已有 output', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue({ id: 'job-2', runId: 'run-1', attempt: 1 })
    runRepo.getById.mockResolvedValue({
      id: 'run-1',
      output: { videoPath: 'studio/runs/run-1/r1/out.mp4', materialId: 'mat-1' },
    })

    await service.completeJob('worker-1', 'job-2', 1, { publicationHint: 'published' }, { publicationHint: 'published' })

    const update = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(update).not.toHaveProperty('output')
    expect(update.publicationHint).toBe('published')
  })

  it('租约过期时不覆盖 run', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue({ id: 'job-3', runId: 'run-1', attempt: 1 })
    jobRepo.finish.mockResolvedValue(false)

    const result = await service.completeJob('worker-1', 'job-3', 1, { videoKey: 'k.mp4' })

    expect(result).toEqual({ ok: false, reason: 'stale-lease' })
    expect(runRepo.updateById).not.toHaveBeenCalled()
  })
})

describe('planPublication', () => {
  const SUCCEEDED_RUN = {
    id: 'run-1',
    projectId: 'project-1',
    revision: 2,
    status: 'succeeded',
    output: {
      videoPath: 'studio/runs/run-1/r2/master.mp4',
      platforms: {
        douyin: { key: 'studio/runs/run-1/r2/platforms/douyin/douyin.mp4' },
        bilibili: { key: 'studio/runs/run-1/r2/platforms/bilibili/bilibili.mp4' },
      },
    },
  }

  it('优先投递该平台自己的成片，并记录实际使用的 key', async () => {
    const { service, runRepo, attemptRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(SUCCEEDED_RUN)

    const result = await service.planPublication('user-1', {
      runId: 'run-1',
      platform: 'douyin',
      dispatcher: 'sau',
      accountRef: 'acc-1',
      content: { caption: '文案', tags: ['#标签'] },
    })

    expect(result.deduped).toBe(false)
    expect(attemptRepo.create.mock.calls[0][0].content).toEqual({
      caption: '文案',
      tags: ['#标签'],
      assetKey: 'studio/runs/run-1/r2/platforms/douyin/douyin.mp4',
    })
  })

  it('该平台没有独立成片时回退母版 key', async () => {
    const { service, runRepo, attemptRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue({
      ...SUCCEEDED_RUN,
      output: { videoPath: 'studio/runs/run-1/r2/master.mp4', platforms: {} },
    })

    await service.planPublication('user-1', {
      runId: 'run-1',
      platform: 'douyin',
      dispatcher: 'sau',
      accountRef: 'acc-1',
    })

    expect(attemptRepo.create.mock.calls[0][0].content).toEqual({
      assetKey: 'studio/runs/run-1/r2/master.mp4',
    })
  })

  it('没有任何成片时拒绝计划发布', async () => {
    const { service, runRepo, attemptRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue({ ...SUCCEEDED_RUN, output: {} })

    await expect(service.planPublication('user-1', {
      runId: 'run-1',
      platform: 'douyin',
      dispatcher: 'sau',
      accountRef: 'acc-1',
    })).rejects.toThrow('运行还没有可发布的成片产物')
    expect(attemptRepo.create).not.toHaveBeenCalled()
  })

  it('同一幂等键命中时直接返回已有计划', async () => {
    const { service, runRepo, attemptRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(SUCCEEDED_RUN)
    attemptRepo.getByIdempotencyKey.mockResolvedValue({ id: 'attempt-existing' })

    const result = await service.planPublication('user-1', {
      runId: 'run-1',
      platform: 'douyin',
      dispatcher: 'sau',
      accountRef: 'acc-1',
    })

    expect(result).toEqual({ attempt: { id: 'attempt-existing' }, deduped: true })
    expect(attemptRepo.create).not.toHaveBeenCalled()
  })
})

describe('refreshWorkerCapabilities', () => {
  it('去掉空白与重复项后写回 worker 记录', async () => {
    const { service, workerRepo } = createService()

    const result = await service.refreshWorkerCapabilities('worker-1', [
      'render_mpt',
      ' render_mpt ',
      '',
      'render_timeline',
    ])

    expect(workerRepo.updateById).toHaveBeenCalledWith('worker-1', {
      capabilities: ['render_mpt', 'render_timeline'],
    })
    expect(result).toEqual({ ok: true, capabilities: ['render_mpt', 'render_timeline'] })
  })
})

describe('reapExpiredLeases', () => {
  const LEASE_EXPIRED_AT = new Date('2020-01-01T00:00:00.000Z')
  const expiredJob = {
    id: 'job-1',
    runId: 'run-1',
    kind: 'render_timeline',
    attempt: 1,
    status: 'running',
    leaseOwner: 'worker-1',
    leaseVersion: 2,
    leaseExpiresAt: LEASE_EXPIRED_AT,
  }

  it('把过期 job 判失败，run 回 queued 并补一条新的队列任务', async () => {
    const { service, jobRepo, runRepo, eventRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([expiredJob])
    jobRepo.listByRun.mockResolvedValue([expiredJob])
    jobRepo.nextAttempt.mockResolvedValue(2)
    runRepo.getById.mockResolvedValue({ id: 'run-1', userId: 'user-1', status: 'queued', spec: { engine: 'timeline' } })

    await expect(service.reapExpiredLeases()).resolves.toBe(1)

    // 用扫描时看到的 leaseExpiresAt 做条件更新：worker 在扫描间隙续约时不会被误杀
    expect(jobRepo.updateByLeaseExpiry).toHaveBeenCalledWith('job-1', 'worker-1', 2, LEASE_EXPIRED_AT, {
      status: 'failed',
      message: 'lease expired',
      lastError: 'worker stopped heart-beating',
    })
    const runUpdate = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(runUpdate.status).toBe('queued')
    expect(runUpdate.currentStage).toBe('queued')
    const created = jobRepo.create.mock.calls[0][0] as Record<string, unknown>
    expect(created).toMatchObject({ runId: 'run-1', kind: 'render_timeline', attempt: 2, status: 'queued' })
    const kinds = eventRepo.append.mock.calls.map(call => (call[0] as Record<string, unknown>).kind)
    expect(kinds).toEqual(['job.failed', 'job.requeued'])
  })

  it('扫描之间 worker 续约了就不动 run', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([expiredJob])
    jobRepo.updateByLeaseExpiry.mockResolvedValue(null)

    await expect(service.reapExpiredLeases()).resolves.toBe(0)

    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('run 已成功时不回退状态', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([expiredJob])
    runRepo.getById.mockResolvedValue({ id: 'run-1', userId: 'user-1', status: 'succeeded' })

    await expect(service.reapExpiredLeases()).resolves.toBe(0)

    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('run 已有排队/执行中的 job 时不重复补任务', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([expiredJob])
    jobRepo.listByRun.mockResolvedValue([
      expiredJob,
      { ...expiredJob, id: 'job-2', attempt: 2, status: 'queued' },
    ])
    runRepo.getById.mockResolvedValue({ id: 'run-1', userId: 'user-1', status: 'queued' })

    await expect(service.reapExpiredLeases()).resolves.toBe(0)

    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('连续丢 3 次租约后停止自动重试，run 判失败', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([{ ...expiredJob, attempt: 3 }])
    jobRepo.listByRun.mockResolvedValue([{ ...expiredJob, attempt: 3 }])
    runRepo.getById.mockResolvedValue({ id: 'run-1', userId: 'user-1', status: 'queued' })

    await expect(service.reapExpiredLeases()).resolves.toBe(0)

    const runUpdate = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(runUpdate.status).toBe('failed')
    expect(runUpdate.lastError).toContain('停止自动重试')
    expect(jobRepo.create).not.toHaveBeenCalled()
  })
})

describe('任务领取与心跳', () => {
  it('领取后 run 从排队翻成渲染中', async () => {
    const { service, runRepo, jobRepo } = createService()
    jobRepo.claimNext.mockResolvedValue({
      id: 'job-1',
      runId: 'run-1',
      attempt: 1,
      stage: 'queued',
      payload: {},
    })
    runRepo.getById.mockResolvedValue({ id: 'run-1', status: 'queued' })
    const job = await service.claimJob(
      { id: 'worker-1', userId: 'user-1', capabilities: ['render_timeline'] },
      'render',
    )
    expect(job).not.toBeNull()
    expect(jobRepo.claimNext).toHaveBeenCalledWith('render', 'worker-1', 60_000, ['render_timeline'], 'user-1')
    const runUpdate = runRepo.updateByRevision.mock.calls.find(call => call[0] === 'run-1')
    expect(runUpdate?.[2]).toMatchObject({ status: 'running', currentStage: 'starting' })
  })

  it('心跳把阶段和进度同步回 run', async () => {
    const { service, runRepo, jobRepo } = createService()
    jobRepo.renewLease.mockResolvedValue({ id: 'job-1', runId: 'run-1', stage: 'timeline:script', progress: 4, leaseExpiresAt: new Date() })
    await service.heartbeatJob('worker-1', 'job-1', 1, 12, 'timeline:rendering')
    const runUpdate = runRepo.updateByRevision.mock.calls.find(call => call[0] === 'run-1')
    expect(runUpdate?.[2]).toMatchObject({ status: 'running', currentStage: 'timeline:rendering', progress: 12 })
  })
})

describe('startJianyingExport', () => {
  it('记住草稿目录后单独排一条导出任务，不再让渲染队列猜草稿', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue({
      id: 'run-1',
      revision: 3,
      status: 'draft_ready',
      output: { draftPath: '/Users/apple/Movies/JianyingPro/demo' },
    })

    const result = await service.startJianyingExport('user-1', 'run-1', { allowGui: false })

    expect(result.draftPath).toBe('/Users/apple/Movies/JianyingPro/demo')
    const created = jobRepo.create.mock.calls[0][0] as Record<string, unknown>
    expect(created['kind']).toBe('jianying_export')
    expect(created['revision']).toBe(3)
    expect(created['payload']).toMatchObject({
      runId: 'run-1',
      revision: 3,
      draftPath: '/Users/apple/Movies/JianyingPro/demo',
      allowGui: false,
    })
  })

  it('没有草稿路径却要驱动导出面板时直接拒绝，不排一个必然失败的任务', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue({ id: 'run-2', revision: 1, status: 'done', output: {} })

    await expect(service.startJianyingExport('user-1', 'run-2', { allowGui: true })).rejects.toThrow()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })
})

describe('failJob', () => {
  const failingJob = {
    id: 'job-1',
    runId: 'run-1',
    kind: 'render_timeline',
    attempt: 1,
    revision: 1,
    stage: 'rendering',
  }
  const failedRun = {
    id: 'run-1',
    userId: 'user-1',
    status: 'running',
    revision: 1,
    spec: { engine: 'timeline' },
  }

  it('本机缺依赖时自动重排，不把 run 判死', async () => {
    const { service, jobRepo, runRepo, eventRepo } = createService()
    jobRepo.getById.mockResolvedValue(failingJob)
    runRepo.getById.mockResolvedValue(failedRun)

    const result = await service.failJob(
      'worker-1',
      'job-1',
      1,
      'ModuleNotFoundError: No module named \'numpy\'',
    )

    expect(result).toEqual({ ok: true, retry: true })
    expect(jobRepo.finish).toHaveBeenCalledWith('job-1', 'worker-1', 1, 1, {
      status: 'failed',
      lastError: 'ModuleNotFoundError: No module named \'numpy\'',
    })
    const runUpdate = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(runUpdate.status).toBe('queued')
    expect(runUpdate.currentStage).toBe('queued')
    expect(String(runUpdate.lastError)).toContain('已自动重排')
    const created = jobRepo.create.mock.calls[0][0] as Record<string, unknown>
    expect(created.kind).toBe('render_timeline')
    expect(eventRepo.append).toHaveBeenCalledWith(expect.objectContaining({ kind: 'job.requeued' }))
  })

  it('稿子本身跑挂了就照实记失败，不无限重排', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue(failingJob)
    runRepo.getById.mockResolvedValue(failedRun)

    await expect(service.failJob('worker-1', 'job-1', 1, '导出失败 rc=4: 面板不可用')).resolves.toEqual({ ok: true })

    const runUpdate = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(runUpdate.status).toBe('failed')
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('已出片的 run 不会被一条迟到的环境类失败拉回队列', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.getById.mockResolvedValue(failingJob)
    runRepo.getById.mockResolvedValue({ ...failedRun, status: 'succeeded' })

    await expect(service.failJob('worker-1', 'job-1', 1, 'FileNotFoundError: /tmp/ax_tool')).resolves.toEqual({ ok: true })

    const runUpdate = runRepo.updateByRevision.mock.calls[0][2] as Record<string, unknown>
    expect(runUpdate.status).toBe('failed')
    expect(jobRepo.create).not.toHaveBeenCalled()
  })
})
