import { Injectable } from '@nestjs/common'
import { AppException, ResponseCode } from '@yikart/common'
import { config, StudioAssistantWire } from '../../config'

/** 单次推理请求的超时：本地 Ollama 冷启动 + 9B 模型生成，120s 是实测可用上限。 */
const REQUEST_TIMEOUT_MS = 120_000

/** OpenAI 兼容的 tool 调用（回灌给模型时原样带回）。 */
export interface AssistantToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
}

export interface AssistantChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_calls?: AssistantToolCall[]
  tool_call_id?: string
}

export interface AssistantToolDefinition {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown>
  }
}

export interface AssistantChatResponse {
  content: string
  toolCalls: AssistantToolCall[]
}

type AnthropicBlock =
  | { type: 'text', text: string }
  | { type: 'thinking', thinking: string }
  | { type: 'tool_use', id: string, name: string, input: unknown }
  | { type: 'tool_result', tool_use_id: string, content: string }

interface AnthropicMessage {
  role: 'user' | 'assistant'
  content: AnthropicBlock[]
}

/**
 * 极简客户端，两种线协议同一份对话状态：
 *
 * - `openai`（默认）：POST {baseUrl}/chat/completions，tools/原生 tool_calls。
 * - `anthropic`：POST {baseUrl}/messages，thinking 预算由 `thinkingBudget` 控制。
 *
 * 内部统一用 OpenAI 形状的消息循环，只在边界做协议转换，所以上层工具循环两种协议通用。
 */
@Injectable()
export class StudioAssistantClient {
  async chat(
    messages: AssistantChatMessage[],
    tools: AssistantToolDefinition[],
  ): Promise<AssistantChatResponse> {
    return config.studioAssistant.wire === StudioAssistantWire.Anthropic
      ? this.chatAnthropic(messages, tools)
      : this.chatOpenAi(messages, tools)
  }

  private async chatOpenAi(
    messages: AssistantChatMessage[],
    tools: AssistantToolDefinition[],
  ): Promise<AssistantChatResponse> {
    const { baseUrl, model, apiKey } = config.studioAssistant
    const payload = await this.post(
      `${baseUrl.replace(/\/+$/, '')}/chat/completions`,
      { 'authorization': apiKey ? `Bearer ${apiKey}` : '' },
      {
        model,
        messages,
        tools,
        tool_choice: 'auto',
        stream: false,
        temperature: 0.3,
      },
    )

    const message = (payload as {
      choices?: Array<{
        message?: { content?: unknown, reasoning?: unknown, tool_calls?: AssistantToolCall[] }
      }>
    })?.choices?.[0]?.message
    if (!message) {
      throw new AppException(ResponseCode.ProductionAssistantModelFailed, '推理服务没有返回任何候选结果')
    }
    // qwen3.5 在 Ollama 上偶尔把最终答复写进 reasoning 而 content 为空，此时退回 reasoning，避免面板显示空回复
    const content = typeof message.content === 'string' && message.content.trim()
      ? message.content
      : typeof message.reasoning === 'string'
        ? message.reasoning
        : ''
    return { content, toolCalls: message.tool_calls ?? [] }
  }

  private async chatAnthropic(
    messages: AssistantChatMessage[],
    tools: AssistantToolDefinition[],
  ): Promise<AssistantChatResponse> {
    const { baseUrl, model, apiKey, thinkingBudget } = config.studioAssistant
    const payload = await this.post(
      `${baseUrl.replace(/\/+$/, '')}/messages`,
      {
        'x-api-key': apiKey,
        // 网关两种鉴权都认，带上 Bearer 兼容只校验 Authorization 的部署。
        'authorization': apiKey ? `Bearer ${apiKey}` : '',
        'anthropic-version': '2023-06-01',
      },
      {
        model,
        // thinking 预算必须小于 max_tokens，否则网关直接 400。
        max_tokens: thinkingBudget + 8_192,
        messages: toAnthropicMessages(messages),
        tools: tools.map(tool => ({
          name: tool.function.name,
          description: tool.function.description,
          input_schema: tool.function.parameters,
        })),
        ...(thinkingBudget > 0
          ? { thinking: { type: 'enabled', budget_tokens: thinkingBudget } }
          : {}),
      },
    )

    const blocks = (payload as { content?: AnthropicBlock[] })?.content
    if (!Array.isArray(blocks)) {
      throw new AppException(ResponseCode.ProductionAssistantModelFailed, '推理服务没有返回任何内容块')
    }
    const text = blocks
      .flatMap(block => (block.type === 'text' ? [block.text] : []))
      .join('')
      .trim()
    const thinking = blocks
      .flatMap(block => (block.type === 'thinking' ? [block.thinking] : []))
      .join('')
      .trim()
    const toolCalls = blocks.flatMap<AssistantToolCall>((block) => {
      if (block.type !== 'tool_use') {
        return []
      }
      return [{
        id: block.id,
        type: 'function' as const,
        function: { name: block.name, arguments: JSON.stringify(block.input ?? {}) },
      }]
    })
    // 只有 thinking、没有 text 时不要把思维链当成答复给用户，除非连工具调用也没有。
    return { content: text || (toolCalls.length ? '' : thinking), toolCalls }
  }

  private async post(
    url: string,
    extraHeaders: Record<string, string>,
    body: Record<string, unknown>,
  ): Promise<unknown> {
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    for (const [key, value] of Object.entries(extraHeaders)) {
      if (value) {
        headers[key] = value
      }
    }

    let response: Response
    try {
      response = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      })
    }
    catch (error) {
      throw new AppException(
        ResponseCode.ProductionAssistantModelFailed,
        `推理服务连不上（${url}）：${error instanceof Error ? error.message : String(error)}`,
      )
    }

    if (!response.ok) {
      const detail = (await response.text().catch(() => '')).slice(0, 200)
      throw new AppException(
        ResponseCode.ProductionAssistantModelFailed,
        `推理服务返回 ${response.status}：${detail || '无错误详情'}`,
      )
    }

    return response.json().catch(() => null)
  }
}

/**
 * 把 OpenAI 形状的对话折成 Anthropic 的 content blocks。
 *
 * 三个必须对齐的点：system 抽到顶层字段、tool_calls 转 tool_use、连续的 tool 结果
 * 合并进同一条 user 消息（Anthropic 要求 tool_use 后紧跟 tool_result）。
 */
function toAnthropicMessages(messages: AssistantChatMessage[]): AnthropicMessage[] {
  const result: AnthropicMessage[] = []
  for (const message of messages) {
    if (message.role === 'system') {
      continue
    }
    if (message.role === 'tool') {
      const block: AnthropicBlock = {
        type: 'tool_result',
        tool_use_id: message.tool_call_id ?? '',
        content: message.content,
      }
      const last = result[result.length - 1]
      if (last?.role === 'user' && last.content.some(item => item.type === 'tool_result')) {
        last.content.push(block)
      }
      else {
        result.push({ role: 'user', content: [block] })
      }
      continue
    }
    if (message.role === 'assistant') {
      const content: AnthropicBlock[] = message.content
        ? [{ type: 'text', text: message.content }]
        : []
      for (const call of message.tool_calls ?? []) {
        content.push({
          type: 'tool_use',
          id: call.id,
          name: call.function.name,
          input: parseArguments(call.function.arguments),
        })
      }
      result.push({ role: 'assistant', content: content.length ? content : [{ type: 'text', text: '（继续）' }] })
      continue
    }
    result.push({ role: 'user', content: [{ type: 'text', text: message.content }] })
  }
  return result
}

function parseArguments(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw || '{}')
    return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {}
  }
  catch {
    return {}
  }
}
