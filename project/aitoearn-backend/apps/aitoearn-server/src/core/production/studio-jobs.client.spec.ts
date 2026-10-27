import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { StudioJobKind, StudioJobsClient } from './studio-jobs.client'

const BASE = 'http://127.0.0.1:8899/'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

describe('studioJobsClient', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('把下单请求发到 /jobs 并复用队列返回的字段', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 'abc-1', kind: 'timeline', status: 'queued', progress: 0, stage: 'queued' }, 202))
    const client = new StudioJobsClient(BASE)

    const job = await client.submit(StudioJobKind.TIMELINE, { specPath: '/tmp/spec.json', slug: 'demo' })

    expect(job).toMatchObject({ id: 'abc-1', kind: 'timeline', status: 'queued', progress: 0 })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8899/jobs')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: 'timeline',
      payload: { specPath: '/tmp/spec.json', slug: 'demo' },
    })
  })

  it('查任务时 404 返回 null，不抛异常', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ message: 'not found' }, 404))
    const client = new StudioJobsClient(BASE)

    await expect(client.get('missing')).resolves.toBeNull()
    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:8899/jobs/missing')
  })

  it('完成的任务带出产物路径和时长', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({
      id: 'abc-2',
      kind: 'timeline',
      status: 'succeeded',
      progress: 100,
      stage: 'timeline:done',
      result: { files: ['/tmp/studio-deliver/demo.mp4'], duration: 32.5 },
    }))
    const client = new StudioJobsClient(BASE)

    const job = await client.get('abc-2')

    expect(job?.result?.files).toEqual(['/tmp/studio-deliver/demo.mp4'])
    expect(job?.result?.duration).toBe(32.5)
  })

  it('取消只认队列回写的 status=cancelled', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 'abc-3', kind: 'motion', status: 'cancelled', progress: 40, stage: 'cancelled' }))
    const client = new StudioJobsClient(BASE)

    const job = await client.cancel('abc-3')

    expect(job?.cancelled).toBe(true)
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST')
    expect(String(fetchMock.mock.calls[0][0])).toBe('http://127.0.0.1:8899/jobs/abc-3/cancel')
  })

  it('队列不可达翻译成可读异常，而不是裸 fetch 错误', async () => {
    fetchMock.mockRejectedValueOnce(new Error('connect ECONNREFUSED 127.0.0.1:8899'))
    const client = new StudioJobsClient(BASE)

    await expect(client.submit(StudioJobKind.PAPER, { specPath: '/tmp/a.json' }))
      .rejects
      .toThrow(/渲染队列连不上/)
  })

  it('队列返回 500 时把状态码带进异常', async () => {
    fetchMock.mockResolvedValueOnce(new Response('boom', { status: 500 }))
    const client = new StudioJobsClient(BASE)

    await expect(client.get('abc-4')).rejects.toThrow(/500/)
  })
})
