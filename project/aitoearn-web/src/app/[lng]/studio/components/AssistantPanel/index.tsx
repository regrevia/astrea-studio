/**
 * AssistantPanel - Studio 顶部的「AI 指挥台」
 *
 * 用户用一句话描述目标（查资料、做片、改字幕、发布），后端按阶段推进并把每一步
 * 回报成状态卡片。本组件只做三件事：渲染会话、乐观发送并等待回复、失败时说人话。
 *
 * 失败约定（后端可能还没上线）：
 * - 请求拿不到 JSON（接口 404 / 服务没起）→ 显示「指挥台后端未就绪」，不弹全局报错；
 * - 业务错误（code !== 0）→ 把后端 message 贴在对话上方；
 * - 会话 id 存在 localStorage，刷新后先 GET 恢复，服务端不认了就静默新建。
 */
'use client'

import type { KeyboardEvent } from 'react'
import type { AssistantPanelProps, AssistantQuickPromptKey } from './types'
import type { AssistantMessage } from '@/api/production/production.types'
import { AlertCircle, Loader2, Plus, Send, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createAssistantSession,
  getAssistantSession,
  sendAssistantMessage,
} from '@/api/production/production.api'
import { useTransClient } from '@/app/i18n/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/utils/className'
import { AssistantMessageItem } from './components/AssistantMessageItem'
import {
  ASSISTANT_QUICK_PROMPTS,
  clearAssistantSessionId,
  createLocalMessageId,
  readAssistantSessionId,
  writeAssistantSessionId,
} from './utils'

