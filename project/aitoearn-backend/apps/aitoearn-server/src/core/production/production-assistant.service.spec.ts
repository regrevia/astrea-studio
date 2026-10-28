import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MAX_TOOL_ROUNDS, ProductionAssistantService, TOOL_RESULT_CHARS } from './production-assistant.service'
import { ASSISTANT_TOOLS } from './production-assistant.tools'

vi.mock('../../config', () => ({
  config: {
    studioAssistant: {
      baseUrl: 'http://127.0.0.1:11434/v1',
      model: 'qwen3.5:9b',
      apiKey: '',
      inventoryUrl: 'http://127.0.0.1:8899',
    },
  },
}))

vi.mock('./production.service', () => ({ ProductionService: class {} }))

vi.mock('@yikart/mongodb', () => ({
  ProductionAssistantSessionRepository: class {},
  ProductionAssistantRole: { USER: 'user', ASSISTANT: 'assistant' },
  ProductionAssistantActionType: {
    RESEARCH: 'research',
    MATERIAL: 'material',
    RUN: 'run',
    RENDER: 'render',
    DRAFT: 'draft',
    PUBLISH: 'publish',
    INFO: 'info',
  },
  ProductionAssistantActionStatus: { RUNNING: 'running', OK: 'ok', FAILED: 'failed' },
  ProductionRunStatus: {
    DRAFT: 'draft',
    QUEUED: 'queued',
    RUNNING: 'running',
    SUCCEEDED: 'succeeded',
    FAILED: 'failed',
    WAITING_USER: 'waiting_user',
    CANCELLED: 'cancelled',
  },
  PublicationDispatcher: { AITOEARN: 'aitoearn', SAU: 'sau', MANUAL: 'manual' },
}))

const SESSION_ID = '507f1f77bcf86cd799439011'
const RUN_ID = '507f1f77bcf86cd799439012'

function toolCall(name: string, args: Record<string, unknown>) {
  return { id: `call-${name}`, type: 'function' as const, function: { name, arguments: JSON.stringify(args) } }
}

