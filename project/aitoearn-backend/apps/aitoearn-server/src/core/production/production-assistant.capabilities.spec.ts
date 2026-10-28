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

  it('素材清单只取 shots.recent 里带路径的行，视频规格原样带回', async () => {
    const rows = [
      { path: '/Users/apple/CreatorStudio/materials/shots/promo2/seg-a.mp4', rel: 'shots/promo2/seg-a.mp4', kind: 'video', duration: 7.13, width: 1600, height: 1000, fps: 30 },
      { path: '/Users/apple/CreatorStudio/materials/shots/promo2/bg.png' },
      null,
    ]
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ shots: { count: 3, recent: rows } }) as never)

    const clips = await service().capabilityShotRows()
    expect(clips).toHaveLength(1)
    expect(clips[0]['duration']).toBe(7.13)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/assets')
  })

  it('旧能力服务没有 shots 段时素材清单是空数组，不抛错', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ packs: ['demo'] }) as never)
    await expect(service().capabilityShotRows()).resolves.toEqual([])
  })

  it('配乐清单只取 tracks 里带路径的行，时长原样带回', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      tracks: [
        { path: '/m/bgm/demo.m4a', rel: 'bgm/demo.m4a', duration: 8 },
        { path: '/m/bgm/no-rel.m4a', duration: 3 },
      ],
    }) as never)

    const tracks = await service().capabilityMusicRows()
    expect(tracks).toHaveLength(1)
    expect(tracks[0]['rel']).toBe('bgm/demo.m4a')
    expect(tracks[0]['duration']).toBe(8)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/music')
  })

  it('拍点接口把相对路径编码进查询，没测出拍点就说清原因', async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      rel: 'bgm/demo.m4a', bpm: 120, offset: 0.02, beats: [0.02, 0.52], duration: 8,
    }) as never)
    const beats = await service().capabilityMusicBeats('bgm/demo.m4a')
    expect(beats['bpm']).toBe(120)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/music/beats?path=bgm%2Fdemo.m4a')

    vi.mocked(fetch).mockResolvedValue(jsonResponse({ rel: 'bgm/quiet.m4a', bpm: 0, beats: [] }) as never)
    await expect(service().capabilityMusicBeats('bgm/quiet.m4a')).rejects.toThrow('这条配乐没测出拍点')
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

  it('视频缩略图按秒抽帧，空路径和越界都不去请求宿主机', async () => {
    await expect(service().capabilityMaterialFrame('')).rejects.toThrow('素材路径不合法')
    await expect(service().capabilityMaterialFrame('../a.mp4')).rejects.toThrow('素材路径不合法')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()

    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'image/jpeg' }),
      arrayBuffer: async () => new Uint8Array([9, 9]).buffer,
    } as never)
    const frame = await service().capabilityMaterialFrame('/shots/a.mp4', 1.5)
    expect(frame.contentType).toBe('image/jpeg')
    expect(frame.body.length).toBe(2)
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe('http://host.docker.internal:8899/materials/frame?path=shots%2Fa.mp4&t=1.5')

    vi.mocked(fetch).mockResolvedValue({ ok: false, status: 404 } as never)
    await expect(service().capabilityMaterialFrame('shots/a.mp4')).rejects.toThrow('没有这张素材')
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
