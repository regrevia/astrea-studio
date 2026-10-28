/**
 * 按真实素材排一条 timeline 任务。
 *
 * 素材是宿主机上的成片片段，每条时长都不一样，所以这里不用「每段固定几秒」的
 * 友好输入，而是按每条素材的真实时长切段、循环用到目标时长为止；最后一段按剩余
 * 秒数收口，宁可短一点，也不把片段拉长成慢放。
 *
 * 选了配乐并按鼓点排时，切点落在拍点网格上（每两拍一刀），每段从素材的
 * 上一次落点继续往后取，避免整条片子重复同一批首帧。
 */

export interface MaterialTimelineClip {
  /** 宿主机绝对路径。 */
  src: string
  /** 片段真实秒数（ffprobe 得到）。 */
  duration: number
}

/** 配乐拍点计划：来自宿主机对这段音频的实测。 */
export interface MaterialBeatPlan {
  /** 配乐在宿主机上的绝对路径。 */
  src: string
  bpm: number
  /** 第一拍偏移秒数。 */
  offset: number
  beats: number[]
}

/** 三段钩子：开场抛问题、中段给卖点、收尾给行动。 */
export interface MaterialHooks {
  open: string
  mid?: string
  end?: string
}

/** 成片上的文字层：标题/副标题常驻，三段钩子各占三分之一时长。 */
export interface MaterialTextPlan {
  title: string
  subtitle?: string
  hooks?: MaterialHooks
  /** 批量出片时第 1 行钩子的备选文案，第 n 条取第 n 个。 */
  hookVariants?: string[]
}

/** 一段视频最少 1 秒，再短的关键帧推拉肉眼看不出，也让字幕/拍点没有落点。 */
export const MIN_SEGMENT_SECONDS = 1

/** 目标时长可选项；短视频平台常规长度。 */
export const TARGET_SECONDS = [15, 30, 45] as const
export type TargetSeconds = (typeof TARGET_SECONDS)[number]

/** 卡点密度：两拍一刀，30 秒约 15 段；一拍一刀太碎，四拍一刀跟不上节奏。 */
export const BEATS_PER_CUT = 2

const round = (value: number) => Number(value.toFixed(2))

/** 把配乐拍点铺到目标时长上：音乐循环，拍点网格跟着循环。 */
function cutBoundaries(plan: MaterialBeatPlan, targetSeconds: number): number[] {
  if (!(plan.bpm > 0))
    return []
  const step = (60 / plan.bpm) * BEATS_PER_CUT
  const start = Math.max(0, plan.offset)
  const cuts: number[] = []
  for (let t = start; t < targetSeconds; t = round(t + step))
    cuts.push(round(t))
  return cuts
}

/** 工作区里那几行文字拼成渲染用的文字层；全空就返回 null，不占帧。 */
export function composeTextPlan(input: {
  title: string
  subtitle: string
  hookOpen: string
  hookMid: string
  hookEnd: string
}): MaterialTextPlan | null {
  // 开场一行写多个钩子时用「｜」隔开，批量出片按条轮换开场文案。
  const openLines = input.hookOpen.split(/[｜|]/).map(line => line.trim()).filter(Boolean)
  const hooks: MaterialHooks = { open: openLines[0] ?? '', mid: input.hookMid.trim(), end: input.hookEnd.trim() }
  const hasText = [input.title.trim(), input.subtitle.trim(), hooks.open, hooks.mid, hooks.end].some(Boolean)
  if (!hasText)
    return null
  return {
    title: input.title.trim(),
    subtitle: input.subtitle.trim(),
    ...(hooks.open || hooks.mid || hooks.end ? { hooks } : {}),
    ...(openLines.length > 1 ? { hookVariants: openLines } : {}),
  }
}

