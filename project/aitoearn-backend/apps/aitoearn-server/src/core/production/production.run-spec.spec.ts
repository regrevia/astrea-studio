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
})