describe('productionAssistantService', () => {
  const session = {
    id: SESSION_ID,
    userId: 'user-1',
    title: 'Studio 指挥台会话',
    messages: [] as Array<{ id: string, role: string, content: string, createdAt: Date, actions: unknown[] }>,
    research: [] as unknown[],
    createdAt: new Date('2026-01-01T00:00:00.000Z'),
  }

  let service: ProductionAssistantService
  let sessionRepo: {
    create: ReturnType<typeof vi.fn>
    getByIdForUser: ReturnType<typeof vi.fn>
    appendMessage: ReturnType<typeof vi.fn>
    appendResearch: ReturnType<typeof vi.fn>
    updateById: ReturnType<typeof vi.fn>
    listByUser: ReturnType<typeof vi.fn>
  }
  let production: {
    listProjects: ReturnType<typeof vi.fn>
    createProject: ReturnType<typeof vi.fn>
    createRun: ReturnType<typeof vi.fn>
    createRunBatch: ReturnType<typeof vi.fn>
    reviseRun: ReturnType<typeof vi.fn>
    runDetail: ReturnType<typeof vi.fn>
    listRuns: ReturnType<typeof vi.fn>
    listEvents: ReturnType<typeof vi.fn>
    getRun: ReturnType<typeof vi.fn>
    attachMaterialForWorker: ReturnType<typeof vi.fn>
    planPublication: ReturnType<typeof vi.fn>
    submitPublication: ReturnType<typeof vi.fn>
    workerHealth: ReturnType<typeof vi.fn>
    startJianyingExport: ReturnType<typeof vi.fn>
  }
  let client: { chat: ReturnType<typeof vi.fn> }
  let researchFetcher: { fetch: ReturnType<typeof vi.fn> }

  beforeEach(() => {
    session.messages = []
    sessionRepo = {
      create: vi.fn(),
      getByIdForUser: vi.fn(async () => session),
      appendMessage: vi.fn(async (_id: string, _userId: string, message: never) => ({
        ...session,
        messages: [...session.messages, message],
      })),
      appendResearch: vi.fn(async () => session),
      updateById: vi.fn(async () => session),
      listByUser: vi.fn(async () => []),
    }
    production = {
      listProjects: vi.fn(async () => [{ id: 'project-1', name: '默认项目', isDefault: true }]),
      createProject: vi.fn(async (_userId: string, data: { name: string }) => ({ id: 'project-2', name: data.name })),
      createRun: vi.fn(async () => ({ id: RUN_ID, title: '30 秒竖屏', status: 'queued', revision: 1 })),
      createRunBatch: vi.fn(async () => []),
      reviseRun: vi.fn(async () => ({ id: RUN_ID, revision: 2 })),
      runDetail: vi.fn(),
      listRuns: vi.fn(async () => ({ list: [], total: 0, pageNo: 1, pageSize: 5 })),
      listEvents: vi.fn(async () => []),
      getRun: vi.fn(),
      attachMaterialForWorker: vi.fn(),
      planPublication: vi.fn(async () => ({ attempt: { id: 'attempt-1' }, deduped: false })),
      submitPublication: vi.fn(),
      workerHealth: vi.fn(async () => []),
      startJianyingExport: vi.fn(async () => ({ jobId: 'job-export-1', attempt: 2, draftPath: '/draft/studio_1' })),
    }
    client = { chat: vi.fn() }
    researchFetcher = { fetch: vi.fn() }
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, json: async () => null })))
    service = new ProductionAssistantService(
      sessionRepo as never,
      production as never,
      client as never,
      researchFetcher as never,
    )
  })

  it('没有工具调用时直接采用模型 content，并把用户/助手两条消息都落库', async () => {
    client.chat.mockResolvedValue({ content: '先给我素材路径，我再建任务。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '帮我做条视频')

    expect(client.chat).toHaveBeenCalledTimes(1)
    const [messages, tools] = client.chat.mock.calls[0]
    expect(messages[0].role).toBe('system')
    expect(messages[0].content).toContain('120 字')
    expect(tools).toHaveLength(ASSISTANT_TOOLS.length)
    expect(result.reply).toMatchObject({ role: 'assistant', content: '先给我素材路径，我再建任务。' })
    expect(result.reply.createdAt).toMatch(/^\d{4}-\d{2}-\d{2}T/)
    expect(sessionRepo.appendMessage).toHaveBeenCalledTimes(2)
    expect(sessionRepo.appendMessage.mock.calls[0][2]).toMatchObject({ role: 'user', content: '帮我做条视频' })
    expect(result.reply.actions).toBeUndefined()
  })

  it('工具参数缺字段时不执行业务调用，只把校验错误回灌给模型', async () => {
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('create_run', { title: '缺素材' })] })
      .mockResolvedValueOnce({ content: '还缺文案和素材，请先给我。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '直接建任务')

    expect(production.createRun).not.toHaveBeenCalled()
    const toolMessage = client.chat.mock.calls[1][0].find((item: { role: string }) => item.role === 'tool')
    expect(toolMessage.content).toContain('参数不合法')
    expect(result.reply.content).toBe('还缺文案和素材，请先给我。')
  })

  it('create_run 交给 buildTimelineSpec 的友好规格，并返回 run 类型动作', async () => {
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('create_run', {
          title: '30 秒竖屏',
          script: ['第一句', '第二句'],
          materials: ['/tmp/a.mp4'],
          canvas: 'portrait',
        })],
      })
      .mockResolvedValueOnce({ content: '任务已入队，等我汇报进度。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '用这两个素材做个竖屏')

    expect(production.createRun).toHaveBeenCalledTimes(1)
    const [, input] = production.createRun.mock.calls[0]
    expect(input.projectId).toBe('project-1')
    expect(input.spec).toMatchObject({
      engine: 'timeline',
      script: ['第一句', '第二句'],
      materials: ['/tmp/a.mp4'],
      canvas: 'portrait',
    })
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'run', status: 'running' })
    expect(result.reply.actions?.[0].label).toContain('已创建任务')
  })

  it('create_run_batch 一次排 N 条：同一套规格交给 createRunBatch，标题由服务补 1/N 后缀', async () => {
    production.createRunBatch.mockResolvedValue([
      { id: 'run-1', title: '同题多版 · 1/2' },
      { id: 'run-2', title: '同题多版 · 2/2' },
    ])
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('create_run_batch', {
          title: '同题多版',
          script: ['第一句'],
          materials: ['/tmp/a.mp4'],
          canvas: 'portrait',
          count: 2,
        })],
      })
      .mockResolvedValueOnce({ content: '已排两条，出片后一起看。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '这两版各出一条')

    expect(production.createRunBatch).toHaveBeenCalledTimes(1)
    const [, input, count] = production.createRunBatch.mock.calls[0]
    expect(count).toBe(2)
    expect(input.spec).toMatchObject({ engine: 'timeline', materials: ['/tmp/a.mp4'] })
    expect(result.reply.content).toContain('已排两条')
  })

  it('不给素材时走竖屏脚本通道：materials 退场，三段式要点与口播稿一起落到 spec', async () => {
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('create_run', {
          title: 'Envoy 凭什么读完一篇长文',
          script: ['给 AI 一个链接，它只读了三行', '整篇读完再开口', '装上 Envoy，链接就是材料'],
          points: ['整篇读完再开口::216 秒，109 段::整篇读完再开口。两分多钟，一百零九段字幕，全在手上。'],
          hook: '给 AI 一个链接，它只读到标题',
          cta: '装上 Envoy，链接就是材料',
          voice: 'sami:BV405_streaming',
        })],
      })
      .mockResolvedValueOnce({ content: '已开渲，等我汇报。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '出一条讲 Envoy 的竖屏，文案你写')

    expect(production.createRun).toHaveBeenCalledTimes(1)
    const [, input] = production.createRun.mock.calls[0]
    expect(input.spec).toMatchObject({
      engine: 'timeline',
      mode: 'vertical_short',
      topic: 'Envoy 凭什么读完一篇长文',
      hook: '给 AI 一个链接，它只读到标题',
      points: ['整篇读完再开口::216 秒，109 段::整篇读完再开口。两分多钟，一百零九段字幕，全在手上。'],
      cta: '装上 Envoy，链接就是材料',
      voice: 'sami:BV405_streaming',
      character: true,
    })
    const spec = input.spec as Record<string, unknown>
    expect(spec).not.toHaveProperty('materials')
    expect(spec.narration).toMatchObject({
      intro: '给 AI 一个链接，它只读到标题',
      outro: '装上 Envoy，链接就是材料',
    })
  })

  it('工具循环最多 6 轮，超限即停下并说明原因', async () => {
    client.chat.mockResolvedValue({ content: '', toolCalls: [toolCall('list_runs', {})] })

    const result = await service.sendMessage('user-1', SESSION_ID, '看下最近的任务')

    expect(client.chat).toHaveBeenCalledTimes(MAX_TOOL_ROUNDS)
    expect(result.reply.content).toContain(`工具连调 ${MAX_TOOL_ROUNDS} 轮`)
  })

  it('research_web 保存全文并把回灌内容截断到 2000 字', async () => {
    researchFetcher.fetch.mockResolvedValue({
      url: 'https://example.com/hot',
      title: '本周热点',
      chars: 5231,
      text: 'x'.repeat(5231),
      excerpt: 'y'.repeat(2000),
    })
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('research_web', { url: 'https://example.com/hot' })],
      })
      .mockResolvedValueOnce({ content: '资料够用了，先确认选题。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '查一下本周热点')

    expect(researchFetcher.fetch).toHaveBeenCalledWith('https://example.com/hot')
    expect(sessionRepo.appendResearch).toHaveBeenCalledTimes(1)
    expect(sessionRepo.appendResearch.mock.calls[0][2]).toMatchObject({ chars: 5231, title: '本周热点' })
    const toolMessage = client.chat.mock.calls[1][0].find((item: { role: string }) => item.role === 'tool')
    expect(toolMessage.content.length).toBeLessThanOrEqual(TOOL_RESULT_CHARS)
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'research', status: 'ok' })
    expect(result.reply.actions?.[0].label).toBe('已收集：本周热点')
  })

  it('plan_publication 只登记意向，绝不触发真实提交', async () => {
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('plan_publication', { runId: RUN_ID, platforms: ['douyin'] })],
      })
      .mockResolvedValueOnce({ content: '已记录发布意向，请在面板人工确认。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '发到抖音')

    expect(production.planPublication).toHaveBeenCalledTimes(1)
    expect(production.planPublication).toHaveBeenCalledWith(
      'user-1',
      expect.objectContaining({ runId: RUN_ID, platform: 'douyin', dispatcher: 'manual' }),
    )
    expect(production.submitPublication).not.toHaveBeenCalled()
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'publish', status: 'ok' })
    expect(result.reply.actions?.[0].detail).toContain('人工确认')
  })

  it('get_run 把成片产物整理成 render 动作，detail 带画幅与体积', async () => {
    production.runDetail.mockResolvedValue({
      run: {
        id: RUN_ID,
        title: '30 秒竖屏',
        status: 'succeeded',
        currentStage: 'done',
        lastError: '',
        output: {
          videoUrl: 'http://127.0.0.1:9001/out.mp4',
          videoPath: 'out.mp4',
          platforms: { douyin: { url: 'http://127.0.0.1:9001/douyin.mp4', width: 1080, height: 1920, bytes: 1782579 } },
        },
      },
      jobs: [{ progress: 100, stage: 'done', lastError: '' }],
      events: [{ seq: 3, kind: 'render_done', message: '渲染完成' }],
    })
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('get_run', { runId: RUN_ID })] })
      .mockResolvedValueOnce({ content: '成片好了，去面板看一眼。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '好了吗')

    const renderAction = result.reply.actions?.find(item => item.type === 'render')
    expect(renderAction).toMatchObject({ status: 'ok', link: 'http://127.0.0.1:9001/out.mp4' })
    expect(renderAction?.detail).toBe('douyin 1080x1920 · 1.7 MB')
  })

  it('list_platforms 把素材包和可引用的 pack 写法一起交给模型', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        platforms: [{ platform: 'douyin', label: '抖音', ready: true, accounts: [{ account: 'main' }] }],
        voices: { jianying: [{ id: '1' }], edge: [{ id: '2' }, { id: '3' }] },
        materials: {
          packs: [{ slug: 'envoy-demo', shots: 2, refs: ['pack:envoy-demo/hero', 'pack:envoy-demo/dash'] }],
        },
      }),
    })))
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('list_platforms', {})] })
      .mockResolvedValueOnce({ content: '抖音能发，素材包也有。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '能发哪里，有什么素材')

    const toolMessage = client.chat.mock.calls[1][0].find((item: { role: string }) => item.role === 'tool')
    expect(toolMessage.content).toContain('素材包：envoy-demo（2 张')
    expect(toolMessage.content).toContain('pack:envoy-demo/hero')
    expect(toolMessage.content).toContain('抖音')
  })

  it('get_run 把成片本地路径、时长和 md5 一起交给模型', async () => {
    production.runDetail.mockResolvedValue({
      run: {
        id: RUN_ID,
        title: '30 秒竖屏',
        status: 'succeeded',
        currentStage: 'done',
        lastError: '',
        output: {
          videoUrl: 'http://127.0.0.1:9001/out.mp4',
          libraryPath: '/Users/apple/CreatorStudio/library/studio/2026-10/20261028-183224__studio__001__x__douyin__r1.mp4',
          duration: 30,
          md5: 'abc123',
          platforms: {},
        },
      },
      jobs: [{ progress: 100, stage: 'done', lastError: '' }],
      events: [],
    })
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('get_run', { runId: RUN_ID })] })
      .mockResolvedValueOnce({ content: '片子已经进素材库了。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '片子在哪')

    const toolMessage = client.chat.mock.calls[1][0].find((item: { role: string }) => item.role === 'tool')
    expect(toolMessage.content).toContain('本地文件：/Users/apple/CreatorStudio/library/studio/')
    expect(toolMessage.content).toContain('30.0s')
    expect(toolMessage.content).toContain('md5 abc123')
  })

  it('推理服务不可用时降级成可读中文，仍然 200 返回并落库', async () => {
    client.chat.mockRejectedValue(new Error('connect ECONNREFUSED 127.0.0.1:11434'))

    const result = await service.sendMessage('user-1', SESSION_ID, '在吗')

    expect(result.reply.content).toContain('请求地址')
    expect(result.reply.content).toContain('ECONNREFUSED')
    expect(sessionRepo.appendMessage).toHaveBeenCalledTimes(2)
  })

  it('会话不存在时抛业务异常而不是返回空会话', async () => {
    sessionRepo.getByIdForUser.mockResolvedValue(null)

    await expect(service.getSession('user-1', SESSION_ID)).rejects.toThrow('会话不存在')
    await expect(service.getSession('user-1', 'not-an-id')).rejects.toThrow('会话不存在')
  })

  it('createSession 不传标题时用默认标题并返回 createdAt', async () => {
    sessionRepo.create.mockResolvedValue({
      id: SESSION_ID,
      title: 'Studio 指挥台会话',
      createdAt: new Date('2026-01-02T03:04:05.000Z'),
    })

    const created = await service.createSession('user-1')

    expect(sessionRepo.create).toHaveBeenCalledWith(expect.objectContaining({ userId: 'user-1', messages: [], research: [] }))
    expect(created).toEqual({
      id: SESSION_ID,
      title: 'Studio 指挥台会话',
      createdAt: '2026-01-02T03:04:05.000Z',
    })
  })


  it('listSessions 只列自己的会话并带消息条数', async () => {
    sessionRepo.listByUser.mockResolvedValue([
      {
        _id: SESSION_ID,
        title: '改一版文案',
        createdAt: new Date('2026-01-02T03:04:05.000Z'),
        updatedAt: new Date('2026-01-03T00:00:00.000Z'),
        messages: [{ id: 'm1' }, { id: 'm2' }],
      },
    ])

    const list = await service.listSessions('user-1', 10)

    expect(sessionRepo.listByUser).toHaveBeenCalledWith('user-1', 10)
    expect(list).toEqual([
      {
        id: SESSION_ID,
        title: '改一版文案',
        createdAt: '2026-01-02T03:04:05.000Z',
        updatedAt: '2026-01-03T00:00:00.000Z',
        messageCount: 2,
      },
    ])
  })

  it('start_jianying_export 交给 production 排导出任务，并区分人工导出', async () => {
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('start_jianying_export', { runId: RUN_ID })],
      })
      .mockResolvedValueOnce({ content: '已排队等你在剪映点导出。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '导出成片')

    expect(production.startJianyingExport).toHaveBeenCalledWith('user-1', RUN_ID, { allowGui: false })
  })

  it('submit_render 把出片任务下到本机渲染队列，并把任务号回给模型和面板', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      id: 'job-9',
      kind: 'timeline',
      status: 'queued',
      progress: 0,
      stage: 'queued',
    }), { status: 202 }))
    vi.stubGlobal('fetch', fetchMock)
    client.chat
      .mockResolvedValueOnce({
        content: '',
        toolCalls: [toolCall('submit_render', { kind: 'timeline', payload: { specPath: '/tmp/spec.json' } })],
      })
      .mockResolvedValueOnce({ content: '已下单，任务 job-9。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '按这个规格出片')

    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('http://127.0.0.1:8899/jobs')
    expect(JSON.parse(String(init?.body))).toMatchObject({
      kind: 'timeline',
      payload: { specPath: '/tmp/spec.json' },
    })
    expect(result.reply.content).toBe('已下单，任务 job-9。')
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'render', status: 'running', detail: 'job-9' })
  })

  it('get_render_job 把成片路径如实交给模型，任务不存在时说明原因', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: 'job-9',
        kind: 'timeline',
        status: 'succeeded',
        progress: 100,
        stage: 'timeline:done',
        result: { files: ['/tmp/studio-deliver/demo.mp4'], duration: 32.7, width: 1080, height: 1920 },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ message: 'not found' }), { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('get_render_job', { jobId: 'job-9' })] })
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('get_render_job', { jobId: 'missing' })] })
      .mockResolvedValueOnce({ content: '成功的是 job-9，missing 不存在。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '出好了吗')

    const secondRound = client.chat.mock.calls[1][0] as Array<{ role: string, content: string }>
    const toolMessages = secondRound.filter(message => message.role === 'tool').map(message => message.content)
    expect(toolMessages[0]).toContain('/tmp/studio-deliver/demo.mp4')
    expect(toolMessages[1]).toContain('队列里没有任务 missing')
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'render', status: 'ok', label: '已出片' })
    expect(toolMessages[0]).toContain('画面 1080×1920')
  })

  it('create_jianying_draft 用任务成片落工程，没成片时直接说清原因', async () => {
    production.getRun = vi.fn(async (_userId: string, runId: string) => ({
      id: runId,
      title: '30 秒竖屏',
      output: runId === RUN_ID ? { videoPath: 'studio/runs/r1/master.mp4' } : {},
    }))
    production.attachMaterialForWorker = vi.fn(async () => ({ materialId: 'material-1', deduped: false }))
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('create_jianying_draft', { runId: RUN_ID })] })
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('create_jianying_draft', { runId: '507f1f77bcf86cd799439099' })] })
      .mockResolvedValueOnce({ content: '工程已落地。', toolCalls: [] })

    const result = await service.sendMessage('user-1', SESSION_ID, '落成剪映工程')

    expect(production.attachMaterialForWorker).toHaveBeenCalledWith('user-1', RUN_ID, {
      videoKey: 'studio/runs/r1/master.mp4',
      title: '30 秒竖屏',
    })
    const secondRound = client.chat.mock.calls[1][0] as Array<{ role: string, content: string }>
    const toolMessages = secondRound.filter(message => message.role === 'tool').map(message => message.content)
    expect(toolMessages[0]).toContain('material-1')
    expect(toolMessages[1]).toContain('还没有成片')
    expect(result.reply.actions?.[0]).toMatchObject({ type: 'render', status: 'ok', label: '剪映工程已落地' })
  })

  it('list_studio_assets 与 list_studio_deliverables 读宿主机素材与成片清单', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const body = String(url).endsWith('/assets')
        ? {
            root: '/Users/me/materials',
            specs: [{ path: 'a.yaml', title: '竖屏 30 秒', engine: 'timeline', kind: 'spec', ok: true }],
            shots: { count: 2, recent: [{ path: '/Users/me/materials/packs/envoy/hero.png', bytes: 1024, mtime: 0 }] },
            packs: ['envoy-demo'],
            films: { count: 3, recent: [] },
          }
        : {
            files: [{ name: '片子A.mp4', path: '/Users/me/deliver/片子A.mp4', bytes: 2097152, mtime: 2 }],
          }
      return { ok: true, status: 200, json: async () => body }
    }))
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('list_studio_assets', {})] })
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('list_studio_deliverables', { limit: 5 })] })
      .mockResolvedValueOnce({ content: '素材和成片都看到了。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '有什么素材和成片')

    const firstRound = client.chat.mock.calls[1][0] as Array<{ role: string, content: string }>
    const assetText = firstRound.filter(message => message.role === 'tool').map(message => message.content)[0]
    expect(assetText).toContain('/Users/me/materials')
    expect(assetText).toContain('envoy-demo')
    expect(assetText).toContain('成片：3 部')
    const secondRound = client.chat.mock.calls[2][0] as Array<{ role: string, content: string }>
    const deliverText = secondRound.filter(message => message.role === 'tool').map(message => message.content).at(-1)
    expect(deliverText).toContain('片子A.mp4')
    expect(deliverText).toContain('2.0 MB')
  })

  it('list_runs 带 projectId 时把项目范围传给 service', async () => {
    production.listRuns = vi.fn(async () => ({
      list: [{ id: RUN_ID, title: '项目里的片', status: 'succeeded' }],
      total: 1,
      pageNo: 1,
      pageSize: 3,
    }))
    client.chat
      .mockResolvedValueOnce({ content: '', toolCalls: [toolCall('list_runs', { limit: 3, projectId: '6adc2161656a13996166bbfc' })] })
      .mockResolvedValueOnce({ content: '这个项目最近一条已经出完了。', toolCalls: [] })

    await service.sendMessage('user-1', SESSION_ID, '这个项目最近的任务')

    expect(production.listRuns).toHaveBeenCalledWith('user-1', 1, 3, undefined, '6adc2161656a13996166bbfc')
  })
})
