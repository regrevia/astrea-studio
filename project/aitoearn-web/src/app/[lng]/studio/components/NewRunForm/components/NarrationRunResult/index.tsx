/**
 * NarrationRunResult - 提交后的任务状态与成片
 *
 * 轮询 getRun 直到状态落定：完成了给母版和各平台成片的链接，失败把渲染进程的
 * 原话和最后 20 行任务日志一起摊开，用户不用去翻后台。
 */
'use client'

import type { RunDetail } from '@/api/production/production.types'
import { AlertCircle, CheckCircle2, Clock, Download, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getRun } from '@/api/production/production.api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { PLATFORM_LABELS } from '../../../../studioConstants'

/** 渲染进程的阶段标识是内部名字，界面上只给状态文案。 */
const STAGE_LABELS: Record<string, string> = {
  queued: '排队中', // ui-copy-ignore
  running: '渲染中', // ui-copy-ignore
  done: '收尾', // ui-copy-ignore
}

const POLL_MS = 3000

const STATUS_META: Record<string, { label: string, tone: string, icon: typeof Clock }> = {
  queued: { label: '排队中', tone: 'bg-slate-100 text-slate-700', icon: Clock },
  running: { label: '渲染中', tone: 'bg-blue-100 text-blue-700', icon: Loader2 },
  succeeded: { label: '已完成', tone: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2 },
  failed: { label: '失败', tone: 'bg-red-100 text-red-700', icon: AlertCircle },
  canceled: { label: '已取消', tone: 'bg-slate-100 text-slate-500', icon: AlertCircle },
}

const TERMINAL = new Set(['succeeded', 'failed', 'canceled'])

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status, tone: 'bg-slate-100 text-slate-700', icon: Clock }
}

/** 任务日志最后 20 行：失败时这是唯一能看到渲染进程原话的地方。 */
function tailLog(detail: RunDetail | null): string {
  if (!detail)
    return ''
  const lines = [
    ...detail.events.map(event => `[${event.kind}] ${event.stage ?? ''} ${event.message ?? ''}`.trim()),
    ...detail.jobs.map(job => `[job] ${job.kind ?? ''} ${job.status ?? ''} ${job.stage ?? ''}`.trim()),
  ]
  return lines.slice(-20).join('\n')
}

export default function NarrationRunResult({ runId }: { runId: string }) {
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const tick = async () => {
      const res = await getRun(runId)
      if (!alive)
        return
      if (!res || res.code !== 0 || !res.data) {
        setError('任务状态暂时读不到，正在重试')
        timer = setTimeout(tick, POLL_MS)
        return
      }
      setError('')
      setDetail(res.data)
      // 状态落定就不再轮询，避免用户停在页面上一直打接口。
      if (!TERMINAL.has(res.data.run.status))
        timer = setTimeout(tick, POLL_MS)
    }
    tick()
    return () => {
      alive = false
      if (timer)
        clearTimeout(timer)
    }
  }, [runId])

  const run = detail?.run
  const meta = statusMeta(run?.status ?? 'queued')
  const Icon = meta.icon
  const busy = !run || run.status === 'queued' || run.status === 'running'
  // 落定后再报阶段名会把「已完成」和「收尾」并排，只在进行中给阶段
  const inFlight = run?.status === 'running' || run?.status === 'queued'
  const stageLabel = inFlight ? (STAGE_LABELS[run?.currentStage ?? ''] ?? '') : ''
  const platforms = Object.entries(run?.output?.platforms ?? {})
  const videoUrl = run?.output?.videoUrl ?? run?.videoUrl
  const videoPath = run?.output?.videoPath
  const log = tailLog(detail)

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />
          任务
          {' '}
          {runId}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className={`rounded px-2 py-0.5 text-xs ${meta.tone}`}>{meta.label}</span>
          {typeof run?.progress === 'number' && (
            <span className="text-muted-foreground">
              {Math.round(run.progress)}
              %
            </span>
          )}
          {stageLabel && <span className="text-muted-foreground">{stageLabel}</span>}
        </div>

        {error && <p className="text-sm text-amber-700">{error}</p>}

        {videoUrl && (
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild size="sm" variant="outline" className="cursor-pointer">
              <a href={videoUrl} target="_blank" rel="noreferrer">
                <Download className="mr-1 h-4 w-4" />
                下载母版
              </a>
            </Button>
            {platforms.map(([platform, video]) => (
              <Button key={platform} asChild size="sm" variant="outline" className="cursor-pointer">
                <a href={video.url} target="_blank" rel="noreferrer">
                  {`下载 ${PLATFORM_LABELS[platform] ?? platform}`}
                </a>
              </Button>
            ))}
          </div>
        )}

        {videoPath && <code className="break-all text-[11px] text-muted-foreground">{videoPath}</code>}

        {run?.status === 'failed' && (
          <div className="flex flex-col gap-1.5">
            <p className="text-sm text-destructive">{run.lastError || '渲染失败，原因没传回来'}</p>
            {log && (
              <details className="text-[11px] text-muted-foreground">
                <summary className="cursor-pointer">任务日志最后 20 行</summary>
                <pre className="mt-1 whitespace-pre-wrap break-all">{log}</pre>
              </details>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
