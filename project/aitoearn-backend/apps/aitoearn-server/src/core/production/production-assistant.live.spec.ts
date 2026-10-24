/**
 * 真实推理联调用例：默认跳过，只有显式打开才跑（需要本机 Ollama 已拉取 qwen3.5:9b）。
 *
 *   STUDIO_ASSISTANT_LIVE=1 pnpm nx test aitoearn-server -- production-assistant.live
 *
 * 会话存储与业务服务用替身（本机没有 Mongo 端口映射），但推理、工具定义、
 * 工具循环、消息落盘逻辑都是真实代码，用来验证「模型确实会选工具并给出中文结论」。
 */
import { describe, expect, it, vi } from 'vitest'

import { ProductionAssistantService } from './production-assistant.service'
import { StudioAssistantClient } from './studio-assistant.client'

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

describe.skipIf(!process.env['STUDIO_ASSISTANT_LIVE'])('assistant live round trip', () => {
  it('模型自己选 list_runs，并用工具结果作答', async () => {
    const session = { id: SESSION_ID, userId: 'user-1', title: 'Studio 指挥台会话', messages: [], research: [] }
    const sessionRepo = {
      create: vi.fn(),
      getByIdForUser: vi.fn(async () => session),
      appendMessage: vi.fn(async (_id: string, _userId: string, message: unknown) => {
        session.messages.push(message as never)
        return session
      }),
      appendResearch: vi.fn(async () => session),
      updateById: vi.fn(async () => session),
    }
    const production = {
      listProjects: vi.fn(async () => [{ id: 'project-1', name: '默认项目', isDefault: true }]),
      createProject: vi.fn(),
      createRun: vi.fn(),
      reviseRun: vi.fn(),
      runDetail: vi.fn(),
      listRuns: vi.fn(async () => ({
        list: [
          { id: '507f1f77bcf86cd799439012', title: '30 秒竖屏测试', status: 'succeeded' },
          { id: '507f1f77bcf86cd799439013', title: '横屏产品片', status: 'running' },
        ],
        total: 2,
        pageNo: 1,
        pageSize: 5,
      })),
      listEvents: vi.fn(async () => []),
      planPublication: vi.fn(),
      workerHealth: vi.fn(async () => []),
    }

    const service = new ProductionAssistantService(
      sessionRepo as never,
      production as never,
      new StudioAssistantClient(),
      { fetch: vi.fn() } as never,
    )

    const result = await service.sendMessage('user-1', SESSION_ID, '我最近都有哪些任务？挑一条说下状态。')
    process.stdout.write(`\n[live reply] ${result.reply.content}\n`)

    expect(result.reply.role).toBe('assistant')
    expect(result.reply.content).not.toContain('没想好')
    expect(result.reply.content).toContain('竖屏测试')
    expect(production.listRuns).toHaveBeenCalled()
    expect(session.messages).toHaveLength(2)
  }, 240_000)
})