export function buildMaterialTimeline(
  clips: MaterialTimelineClip[],
  targetSeconds: number,
  beatPlan?: MaterialBeatPlan | null,
  textPlan?: MaterialTextPlan | null,
) {
  const usable = clips.filter(clip => clip.src && clip.duration >= MIN_SEGMENT_SECONDS)
  if (!usable.length)
    return null

  const cuts = beatPlan ? cutBoundaries(beatPlan, targetSeconds) : []
  const video: Array<Record<string, unknown>> = []
  /** 每条素材已经用到第几秒；绕回开头再循环。 */
  const cursors = new Map<string, number>()
  let elapsed = 0
  let cursor = 0
  // 循环取素材直到填满目标时长；每段都尽量落在拍点上。
  while (elapsed < targetSeconds - 0.05 && cursor < usable.length * 512) {
    let clip = usable[cursor % usable.length]
    let offset = cursors.get(clip.src) ?? 0
    let available = clip.duration - offset
    // 这条只剩不到一秒的尾巴时整条跳过，下轮从头再取。
    if (available < MIN_SEGMENT_SECONDS) {
      cursors.set(clip.src, 0)
      cursor += 1
      clip = usable[cursor % usable.length]
      offset = cursors.get(clip.src) ?? 0
      available = clip.duration - offset
      if (available < MIN_SEGMENT_SECONDS)
        continue
    }
    // 有拍点时，下一刀落在至少一秒之后的拍点上；没拍点就按素材时长切。
    const nextCut = beatPlan ? cuts.find(cut => cut > elapsed + MIN_SEGMENT_SECONDS) : undefined
    const wanted = beatPlan ? (nextCut ? nextCut - elapsed : targetSeconds - elapsed) : clip.duration
    const length = round(Math.min(wanted, available, targetSeconds - elapsed))
    if (length < MIN_SEGMENT_SECONDS) {
      cursor += 1
      continue
    }
    // 首尾交替推拉，避免整条片子朝同一个方向漂。
    const zoomIn = video.length % 2 === 0
    video.push({
      src: clip.src,
      in: round(offset),
      out: round(offset + length),
      fit: 'cover',
      keyframes: zoomIn
        ? [{ at: 0, zoom: 1 }, { at: round(length), zoom: 1.08 }]
        : [{ at: 0, zoom: 1.08 }, { at: round(length), zoom: 1 }],
    })
    cursors.set(clip.src, round(offset + length))
    elapsed = round(elapsed + length)
    // 这条素材用到尾巴了就换下一条；没拍点时每条只取一段。
    if (!beatPlan || length < wanted - 0.05)
      cursor += 1
  }

  // 收尾差一点点时把最后一段拉长补齐，避免成片比目标短一截。
  const gap = round(targetSeconds - elapsed)
  const last = video[video.length - 1]
  if (last && gap > 0 && gap < MIN_SEGMENT_SECONDS) {
    const src = String(last.src)
    const source = usable.find(clip => clip.src === src)
    const tail = cursors.get(src) ?? 0
    if (source && tail + gap <= source.duration + 0.05)
      last.out = round(Number(last.out) + gap)
  }

  if (!video.length)
    return null

  const music = beatPlan
    ? {
        src: beatPlan.src,
        volume: 0.32,
        loop: true,
        // 没测出拍点就只铺音乐，不写 bpm，免得渲染端起一条空拍点网格。
        ...(beatPlan.bpm > 0 ? { bpm: beatPlan.bpm, offset: beatPlan.offset } : {}),
      }
    : null

  // 标题压在上三分之一常驻，副标题晚半秒进、早半秒走，别跟收尾压黑撞在一起。
  const texts: Array<Record<string, unknown>> = []
  if (textPlan?.title) {
    texts.push({
      text: textPlan.title,
      start: 0.3,
      end: round(targetSeconds - 0.4),
      x: 0.5,
      y: 0.2,
      size: 0.062,
      color: '#ffffff',
      stroke: 2,
      stroke_color: '#000000',
      fade: 0.3,
      align: 'center',
    })
  }
  if (textPlan?.subtitle) {
    texts.push({
      text: textPlan.subtitle,
      start: 0.9,
      end: round(targetSeconds - 0.5),
      x: 0.5,
      y: 0.29,
      size: 0.04,
      color: '#ffffff',
      stroke: 2,
      stroke_color: '#000000',
      fade: 0.3,
      align: 'center',
    })
  }

  // 三段钩子按三段等宽排，各自留 0.2 s 呼吸，避免两行字在同一帧里换掉。
  const thirds = targetSeconds / 3
  const hookRows: Array<{ text: string, start: number, end: number, size: number }> = []
  if (textPlan?.hooks) {
    const slots: Array<{ text?: string, size: number }> = [
      { text: textPlan.hooks.open, size: 0.056 },
      { text: textPlan.hooks.mid, size: 0.048 },
      { text: textPlan.hooks.end, size: 0.052 },
    ]
    slots.forEach((slot, index) => {
      const text = slot.text?.trim()
      if (!text)
        return
      const start = round(index * thirds + 0.2)
      const end = index === 2 ? round(targetSeconds - 0.5) : round((index + 1) * thirds - 0.15)
      if (end - start < 0.6)
        return
      hookRows.push({ text, start, end, size: slot.size })
    })
    hookRows.forEach((row, index) => {
      texts.push({
        text: row.text,
        start: row.start,
        end: row.end,
        x: 0.5,
        y: 0.66,
        size: row.size,
        color: '#ffffff',
        stroke: 2,
        stroke_color: '#000000',
        fade: 0.25,
        align: 'center',
        ...(index === 0 && (textPlan.hookVariants?.length ?? 0) > 1 ? { hookVariants: textPlan.hookVariants } : {}),
      })
    })
  }

  return {
    engine: 'timeline',
    timeline: {
      canvas: { width: 1080, height: 1920, fps: 30 },
      video,
      text: texts,
      audio: [],
      ...(music ? { music } : {}),
    },
    fit: 'cover',
    platforms: ['douyin'],
  }
}
