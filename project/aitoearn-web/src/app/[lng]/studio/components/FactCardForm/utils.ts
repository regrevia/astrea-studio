import type { FactCardSpec, FactCardVoice, CreateFactCardRunInput } from '@/api/production/production.types'

/** 剪映音色：BV405 甜美解说（女声，默认），BV411 解说小帅（男声）。 */
export const FACT_CARD_VOICE_OPTIONS: Array<{ value: FactCardVoice, labelKey: string }> = [
  { value: 'sami:BV405_streaming', labelKey: 'factCard.voiceOptions.female' },
  { value: 'sami:BV411_streaming', labelKey: 'factCard.voiceOptions.male' },
]

export const DEFAULT_FACT_CARD_VOICE: FactCardVoice = 'sami:BV405_streaming'
/** 默认服装：水手裙，动作表情最全的一套。 */
export const DEFAULT_FACT_CARD_OUTFIT = '01_sailor'
export const DEFAULT_FACT_CARD_KICKER = '大模型面试题'
/** 收尾口播：表单不暴露 cta，用固定收尾保证片子结构完整。 */
export const DEFAULT_FACT_CARD_CTA = '记住这几条，面试能答到点上。下一题想看什么，评论区说'
export const MIN_FACT_CARD_POINTS = 2
export const MAX_FACT_CARD_POINTS = 4
export const DEFAULT_FACT_CARD_POINTS = 3
/** 要点用英文双冒号分隔，与出片侧 `标题::说明` 的解析一致。 */
export const FACT_CARD_POINT_SEPARATOR = '::'

export type FactCardOutput = 'mp4' | 'draft'

export interface FactCardFormValues {
  topic: string // ui-copy-ignore
  hook: string // ui-copy-ignore
  /** 每条「标题::说明」，按出卡顺序排列。 */
  points: string[] // ui-copy-ignore
  /** 口播稿：一行一屏。 */
  narration: string
  voice: FactCardVoice // ui-copy-ignore
  outfit: string // ui-copy-ignore
  kicker: string // ui-copy-ignore
  output: FactCardOutput // ui-copy-ignore
}

/** 口播稿拆出来的三段，与镜头顺序对齐。 */
export interface FactCardNarrationParts {
  intro: string // ui-copy-ignore
  /** 与 points 一一对应。 */
  points: string[]
  cta: string // ui-copy-ignore
}

/** 校验/提交失败用到的翻译 key，文案由组件按当前语言渲染。 */
export type FactCardErrorKey =
  | 'factCard.error.topicRequired'
  | 'factCard.error.minPoints'
  | 'factCard.error.pointFormat'
  | 'factCard.error.narrationLines'
  | 'factCard.error.noProject'
  | 'factCard.error.submitFailed'

export interface FactCardFormError {
  key: FactCardErrorKey
  params?: Record<string, string | number>
}

export function isFactCardVoice(value: string): value is FactCardVoice {
  return FACT_CARD_VOICE_OPTIONS.some(option => option.value === value)
}

/** 「标题::说明」→ 标题 + 说明；缺分隔符或一边为空都算格式不对。 */
export function parseFactCardPoint(text: string): { title: string, detail: string } | null {
  const raw = text.trim()
  const separatorAt = raw.indexOf(FACT_CARD_POINT_SEPARATOR)
  if (separatorAt < 0)
    return null
  const title = raw.slice(0, separatorAt).trim()
  const detail = raw.slice(separatorAt + FACT_CARD_POINT_SEPARATOR.length).trim()
  if (!title || !detail)
    return null
  return { title, detail }
}

/**
 * 口播稿 → 分镜口播。一行一屏，两种行数都认：
 * 只写要点行，或写全开场 + 各要点 + 收尾。
 * 其它行数返回 null，交给表单报错，不猜。
 */
export function mapFactCardNarration(raw: string, pointCount: number): FactCardNarrationParts | null {
  const lines = raw
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
  if (lines.length === 0)
    return { intro: '', points: Array.from({ length: pointCount }, () => ''), cta: '' }
  if (lines.length === pointCount)
    return { intro: '', points: lines, cta: '' }
  if (lines.length === pointCount + 2)
    return { intro: lines[0], points: lines.slice(1, -1), cta: lines[lines.length - 1] }
  return null
}

