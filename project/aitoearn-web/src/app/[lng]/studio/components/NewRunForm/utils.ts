/**
 * 口播出片表单的纯函数：口播稿解析、音色候选、时长估算、spec 组装。
 *
 * 提交走 timeline 通道的竖屏脚本分支（mode=vertical_short）：worker 按这份稿子 // ui-copy-ignore
 * 现场出卡面、立绘和逐屏配音，前端只发脚本文本，不拼时间轴细节。
 * 整片时长上限只在提交前拦截——渲染进程没有这个参数，前端不凭空造一个。
 */

import type { Inventory, VerticalShortSpecInput } from '@/api/production/production.types'

/** 一屏三段的分隔符：标题::镜头注解::口播句。 */
export const SCRIPT_SEPARATOR = '::'

/** 默认音色：edge 晓晓，语速 0.98。 */ // ui-copy-ignore
export const DEFAULT_VOICE = 'edge:zh-CN-XiaoxiaoNeural@rate=0.98'

/** 整片时长上限（秒）。只用于提交前估算，不写进 spec。 */
export const DEFAULT_MAX_SECONDS = 90
export const MIN_MAX_SECONDS = 10
export const MAX_MAX_SECONDS = 600

/** 口播估算：普通话约 3.4 字/秒，停顿标点每个按 0.18 秒折算。 */
const CHARS_PER_SECOND = 3.4
const PAUSE_SECONDS = 0.18
const PAUSE_PATTERN = /[，,。.、；;：:！!？?…—]/g

/** SAMI 音色没有逐字时间，口型只能按句子估。 */
const SAMI_NOTE = '无逐字时间，口型为估算'

/** 音色下拉的一项。 */
export interface VoiceOption {
  value: string
  label: string
  note?: string
}

/** 能力服务不在线时的兜底音色，与 docs/shorts/voice-catalog.md 的可用清单一致。 */
const FALLBACK_EDGE_VOICES: Array<{ id: string, name: string }> = [
  { id: 'zh-CN-XiaoxiaoNeural', name: '晓晓·亲和女声' },
  { id: 'zh-CN-YunxiNeural', name: '云希·磁性男声' },
  { id: 'zh-CN-YunjianNeural', name: '云健·沉稳男声' },
  { id: 'zh-CN-YunyangNeural', name: '云扬·播报男声' },
  { id: 'zh-CN-YunxiaNeural', name: '云夏·少年音' },
  { id: 'zh-CN-XiaoyiNeural', name: '晓伊·俏皮女声' },
  { id: 'liaoning-XiaobeiNeural', name: '晓北·东北女声' },
  { id: 'shaanxi-XiaoniNeural', name: '晓妮·陕西女声' },
]

/** 用户输入的一屏口播稿。 */
export interface ScriptLine {
  /** 用户写的那一行原文，spec 里原样下发。 */
  raw: string // ui-copy-ignore
  title: string // ui-copy-ignore
  note: string // ui-copy-ignore
  say: string // ui-copy-ignore
}

/** 口播稿解析结果：invalidLine 是第一个格式不对的行号（从 1 起）。 */
export interface ParsedScript {
  lines: ScriptLine[] // ui-copy-ignore
  invalidLine?: number
}

/** 表单原始值。 */
export interface NarrationFormValues {
  topic: string // ui-copy-ignore
  /** 多行口播稿，一行一屏。 */
  scriptText: string
  voice: string // ui-copy-ignore
  /** 多行素材，绝对路径或 pack:包名/文件名。 */
  materialsText: string
  /** 整片最长秒数；用户清空输入框时是 undefined。 */
  maxSeconds?: number
  /** 分发平台：勾了的平台各出一版，空数组只出母版。 */
  platforms?: string[]
}

/** 提交前能拦住的错误。带行号的是稿子的问题，带秒数的是时长超限。 */
export type NarrationFormError
  = | { kind: 'topic' }
    | { kind: 'script_empty' }
    | { kind: 'line_shape', line: number }
    | { kind: 'material', line: number }
    | { kind: 'material_overflow', count: number, scenes: number, line: number }
    | { kind: 'too_long', estimated: number, limit: number, worstLine: number }

/** 多行文本 → 去空白后的行数组。空行丢弃。 */
export function parseLines(text: string): string[] {
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
}

/** 一行 → 三段。口播句里允许再出现双冒号，第三段之后的内容都算口播句。 */
export function parseScriptLine(raw: string): ScriptLine | null {
  const parts = raw.split(SCRIPT_SEPARATOR).map(part => part.trim())
  if (parts.length < 3)
    return null
  const title = parts[0] ?? ''
  const note = parts[1] ?? ''
  const say = parts.slice(2).join(SCRIPT_SEPARATOR).trim()
  if (!title || !say)
    return null
  return { raw, title, note, say }
}

/** 整段口播稿 → 屏列表。遇到格式不对的行就停下，把行号带回去让用户改那一行。 */
export function parseScriptText(text: string): ParsedScript {
  const rawLines = parseLines(text)
  const lines: ScriptLine[] = []
  for (const [index, raw] of rawLines.entries()) {
    const parsed = parseScriptLine(raw)
    if (!parsed)
      return { lines, invalidLine: index + 1 }
    lines.push(parsed)
  }
  return { lines }
}

/** 音色标识里的语速（`@rate=0.98`）；没写按 1 算。 */
export function voiceRate(voice: string): number {
  const matched = /rate=([0-9.]+)/.exec(voice)
  const rate = matched ? Number(matched[1]) : 1
  if (!Number.isFinite(rate) || rate <= 0)
    return 1
  return Math.min(2, Math.max(0.5, rate))
}

