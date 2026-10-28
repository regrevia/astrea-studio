import { isAbsolute } from 'node:path'

import { AppException, ResponseCode } from '@yikart/common'

/**
 * 友好输入只暴露「画幅朝向」，具体像素尺寸由后端固定。
 * 宽高必须是偶数（h264 要求），fps 统一 30，和 deploy/p2_timeline_e2e.py 验证过的一致。
 */
export enum TimelineCanvasPreset {
  PORTRAIT = 'portrait',
  LANDSCAPE = 'landscape',
  SQUARE = 'square',
}

const CANVAS_PRESETS: Record<TimelineCanvasPreset, { width: number, height: number, fps: number }> = {
  [TimelineCanvasPreset.PORTRAIT]: { width: 1080, height: 1920, fps: 30 },
  [TimelineCanvasPreset.LANDSCAPE]: { width: 1920, height: 1080, fps: 30 },
  [TimelineCanvasPreset.SQUARE]: { width: 1080, height: 1080, fps: 30 },
}

/** 字幕默认样式：底部居中的白字黑边，e2e 验证过在竖屏上可读。 */
const SUBTITLE_STYLE = { size: 0.052, y: 0.86, color: '#FFFFFF', stroke: 4, box: '#00000066' }

/** 字符串数组清洗：非数组/空行直接丢弃，剩下的去首尾空白。 */
function stringList(value: unknown) {
  if (!Array.isArray(value)) {
    return []
  }
  return value
    .filter((item): item is string => typeof item === 'string')
    .map(item => item.trim())
    .filter(item => item.length > 0)
}

/**
 * 把前端提交的友好 spec 展开成 worker 能直接渲染的 timeline 规格。
 *
 * 只处理 `engine='timeline'` 且没有 `timeline` 的友好输入；其他引擎、以及已经
 * 带了 timeline 的高级模式原样返回，避免覆盖用户手写的精细控制。
 *
 * 友好输入的字段（materials/script/canvas/bgm…）会保留到 `input` 里做审计，
 * 出问题能回溯这一版到底按什么指令渲染。
 */
export function buildTimelineSpec(spec: Record<string, unknown>): Record<string, unknown> {
  const existing = spec['timeline']
  if (spec['engine'] !== 'timeline' || (existing !== undefined && existing !== null)) {
    return spec
  }
  // 竖屏 factcard：素材是 worker 现场生成的卡面与配音，脚本本身就是渲染输入，
  // 这里按「素材 + 文案 + 切片」那套友好输入去要求它必然缺料。
  if (spec['mode'] === 'vertical_short') {
    // 竖屏通道里第 N 行素材只进第 N 屏，比屏数多的行渲染时会静默丢掉，提交时就拦。
    const scenes = Array.isArray(spec['points']) ? spec['points'].length : stringList(spec['script']).length
    const extra = stringList(spec['materials'])
    if (scenes > 0 && extra.length > scenes) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        `素材 ${extra.length} 行多于 ${scenes} 屏，第 ${scenes + 1} 行起不会进片`,
      )
    }
    return spec
  }

  const materials = stringList(spec['materials'])
  if (!materials.length) {
    throw new AppException(ResponseCode.ProductionInvalidState, '时间轴规格缺少素材')
  }
  const script = stringList(spec['script'])
  if (!script.length) {
    throw new AppException(ResponseCode.ProductionInvalidState, '时间轴规格缺少文案')
  }
  const canvas = CANVAS_PRESETS[spec['canvas'] as TimelineCanvasPreset]
  if (!canvas) {
    throw new AppException(ResponseCode.ProductionInvalidState, `时间轴画幅不支持：${String(spec['canvas'])}`)
  }
  const clipSeconds = typeof spec['clipSeconds'] === 'number' ? spec['clipSeconds'] : 3
  // 字幕前后各留 0.2s，切片短于 1s 时两个时间点会撞在一起
  if (!(clipSeconds >= 1)) {
    throw new AppException(ResponseCode.ProductionInvalidState, '时间轴切片时长不能小于 1 秒')
  }

  const bgm = spec['bgm']
  const bgmTrack = bgm && typeof bgm === 'object' ? bgm as Record<string, unknown> : {}
  const bgmSrc = typeof bgmTrack['src'] === 'string' ? bgmTrack['src'].trim() : ''
  const assetBase = typeof spec['assetBase'] === 'string' ? spec['assetBase'].trim() : ''
  if ((!assetBase) && [...materials, bgmSrc].some(src => src.length > 0 && !isAbsolute(src))) {
    throw new AppException(
      ResponseCode.ProductionInvalidState,
      '时间轴素材是相对路径，但规格缺少素材根目录 assetBase',
    )
  }

  // 素材不够时循环复用，文案不够时多出来的切片没有字幕，二者都不阻断渲染。
  const clipCount = Math.max(materials.length, script.length)
  const video = Array.from({ length: clipCount }, (_, index) => ({
    src: materials[index % materials.length],
    in: 0,
    out: clipSeconds,
  }))
  const text = script.map((line, index) => {
    const start = Number((index * clipSeconds + 0.2).toFixed(2))
    return {
      text: line,
      start,
      // 前后各留 0.2s，相邻两句不会首尾相接
      end: Number((start + clipSeconds - 0.4).toFixed(2)),
      ...SUBTITLE_STYLE,
    }
  })
  const audio = bgmSrc
    ? [{
        src: bgmSrc,
        start: 0,
        volume: typeof bgmTrack['volume'] === 'number' ? bgmTrack['volume'] : 0.35,
        loop: true,
      }]
    : []

  const expanded: Record<string, unknown> = {
    engine: 'timeline',
    input: spec,
    timeline: { canvas: { ...canvas }, video, text, audio },
  }
  if (assetBase) {
    expanded['assetBase'] = assetBase
  }
  if (Array.isArray(spec['platforms'])) {
    expanded['platforms'] = spec['platforms']
  }
  if (typeof spec['fit'] === 'string' && spec['fit']) {
    expanded['fit'] = spec['fit']
  }
  if (typeof spec['attachDraft'] === 'boolean') {
    expanded['attachDraft'] = spec['attachDraft']
  }
  return expanded
}
