/**
 * TimelineRuler - 时间轴标尺
 *
 * 上半根画镜头分段、音乐卡点和关键帧，下半根是字幕占的时间段。
 * 点某一句就能看到它从几秒到几秒、开口时踩在第几拍。
 */
'use client'

import type { TimelineMarks } from '@/utils/studio/timeline'
import { useTransClient } from '@/app/i18n/client'
import { cn } from '@/utils/className'

interface TimelineRulerProps {
  marks: TimelineMarks
  selectedLine?: number
  onSelectLine?: (index: number) => void
}

/** 按占比定位；越界的收进 0–100，避免标点跑到尺子外面。 */
function position(seconds: number, duration: number): string {
  const ratio = duration > 0 ? seconds / duration : 0
  return `${Math.max(0, Math.min(100, ratio * 100))}%`
}

function width(from: number, to: number, duration: number): string {
  const ratio = duration > 0 ? (to - from) / duration : 0
  return `${Math.max(0.6, Math.min(100, ratio * 100))}%`
}

export function TimelineRuler({ marks, selectedLine, onSelectLine }: TimelineRulerProps) {
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
            className="absolute top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-cyan"
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
      {marks.lines.length > 0 && (
        <div className="relative h-2 w-full">
          {marks.lines.map(line => (
            <button
              key={`line-${line.index}`}
              type="button"
              aria-label={t('run.detail.lineNo', { n: line.index })}
              aria-pressed={selectedLine === line.index}
              onClick={() => onSelectLine?.(line.index)}
              className={cn(
                'absolute top-0 h-full rounded-sm transition-colors',
                selectedLine === line.index ? 'bg-primary' : 'bg-primary/25 hover:bg-primary/40',
              )}
              style={{ left: position(line.start, marks.duration), width: width(line.start, line.end, marks.duration) }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default TimelineRuler
