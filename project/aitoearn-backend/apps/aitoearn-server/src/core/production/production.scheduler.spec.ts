import { describe, expect, it, vi } from 'vitest'
import { ProductionScheduler } from './production.scheduler'

vi.mock('@nestjs/schedule', () => ({
  Cron: () => () => undefined,
  CronExpression: {
    EVERY_MINUTE: '* * * * *',
  },
}))

vi.mock('./production.service', () => ({ ProductionService: class ProductionService {} }))

describe('production scheduler', () => {
  it('每分钟把过期租约回收一次', async () => {
    const production = { reapExpiredLeases: vi.fn(async () => 2) }
    const scheduler = new ProductionScheduler(production as never)

    await scheduler.reapExpiredLeases()

    expect(production.reapExpiredLeases).toHaveBeenCalledTimes(1)
  })

  it('没有过期租约时静默通过', async () => {
    const production = { reapExpiredLeases: vi.fn(async () => 0) }
    const scheduler = new ProductionScheduler(production as never)

    await expect(scheduler.reapExpiredLeases()).resolves.toBeUndefined()
  })

  it('回收失败不向调度器抛异常，等下一分钟重试', async () => {
    const production = { reapExpiredLeases: vi.fn(async () => { throw new Error('mongo down') }) }
    const scheduler = new ProductionScheduler(production as never)

    await expect(scheduler.reapExpiredLeases()).resolves.toBeUndefined()
    expect(production.reapExpiredLeases).toHaveBeenCalledTimes(1)
  })
})
