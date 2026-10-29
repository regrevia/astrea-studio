import type { Model } from 'mongoose'
import type { ProductionJob } from '../schemas/production.schema'
import { describe, expect, it, vi } from 'vitest'
import { ProductionJobRepository } from './production.repository'

vi.mock('../schemas/production.schema', () => ({
  ProductionJob: class ProductionJob {},
  ProductionJobStatus: {
    QUEUED: 'queued',
    RUNNING: 'running',
    SUCCEEDED: 'succeeded',
    FAILED: 'failed',
    CANCELLED: 'cancelled',
  },
  ProductionRun: class ProductionRun {},
  ProductionProject: class ProductionProject {},
  ProductionWorker: class ProductionWorker {},
  ProductionEvent: class ProductionEvent {},
  PublicationAttempt: class PublicationAttempt {},
}))

function fakeModel(doc: Record<string, unknown> | null) {
  const exec = vi.fn(async () => doc)
  const lean = vi.fn(() => ({ exec }))
  const findOneAndUpdate = vi.fn(() => ({ lean }))
  return { model: { findOneAndUpdate } as unknown as Model<ProductionJob>, findOneAndUpdate, lean, exec }
}

describe('production job repository claimNext', () => {
  it('带着 id 返回领取到的作业', async () => {
    // lean 不带 virtuals 时 mongoose 只给 _id，service 里 appendEvent/取消分支
    // 用的都是 job.id，拿到 undefined 就会写出没有指向的 job.claimed 事件、
    // 取消分支静默不生效——现象是事件流里看不到作业号，排查时找不到那条作业。
    const { model, lean } = fakeModel({
      _id: '6ae2beb0b3562e66f3cefb85',
      id: '6ae2beb0b3562e66f3cefb85',
      runId: '6ae237a72a5e71e140145c49',
      lane: 'publish',
      kind: 'publish_sau',
      status: 'running',
    })
    const repository = new ProductionJobRepository(model)

    const job = await repository.claimNext('publish', 'worker-1', 60_000, ['publish_sau'], 'user-1')

    expect(lean).toHaveBeenCalledWith({ virtuals: true })
    expect(job?.id).toBe('6ae2beb0b3562e66f3cefb85')
  })

  it('只领本用户、本泳道、写进能力的活', async () => {
    const { model, findOneAndUpdate } = fakeModel(null)
    const repository = new ProductionJobRepository(model)

    const job = await repository.claimNext('publish', 'worker-1', 60_000, ['publish_sau'], 'user-1')

    expect(job).toBeNull()
    const [filter] = findOneAndUpdate.mock.calls[0] as unknown as [Record<string, unknown>]
    expect(filter).toMatchObject({ lane: 'publish', userId: 'user-1', status: 'queued' })
    expect(filter['kind']).toEqual({ $in: ['publish_sau'] })
  })

  it('没有能力就一条都不领', async () => {
    const { model, findOneAndUpdate } = fakeModel(null)
    const repository = new ProductionJobRepository(model)

    const job = await repository.claimNext('publish', 'worker-1', 60_000, [], 'user-1')

    expect(job).toBeNull()
    expect(findOneAndUpdate).not.toHaveBeenCalled()
  })
})
