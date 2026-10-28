/**
 * 按真实素材排一条 timeline 任务。
 *
 * 素材是宿主机上的成片片段，每条时长都不一样，所以这里不用「每段固定几秒」的
 * 友好输入，而是按每条素材的真实时长切段、循环用到目标时长为止；最后一段按剩余
 * 秒数收口，宁可短一点，也不把片段拉长成慢放。
 */

export interface MaterialTimelineClip {
  /** 宿主机绝对路径。 */
  src: string
  /** 片段真实秒数（ffprobe 得到）。 */
  duration: number
}

/** 一段视频最少 1 秒，再短的关键帧推拉肉眼看不出，也让字幕/拍点没有落点。 */
export const MIN_SEGMENT_SECONDS = 1

/** 目标时长可选项；短视频平台常规长度。 */
export const TARGET_SECONDS = [15, 30, 45] as const
export type TargetSeconds = (typeof TARGET_SECONDS)[number]

const round = (value: number) => Number(value.toFixed(2))

export function buildMaterialTimeline(clips: MaterialTimelineClip[], targetSeconds: number) {
  const usable = clips.filter(clip => clip.src && clip.duration >= MIN_SEGMENT_SECONDS)
  if (!usable.length)
    return null

  const video: Array<Record<string, unknown>> = []
  let elapsed = 0
  let cursor = 0
  // 循环取素材直到填满目标时长；素材比目标长时只取前一段。
  while (elapsed < targetSeconds - 0.05 && cursor < usable.length * 64) {
    const clip = usable[cursor % usable.length]
    const remaining = targetSeconds - elapsed
    const length = round(Math.min(clip.duration, remaining))
    if (length < MIN_SEGMENT_SECONDS && video.length > 0)
      break
    // 首尾交替推拉，避免整条片子朝同一个方向漂。
    const zoomIn = video.length % 2 === 0
    video.push({
      src: clip.src,
      in: 0,
      out: length,
      fit: 'cover',
      keyframes: zoomIn
        ? [{ at: 0, zoom: 1 }, { at: round(length), zoom: 1.08 }]
        : [{ at: 0, zoom: 1.08 }, { at: round(length), zoom: 1 }],
    })
    elapsed = round(elapsed + length)
    cursor += 1
  }

  if (!video.length)
    return null

  return {
    engine: 'timeline',
    timeline: {
      canvas: { width: 1080, height: 1920, fps: 30 },
      video,
      text: [],
      audio: [],
    },
    fit: 'cover',
    platforms: ['douyin'],
  }
}
