/**
 * studio/utils/status - 任务与发布状态的唯一口径
 *
 * 状态枚举以后端 ledger 为准（draft/queued/running/succeeded/failed/waiting_user/cancelled）， // ui-copy-ignore
 * 界面上只在这里决定文案 key、色调和分组，避免每处各写一份 local map 拼错。
 * 色调只用语义 token：进行中用 primary，成功用 success，等人决定用 warning，失败用 destructive。 // ui-copy-ignore
 */
import type { LucideIcon } from 'lucide-react'
import type { ProductionRun, ProductionRunStatus, PublicationStatus } from '@/api/production/production.types'
import { AlertCircle, Ban, CheckCircle2, Clock, FileText, Loader2, PauseCircle, Timer } from 'lucide-react'

/** 任务分组：进行中 / 等人决定 / 已结束。 */
export type RunGroup = 'active' | 'waiting' | 'settled'

export interface StatusMeta {
  labelKey: string // ui-copy-ignore
  tone: string // ui-copy-ignore
  icon: LucideIcon // ui-copy-ignore
}

export const RUN_STATUS_META: Record<ProductionRunStatus, StatusMeta> = {
  draft: { labelKey: 'run.status.draft', tone: 'bg-muted text-muted-foreground', icon: FileText },
  queued: { labelKey: 'run.status.queued', tone: 'bg-muted text-muted-foreground', icon: Clock },
  running: { labelKey: 'run.status.running', tone: 'bg-primary/10 text-primary', icon: Loader2 },
  succeeded: { labelKey: 'run.status.succeeded', tone: 'bg-success/10 text-success', icon: CheckCircle2 },
  failed: { labelKey: 'run.status.failed', tone: 'bg-destructive/10 text-destructive', icon: AlertCircle },
  waiting_user: { labelKey: 'run.status.waitingUser', tone: 'bg-warning/20 text-foreground', icon: PauseCircle },
  cancelled: { labelKey: 'run.status.cancelled', tone: 'bg-muted text-muted-foreground', icon: Ban },
}

const UNKNOWN_STATUS: StatusMeta = {
  labelKey: 'run.status.unknown',
  tone: 'bg-muted text-muted-foreground',
  icon: Timer,
}

/** 状态可能来自旧记录或后端新增枚举，认不出就退到中性态，不猜含义。 */
export function runStatusMeta(status?: string): StatusMeta {
  return (status && RUN_STATUS_META[status as ProductionRunStatus]) || UNKNOWN_STATUS
}

/** 还在跑（含排队和草稿）：需要轮询，也允许取消。 */
export function isRunBusy(status?: string): boolean {
  return status === 'draft' || status === 'queued' || status === 'running'
}

/** 状态已定：不再自动轮询。 */
export function isRunSettled(status?: string): boolean {
  return !!status && !isRunBusy(status)
}

/** 只有进行中的任务能取消，已定状态取消没意义。 */
export function canCancelRun(status?: string): boolean {
  return isRunBusy(status)
}

/** 失败、取消和等人决定都能重试；等人决定是流水线停下来，重试即重新排队。 */
export function canRetryRun(status?: string): boolean {
  return status === 'failed' || status === 'cancelled' || status === 'waiting_user'
}

/** 等人决定的任务要显式提示用户，不能混进完成列表。 */
export function runGroup(status?: string): RunGroup {
  if (status === 'waiting_user' || status === 'draft')
    return 'waiting'
  if (isRunBusy(status))
    return 'active'
  return 'settled'
}

export interface GroupedRuns {
  active: ProductionRun[] // ui-copy-ignore
  waiting: ProductionRun[] // ui-copy-ignore
  settled: ProductionRun[] // ui-copy-ignore
}

/** 按分组切一次，列表顺序保持后端给的（createdAt 倒序）。 */
export function groupRuns(runs: ProductionRun[]): GroupedRuns {
  const grouped: GroupedRuns = { active: [], waiting: [], settled: [] }
  for (const run of runs)
    grouped[runGroup(run.status)].push(run)
  return grouped
}

/** 发布台账状态。submission_unknown 表示回执不明，不可自动重投。 */
export const PUBLICATION_STATUS_META: Record<PublicationStatus, StatusMeta> = {
  planned: { labelKey: 'pub.status.planned', tone: 'bg-muted text-muted-foreground', icon: Clock },
  submitting: { labelKey: 'pub.status.submitting', tone: 'bg-primary/10 text-primary', icon: Loader2 },
  submitted_unverified: { labelKey: 'pub.status.submittedUnverified', tone: 'bg-warning/20 text-foreground', icon: PauseCircle },
  submission_unknown: { labelKey: 'pub.status.submissionUnknown', tone: 'bg-destructive/10 text-destructive', icon: AlertCircle },
  reconciling: { labelKey: 'pub.status.reconciling', tone: 'bg-primary/10 text-primary', icon: Loader2 },
  scheduled_remote: { labelKey: 'pub.status.scheduledRemote', tone: 'bg-muted text-muted-foreground', icon: Clock },
  reviewing: { labelKey: 'pub.status.reviewing', tone: 'bg-muted text-muted-foreground', icon: Clock },
  published: { labelKey: 'pub.status.published', tone: 'bg-success/10 text-success', icon: CheckCircle2 },
  rejected: { labelKey: 'pub.status.rejected', tone: 'bg-destructive/10 text-destructive', icon: Ban },
  waiting_user: { labelKey: 'pub.status.waitingUser', tone: 'bg-warning/20 text-foreground', icon: PauseCircle },
  not_submitted: { labelKey: 'pub.status.notSubmitted', tone: 'bg-muted text-muted-foreground', icon: Ban },
}

/** 回执不明的投递必须显眼：重投可能重复发布。 */
export function publicationStatusMeta(status?: string): StatusMeta {
  return (status && PUBLICATION_STATUS_META[status as PublicationStatus]) || UNKNOWN_STATUS
}