export function AssistantPanel({ className }: AssistantPanelProps) {
  const { t, ready } = useTransClient('studio')
  const [sessionId, setSessionId] = useState('')
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [input, setInput] = useState('')
  const [booting, setBooting] = useState(true)
  const [sending, setSending] = useState(false)
  const [unavailable, setUnavailable] = useState(false)
  const [errorText, setErrorText] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const bootedRef = useRef(false)

  /** 恢复上次会话；没有或服务端不认，就新建一个。 */
  const boot = useCallback(async () => {
    setBooting(true)
    setErrorText('')
    setUnavailable(false)

    const stored = readAssistantSessionId()
    if (stored) {
      const restored = await getAssistantSession(stored)
      if (!restored) {
        setUnavailable(true)
        setBooting(false)
        return
      }
      if (restored.code === 0 && restored.data) {
        setSessionId(restored.data.id)
        setMessages(restored.data.messages ?? [])
        writeAssistantSessionId(restored.data.id)
        setBooting(false)
        return
      }
      // 本地记的会话在服务端已经没了：换一个新的，别把用户卡在报错上。
      clearAssistantSessionId()
    }

    const created = await createAssistantSession({ title: t('assistant.defaultSessionTitle') })
    if (!created) {
      setUnavailable(true)
      setBooting(false)
      return
    }
    if (created.code !== 0 || !created.data) {
      setErrorText(t('assistant.error.sessionFailed', { message: created.message }))
      setBooting(false)
      return
    }
    setSessionId(created.data.id)
    writeAssistantSessionId(created.data.id)
    setBooting(false)
  }, [t])

  // 等 i18n namespace 就绪再建会话：标题要用翻译文案，避免先建出一个英文标题。
  useEffect(() => {
    if (!ready || bootedRef.current)
      return
    bootedRef.current = true
    void boot()
  }, [ready, boot])

  // 消息只让列表内部滚动，不用 scrollIntoView，避免整页跟着跳。
  useEffect(() => {
    const node = listRef.current
    if (node)
      node.scrollTop = node.scrollHeight
  }, [messages])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || !sessionId || sending)
      return

    const userMessage: AssistantMessage = {
      id: createLocalMessageId('user'),
      role: 'user',
      content: text,
      createdAt: new Date().toISOString(),
    }
    // 一条占位回复：带 running 的「正在处理」，回复到了整条替换掉。
    const placeholder: AssistantMessage = {
      id: createLocalMessageId('reply'),
      role: 'assistant',
      content: '',
      createdAt: new Date().toISOString(),
      actions: [{ id: createLocalMessageId('action'), type: 'info', status: 'running', label: t('assistant.thinking') }],
    }

    setMessages(prev => [...prev, userMessage, placeholder])
    setInput('')
    setSending(true)
    setErrorText('')

    const response = await sendAssistantMessage(sessionId, text)
    setSending(false)

    if (!response) {
      setMessages(prev => prev.filter(item => item.id !== placeholder.id))
      setUnavailable(true)
      return
    }
    if (response.code !== 0 || !response.data?.reply) {
      setMessages(prev => prev.filter(item => item.id !== placeholder.id))
      setErrorText(t('assistant.error.sendFailed', { message: response.message }))
      return
    }

    const reply = response.data.reply
    setMessages(prev => prev.map(item => (item.id === placeholder.id ? reply : item)))
  }, [input, sending, sessionId, t])

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter 发送、Shift + Enter 换行；输入法候选框里的回车不算发送。
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing)
      return
    event.preventDefault()
    void handleSend()
  }, [handleSend])

  const handleNewSession = useCallback(() => {
    if (sending)
      return
    clearAssistantSessionId()
    setMessages([])
    setSessionId('')
    void boot()
  }, [boot, sending])

  const handleQuickPrompt = useCallback((key: AssistantQuickPromptKey) => {
    // 只填进输入框，不自动发：用户通常要先换成自己的品牌名。
    setInput(t(`assistant.prompts.${key}`))
    inputRef.current?.focus()
  }, [t])

  const canSend = Boolean(sessionId) && input.trim().length > 0 && !sending && !booting

  if (!ready) {
    return (
      <Card className={className}>
        <CardContent className="py-6">
          <Skeleton className="h-24 w-full" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={cn('border-primary/20', className)}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="h-4 w-4" />
              {t('assistant.title')}
            </CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{t('assistant.subtitle')}</p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="shrink-0"
            disabled={sending || booting}
            onClick={handleNewSession}
          >
            <Plus className="h-3.5 w-3.5" />
            {t('assistant.newSession')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {unavailable && (
          <div role="status" className="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{t('assistant.error.unavailable')}</span>
          </div>
        )}
        {errorText && (
          <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{errorText}</span>
          </div>
        )}

        <div ref={listRef} className="flex max-h-80 flex-col gap-3 overflow-y-auto pr-1">
          {booting && (
            <p className="flex items-center justify-center gap-2 py-6 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {t('assistant.restoring')}
            </p>
          )}
          {!booting && messages.length === 0 && (
            <div className="flex flex-col gap-2 py-2">
              <p className="text-sm font-medium">{t('assistant.empty.title')}</p>
              <p className="text-xs text-muted-foreground">{t('assistant.empty.description')}</p>
              <div className="mt-1 grid gap-1.5 sm:grid-cols-2">
                {ASSISTANT_QUICK_PROMPTS.map(key => (
                  <Button
                    key={key}
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-auto justify-start py-2 text-left font-normal whitespace-normal"
                    onClick={() => handleQuickPrompt(key)}
                  >
                    {t(`assistant.prompts.${key}`)}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {!booting && messages.map(message => (
            <AssistantMessageItem key={message.id} message={message} />
          ))}
        </div>

        <div className="flex flex-col gap-2">
          <Textarea
            ref={inputRef}
            rows={2}
            value={input}
            placeholder={t('assistant.placeholder')}
            aria-label={t('assistant.title')}
            disabled={!sessionId || sending}
            onChange={event => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div className="flex items-center justify-between gap-2">
            <p className="text-[11px] text-muted-foreground">{t('assistant.hint')}</p>
            <Button
              type="button"
              size="sm"
              loading={sending}
              disabled={!canSend}
              onClick={() => void handleSend()}
            >
              <Send className="h-3.5 w-3.5" />
              {t('assistant.send')}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

export default AssistantPanel
