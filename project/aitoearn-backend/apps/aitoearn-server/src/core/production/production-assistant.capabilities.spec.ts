import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ProductionAssistantService } from './production-assistant.service'

vi.mock('../../config', () => ({
  config: {
    studioAssistant: {
      baseUrl: 'http://127.0.0.1:11434/v1',
      model: 'qwen3.5:9b',
      apiKey: '',
      inventoryUrl: 'http://host.docker.internal:8899',
    },
  },
}))

vi.mock('./production.service', () => ({ ProductionService: class {} }))

vi.mock('@yikart/mongodb', () => ({
  ProductionAssistantSessionRepository: class {},
  ProductionAssistantRole: { USER: 'user', ASSISTANT: 'assistant' },
  ProductionAssistantActionType: { INFO: 'info' },
  ProductionAssistantActionStatus: { OK: 'ok', FAILED: 'failed' },
  ProductionRunStatus: { SUCCEEDED: 'succeeded' },
  PublicationDispatcher: { MANUAL: 'manual' },
}))

function service() {
  return new ProductionAssistantService({} as never, {} as never, {} as never, {} as never)
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, text: async () => JSON.stringify(body) }
}

describe('宿主机能力读取（MCP 工具用）', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('素材总览原样回给工具层', async () => {
    const payload = { root: '/Users/apple/CreatorStudio/materials', packs: ['envoy-demo'], shots: { count: 2, recent: [] } }
    vi.mocked(fetch).mockResolvedValue(jsonResponse(payload) as never)

    await expect(service().capabilityAssets()).resolves.toEqual(payload)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/assets')
  })

  it('成片清单只取 files，并把 limit 收进 1-500', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ files: [{ name: 'a.mp4', path: '/x/a.mp4' }] }) as never)

    await expect(service().capabilityDeliverables(9999)).resolves.toHaveLength(1)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/deliverables?limit=500')
  })

  it('素材原图只认根目录内的相对路径，越界直接拒', async () => {
    await expect(service().capabilityMaterialFile('../config.py')).rejects.toThrow('素材路径不合法')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'image/png' }),
      arrayBuffer: async () => new Uint8Array([1, 2, 3]).buffer,
    } as never)
    const file = await service().capabilityMaterialFile('/packs/demo/shots/a.png')
    expect(file.contentType).toBe('image/png')
    expect(file.body.length).toBe(3)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/materials/file?path=packs%2Fdemo%2Fshots%2Fa.png')
  })

  it('返回体里没有 files 时给空清单，不抛在工具里', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({}) as never)
    await expect(service().capabilityDeliverables(5)).resolves.toEqual([])
  })

  it('连不上宿主机时说清是能力服务不可达', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('ECONNREFUSED'))
    await expect(service().capabilityAssets()).rejects.toThrow(/能力服务连不上/)
  })

  it('非 2xx 带上状态码和正文片段，不吞成空对象', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ error: 'boom' }, 503) as never)
    await expect(service().capabilityAssets()).rejects.toThrow(/503/)
  })
})
