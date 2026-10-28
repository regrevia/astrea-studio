import { describe, expect, it } from 'vitest'

import { applyRunBatchVariant, applyVerticalShortVariant, buildTimelineSpec, mirrorKeyframes, rotateOpening, RunBatchVariant, TimelineCanvasPreset, varyHook } from './timeline-spec'

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

describe('rotateOpening 同题多版的换开场', () => {
  it('首段挪到最后，镜头数与总时长不变', () => {
    const spec = { video: [{ src: 'a.mp4' }, { src: 'b.mp4' }, { src: 'c.mp4' }] }

    const rotated = rotateOpening(spec as Record<string, unknown>) as typeof spec

    expect(rotated.video.map(v => v.src)).toEqual(['b.mp4', 'c.mp4', 'a.mp4'])
    expect(spec.video[0].src).toBe('a.mp4')
  })

  it('单段素材不动：换开场没有意义', () => {
    const spec = { video: [{ src: 'only.mp4' }] }

    expect(rotateOpening(spec as Record<string, unknown>)['video']).toEqual(spec.video)
  })
})

describe('varyHook 同题多版的钩子轮换', () => {
  it('按条取 hookVariants，位置与其它字段不动，且变体清单不落到渲染规格里', () => {
    const spec = {
      text: [
        { text: '默认钩子', start: 0.4, end: 3.2, y: 0.86, hookVariants: ['钩子A', '钩子B', '钩子C'] },
        { text: '卖点字幕', start: 3.2, end: 8 },
      ],
    }

    const second = varyHook(spec as Record<string, unknown>, 1) as typeof spec
    const first = varyHook(spec as Record<string, unknown>, 0) as typeof spec

    expect(second.text[0]).toEqual({ text: '钩子B', start: 0.4, end: 3.2, y: 0.86 })
    expect(first.text[0]['text']).toBe('钩子A')
    expect(second.text[1]).toEqual({ text: '卖点字幕', start: 3.2, end: 8 })
  })

  it('没有 hookVariants 或没有字幕时原样返回', () => {
    const plain = { text: [{ text: '就一句', start: 0, end: 2 }] }
    const noText = { video: [] }

    expect(varyHook(plain as Record<string, unknown>, 2)).toBe(plain)
    expect(varyHook(noText as Record<string, unknown>, 2)).toBe(noText)
  })
})

describe('applyRunBatchVariant 选择分版维度', () => {
  const spec = {
    video: [
      { src: 'a.mp4', keyframes: [{ at: 0, zoom: 1 }, { at: 3, zoom: 1.12 }] },
      { src: 'b.mp4', keyframes: [{ at: 0, zoom: 1.1 }, { at: 4, zoom: 1 }] },
    ],
    text: [{ text: '原钩子', start: 0.5, end: 3, hookVariants: ['A', 'B', 'C'] }],
  }
  const vars = ['mirror', 'opening', 'hook'] as RunBatchVariant[]

  it('第 1 条永远原稿，不管选了哪几维', () => {
    expect(applyRunBatchVariant(spec as Record<string, unknown>, 0, vars)).toBe(spec)
  })

  it('只选 mirror 时开场与钩子保持原样，只有 zoom 倒序', () => {
    const out = applyRunBatchVariant(spec as Record<string, unknown>, 1, ['mirror']) as typeof spec
    expect(out.video.map(v => v.src)).toEqual(['a.mp4', 'b.mp4'])
    expect(out.video[0].keyframes).toEqual([{ at: 0, zoom: 1.12 }, { at: 3, zoom: 1 }])
    expect(out.text[0]['text']).toBe('原钩子')
  })

  it('只选 opening 时按第几条轮转开场，且第 2 条就生效', () => {
    const second = applyRunBatchVariant(spec as Record<string, unknown>, 1, ['opening']) as typeof spec
    const third = applyRunBatchVariant(spec as Record<string, unknown>, 2, ['opening']) as typeof spec
    expect(second.video.map(v => v.src)).toEqual(['b.mp4', 'a.mp4'])
    expect(third.video.map(v => v.src)).toEqual(['a.mp4', 'b.mp4'])
    expect(second.video[0].keyframes).toEqual(spec.video[1].keyframes)
  })

  it('只选 hook 时按条取变体，其它维度不动', () => {
    const out = applyRunBatchVariant(spec as Record<string, unknown>, 2, ['hook']) as typeof spec
    expect(out.text[0]['text']).toBe('C')
    expect(out.video).toEqual(spec.video)
  })

  it('variants 为空时保持原稿', () => {
    expect(applyRunBatchVariant(spec as Record<string, unknown>, 1, [])).toBe(spec)
  })
})

describe('只勾一维时多条也要两两不同', () => {
  const spec = {
    video: [
      { src: 'a.mp4', keyframes: [{ at: 0, zoom: 1 }, { at: 3, zoom: 1.12 }] },
      { src: 'b.mp4', keyframes: [{ at: 0, zoom: 1.1 }, { at: 4, zoom: 1 }] },
    ],
    text: [{ text: 'A' }],
  }

  it('只选 mirror 时第 2 条镜像、第 3 条回原稿，不会两条一模一样', () => {
    const second = applyRunBatchVariant(spec as Record<string, unknown>, 1, ['mirror'])
    const third = applyRunBatchVariant(spec as Record<string, unknown>, 2, ['mirror'])
    expect(second['video']).not.toEqual(third['video'])
    expect(third['video']).toEqual(spec.video)
  })

  it('mirror 和别的维度一起选时每条都镜像', () => {
    const second = applyRunBatchVariant(spec as Record<string, unknown>, 1, ['mirror', 'hook'])
    const third = applyRunBatchVariant(spec as Record<string, unknown>, 2, ['mirror', 'hook'])
    expect(second['video']).toEqual(third['video'])
  })
})

describe('applyVerticalShortVariant 竖屏口播稿分版', () => {
  const spec = {
    mode: 'vertical_short',
    points: ['一', '二', '三'],
    materials: ['a.png', 'b.png', 'c.png'],
    hook: '原钩子',
    hook_variants: ['钩子甲', '钩子乙'],
  }

  it('第 1 条永远是原稿', () => {
    expect(applyVerticalShortVariant(spec, 0, ['opening', 'hook'])).toBe(spec)
  })

  it('opening 按条轮转 points，素材跟着屏一起转', () => {
    const out = applyVerticalShortVariant(spec, 1, ['opening'])
    expect(out['points']).toEqual(['二', '三', '一'])
    expect(out['materials']).toEqual(['b.png', 'c.png', 'a.png'])
    expect(out['hook']).toBe('原钩子')
  })

  it('hook 有变体文案时按条换，没有变体就保持原钩子', () => {
    expect(applyVerticalShortVariant(spec, 1, ['hook'])['hook']).toBe('钩子乙')
    const noVariants = { ...spec, hook_variants: [] }
    expect(applyVerticalShortVariant(noVariants, 1, ['hook'])['hook']).toBe('原钩子')
  })

  it('没选到的维度一个字都不动', () => {
    const out = applyVerticalShortVariant(spec, 2, ['opening'])
    expect(out['hook']).toBe('原钩子')
    expect(out['points']).toEqual(['三', '一', '二'])
  })
})
