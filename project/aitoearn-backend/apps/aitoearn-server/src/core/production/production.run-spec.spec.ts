import { describe, expect, it } from 'vitest'
import { normalizeRunSpec } from './production.run-spec'

describe('normalizeRunSpec', () => {
  it('把顶层 engine/template 覆盖进 spec，避免图层派切静默落到 MPT', () => {
    const spec = normalizeRunSpec({
      engine: 'jianying',
      template: 'factcard',
      spec: { topic: 'KV Cache', engine: 'motion', template: 'old' },
    })
    expect(spec.engine).toBe('jianying')
    expect(spec.template).toBe('factcard')
    expect(spec.topic).toBe('KV Cache')
  })

  it('顶层缺省时保留 spec 里的旧写法', () => {
    const spec = normalizeRunSpec({ spec: { engine: 'slides' } })
    expect(spec.engine).toBe('slides')
  })

  it('没有 spec 也能用', () => {
    expect(normalizeRunSpec({ engine: 'timeline' })).toEqual({ engine: 'timeline' })
  })

  it('vertical_short 缺 engine 时反推 timeline，不再落到 MPT 报缺素材', () => {
    const spec = normalizeRunSpec({ spec: { mode: 'vertical_short', topic: '卡点' } })
    expect(spec.engine).toBe('timeline')
  })

  it('显式 engine 优先于 mode 反推', () => {
    const spec = normalizeRunSpec({ engine: 'jianying', spec: { mode: 'vertical_short' } })
    expect(spec.engine).toBe('jianying')
  })
})
