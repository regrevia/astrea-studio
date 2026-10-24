import { RequestMethod, UnauthorizedException } from '@nestjs/common'
import { METHOD_METADATA, PATH_METADATA } from '@nestjs/common/constants'
import { AppException } from '@yikart/common'
import { describe, expect, it, vi } from 'vitest'

import { ProductionWorkerController } from './production.controller'

vi.mock('@yikart/aitoearn-auth', () => ({
  GetToken: () => () => undefined,
  Public: () => () => undefined,
  TokenInfo: class TokenInfo {},
}))

vi.mock('@yikart/mongodb', () => ({
  ProductionJobStatus: { FAILED: 'failed' },
  PublicationDispatcher: { AITOEARN: 'aitoearn', SAU: 'sau', MANUAL: 'manual' },
  PublicationStatus: { PLANNED: 'planned' },
}))

vi.mock('./production.service', () => ({ ProductionService: class ProductionService {} }))

function createController(overrides: Record<string, unknown> = {}) {
  const production = {
    resolveWorker: vi.fn().mockResolvedValue({ id: 'worker-1', userId: 'user-1', capabilities: [] }),
    refreshWorkerCapabilities: vi.fn().mockResolvedValue({ ok: true, capabilities: ['render_timeline'] }),
    ...overrides,
  }
  return { controller: new ProductionWorkerController(production as never), production }
}

describe('ProductionWorkerController', () => {
  it('能力刷新接口挂在 worker 的固定 URL 上', () => {
    const handler = ProductionWorkerController.prototype.refreshCapabilities

    expect(Reflect.getMetadata(PATH_METADATA, ProductionWorkerController)).toBe('production/worker')
    expect(Reflect.getMetadata(PATH_METADATA, handler)).toBe('capabilities')
    expect(Reflect.getMetadata(METHOD_METADATA, handler)).toBe(RequestMethod.POST)
  })

  it('把 worker 上报的能力写回它自己的记录', async () => {
    const { controller, production } = createController()

    const result = await controller.refreshCapabilities('wkr_token', {
      capabilities: ['render_timeline'],
    })

    expect(production.resolveWorker).toHaveBeenCalledWith('wkr_token')
    expect(production.refreshWorkerCapabilities).toHaveBeenCalledWith('worker-1', ['render_timeline'])
    expect(result).toEqual({ ok: true, capabilities: ['render_timeline'] })
  })

  it('缺少 worker token 时拒绝', async () => {
    const { controller, production } = createController()

    await expect(controller.refreshCapabilities(undefined, { capabilities: [] }))
      .rejects.toBeInstanceOf(UnauthorizedException)
    expect(production.refreshWorkerCapabilities).not.toHaveBeenCalled()
  })

  it('token 无效时拒绝', async () => {
    const { controller } = createController({ resolveWorker: vi.fn().mockResolvedValue(null) })

    await expect(controller.refreshCapabilities('bad', { capabilities: [] }))
      .rejects.toBeInstanceOf(AppException)
  })
})
