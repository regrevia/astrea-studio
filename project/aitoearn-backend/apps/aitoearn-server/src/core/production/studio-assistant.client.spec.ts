import { beforeEach, describe, expect, it, vi } from 'vitest'

import { config } from '../../config'
import { StudioAssistantClient } from './studio-assistant.client'

vi.mock('../../config', () => ({
  StudioAssistantWire: { Anthropic: 'anthropic', Openai: 'openai' },
  config: {
    studioAssistant: {
      baseUrl: 'http://127.0.0.1:11434/v1',
      model: 'qwen3.5:9b',
      apiKey: '',
      inventoryUrl: 'http://127.0.0.1:8899',
    },
  },
}))

const TOOLS = [{ type: 'function' as const, function: { name: 'list_runs', description: '任务列表', parameters: {} } }]

describe('studioAssistantClient', () => {
  const client = new StudioAssistantClient()
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    config.studioAssistant.apiKey = ''
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('未配置 apiKey 时不发 Authorization，请求体带 model 与 tools', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      choices: [{ message: { content: '好的' } }],
    }), { status: 200 }))

    const result = await client.chat([{ role: 'user', content: '在吗' }], TOOLS)

    expect(result).toEqual({ content: '好的', toolCalls: [] })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:11434/v1/chat/completions')
    expect(init.headers).not.toHaveProperty('authorization')
    const body = JSON.parse(init.body)
    expect(body).toMatchObject({ model: 'qwen3.5:9b', stream: false, tool_choice: 'auto' })
    expect(body.tools).toHaveLength(1)
  })

  it('配置 apiKey 后带上 Bearer 头，baseUrl 末尾斜杠会被规整', async () => {
    config.studioAssistant.apiKey = 'secret'
    config.studioAssistant.baseUrl = 'http://127.0.0.1:11434/v1/'
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      choices: [{ message: { content: '', tool_calls: [{ id: 'call-1', type: 'function', function: { name: 'list_runs', arguments: '{}' } }] } }],
    }), { status: 200 }))

    const result = await client.chat([{ role: 'user', content: '任务' }], TOOLS)

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:11434/v1/chat/completions')
    expect(init.headers.authorization).toBe('Bearer secret')
    expect(result.content).toBe('')
    expect(result.toolCalls[0].function.name).toBe('list_runs')
  })

  it('非 2xx 与网络异常都转成可读中文异常', async () => {
    fetchMock.mockResolvedValueOnce(new Response('model not found', { status: 500 }))
    await expect(client.chat([{ role: 'user', content: 'hi' }], TOOLS)).rejects.toThrow('推理服务返回 500：model not found')

    fetchMock.mockRejectedValueOnce(new Error('connect ECONNREFUSED'))
    await expect(client.chat([{ role: 'user', content: 'hi' }], TOOLS)).rejects.toThrow('推理服务连不上')
  })

  it('content 为空但 reasoning 有内容时退回 reasoning（qwen3.5 在 Ollama 上的实际行为）', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      choices: [{ message: { content: '', reasoning: '最近有两条任务：30 秒竖屏测试已完成。' } }],
    }), { status: 200 }))

    const result = await client.chat([{ role: 'user', content: '任务' }], TOOLS)

    expect(result.content).toBe('最近有两条任务：30 秒竖屏测试已完成。')
  })

  it('响应里没有候选结果时抛异常，而不是静默空回复', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ choices: [] }), { status: 200 }))

    await expect(client.chat([{ role: 'user', content: 'hi' }], TOOLS)).rejects.toThrow('没有返回任何候选结果')
  })
})

describe('anthropic 线协议', () => {
  const client = new StudioAssistantClient()
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      content: [{ type: 'text', text: '好的' }],
    }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
  })

  it('system 提到顶层字段，不留在 messages 里', async () => {
    await client.chat(
      [{ role: 'system', content: '你是指挥台' }, { role: 'user', content: '在吗' }],
      TOOLS,
      { baseUrl: 'http://127.0.0.1:3000/v1', apiKey: 'k', model: 'm', wire: 'anthropic' },
    )

    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.system).toBe('你是指挥台')
    expect(body.messages).toEqual([{ role: 'user', content: [{ type: 'text', text: '在吗' }] }])
    expect(body.tools[0].input_schema).toEqual({})
  })
})
