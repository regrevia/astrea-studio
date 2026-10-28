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
/**
 * 竖屏通道的卡点：友好输入写 `bgm`，渲染层要的是 `music`。
 * 字段一致，这里只做一次校验+改名，worker 按 music 混 BGM、按拍点顶关键帧。
 */
function attachVerticalMusic(spec: Record<string, unknown>): void {
  const raw = spec['music'] ?? spec['bgm']
  if (raw === undefined || raw === null) return
  if (typeof raw !== 'object' || Array.isArray(raw)) {
    throw new AppException(
      ResponseCode.ProductionInvalidState,
      '背景音乐要写成对象：{ src, volume?, bpm?, offset?, beats?, pulse? }',
    )
  }
  const m = raw as Record<string, unknown>
  const src = typeof m['src'] === 'string' ? m['src'].trim() : ''
  if (!src) {
    throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐缺少 src（音频文件路径）')
  }
  const music: Record<string, unknown> = { src }
  for (const key of ['volume'] as const) {
    const v = m[key]
    if (v === undefined) continue
    if (typeof v !== 'number' || !(v > 0 && v <= 1)) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        `背景音乐 volume 要在 0~1，得到 ${String(v)}`,
      )
    }
    music[key] = v
  }
  for (const key of ['in', 'offset'] as const) {
    const v = m[key]
    if (v === undefined) continue
    if (typeof v !== 'number' || v < 0) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        `背景音乐 ${key} 要是非负秒数，得到 ${String(v)}`,
      )
    }
    music[key] = v
  }
  if (m['loop'] !== undefined) {
    if (typeof m['loop'] !== 'boolean') {
      throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐 loop 只能是 true/false')
    }
    music['loop'] = m['loop']
  }
  if (m['bpm'] !== undefined) {
    const bpm = m['bpm']
    if (typeof bpm !== 'number' || !(bpm > 0)) {
      throw new AppException(
        ResponseCode.ProductionInvalidState,
        `背景音乐 bpm 要大于 0，得到 ${String(bpm)}`,
      )
    }
    music['bpm'] = bpm
  }
  if (m['beats'] !== undefined) {
    const beats = m['beats']
    if (!Array.isArray(beats) || !beats.length) {
      throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐 beats 要是秒数数组')
    }
    const nums = beats.map(b => (typeof b === 'number' && b >= 0 ? b : Number.NaN))
    if (nums.some(n => Number.isNaN(n))) {
      throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐 beats 只能是非负秒数')
    }
    for (let i = 1; i < nums.length; i += 1) {
      if (nums[i] <= nums[i - 1]) {
        throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐 beats 必须严格递增')
      }
    }
    music['beats'] = nums
  }
  if (m['pulse'] !== undefined) {
    const pulse = m['pulse']
    if (typeof pulse !== 'object' || Array.isArray(pulse) || !pulse) {
      throw new AppException(ResponseCode.ProductionInvalidState, '背景音乐 pulse 要写成对象 { amount?, decay? }')
    }
    const { amount, decay } = pulse as Record<string, unknown>
    const p: Record<string, unknown> = {}
    if (amount !== undefined) {
      if (typeof amount !== 'number' || amount < 0 || amount > 0.4) {
        throw new AppException(
          ResponseCode.ProductionInvalidState,
          `背景音乐 pulse.amount 要在 0~0.4，得到 ${String(amount)}`,
        )
      }
      p['amount'] = amount
    }
    if (decay !== undefined) {
      if (typeof decay !== 'number' || decay < 0.03 || decay > 1) {
        throw new AppException(
          ResponseCode.ProductionInvalidState,
          `背景音乐 pulse.decay 要在 0.03~1，得到 ${String(decay)}`,
        )
      }
      p['decay'] = decay
    }
    music['pulse'] = p
  }
  spec['music'] = music
}

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
    attachVerticalMusic(spec)
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

/**
 * 同题多版：把镜头推拉方向镜像一份。
 *
 * 批量出片最容易同质化——同一份 spec 渲 N 条就是 N 个一模一样的文件，挑片没有意义。
 * 这里只动「可感知但不动时长/不动拍点」的一维：把每段关键帧的 zoom 值顺序倒过来，
 * 时间点不动。推镜变拉镜、拉镜变推镜，首尾 zoom 数值也跟着互换，所以渲染器排序
 * 逻辑改了也不会把两条渲成一样。时长、beat 对齐、字幕和音频都不受影响。
 */
export function mirrorKeyframes(spec: Record<string, unknown>): Record<string, unknown> {
  const video = spec['video']
  if (!Array.isArray(video) || video.length === 0) return spec
  const flipped = video.map((clip) => {
    if (!clip || typeof clip !== 'object') return clip
    const item = clip as Record<string, unknown>
    const keys = item['keyframes']
    if (!Array.isArray(keys) || keys.length < 2) return item
    const sorted = [...keys].sort((a, b) => Number((a as Record<string, unknown>)['at']) - Number((b as Record<string, unknown>)['at']))
    const zooms = sorted.map((k) => (k as Record<string, unknown>)['zoom']).reverse()
    const mirrored = sorted.map((k, i) => ({ ...(k as Record<string, unknown>), zoom: zooms[i] }))
    return { ...item, keyframes: mirrored }
  })
  return { ...spec, video: flipped }
}

/**
 * 同题多版的第二维差异：换开场镜头。
 *
 * 只倒运镜（mirrorKeyframes）时前两条已经能挑，第三条起还会和第一条撞。这里把
 * 首段素材挪到最后、原第二段顶上开场——镜头清单不变、总时长不变，但开头三秒
 * 是另一个画面，信息流里第一眼就不一样。只动 video 顺序，不碰 keyframes/音频。
 */
export function rotateOpening(spec: Record<string, unknown>): Record<string, unknown> {
  const video = spec['video']
  if (!Array.isArray(video) || video.length < 2) return spec
  const [first, ...rest] = video
  return { ...spec, video: [...rest, first] }
}
