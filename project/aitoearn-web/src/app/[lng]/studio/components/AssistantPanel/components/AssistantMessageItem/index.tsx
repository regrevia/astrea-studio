/**
 * AssistantMessageItem - 指挥台单条消息
 *
 * 用户消息靠右、气泡轻底色；指挥台消息靠左，正文下面挂步骤卡片。
 * 步骤卡片只读后端给的 label / detail / link，前端不猜阶段含义，
 * 只有 status 决定图标与颜色：running 转圈、ok 打勾、failed 报警。
 */
'use client'

import type { LucideIcon } from 'lucide-react'
import type { AssistantAction, AssistantActionStatus, AssistantActionType, AssistantMessage } from '@/api/production/production.types'
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  FileText,
  Film,
  Hammer,
  Image as ImageIcon,
  Info,
  Loader2,
  Search,
  Send,
  Sparkles,
  User,
} from 'lucide-react'
import { useTransClient } from '@/app/i18n/client'
import { cn } from '@/utils/className'

interface AssistantMessageItemProps {
  message: AssistantMessage // ui-copy-ignore
}

/** 步骤类型 → 图标。info 是兜底，也用于「正在思考」占位。 */
const ACTION_ICONS: Record<AssistantActionType, LucideIcon> = {
  research: Search, // ui-copy-ignore
  material: ImageIcon, // ui-copy-ignore
  run: Hammer, // ui-copy-ignore
  render: Film, // ui-copy-ignore
  draft: FileText, // ui-copy-ignore
  publish: Send, // ui-copy-ignore
  info: Info, // ui-copy-ignore
}

/** 状态图标：running 用中性色转圈，避免和 ok 的主色打勾抢注意力。 */
function ActionStatusIcon({ status }: { status: AssistantActionStatus }) {
  if (status === 'running')
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
  if (status === 'ok')
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-primary" />
  return <AlertCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
}

/** 一个步骤卡片。 */
function ActionRow({ action }: { action: AssistantAction }) {
  const { t } = useTransClient('studio')
  const Icon = ACTION_ICONS[action.type] ?? Info

  return (
    <div className="flex items-start gap-2 rounded-md border border-border bg-background/60 px-2.5 py-2">
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-xs font-medium">
          <span className="truncate">{action.label}</span>
          <ActionStatusIcon status={action.status} />
          <span className="shrink-0 text-muted-foreground">
            {t(`assistant.status.${action.status}`)}
          </span>
        </div>
        {action.detail && (
          <p className="mt-0.5 break-words text-xs text-muted-foreground">{action.detail}</p>
        )}
      </div>
      {action.link && (
        <a
          href={action.link}
          target="_blank"
          rel="noreferrer"
          aria-label={t('assistant.openLink')}
          className="flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
        >
          <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </div>
  )
}

/** 一条消息：头像 + 气泡 + 步骤卡片。 */
export function AssistantMessageItem({ message }: AssistantMessageItemProps) {
  const isUser = message.role === 'user'
  const actions = message.actions ?? []

  return (
    <div className={cn('flex w-full gap-2', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <Sparkles className="h-3.5 w-3.5" />
        </span>
      )}
      <div
        className={cn(
          'flex min-w-0 max-w-[85%] flex-col gap-2 rounded-lg border px-3 py-2 text-sm',
          isUser ? 'border-primary/30 bg-primary/5' : 'border-border bg-muted/40',
        )}
      >
        {message.content && (
          <p className="break-words whitespace-pre-wrap text-foreground">{message.content}</p>
        )}
        {actions.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {actions.map(action => <ActionRow key={action.id} action={action} />)}
          </div>
        )}
      </div>
      {isUser && (
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <User className="h-3.5 w-3.5" />
        </span>
      )}
    </div>
  )
}

export default AssistantMessageItem
