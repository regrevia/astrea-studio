/**
 * BatchCompare - 一次出的多版并排看
 *
 * 三列各播一条成片的首帧，点哪列下面就播哪条：多版差异（运镜、开场、钩子）
 * 同屏能比，不用来回切列表。没出完成片的列只显示状态，不给空播放器。
 */
'use client'

import type { ProductionRun } from '@/api/production/production.types'
import { useTransClient } from '@/app/i18n/client'
import { cn } from '@/utils/className'
import { deriveRunArtifacts } from '@/app/[lng]/studio/utils/runArtifacts'

/**
 * 首帧取哪一秒：挑起得最早的那句开场文字，落在它开头往后 1.5 秒。
 *
 * 钩子文案压在前几秒，固定取 1 秒时几版看着一样；文字段常常横跨整条片子，
 * 取中点会跳到中段产品镜头，所以按句子开头算。
 */
function hookFrameAt(run: ProductionRun, duration?: number): number {
  const timeline = run.spec?.timeline as { text?: Array<{ start?: number, end?: number }> } | undefined
  const rows = (timeline?.text ?? []).filter(
    row => typeof row.start === 'number' && typeof row.end === 'number' && (row.end as number) > (row.start as number),
  )
  const first = rows.sort((a, b) => (a.start as number) - (b.start as number))[0]
  if (!first) {
    return 1
  }
  const at = (first.start as number) + 1.5
  const ceiling = ((duration && duration > 0.4) ? duration : at) - 0.3
  return Math.max(0.2, Math.min(at, ceiling))
}

interface BatchCompareProps {
  runs: ProductionRun[]
  selectedId: string
  onSelect: (runId: string) => void
}

export function BatchCompare({ runs, selectedId, onSelect }: BatchCompareProps) {
  const { t } = useTransClient('studio')

  return (
    <div className="grid grid-cols-3 gap-2">
      {runs.map((run, index) => {
        const artifacts = deriveRunArtifacts(run)
        const picked = run.id === selectedId
        const playable = Boolean(artifacts.masterUrl) && run.status === 'succeeded'
        return (
          <button
            key={run.id}
            type="button"
            aria-pressed={picked}
            className={cn(
              'overflow-hidden rounded-md border text-left transition-colors',
              picked ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-muted-foreground',
            )}
            onClick={() => onSelect(run.id)}
          >
            <span className="block aspect-[9/16] bg-muted">
              {playable
                ? (
                    <video
                      // 只取首帧：静音、不自动播，元数据到手就跳到 1 秒处。
                      src={artifacts.masterUrl}
                      className="size-full object-cover"
                      muted
                      playsInline
                      preload="metadata"
                      onLoadedMetadata={(event) => {
                        event.currentTarget.currentTime = hookFrameAt(run, artifacts.duration)
                      }}
                    />
                  )
                : (
                    <span className="flex size-full items-center justify-center px-1 text-center text-[11px] text-muted-foreground">
                      {t(`project.status_${run.status === 'failed' ? 'failed' : 'running'}`)}
                    </span>
                  )}
            </span>
            <span className="flex items-baseline justify-between gap-1 px-1.5 py-1">
              <span className="text-xs text-foreground">{t('project.batchVersion', { n: index + 1 })}</span>
              <span className="text-[11px] text-muted-foreground">
                {artifacts.duration ? `${artifacts.duration.toFixed(0)}s` : ''}
              </span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
