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
  }
  let production: {
    listProjects: ReturnType<typeof vi.fn>
    createProject: ReturnType<typeof vi.fn>
    createRun: ReturnType<typeof vi.fn>
    reviseRun: ReturnType<typeof vi.fn>
    runDetail: ReturnType<typeof vi.fn>
    listRuns: ReturnType<typeof vi.fn>
    listEvents: ReturnType<typeof vi.fn>
    planPublication: ReturnType<typeof vi.fn>
    submitPublication: ReturnType<typeof vi.fn>
    workerHealth: ReturnType<typeof vi.fn>
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
    }
    production = {
      listProjects: vi.fn(async () => [{ id: 'project-1', name: '默认项目', isDefault: true }]),
      createProject: vi.fn(async (_userId: string, data: { name: string }) => ({ id: 'project-2', name: data.name })),
      createRun: vi.fn(async () => ({ id: RUN_ID, title: '30 秒竖屏', status: 'queued', revision: 1 })),
      reviseRun: vi.fn(async () => ({ id: RUN_ID, revision: 2 })),
      runDetail: vi.fn(),
      listRuns: vi.fn(async () => ({ list: [], total: 0, pageNo: 1, pageSize: 5 })),
      listEvents: vi.fn(async () => []),
      planPublication: vi.fn(async () => ({ attempt: { id: 'attempt-1' }, deduped: false })),
      submitPublication: vi.fn(),
      workerHealth: vi.fn(async () => []),
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

  it('推理服务不可用时降级成可读中文，仍然 200 返回并落库', async () => {
    client.chat.mockRejectedValue(new Error('connect ECONNREFUSED 127.0.0.1:11434'))

    const result = await service.sendMessage('user-1', SESSION_ID, '在吗')

    expect(result.reply.content).toContain('Ollama')
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
})
