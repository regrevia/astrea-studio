/**
 * AssistantPanel 的纯工具：会话 id 的本地持久化、空态提示顺序、临时消息 id。
 * 都只在客户端调用，但仍做 window 判空，避免被服务端引用时直接抛错。
 */

import type { AssistantQuickPromptKey } from './types'

/** 会话 id 的 localStorage key：刷新页面后要能接着上次的对话。 */
export const ASSISTANT_SESSION_STORAGE_KEY = 'studio.assistant.sessionId'

/** 推理端点的 localStorage key：填一次，之后每次发送都带上。 */
export const AGENT_ENDPOINT_STORAGE_KEY = 'studio.agent.endpoint'

export interface AgentEndpoint { baseUrl: string, apiKey: string, model: string }

const EMPTY_ENDPOINT: AgentEndpoint = { baseUrl: '', apiKey: '', model: '' }

/** 读本机保存的端点；没填过或存储不可用都返回空值。 */
export function readAgentEndpoint(): AgentEndpoint {
  if (typeof window === 'undefined')
    return { ...EMPTY_ENDPOINT }
  try {
    const raw = window.localStorage.getItem(AGENT_ENDPOINT_STORAGE_KEY)
    if (!raw)
      return { ...EMPTY_ENDPOINT }
    const parsed = JSON.parse(raw) as Partial<AgentEndpoint>
    return { baseUrl: parsed.baseUrl ?? '', apiKey: parsed.apiKey ?? '', model: parsed.model ?? '' }
  }
  catch {
    return { ...EMPTY_ENDPOINT }
  }
}

/** 保存端点。写不进去（无痕模式）不影响本次对话用的还是内存里这份。 */
export function writeAgentEndpoint(value: AgentEndpoint) {
  if (typeof window === 'undefined')
    return
  try {
    window.localStorage.setItem(AGENT_ENDPOINT_STORAGE_KEY, JSON.stringify(value))
  }
  catch {
    // 存不下就算了：本次对话仍然用内存里的值。
  }
}

/** 空态快捷提示的展示顺序。 */
export const ASSISTANT_QUICK_PROMPTS: AssistantQuickPromptKey[] = ['news', 'tweak', 'export', 'publish']

/** 读上次的会话 id；没有、读不到或被浏览器禁用存储时返回空串。 */
export function readAssistantSessionId() {
  if (typeof window === 'undefined')
    return ''
  try {
    return window.localStorage.getItem(ASSISTANT_SESSION_STORAGE_KEY) ?? ''
  }
  catch {
    return ''
  }
}

/** 记住会话 id。写入失败（无痕模式、配额满）不影响本次对话。 */
export function writeAssistantSessionId(id: string) {
  if (typeof window === 'undefined')
    return
  try {
    window.localStorage.setItem(ASSISTANT_SESSION_STORAGE_KEY, id)
  }
  catch {
    // 存不下就算了：用户刷新后会新开一次会话，不是错误。
  }
}

/** 丢掉会话 id（新建会话，或服务端已经不认这个 id）。 */
export function clearAssistantSessionId() {
  if (typeof window === 'undefined')
    return
  try {
    window.localStorage.removeItem(ASSISTANT_SESSION_STORAGE_KEY)
  }
  catch {
    // 同上：清不掉也没有副作用，下次还会命中旧 id 再由后端兜底。
  }
}

/**
 * 前端乐观消息的临时 id，只用于 React key 和「用真实回复替换占位」。
 * 后端返回的消息带真实 id，替换后这些临时 id 就消失了。
 */
export function createLocalMessageId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}
