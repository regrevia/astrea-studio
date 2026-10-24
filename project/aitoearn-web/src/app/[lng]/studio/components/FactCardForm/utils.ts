import type { FactCardSpec, FactCardVoice, CreateFactCardRunInput } from '@/api/production/production.types'

/** 剪映音色：BV405 甜美解说（女声，默认），BV411 解说小帅（男声）。 */
export const FACT_CARD_VOICE_OPTIONS: Array<{ value: FactCardVoice, labelKey: string }> = [
  { value: 'sami:BV405_streaming', labelKey: 'factCard.voiceOptions.female' },
  { value: 'sami:BV411_streaming', labelKey: 'factCard.voiceOptions.male' },
]

export const DEFAULT_FACT_CARD_VOICE: FactCardVoice = 'sami:BV405_streaming'
export const DEFAULT_FACT_CARD_KICKER = '大模型面试题'
/** 收尾口播：表单不暴露 cta，用固定收尾保证片子结构完整。 */
export const DEFAULT_FACT_CARD_CTA = '记住这几条，面试能答到点上。下一题想看什么，评论区说'
export const MIN_FACT_CARD_POINTS = 2
export const MAX_FACT_CARD_POINTS = 4
export const DEFAULT_FACT_CARD_POINTS = 3
/** 要点分隔符是英文双冒号，和 worker 里 `标题::说明` 的解析保持一致。 */
export const FACT_CARD_POINT_SEPARATOR = '::'

export interface FactCardFormValues {
  topic: string
  hook: string
  /** 每条「标题::说明」，按出卡顺序排列。 */
  points: string[]
  voice: FactCardVoice
  kicker: string
}

/** 校验/提交失败用到的翻译 key，文案由组件按当前语言渲染。 */
export type FactCardErrorKey =
  | 'factCard.error.topicRequired'
  | 'factCard.error.minPoints'
  | 'factCard.error.pointFormat'
  | 'factCard.error.noProject'
  | 'factCard.error.submitFailed'

export interface FactCardFormError {
  key: FactCardErrorKey
  params?: Record<string, string | number>
}

export function isFactCardVoice(value: string): value is FactCardVoice {
  return FACT_CARD_VOICE_OPTIONS.some(option => option.value === value)
}

/** 「标题::说明」→ 标题 + 说明；缺分隔符或有一边为空都算格式不对。 */
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

/** 丢掉空行，并把要点统一成「标题::说明」。 */
export function buildFactCardPoints(points: string[]): string[] {
  return points.flatMap((text) => {
    const point = parseFactCardPoint(text)
    return point ? [`${point.title}${FACT_CARD_POINT_SEPARATOR}${point.detail}`] : []
  })
}

/** 校验顺序：选题 → 要点条数 → 每条要点格式，回第一个错误。 */
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

  return null
}

export function buildFactCardSpec(values: FactCardFormValues): FactCardSpec {
  const hook = values.hook.trim()
  return {
    // 后端按 run.spec.engine 派切渲染通道，只有 jianying 才会出剪映草稿；
    // 顶层 engine 会被接口 DTO 丢掉，所以 spec 里必须再带一份。
    engine: 'jianying',
    template: 'factcard',
    topic: values.topic.trim(),
    ...(hook ? { hook } : {}),
    points: buildFactCardPoints(values.points),
    cta: DEFAULT_FACT_CARD_CTA,
    voice: values.voice,
    kicker: values.kicker.trim() || DEFAULT_FACT_CARD_KICKER,
  }
}

/** 表单值 → 创建任务请求体：标题用「系列名 · 选题」，brief 写给人看。 */
export function buildFactCardRunInput(values: FactCardFormValues, projectId: string): CreateFactCardRunInput {
  const topic = values.topic.trim()
  const kicker = values.kicker.trim() || DEFAULT_FACT_CARD_KICKER
  const pointCount = buildFactCardPoints(values.points).length
  return {
    engine: 'jianying',
    template: 'factcard',
    projectId,
    title: `${kicker} · ${topic}`,
    brief: `干货卡：《${topic}》，系列「${kicker}」，${pointCount} 条要点，音色 ${values.voice}。`,
    spec: buildFactCardSpec(values),
  }
}
