/**
 * 时间轴标尺：把稿子里的卡点、镜头分段和关键帧换算成面板上的一串位置。
 *
 * 只做读取，不改稿：这里拿到的位置全部来自任务自己的 timeline，
 * 面板按占比画出来，所以同一个函数也负责算出总时长。
 */

export interface TimelineMarks {
  /** 总时长（秒），由各镜头时长累加得到 */
  duration: number
  /** 每个镜头结束的位置（秒），用于画分段 */
  shotEnds: number[]
  /** 音乐卡点位置（秒） */
  beats: number[]
  /** 关键帧位置（秒），只收带缩放变化的关键帧 */
  keyframes: number[]
  /** 音乐速度，没有音乐时为 0 */
  bpm: number
  /** 字幕行，按出现顺序排 */
  lines: TimelineTextLine[]
}

export interface TimelineTextLine {
  /** 第几句，从 1 开始 */
  index: number
  /** 这一句的文本 */
  text: string
  start: number
  end: number
  /** 这句开口时踩的是第几拍，稿子里没有音乐时为空 */
  beatIndex?: number
  beatAt?: number
}

function asRows(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => !!row && typeof row === 'object') : []
}

function asNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** 从任务稿子里取时间轴标尺；没有时间轴返回 null。 */
export function deriveTimelineMarks(spec: unknown): TimelineMarks | null {
  const timeline = (spec as Record<string, unknown> | undefined)?.['timeline']
  if (!timeline || typeof timeline !== 'object') {
    return null
  }
  const rows = asRows((timeline as Record<string, unknown>)['video'])
  const shotEnds: number[] = []
  const keyframes: number[] = []
  let cursor = 0
  for (const row of rows) {
    const start = asNumber(row['in']) ?? 0
    const end = asNumber(row['out']) ?? start
    const span = Math.max(0, end - start)
    const frames = asRows(row['keyframes'])
    const zooms = frames.map(frame => asNumber(frame['zoom'])).filter((zoom): zoom is number => zoom != null)
    // 只有缩放真的变了才算关键帧，纯起点帧不算卖点。
    if (zooms.some(zoom => zoom !== zooms[0])) {
      for (const frame of frames) {
        const at = asNumber(frame['at'])
        if (at != null) {
          keyframes.push(cursor + Math.max(0, Math.min(span, at)))
        }
      }
    }
    cursor += span
    if (cursor > 0) {
      shotEnds.push(Number(cursor.toFixed(3)))
    }
  }
  if (cursor <= 0) {
    return null
  }
  const music = ((timeline as Record<string, unknown>)['music'] ?? {}) as Record<string, unknown>
  const beats = (Array.isArray(music['beats']) ? music['beats'] : [])
    .map(beat => asNumber(beat))
    .filter((beat): beat is number => beat != null && beat >= 0 && beat <= cursor)
  const lines: TimelineTextLine[] = asRows((timeline as Record<string, unknown>)['text'])
    .map((row, index) => {
      const start = asNumber(row['start']) ?? 0
      const end = asNumber(row['end']) ?? start
      const hit = beats.findIndex(beat => beat >= start - 0.05)
      return {
        index: index + 1,
        text: typeof row['text'] === 'string' ? row['text'] : '',
        start,
        end,
        ...(hit >= 0 ? { beatIndex: hit + 1, beatAt: beats[hit] } : {}),
      }
    })
    .filter(line => line.end > line.start)
  return {
    duration: Number(cursor.toFixed(3)),
    shotEnds,
    beats,
    keyframes,
    bpm: asNumber(music['bpm']) ?? 0,
    lines,
  }
}
