import { Injectable } from '@nestjs/common'
import { AppException, ResponseCode } from '@yikart/common'
import { config } from '../../config'

/** 单次推理请求的超时：Ollama 冷启动 + 9B 模型生成，120s 是实测可用上限。 */
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

/**
 * 极简 OpenAI 兼容客户端：只走 fetch，不引 SDK。
 *
 * 默认指向容器内的宿主机 Ollama（http://host.docker.internal:11434/v1），
 * 因此不需要任何外部厂商 Key；地址/模型/Key 都来自配置（可用
 * STUDIO_ASSISTANT_BASE_URL / STUDIO_ASSISTANT_MODEL / STUDIO_ASSISTANT_API_KEY 覆盖）。
 */
@Injectable()
export class StudioAssistantClient {
  async chat(
    messages: AssistantChatMessage[],
    tools: AssistantToolDefinition[],
  ): Promise<AssistantChatResponse> {
    const { baseUrl, model, apiKey } = config.studioAssistant
    const headers: Record<string, string> = { 'content-type': 'application/json' }
    if (apiKey) {
      headers['authorization'] = `Bearer ${apiKey}`
    }

    let response: Response
    try {
      response = await fetch(`${baseUrl.replace(/\/+$/, '')}/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model,
          messages,
          tools,
          tool_choice: 'auto',
          stream: false,
          temperature: 0.3,
        }),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      })
    }
    catch (error) {
      throw new AppException(
        ResponseCode.ProductionAssistantModelFailed,
        `推理服务连不上（${baseUrl}）：${error instanceof Error ? error.message : String(error)}`,
      )
    }

    if (!response.ok) {
      const detail = (await response.text().catch(() => '')).slice(0, 200)
      throw new AppException(
        ResponseCode.ProductionAssistantModelFailed,
        `推理服务返回 ${response.status}：${detail || '无错误详情'}`,
      )
    }

    const payload = await response.json().catch(() => null) as {
      choices?: Array<{
        message?: { content?: unknown, reasoning?: unknown, tool_calls?: AssistantToolCall[] }
      }>
    } | null
    const message = payload?.choices?.[0]?.message
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
}
