import { describe, expect, it } from 'vitest'

import { buildTimelineSpec, TimelineCanvasPreset } from './timeline-spec'

const FRIENDLY_INPUT = {
  engine: 'timeline',
  mode: 'script',
  assetBase: '/tmp/demo_c/asset',
  materials: ['a.mp4', 'b.mp4'],
  script: ['第一句', '第二句'],
  canvas: TimelineCanvasPreset.PORTRAIT,
  clipSeconds: 3,
  platforms: ['douyin', 'bilibili'],
  fit: 'blur',
  bgm: { src: 'bgm.m4a', volume: 0.35 },
}

describe('buildTimelineSpec', () => {
  it('把友好输入展开成时间轴规格', () => {
    const expanded = buildTimelineSpec({ ...FRIENDLY_INPUT })

    expect(expanded.engine).toBe('timeline')
    expect(expanded.assetBase).toBe('/tmp/demo_c/asset')
    expect(expanded.platforms).toEqual(['douyin', 'bilibili'])
    expect(expanded.fit).toBe('blur')
    expect(expanded.timeline).toEqual({
      canvas: { width: 1080, height: 1920, fps: 30 },
      video: [
        { src: 'a.mp4', in: 0, out: 3 },
        { src: 'b.mp4', in: 0, out: 3 },
      ],
      text: [
        {
          text: '第一句',
          start: 0.2,
          end: 2.8,
          size: 0.052,
          y: 0.86,
          color: '#FFFFFF',
          stroke: 4,
          box: '#00000066',
        },
        {
          text: '第二句',
          start: 3.2,
          end: 5.8,
          size: 0.052,
          y: 0.86,
          color: '#FFFFFF',
          stroke: 4,
          box: '#00000066',
        },
      ],
      audio: [{ src: 'bgm.m4a', start: 0, volume: 0.35, loop: true }],
    })
  })

  it('保留原始友好输入用于审计', () => {
    const expanded = buildTimelineSpec({ ...FRIENDLY_INPUT })

    expect(expanded.input).toEqual(FRIENDLY_INPUT)
  })

  it('文案比素材多时循环复用素材', () => {
    const expanded = buildTimelineSpec({
      ...FRIENDLY_INPUT,
      materials: ['a.mp4', 'b.mp4'],
      script: ['一', '二', '三'],
      bgm: undefined,
    })

    expect((expanded.timeline as Record<string, unknown>).video).toEqual([
      { src: 'a.mp4', in: 0, out: 3 },
      { src: 'b.mp4', in: 0, out: 3 },
      { src: 'a.mp4', in: 0, out: 3 },
    ])
    expect((expanded.timeline as Record<string, unknown>).audio).toEqual([])
  })

  it('素材比文案多时多出来的切片没有字幕', () => {
    const expanded = buildTimelineSpec({
      ...FRIENDLY_INPUT,
      materials: ['a.mp4', 'b.mp4', 'c.mp4'],
      script: ['只有一句'],
      bgm: undefined,
    })

    expect((expanded.timeline as Record<string, unknown>).video).toHaveLength(3)
    expect((expanded.timeline as Record<string, unknown>).text).toHaveLength(1)
  })

  it('横屏与方屏使用各自的画幅预设', () => {
    const landscape = buildTimelineSpec({ ...FRIENDLY_INPUT, canvas: TimelineCanvasPreset.LANDSCAPE })
    const square = buildTimelineSpec({ ...FRIENDLY_INPUT, canvas: TimelineCanvasPreset.SQUARE })

    expect((landscape.timeline as Record<string, unknown>).canvas).toEqual({ width: 1920, height: 1080, fps: 30 })
    expect((square.timeline as Record<string, unknown>).canvas).toEqual({ width: 1080, height: 1080, fps: 30 })
  })

  it('自定义切片时长驱动视频与字幕时序', () => {
    const expanded = buildTimelineSpec({ ...FRIENDLY_INPUT, clipSeconds: 2 })
    const timeline = expanded.timeline as Record<string, unknown>

    expect(timeline.video).toEqual([
      { src: 'a.mp4', in: 0, out: 2 },
      { src: 'b.mp4', in: 0, out: 2 },
    ])
    expect((timeline.text as Array<Record<string, unknown>>)[1]).toMatchObject({ start: 2.2, end: 3.8 })
  })

  it('缺省切片时长回落为 3 秒', () => {
    const expanded = buildTimelineSpec({ ...FRIENDLY_INPUT, clipSeconds: undefined })
    const timeline = expanded.timeline as Record<string, unknown>

    expect((timeline.video as Array<Record<string, unknown>>)[0].out).toBe(3)
  })

  it('高级模式已带 timeline 时原样透传', () => {
    const advanced = {
      engine: 'timeline',
      assetBase: '/tmp/demo_c/asset',
      timeline: { canvas: { width: 720, height: 1280, fps: 24 }, video: [{ src: '/tmp/a.mp4', in: 0, out: 5 }] },
    }

    expect(buildTimelineSpec(advanced)).toBe(advanced)
  })

  it('非 timeline 引擎原样透传', () => {
    const slides = { engine: 'slides', theme: 'dark' }

    expect(buildTimelineSpec(slides)).toBe(slides)
  })

  it('缺素材时报错', () => {
    expect(() => buildTimelineSpec({ ...FRIENDLY_INPUT, materials: [] })).toThrow('时间轴规格缺少素材')
  })

  it('缺文案时报错', () => {
    expect(() => buildTimelineSpec({ ...FRIENDLY_INPUT, script: ['  '] })).toThrow('时间轴规格缺少文案')
  })

  it('画幅不支持时报错', () => {
    expect(() => buildTimelineSpec({ ...FRIENDLY_INPUT, canvas: 'ultrawide' })).toThrow('时间轴画幅不支持')
  })

  it('切片时长小于 1 秒时报错', () => {
    expect(() => buildTimelineSpec({ ...FRIENDLY_INPUT, clipSeconds: 0 })).toThrow('时间轴切片时长不能小于 1 秒')
  })

  it('相对路径素材缺 assetBase 时报错', () => {
    expect(() => buildTimelineSpec({ ...FRIENDLY_INPUT, assetBase: '' })).toThrow('规格缺少素材根目录 assetBase')
  })

  it('素材是绝对路径时可以不填 assetBase', () => {
    const expanded = buildTimelineSpec({
      ...FRIENDLY_INPUT,
      assetBase: '',
      materials: ['/tmp/demo_c/asset/a.mp4'],
      bgm: { src: '/tmp/demo_c/asset/bgm.m4a' },
    })

    expect(expanded.assetBase).toBeUndefined()
    expect((expanded.timeline as Record<string, unknown>).audio).toEqual([
      { src: '/tmp/demo_c/asset/bgm.m4a', start: 0, volume: 0.35, loop: true },
    ])
  })
})
