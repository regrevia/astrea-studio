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
    PUBLISH_SAU: 'publish_sau',
    PUBLISH_READBACK: 'publish_readback',
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

function createService() {
  type Deps = ConstructorParameters<typeof ProductionService>
  const projectRepo = {
    getByIdForUser: vi.fn(),
  }
  const runRepo = {
    create: vi.fn().mockImplementation(async (data: Record<string, unknown>) => ({ id: 'run-1', revision: 1, ...data })),
    getById: vi.fn(),
    getByIdForUser: vi.fn(),
    updateById: vi.fn().mockImplementation(async (id: string, update: Record<string, unknown>) => ({ id, ...update })),
    updateByRevision: vi.fn().mockImplementation(async (id: string, revision: number, update: Record<string, unknown>) => ({
      id,
      revision,
      ...update,
    })),
  }
  const jobRepo = {
    create: vi.fn().mockImplementation(async (data: Record<string, unknown>) => ({ id: 'job-1', ...data })),
    getById: vi.fn(),
    finish: vi.fn().mockResolvedValue(null),
    updateById: vi.fn(),
    updateByStatusIn: vi.fn().mockResolvedValue(null),
    updateByLeaseExpiry: vi.fn().mockResolvedValue(null),
    listByStatus: vi.fn().mockResolvedValue([]),
    claimNext: vi.fn().mockResolvedValue(null),
    renewLease: vi.fn().mockResolvedValue(null),
    findExpired: vi.fn().mockResolvedValue([]),
    listByRun: vi.fn().mockResolvedValue([]),
    listByRunBeforeRevision: vi.fn().mockResolvedValue([]),
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
    listByRun: vi.fn().mockResolvedValue([]),
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

function runOf(overrides: Record<string, unknown> = {}) {
  return {
    id: 'run-1',
    userId: 'user-1',
    revision: 1,
    brief: 'b',
    spec: {},
    status: 'queued',
    checkpoints: [],
    output: {},
    ...overrides,
  }
}

describe('G01 旧 revision 迟到回写不覆盖新 revision', () => {
  it('revise 只接受终态 run：排队中的 run 不能开新 revision', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ status: 'queued' }))

    await expect(service.reviseRun('user-1', 'run-1', { brief: 'next' })).rejects.toThrow()
    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('revise 用读到的 revision 做 CAS，并发改稿失败而不是覆盖', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ status: 'succeeded', revision: 2 }))
    runRepo.updateByRevision.mockResolvedValue(null)

    await expect(service.reviseRun('user-1', 'run-1', { brief: 'next' })).rejects.toThrow()
    expect(runRepo.updateByRevision).toHaveBeenCalledWith('run-1', 2, expect.anything())
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('revise 成功时新 job 带上新 revision', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ status: 'failed', revision: 1, spec: { engine: 'slides' } }))
    runRepo.updateByRevision.mockResolvedValue(runOf({ status: 'queued', revision: 2, spec: { engine: 'slides' } }))
    jobRepo.nextAttempt.mockResolvedValue(2)

    await service.reviseRun('user-1', 'run-1', { brief: 'next' })

    expect(jobRepo.create).toHaveBeenCalledWith(expect.objectContaining({
      runId: 'run-1',
      revision: 2,
      kind: 'slides_render',
      attempt: 2,
    }))
  })

  it('旧 revision 的 job 迟到完成：只作废 job，不改 run', async () => {
    const { service, runRepo, jobRepo } = createService()
    jobRepo.getById.mockResolvedValue({
      id: 'job-1',
      runId: 'run-1',
      revision: 1,
      attempt: 1,
      status: 'running',
      leaseOwner: 'worker-1',
      leaseVersion: 3,
      stage: 'rendering',
    })
    runRepo.getById.mockResolvedValue(runOf({ revision: 2, status: 'queued' }))
    jobRepo.finish.mockResolvedValue({ id: 'job-1', revision: 1, status: 'cancelled' })

    const result = await service.completeJob('worker-1', 'job-1', 3, { videoKey: 'late.mp4' })

    expect(result).toEqual({ ok: false, reason: 'stale-revision' })
    expect(jobRepo.finish).toHaveBeenCalledWith('job-1', 'worker-1', 3, 1, expect.objectContaining({
      status: 'cancelled',
    }))
    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
    expect(runRepo.updateById).not.toHaveBeenCalled()
  })
})

