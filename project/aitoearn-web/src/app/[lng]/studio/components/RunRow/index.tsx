/**
 * RunRow - 任务列表行
 *
 * 一行读完：状态、标题、项目、阶段/进度、产物类型、失败原因，右侧给取消/重试/改稿。
 * 点标题把这条任务选进右侧详情，列表本身不承载成片播放。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { Clapperboard, ExternalLink, FileVideo } from 'lucide-react'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/utils/className'
import { formatKib } from '../../studioConstants'
import { deriveRunArtifacts } from '../../utils/runArtifacts'
import { isRunBusy, runStatusMeta } from '../../utils/status'
import { RunActions } from '../RunActions'

export interface RunRowProps {
  run: ProductionRun
  projectName?: string
  selected?: boolean
  onSelect: (runId: string) => void
  onChanged?: () => void
}

export function RunRow({ run, projectName, selected = false, onSelect, onChanged }: RunRowProps) {
  const { t } = useTransClient('studio')
  const meta = runStatusMeta(run.status)
  const Icon = meta.icon
  const artifacts = deriveRunArtifacts(run)
  const busy = isRunBusy(run.status)
  const platformCount = artifacts.platforms.length
  const bytes = artifacts.platforms.reduce((sum, item) => sum + (item.bytes ?? 0), 0)

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border bg-card p-3 transition-colors',
        selected ? 'border-primary/60 bg-primary/5' : 'border-border hover:border-primary/30',
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2">
          <span className={cn('mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md', meta.tone)}>
            <Icon className={cn('h-4 w-4', run.status === 'running' && 'animate-spin')} />
          </span>
          <div className="min-w-0">
            <button
              type="button"
              className="cursor-pointer truncate text-left text-sm font-medium text-foreground hover:underline"
              onClick={() => onSelect(run.id)}
            >
              {run.title || run.id}
            </button>
            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>{t(meta.labelKey)}</span>
              {projectName && <span>{projectName}</span>}
              {typeof run.revision === 'number' && run.revision > 0 && (
                <span>{t('run.row.revision', { n: run.revision })}</span>
              )}
              {run.currentStage && <span>{run.currentStage}</span>}
            </div>
          </div>
        </div>
        <RunActions run={run} compact onChanged={onChanged} />
      </div>

      {busy && typeof run.progress === 'number' && (
        <Progress value={Math.max(0, Math.min(100, run.progress))} className="h-1.5" />
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        {artifacts.masterUrl && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <FileVideo className="h-3 w-3" />
            {t('run.artifact.master')}
          </Badge>
        )}
        {platformCount > 0 && (
          <Badge variant="secondary" className="font-normal">
            {t('run.artifact.platforms', { n: platformCount })}
          </Badge>
        )}
        {artifacts.jianying && (
          <Badge variant="secondary" className="gap-1 font-normal">
            <Clapperboard className="h-3 w-3" />
            {t('run.artifact.jianying')}
          </Badge>
        )}
        {artifacts.publishDraftId && (
          <Badge variant="secondary" className="font-normal">
            {t('run.artifact.publishDraft')}
          </Badge>
        )}
        {bytes > 0 && <span className="text-xs text-muted-foreground">{formatKib(bytes)}</span>}
        {artifacts.masterUrl && busy === false && run.status === 'succeeded' && (
          <a
            href={artifacts.masterUrl}
            target="_blank"
            rel="noreferrer"
            className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            {t('run.action.openVideo')}
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {run.lastError && (
        <p className="line-clamp-2 text-xs text-destructive">{run.lastError}</p>
      )}
    </div>
  )
}

export default RunRow