/** 口播稿的合法行数，用于提示与报错。 */
export function factCardNarrationLineCounts(pointCount: number): { pointsOnly: number, withIntroOutro: number } {
  return { pointsOnly: pointCount, withIntroOutro: pointCount + 2 }
}

/** 丢掉空行，并把要点统一成「标题::说明」；有口播稿则补成第三段。 */
export function buildFactCardPoints(points: string[], narrationPoints?: string[]): string[] {
  let seen = 0
  return points.flatMap((text) => {
    const point = parseFactCardPoint(text)
    if (!point)
      return []
    const say = narrationPoints?.[seen] ?? ''
    seen += 1
    const base = `${point.title}${FACT_CARD_POINT_SEPARATOR}${point.detail}`
    return [say ? `${base}${FACT_CARD_POINT_SEPARATOR}${say}` : base]
  })
}

/** 校验顺序：选题 → 要点条数 → 每条要点格式 → 口播稿行数，回第一个错误。 */
export function validateFactCardForm(values: FactCardFormValues): FactCardFormError | null {
  if (!values.topic.trim())
    return { key: 'factCard.error.topicRequired' }

  const filled = values.points
    .map((text, index) => ({ index, text: text.trim() }))
    .filter(item => item.text)
  if (filled.length < MIN_FACT_CARD_POINTS)
    return { key: 'factCard.error.minPoints', params: { min: MIN_FACT_CARD_POINTS } }

  const broken = filled.find(item => !parseFactCardPoint(item.text))
  if (broken)
    return { key: 'factCard.error.pointFormat', params: { index: broken.index + 1 } }

  if (values.narration.trim() && !mapFactCardNarration(values.narration, filled.length)) {
    const counts = factCardNarrationLineCounts(filled.length)
    return {
      key: 'factCard.error.narrationLines',
      params: { pointsOnly: counts.pointsOnly, withIntroOutro: counts.withIntroOutro },
    }
  }

  return null
}

export function buildFactCardSpec(values: FactCardFormValues): FactCardSpec {
  const vertical = values.output === 'mp4'
  const hook = values.hook.trim()
  const pointCount = buildFactCardPoints(values.points).length
  const narration = mapFactCardNarration(values.narration, pointCount)
  const intro = narration?.intro ?? ''
  const cta = narration?.cta ?? ''
  return {
    // 后端按 run.spec.engine 派切渲染通道，只有 jianying 才会出剪映草稿；
    // 顶层 engine 会被接口 DTO 丢掉，所以 spec 里必须再带一份。
    engine: vertical ? 'timeline' : 'jianying', // ui-copy-ignore
    template: 'factcard', // ui-copy-ignore
    ...(vertical ? { mode: 'vertical_short' as const } : {}),
    topic: values.topic.trim(),
    ...(hook ? { hook } : {}),
    points: buildFactCardPoints(values.points, narration?.points),
    cta: cta || DEFAULT_FACT_CARD_CTA,
    voice: values.voice, // ui-copy-ignore
    ...(vertical && values.outfit ? { character: { outfit: values.outfit } } : {}),
    kicker: values.kicker.trim() || DEFAULT_FACT_CARD_KICKER,
    ...(intro ? { narration: { intro } } : {}),
  }
}

/** 表单值 → 创建任务请求体：标题用「系列名 · 选题」，brief 写给人看。 */
export function buildFactCardRunInput(values: FactCardFormValues, projectId: string): CreateFactCardRunInput {
  const topic = values.topic.trim()
  const kicker = values.kicker.trim() || DEFAULT_FACT_CARD_KICKER
  const pointCount = buildFactCardPoints(values.points).length
  const hasNarration = Boolean(values.narration.trim())
  return {
    engine: values.output === 'mp4' ? 'timeline' : 'jianying',
    template: 'factcard',
    projectId,
    title: `${kicker} · ${topic}`,
    brief: `干货卡：《${topic}》，系列「${kicker}」，${pointCount} 条要点，音色 ${values.voice}${hasNarration ? '，含口播稿' : ''}。`,
    spec: buildFactCardSpec(values),
  }
}