describe('G02 retry 使用真实渲染 kind，不与唯一索引相撞', () => {
  it('timeline run 重试按 render_timeline 取 attempt 并补同 kind 的 job', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ status: 'failed', revision: 3, spec: { engine: 'timeline' } }))
    jobRepo.nextAttempt.mockResolvedValue(4)

    const result = await service.retryRun('user-1', 'run-1')

    expect(jobRepo.nextAttempt).toHaveBeenCalledWith('run-1', 'render_timeline')
    expect(jobRepo.create).toHaveBeenCalledWith(expect.objectContaining({
      runId: 'run-1',
      kind: 'render_timeline',
      revision: 3,
      attempt: 4,
    }))
    expect(result).toEqual({ runId: 'run-1', attempt: 4 })
  })

  it('motion run 重试走 motion_render，不会退回 render_mpt', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ status: 'failed', revision: 2, spec: { engine: 'motion' } }))

    await service.retryRun('user-1', 'run-1')

    expect(jobRepo.nextAttempt).toHaveBeenCalledWith('run-1', 'motion_render')
    expect(jobRepo.create).toHaveBeenCalledWith(expect.objectContaining({ kind: 'motion_render' }))
  })
})

describe('G03 取消后 complete 不得生效', () => {
  it('cancel 用条件更新作废 job（并推进 fencing token），再改 run 状态', async () => {
    const { service, runRepo, jobRepo } = createService()
    runRepo.getByIdForUser.mockResolvedValue(runOf({ revision: 2, status: 'running' }))
    jobRepo.listByRun.mockResolvedValue([
      { id: 'job-1', runId: 'run-1', revision: 2, status: 'running', leaseOwner: 'worker-1', leaseVersion: 5, stage: 'rendering' },
    ])
    jobRepo.updateByStatusIn.mockResolvedValue({ id: 'job-1', status: 'cancelled' })
    runRepo.updateByRevision.mockResolvedValue(runOf({ revision: 2, status: 'cancelled' }))

    await service.cancelRun('user-1', 'run-1')

    const [jobId, statuses, update] = jobRepo.updateByStatusIn.mock.calls[0]
    expect(jobId).toBe('job-1')
    expect(statuses).toEqual(['queued', 'running'])
    expect(update).toEqual(expect.objectContaining({
      $set: expect.objectContaining({ status: 'cancelled' }),
      $inc: { leaseVersion: 1 },
    }))
    expect(runRepo.updateByRevision).toHaveBeenCalledWith('run-1', 2, expect.anything(), expect.anything())
    expect(runRepo.updateById).not.toHaveBeenCalled()
  })

  it('取消后的 job 再报完成：finish 被拒，run 不会再被写成 succeeded', async () => {
    const { service, runRepo, jobRepo } = createService()
    const job: Record<string, unknown> = {
      id: 'job-1',
      runId: 'run-1',
      revision: 2,
      attempt: 1,
      status: 'running',
      leaseOwner: 'worker-1',
      leaseVersion: 5,
      stage: 'rendering',
    }
    jobRepo.getById.mockImplementation(async () => ({ ...job }))
    jobRepo.updateByStatusIn.mockImplementation(async (_id: string, statuses: string[], update: Record<string, any>) => {
      if (!statuses.includes(job['status'] as string)) {
        return null
      }
      Object.assign(job, update.$set ?? {}, { leaseVersion: (job['leaseVersion'] as number) + 1, leaseExpiresAt: null })
      return { ...job }
    })
    jobRepo.finish.mockImplementation(async (_id: string, workerId: string, leaseVersion: number) => {
      if (job['status'] !== 'running' || job['leaseOwner'] !== workerId || job['leaseVersion'] !== leaseVersion) {
        return null
      }
      job['status'] = 'succeeded'
      return { ...job }
    })
    runRepo.getByIdForUser.mockResolvedValue(runOf({ revision: 2, status: 'running' }))
    jobRepo.listByRun.mockResolvedValue([job])
    runRepo.updateByRevision.mockResolvedValue(runOf({ revision: 2, status: 'cancelled' }))

    await service.cancelRun('user-1', 'run-1')
    const result = await service.completeJob('worker-1', 'job-1', 5, { videoKey: 'ghost.mp4' })

    expect(result).toEqual({ ok: false, reason: 'stale-lease' })
    expect(runRepo.updateByRevision).toHaveBeenCalledTimes(1)
    expect(runRepo.updateByRevision.mock.calls.every(call => call[2]['status'] !== 'succeeded')).toBe(true)
  })
})

