/**
 * TimelineRuler - 时间轴标尺
 *
 * 把一条任务的时间轴摊平成一根尺子：镜头分段、音乐卡点、关键帧各自落在哪。
 * 只画位置不播动画，用来核对「卡点踩没踩上、关键帧在不在重点上」。
 */
'use client'

import type { TimelineMarks } from '../../../../utils/timeline'
import { useTransClient } from '@/app/i18n/client'
import { cn } from '@/utils/className'

interface TimelineRulerProps {
  marks: TimelineMarks
}

/** 按占比定位；越界的收进 0–100，避免标点跑到尺子外面。 */
function position(seconds: number, duration: number): string {
  const ratio = duration > 0 ? seconds / duration : 0
  return `${Math.max(0, Math.min(100, ratio * 100))}%`
}

export function TimelineRuler({ marks }: TimelineRulerProps) {
  const { t } = useTransClient('studio')
  // 卡点很密时抽稀，尺子上看得清比一根一根都画重要。
  const beats = marks.beats.length > 60
    ? marks.beats.filter((_, index) => index % Math.ceil(marks.beats.length / 60) === 0)
    : marks.beats

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-foreground">{t('run.detail.timeline')}</span>
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{t('run.detail.timelineDuration', { n: Math.round(marks.duration) })}</span>
          {marks.bpm > 0 && <span>{t('run.detail.timelineBpm', { n: Math.round(marks.bpm) })}</span>}
          <span>{t('run.detail.timelineBeats', { n: marks.beats.length })}</span>
          <span>{t('run.detail.timelineKeyframes', { n: marks.keyframes.length })}</span>
        </span>
      </div>
      <div className="relative h-4 w-full overflow-hidden rounded-sm bg-muted">
        {beats.map((beat, index) => (
          <span
            key={`beat-${index}`}
            className="absolute top-0 h-full w-px bg-primary/40"
            style={{ left: position(beat, marks.duration) }}
          />
        ))}
        {marks.keyframes.map((at, index) => (
          <span
            key={`kf-${index}`}
            className={cn('absolute top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-cyan')}
            style={{ left: position(at, marks.duration) }}
          />
        ))}
        {marks.shotEnds.map((end, index) => (
          <span
            key={`shot-${index}`}
            className="absolute top-0 h-full w-px bg-foreground/40"
            style={{ left: position(end, marks.duration) }}
          />
        ))}
      </div>
    </div>
  )
}

export default TimelineRuler
