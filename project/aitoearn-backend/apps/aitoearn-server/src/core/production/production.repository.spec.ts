import type { Model } from 'mongoose'
import { describe, expect, it } from 'vitest'

// 直接引仓库实现，不走 `@yikart/mongodb` barrel：barrel 会连带加载 account.schema，
// 在 vitest 的 oxc 转译下缺少 decorator metadata 会直接报错。
import { ProductionJobRepository } from '../../../../../libs/mongodb/src/repositories/production.repository'
import { ProductionJobStatus } from '../../../../../libs/mongodb/src/schemas/production.schema'

interface CapturedCall {
  filter: Record<string, unknown>
  update?: Record<string, unknown>
  options?: Record<string, unknown>
}

/**
 * Fake Mongoose model: records the filter/update each query would send and
 * returns a fixed result. Repository guards are assertions on the filter, so
 * this keeps the test at the DB-contract level without a running MongoDB.
 */
function createModel(result: unknown = null) {
  const calls: CapturedCall[] = []
  const chain = (value: unknown) => {
    const query: Record<string, unknown> = {}
    for (const method of ['lean', 'sort', 'limit', 'select']) {
      query[method] = () => query
    }
    query['exec'] = async () => value
    return query
  }
  const record = (call: CapturedCall) => {
    calls.push(call)
    return chain(result)
  }
  const model = {
    findOneAndUpdate: (filter: Record<string, unknown>, update: Record<string, unknown>, options: Record<string, unknown>) =>
      record({ filter, update, options }),
    findOne: (filter: Record<string, unknown>, _projection: unknown, options: Record<string, unknown>) =>
      record({ filter, options }),
    find: (filter: Record<string, unknown>, _projection: unknown, options: Record<string, unknown>) =>
      record({ filter, options }),
    findById: (id: string, _projection: unknown, options: Record<string, unknown>) => record({ filter: { _id: id }, options }),
    countDocuments: (filter: Record<string, unknown>) => record({ filter }),
  }
  return { model: model as unknown as Model<unknown>, calls }
}

function jobRepository(result: unknown = null) {
  const { model, calls } = createModel(result)
  return { repo: new ProductionJobRepository(model as Model<never>), calls }
}

describe('ProductionJobRepository: 领取前按鉴权 worker 收窄', () => {
  it('空 capabilities 直接返回 null，不查库', async () => {
    const { repo, calls } = jobRepository()

    await expect(repo.claimNext('render', 'worker-1', 60_000, [], 'user-1')).resolves.toBeNull()
    expect(calls).toHaveLength(0)
  })

  it('filter 同时带 userId、kind $in capabilities，不再无条件领取', async () => {
    const { repo, calls } = jobRepository({ _id: 'job-1' })

    await repo.claimNext('render', 'worker-1', 60_000, ['slides_render', 'render_timeline'], 'user-9')

    const filter = calls[0].filter
    expect(filter).toEqual(expect.objectContaining({
      status: ProductionJobStatus.QUEUED,
      lane: 'render',
      userId: 'user-9',
      kind: { $in: ['slides_render', 'render_timeline'] },
    }))
  })
})

describe('ProductionJobRepository: 写回必须带租约与状态护栏', () => {
  it('finish 只允许 running 且租约未过期的当前持有者，并校验 revision', async () => {
    const { repo, calls } = jobRepository({ _id: 'job-1' })

    await repo.finish('job-1', 'worker-1', 4, 2, { status: ProductionJobStatus.SUCCEEDED })

    const filter = calls[0].filter
    expect(filter).toEqual(expect.objectContaining({
      _id: 'job-1',
      leaseOwner: 'worker-1',
      leaseVersion: 4,
      status: ProductionJobStatus.RUNNING,
      revision: { $in: [2, null] },
    }))
    expect((filter['leaseExpiresAt'] as Record<string, unknown>)['$gt']).toBeInstanceOf(Date)
    expect(calls[0].update).toEqual(expect.objectContaining({
      $set: expect.objectContaining({ status: ProductionJobStatus.SUCCEEDED }),
    }))
  })

  it('renewLease 同样拒绝已过期的租约，并把 progress/stage 合进同一次更新', async () => {
    const { repo, calls } = jobRepository({ _id: 'job-1' })

    await repo.renewLease('job-1', 'worker-1', 4, 60_000, { progress: 40, stage: 'rendering' })

    const filter = calls[0].filter
    expect(filter).toEqual(expect.objectContaining({
      _id: 'job-1',
      leaseOwner: 'worker-1',
      leaseVersion: 4,
      status: ProductionJobStatus.RUNNING,
    }))
    expect((filter['leaseExpiresAt'] as Record<string, unknown>)['$gt']).toBeInstanceOf(Date)
    expect(calls[0].update).toEqual(expect.objectContaining({
      $set: expect.objectContaining({ progress: 40, stage: 'rendering' }),
    }))
  })

  it('续约成功后 leaseExpiresAt 变化会让按旧值作废的更新落空', async () => {
    const { repo, calls } = jobRepository(null)
    const observed = new Date('2026-01-01T00:00:00.000Z')

    await repo.updateByLeaseExpiry('job-1', 'worker-1', 2, observed, { status: ProductionJobStatus.FAILED })

    expect(calls[0].filter).toEqual(expect.objectContaining({
      _id: 'job-1',
      leaseOwner: 'worker-1',
      leaseVersion: 2,
      status: ProductionJobStatus.RUNNING,
      leaseExpiresAt: observed,
    }))
  })

  it('取消用条件更新并推进 fencing token', async () => {
    const { repo, calls } = jobRepository({ _id: 'job-1' })

    await repo.updateByStatusIn('job-1', [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING], {
      $set: { status: ProductionJobStatus.CANCELLED },
      $inc: { leaseVersion: 1 },
    })

    expect(calls[0].filter).toEqual(expect.objectContaining({
      _id: 'job-1',
      status: { $in: [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING] },
    }))
    expect(calls[0].update).toEqual(expect.objectContaining({ $inc: { leaseVersion: 1 } }))
  })
})

describe('ProductionJobRepository: attempt 与 revision 的兼容查询', () => {
  it('nextAttempt 按 runId+kind 在所有 revision 上取最大 attempt，避免跨 revision 撞唯一索引', async () => {
    const { repo, calls } = jobRepository([{ attempt: 7 }])

    await expect(repo.nextAttempt('run-1', 'render_timeline')).resolves.toBe(8)
    expect(calls[0].filter).toEqual({ runId: 'run-1', kind: 'render_timeline' })
  })

  it('refresh 之外的旧 job（无 revision 字段）按 revision 1 处理', async () => {
    const { repo, calls } = jobRepository([{ id: 'job-legacy' }])

    await repo.listByRunBeforeRevision('run-1', 2)

    expect(calls[0].filter).toEqual(expect.objectContaining({
      runId: 'run-1',
      status: { $in: [ProductionJobStatus.QUEUED, ProductionJobStatus.RUNNING] },
      $or: [{ revision: { $lt: 2 } }, { revision: null }],
    }))
  })
})
