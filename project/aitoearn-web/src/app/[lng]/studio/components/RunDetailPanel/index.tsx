/**
 * RunDetailPanel - 选中任务的成片与阶段
 *
 * 右侧固定展示「这一条任务现在到哪了、出了什么、下一步能做什么」：
 * 母版视频直接可播，平台成片单列，剪映草稿和 AiToEarn 发布草稿分成两张卡，
 * 底部折叠 jobs / events 便于排查。列表里的状态是权威的，详情只补 jobs/attempts/events。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { Clapperboard, ExternalLink, FileText } from 'lucide-react'
import Link from 'next/link'
import { useTransClient } from '@/app/i18n/client'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/utils/className'
import { useRunDetail } from '../../hooks/useRunDetail'
import { formatKib } from '../../studioConstants'
import { deriveRunArtifacts } from '../../utils/runArtifacts'
import { isRunBusy, runStageMeta, runStatusMeta } from '../../utils/status'
import { RunActions } from '../RunActions'

interface RunDetailPanelProps {
  run: ProductionRun
  projectName?: string
  onChanged?: () => void
}

export function RunDetailPanel({ run, projectName, onChanged }: RunDetailPanelProps) {
  const { t } = useTransClient('studio')
  const meta = runStatusMeta(run.status)
  const artifacts = deriveRunArtifacts(run)
  const busy = isRunBusy(run.status)
  // 阶段只在与状态不同时上屏，避免同一句话出现两次。
  const stage = run.currentStage ? runStageMeta(run.currentStage, run.status) : null
  const stageLabel = stage && stage.labelKey !== meta.labelKey ? stage : null
  // 列表 revision/status 一变就重新拉详情，改稿重跑后不会停在旧成片上。
  const { detail, error } = useRunDetail(run.id, `${run.revision ?? 0}:${run.status}`)
  const events = (detail?.events ?? []).slice(-12).reverse()
  const jobs = detail?.jobs ?? []

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-foreground">{run.title || run.id}</h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5', meta.tone)}>
              <meta.icon className={cn('h-3 w-3', run.status === 'running' && 'animate-spin')} />
              {t(meta.labelKey)}
            </span>
            {projectName && <span>{projectName}</span>}
            <span>{t('run.detail.revision', { n: run.revision ?? 0 })}</span>
            {stageLabel && <span>{t(stageLabel.labelKey)}</span>}
          </div>
        </div>
        <RunActions run={run} onChanged={onChanged} />
      </div>

      {busy && typeof run.progress === 'number' && (
        <div className="flex flex-col gap-1">
          <Progress value={Math.max(0, Math.min(100, run.progress))} className="h-1.5" />
          <span className="text-xs text-muted-foreground">
            {t('run.detail.progress', { n: Math.round(run.progress) })}
          </span>
        </div>
      )}

      {run.status === 'waiting_user' && (
        <p className="rounded-md bg-warning/15 px-3 py-2 text-xs text-foreground">
          {run.lastError || (run.output?.manualExportReason === 'screen_locked'
            ? t('run.detail.screenLocked')
            : t('run.detail.waitingHint'))}
        </p>
      )}

      {/* 母版成片 */}
      {artifacts.masterUrl
        ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-foreground">{t('run.detail.master')}</span>
                <div className="flex items-center gap-3 text-xs">
                  {artifacts.duration != null && (
                    <span className="text-muted-foreground">{t('run.detail.duration', { n: Math.round(artifacts.duration) })}</span>
                  )}
                  <a
                    href={artifacts.masterUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    {t('run.action.openVideo')}
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
              </div>
              <video key={artifacts.masterUrl} src={artifacts.masterUrl} controls className="aspect-video w-full rounded-md bg-muted" />
            </div>
          )
        : (
            <p className="text-xs text-muted-foreground">{t('run.detail.noVideo')}</p>
          )}

      {/* 入库落点：素材库按项目/月分目录 */}
      {artifacts.libraryPath && (
        <div className="flex flex-col gap-1 rounded-md border border-border p-3">
          <span className="text-xs font-medium text-foreground">{t('run.detail.library')}</span>
          <code className="break-all text-[11px] text-muted-foreground">{artifacts.libraryPath}</code>
        </div>
      )}

      {/* 剪映草稿：产物是草稿目录，导出由用户在剪映里完成 */}
      {artifacts.jianying && (
        <div className="flex flex-col gap-1 rounded-md border border-border p-3">
          <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
            <Clapperboard className="h-3.5 w-3.5" />
            {t('run.detail.jianying')}
          </span>
          <p className="text-xs text-muted-foreground">
            {artifacts.jianyingClaimed
              ? t('run.detail.jianyingClaimed')
              : artifacts.jianyingDelivered
                ? t('run.detail.jianyingDone')
                : t('run.detail.jianyingPending')}
          </p>
        </div>
      )}

      {/* 发布草稿：AiToEarn 草稿箱条目，和剪映草稿不是一回事 */}
      {artifacts.publishDraftId && (
        <div className="flex flex-col gap-1 rounded-md border border-border p-3">
          <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
            <FileText className="h-3.5 w-3.5" />
            {t('run.detail.publishDraft')}
          </span>
          <Link href="/draft-box" className="text-xs text-primary hover:underline">
            {t('run.detail.openDraftBox')}
          </Link>
        </div>
      )}

      {/* 平台成片 */}
      {artifacts.platforms.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-foreground">{t('run.detail.platforms')}</span>
          <ul className="flex flex-col divide-y divide-border">
            {artifacts.platforms.map(item => (
              <li key={item.platform} className="flex items-center justify-between gap-2 py-1.5">
                <span className="text-xs text-foreground">{t(`platform.${item.platform}`)}</span>
                <span className="text-xs text-muted-foreground">
                  {item.width && item.height ? `${item.width}×${item.height}` : ''}
                  {item.bytes ? ` · ${formatKib(item.bytes)}` : ''}
                </span>
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                >
                  {t('run.action.openVideo')}
                  <ExternalLink className="h-3 w-3" />
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 体检提醒 */}
      {(artifacts.copyFindings.length > 0 || artifacts.qaFindings.length > 0) && (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-foreground">{t('run.detail.qa')}</span>
          <ul className="flex flex-col gap-1">
            {[...artifacts.qaFindings, ...artifacts.copyFindings].slice(0, 4).map((finding, index) => (
              <li key={`${finding.code ?? 'finding'}-${index}`} className="text-xs text-muted-foreground">
                {finding.detail || finding.code}
                <span className="ml-1 text-foreground/70">{finding.where}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {error && <p role="alert" className="text-xs text-destructive">{t(error)}</p>}

      {(jobs.length > 0 || events.length > 0) && (
        <details className="rounded-md border border-border px-3 py-2">
          <summary className="cursor-pointer text-xs text-muted-foreground">{t('run.detail.log')}</summary>
          <ul className="mt-2 flex flex-col gap-1">
            {jobs.map(job => (
              <li key={job.id} className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="outline" className="font-normal">{String(job.kind ?? '')}</Badge>
                <span>{String(job.status ?? '')}</span>
                <span>
                  #
                  {job.attempt ?? 1}
                </span>
                {job.message ? <span className="truncate">{String(job.message)}</span> : null}
              </li>
            ))}
            {events.map((event, index) => (
              <li key={`${event.seq ?? index}-${event.stage ?? ''}`} className="text-xs text-muted-foreground">
                {event.stage || event.kind}
                {event.message ? ` · ${event.message}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

export default RunDetailPanel
