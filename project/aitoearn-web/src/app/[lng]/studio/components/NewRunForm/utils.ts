/**
 * 新建时间轴任务表单的纯函数：多行解析、轻校验、友好 spec 组装。
 *
 * 这里只产出「友好输入」（engine/mode/assetBase/materials/script/canvas…），
 * 画布、切片、字幕时序、BGM 轨道的展开全部交给后端 timeline-spec builder，
 * 前端不拼 timeline 内部细节，避免两边各写一份渲染器规格。
 */

import type { TimelineCanvas, TimelineSpecInput } from '@/api/production/production.types'

/** 每台机器的素材目录不同，没有通用默认值。 */
export const DEFAULT_ASSET_BASE = ''

/** 每句文案的默认时长（秒）。表单不暴露，需要更细的控制直接调后端 API。 */
export const DEFAULT_CLIP_SECONDS = 3

/** BGM 默认音量，0–1。 */
export const DEFAULT_BGM_VOLUME = 0.35

/** 多行文本 → 去空白后的行数组。空行丢弃；文案按行分句、素材按行一个文件名。 */
export function parseLines(text: string): string[] {
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
}

/** 表单原始值。 */
export interface RunFormValues {
  title: string
  /** 多行文案，每行一句。 */
  scriptText: string
  /** 多行素材，每行一个相对 assetBase 的文件名。 */
  materialsText: string
  assetBase: string
  canvas: TimelineCanvas
  platforms: string[]
  bgmSrc: string
  /** 音量；用户清空输入框时是 undefined，组装 spec 时回落到默认值。 */
  bgmVolume?: number
}

/**
 * 轻校验：只拦明显会白跑一次渲染的空字段。
 * 返回第一条中文提示；全部通过返回空串。
 */
export function validateRunForm(values: RunFormValues): string {
  if (!values.title.trim())
    return '请填写任务标题'
  if (parseLines(values.scriptText).length === 0)
    return '请填写文案，每行一句'
  if (parseLines(values.materialsText).length === 0)
    return '请填写素材文件名，每行一个'
  if (!values.assetBase.trim())
    return '请填写素材根目录（本机可读的绝对路径）'
  if (values.platforms.length === 0)
    return '请至少选择一个发布平台'
  return ''
}

/** 表单值 → POST production/runs 的 spec。后端负责展开成 timeline。 */
export function buildTimelineSpec(values: RunFormValues): TimelineSpecInput {
  const spec: TimelineSpecInput = {
    engine: 'timeline',
    mode: 'script',
    assetBase: values.assetBase.trim(),
    materials: parseLines(values.materialsText),
    script: parseLines(values.scriptText),
    canvas: values.canvas,
    clipSeconds: DEFAULT_CLIP_SECONDS,
    platforms: values.platforms,
    fit: 'blur',
  }

  // BGM 是可选项：没填文件名就不传，避免后端收到空轨道。
  const bgmSrc = values.bgmSrc.trim()
  if (bgmSrc)
    spec.bgm = { src: bgmSrc, volume: values.bgmVolume ?? DEFAULT_BGM_VOLUME }

  return spec
}
