/**
 * QueueJobRow - 本机渲染队列（8899）任务行
 *
 * 生产任务（ProductionRun）和本机队列是两条路：指挥台出片、手写 spec 都直接进队列，
 * 不进 run 台账。面板只列 run 的话，队列在跑时仍写「没有正在渲染的任务」，
 * 所以进行中一栏把队列里的排队/渲染中任务一起列出来。
 */
'use client'

import type { RenderJob } from '@/api/production/production.types'
import { Loader2 } from 'lucide-react'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/utils/className'

const KIND_KEYS = ['timeline', 'motion', 'slides', 'jianying', 'paper'] as const

const STATUS_KEY: Record<string, string> = {
  queued: 'run.status.queued',
  running: 'run.status.running',
  succeeded: 'run.status.succeeded',
  failed: 'run.status.failed',
  cancelled: 'run.status.cancelled',
}

export function QueueJobRow({ job }: { job: RenderJob }) {
  const { t } = useTransClient('studio')
  const running = job.status === 'running'
  const file = job.result?.files?.[0]
  const kind = (KIND_KEYS as readonly string[]).includes(job.kind) ? `run.queue.kind.${job.kind}` : job.kind
  const done = job.status === 'succeeded' || job.status === 'failed' || job.status === 'cancelled'
  const fileName = file?.split('/').pop()
  const seconds = job.result?.duration ? `${job.result.duration.toFixed(1)}s` : undefined

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border bg-card/60 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <Loader2 className={cn('h-4 w-4', running && 'animate-spin')} />
          </span>
          <div className="min-w-0">
            <span className="text-sm font-medium text-foreground">{t(kind)}</span>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">
              {t('run.queue.source')}
              {' · '}
              {job.id}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant="secondary">{STATUS_KEY[job.status] ? t(STATUS_KEY[job.status]) : job.status}</Badge>
        </div>
      </div>
      {!done && (
        <div className="flex items-center gap-2">
          <Progress value={job.progress} className="h-1.5 flex-1" />
          <span className="w-10 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
            {job.progress}
            %
          </span>
        </div>
      )}
      {done
        ? (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {fileName && (
                <span className="min-w-0 truncate" title={file}>{fileName}</span>
              )}
              {seconds && <span className="tabular-nums">{seconds}</span>}
              {job.error && <span className="min-w-0 truncate text-destructive" title={job.error}>{job.error}</span>}
            </div>
          )
        : <span className="text-xs text-muted-foreground">{t('run.queue.waiting')}</span>}
    </div>
  )
}
