import { describe, expect, it } from 'vitest'

import { buildTimelineSpec, mirrorKeyframes, TimelineCanvasPreset } from './timeline-spec'

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

  it('竖屏 factcard 脚本原样透传：素材由 worker 现场生成', () => {
    const vertical = {
      engine: 'timeline',
      mode: 'vertical_short',
      template: 'factcard',
      topic: 'KV Cache',
      points: ['是什么::留住算过的 Key 和 Value'],
    }

    expect(buildTimelineSpec(vertical)).toBe(vertical)
  })

  it('竖屏 factcard 素材比屏数多时直接报错，不静默丢行', () => {
    const vertical = {
      engine: 'timeline',
      mode: 'vertical_short',
      topic: 'KV Cache',
      points: ['一::甲', '二::乙'],
      materials: ['pack:demo/a.png', 'pack:demo/b.png', 'pack:demo/c.png'],
    }

    expect(() => buildTimelineSpec(vertical)).toThrow('素材 3 行多于 2 屏，第 3 行起不会进片')
  })

  it('竖屏 factcard 素材与屏数相等时正常透传', () => {
    const vertical = {
      engine: 'timeline',
      mode: 'vertical_short',
      topic: 'KV Cache',
      points: ['一::甲', '二::乙'],
      materials: ['pack:demo/a.png', 'pack:demo/b.png'],
    }

    expect(buildTimelineSpec(vertical)).toBe(vertical)
  })

  it('竖屏 bgm 按拍点转成 music，音量/节奏原样带给渲染', () => {
    const vertical: Record<string, unknown> = {
      engine: 'timeline',
      mode: 'vertical_short',
      topic: 'KV Cache',
      points: ['一::甲', '二::乙'],
      bgm: { src: '/tmp/bgm.mp3', volume: 0.4, bpm: 120, offset: 0.5, pulse: { amount: 0.06 } },
    }

    const out = buildTimelineSpec(vertical)

    expect(out['music']).toEqual({
      src: '/tmp/bgm.mp3',
      volume: 0.4,
      bpm: 120,
      offset: 0.5,
      pulse: { amount: 0.06 },
    })
  })

  it('竖屏 bgm 缺 src 时报错', () => {
    expect(() =>
      buildTimelineSpec({
        engine: 'timeline',
        mode: 'vertical_short',
        points: ['一::甲'],
        bgm: { volume: 0.4 },
      }),
    ).toThrow('背景音乐缺少 src')
  })

  it('竖屏 beats 不递增时报错', () => {
    expect(() =>
      buildTimelineSpec({
        engine: 'timeline',
        mode: 'vertical_short',
        points: ['一::甲'],
        bgm: { src: '/tmp/bgm.mp3', beats: [0.5, 0.5] },
      }),
    ).toThrow('beats 必须严格递增')
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

describe('mirrorKeyframes 同题多版的运镜镜像', () => {
  it('每段关键帧倒序，推镜变拉镜', () => {
    const spec = {
      video: [
        { src: 'a.mp4', keyframes: [{ at: 0, zoom: 1 }, { at: 6, zoom: 1.12 }] },
        { src: 'b.mp4', keyframes: [{ at: 0, zoom: 1.14 }, { at: 10, zoom: 1 }] },
      ],
    }

    const mirrored = mirrorKeyframes(spec as Record<string, unknown>) as typeof spec

    expect(mirrored.video[0].keyframes).toEqual([{ at: 0, zoom: 1.12 }, { at: 6, zoom: 1 }])
    expect(mirrored.video[1].keyframes).toEqual([{ at: 0, zoom: 1 }, { at: 10, zoom: 1.14 }])
    // 原稿不被就地改掉：批量里第 1 条还要用原序
    expect(spec.video[0].keyframes[0].zoom).toBe(1)
  })

  it('没有关键帧的片段原样保留，时长等其它字段不受影响', () => {
    const spec = { video: [{ src: 'still.png', in: 0, out: 3 }], music: { src: 'bgm.mp3' } }

    const mirrored = mirrorKeyframes(spec as Record<string, unknown>)

    expect(mirrored['video']).toEqual(spec.video)
    expect(mirrored['music']).toEqual(spec.music)
  })
})