describe('G04 回收器与续约竞争：续约赢', () => {
  const leaseExpiresAt = new Date('2026-01-01T00:00:00.000Z')

  function expiredJob() {
    return {
      id: 'job-1',
      runId: 'run-1',
      userId: 'user-1',
      kind: 'render_timeline',
      revision: 2,
      attempt: 1,
      status: 'running',
      leaseOwner: 'worker-1',
      leaseVersion: 2,
      leaseExpiresAt,
    }
  }

  it('用过观察到的 leaseExpiresAt 做条件作废，续约成功则回收不生效', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.findExpired.mockResolvedValue([expiredJob()])
    jobRepo.updateByLeaseExpiry.mockResolvedValue(null)
    runRepo.getById.mockResolvedValue(runOf({ revision: 2, status: 'running' }))

    await expect(service.reapExpiredLeases()).resolves.toBe(0)

    expect(jobRepo.updateByLeaseExpiry).toHaveBeenCalledWith('job-1', 'worker-1', 2, leaseExpiresAt, expect.objectContaining({
      status: 'failed',
    }))
    expect(jobRepo.finish).not.toHaveBeenCalled()
    expect(jobRepo.create).not.toHaveBeenCalled()
  })

  it('租约确实过期：作废旧 job 后按同一 revision 补队列任务', async () => {
    const { service, jobRepo, runRepo } = createService()
    const job = expiredJob()
    jobRepo.findExpired.mockResolvedValue([job])
    jobRepo.updateByLeaseExpiry.mockResolvedValue({ ...job, status: 'failed' })
    jobRepo.listByRun.mockResolvedValue([{ ...job, status: 'failed' }])
    runRepo.getById.mockResolvedValue(runOf({ revision: 2, status: 'running' }))
    runRepo.updateByRevision.mockResolvedValue(runOf({ revision: 2, status: 'queued' }))
    jobRepo.nextAttempt.mockResolvedValue(2)

    await expect(service.reapExpiredLeases()).resolves.toBe(1)

    expect(jobRepo.create).toHaveBeenCalledWith(expect.objectContaining({
      runId: 'run-1',
      kind: 'render_timeline',
      revision: 2,
      attempt: 2,
    }))
  })
})

describe('G05 worker 隔离与空 capabilities', () => {
  it('空 capabilities 不派活', async () => {
    const { service, jobRepo } = createService()

    await expect(service.claimJob({ id: 'worker-1', userId: 'user-1', capabilities: [] }, 'render')).resolves.toBeNull()
    expect(jobRepo.claimNext).not.toHaveBeenCalled()
  })

  it('claimNext 带上已鉴权 worker 的 userId 与 capability 列表', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.claimNext.mockResolvedValue({
      id: 'job-1',
      runId: 'run-1',
      revision: 2,
      kind: 'slides_render',
      attempt: 1,
      status: 'running',
      leaseOwner: 'worker-1',
      leaseVersion: 1,
    })
    runRepo.getById.mockResolvedValue(runOf({ revision: 2, status: 'queued' }))
    runRepo.updateByRevision.mockResolvedValue(runOf({ revision: 2, status: 'running' }))

    const job = await service.claimJob({ id: 'worker-1', userId: 'user-9', capabilities: ['slides_render'] }, 'render')

    expect(job).toEqual(expect.objectContaining({ id: 'job-1' }))
    expect(jobRepo.claimNext).toHaveBeenCalledWith('render', 'worker-1', 60_000, ['slides_render'], 'user-9')
  })

  it('旧 revision 的队列 job 被领取时直接作废，不执行', async () => {
    const { service, jobRepo, runRepo } = createService()
    jobRepo.claimNext
      .mockResolvedValueOnce({
        id: 'job-stale',
        runId: 'run-1',
        revision: 1,
        attempt: 1,
        status: 'running',
        leaseOwner: 'worker-1',
        leaseVersion: 1,
        stage: 'rendering',
      })
      .mockResolvedValueOnce(null)
    jobRepo.updateByStatusIn.mockResolvedValue({ id: 'job-stale', status: 'cancelled' })
    runRepo.getById.mockResolvedValue(runOf({ revision: 2, status: 'queued' }))

    await expect(service.claimJob({ id: 'worker-1', userId: 'user-1', capabilities: ['render_timeline'] }, 'render'))
      .resolves.toBeNull()

    expect(jobRepo.updateByStatusIn).toHaveBeenCalledWith('job-stale', ['queued', 'running'], expect.anything())
    expect(runRepo.updateByRevision).not.toHaveBeenCalled()
  })
})