/** 一句口播的预计秒数。 */
export function estimateLineSeconds(say: string, rate = 1): number {
  const chars = [...say].length
  const pauses = say.match(PAUSE_PATTERN)?.length ?? 0
  return chars / (CHARS_PER_SECOND * rate) + (pauses * PAUSE_SECONDS) / rate
}

/** 整片预计秒数。 */
export function estimateSeconds(lines: ScriptLine[], voice = DEFAULT_VOICE): number {
  const rate = voiceRate(voice)
  return lines.reduce((total, line) => total + estimateLineSeconds(line.say, rate), 0)
}

/** 素材行只认绝对路径和 pack: 前缀；存在性由渲染进程判断。 */
function isMaterialRef(value: string) {
  return value.startsWith('/') || value.startsWith('~/') || value.startsWith('pack:')
}

/** 提交前校验：只拦明显会白跑一次渲染的输入。 */
export function validateNarrationForm(
  values: NarrationFormValues,
  lines: ScriptLine[],
): NarrationFormError | null {
  if (!values.topic.trim())
    return { kind: 'topic' }
  const parsed = parseScriptText(values.scriptText)
  if (parsed.invalidLine !== undefined)
    return { kind: 'line_shape', line: parsed.invalidLine }
  if (parsed.lines.length === 0)
    return { kind: 'script_empty' }

  const materials = parseLines(values.materialsText)
  const badMaterial = materials.findIndex(item => !isMaterialRef(item))
  if (badMaterial >= 0)
    return { kind: 'material', line: badMaterial + 1 }
  // 第几条素材进第几屏：比屏数多的那几行渲染时会被静默丢掉，提交前就得拦住。
  if (materials.length > parsed.lines.length) {
    return {
      kind: 'material_overflow',
      count: materials.length,
      scenes: parsed.lines.length,
      line: parsed.lines.length + 1,
    }
  }

  const limit = values.maxSeconds ?? DEFAULT_MAX_SECONDS
  const estimated = estimateSeconds(lines, values.voice)
  if (estimated > limit) {
    const rate = voiceRate(values.voice)
    let worstLine = 1
    let worst = 0
    lines.forEach((line, index) => {
      const seconds = estimateLineSeconds(line.say, rate)
      if (seconds > worst) {
        worst = seconds
        worstLine = index + 1
      }
    })
    return { kind: 'too_long', estimated: Math.round(estimated), limit, worstLine }
  }
  return null
}

/** 提交前给用户看的一句话。 */
export function describeFormError(error: NarrationFormError): string {
  switch (error.kind) {
    case 'topic':
      return '请填写主题'
    case 'script_empty':
      return '请写口播稿，一行一屏'
    case 'line_shape':
      return `第 ${error.line} 行格式不对，一行要写成 标题 :: 镜头注解 :: 口播句`
    case 'material':
      return `第 ${error.line} 行素材要用绝对路径，或用 pack 前缀指向素材包`
    case 'material_overflow':
      return `素材 ${error.count} 行、稿子 ${error.scenes} 屏：第 ${error.line} 行起不会进片，删掉或补一屏`
    case 'too_long':
      return `预计 ${error.estimated} 秒，上限 ${error.limit} 秒，建议删掉第 ${error.worstLine} 屏或压缩口播`
  }
}

/** 能力清单 → 音色下拉。edge 带逐字时间；SAMI 的项要标出口型差异。 */
export function buildVoiceOptions(inventory?: Inventory | null): VoiceOption[] {
  const edge = inventory?.voices?.edge?.length ? inventory.voices.edge : FALLBACK_EDGE_VOICES
  const edgeOptions: VoiceOption[] = edge.map(item => ({
    value: item.id.startsWith('edge:') ? item.id : `edge:${item.id}@rate=0.98`,
    label: item.name,
  }))
  const samiOptions: VoiceOption[] = (inventory?.voices?.jianying ?? [])
    .filter(item => (item.voice ?? '').startsWith('sami:'))
    .map(item => ({ value: item.voice ?? '', label: item.name, note: SAMI_NOTE }))
  const seen = new Set<string>()
  return [...edgeOptions, ...samiOptions].filter((option) => {
    if (!option.value || seen.has(option.value))
      return false
    seen.add(option.value)
    return true
  })
}

/** 表单值 → POST production/runs 的 spec。竖屏脚本通道，worker 出卡面与配音。 */ // ui-copy-ignore
export function buildVerticalShortSpec(
  values: NarrationFormValues,
  lines: ScriptLine[], // ui-copy-ignore
): VerticalShortSpecInput {
  const topic = values.topic.trim()
  const spec: VerticalShortSpecInput = {
    engine: 'timeline', // ui-copy-ignore
    mode: 'vertical_short', // ui-copy-ignore
    template: 'factcard', // ui-copy-ignore
    topic,
    // 开场卡只放一行短字，用第一屏标题；开场口播由 worker 按「主题。钩子」生成。 // ui-copy-ignore
    hook: lines[0]?.title ?? topic,
    points: lines.map(line => line.raw),
    // 收尾卡写最后一句的结论，配音也念同一句。
    cta: lines[lines.length - 1]?.title ?? '',
    voice: values.voice || DEFAULT_VOICE,
    kicker: topic, // ui-copy-ignore
  }
  const materials = parseLines(values.materialsText)
  if (materials.length > 0)
    spec.materials = materials
  const platforms = values.platforms ?? []
  if (platforms.length > 0)
    spec.platforms = platforms
  return spec
}
