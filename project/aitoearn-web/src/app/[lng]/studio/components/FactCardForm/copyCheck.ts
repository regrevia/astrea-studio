/**
 * 文案体检：出片前指出「像模板、没节奏、卡面剧透」的地方。
 * 出片后服务端还会再判一遍，这里只为少跑一次废片。
 * 只返回 code/scope/params，文字由工作室文案渲染。
 */

const TEMPLATE_PHRASES = [
  '首先', '其次', '再次', '最后一点', '综上所述', '总而言之', '总之',
  '众所周知', '不言而喻', '值得一提的是', '不得不说', '据悉', '据了解',
  '在当今', '随着', '的发展', '让我们一起', '接下来我们', '下面我们',
  '干货', '赋能', '抓手', '闭环', '生态位', '壁垒', '护城河', '玩法',
  '全方位', '多维度', '深度解析', '一文读懂', '建议收藏',
]

const EXPLAINER_PHRASES = [
  '也就是说', '换句话说', '简单来说', '简单讲', '其实', '实际上',
  '可以说', '某种程度上', '某种意义上', '换言之', '通俗地讲', '打个比方',
]

export type CopyWarnCode =
  | 'template_phrase'
  | 'explainer_tone'
  | 'flat_rhythm'
  | 'no_short_line'
  | 'long_line'
  | 'long_spoken'
  | 'point_body_long'
  | 'card_echoes_voice'

export type CopyScope = 'all' | 'hook' | 'topic' | 'cta' | 'points' | 'voice' | 'point' | 'spoken'

export interface CopyWarn {
  code: CopyWarnCode // ui-copy-ignore
  scope: CopyScope // ui-copy-ignore
  /** point / spoken 的序号，从 1 开始。 */
  index?: number // ui-copy-ignore
  params: Record<string, string | number> // ui-copy-ignore
}

const squash = (text: string) => (text || '').replace(/\s+/g, '')

function splitPoint(raw: string): [string, string, string] {
  const parts = String(raw || '').split('::').map(p => p.trim())
  if (parts.length >= 3)
    return [parts[0], parts[1], parts[2]]
  if (parts.length === 2)
    return [parts[0], parts[1], '']
  return ['', parts[0], '']
}

/** 两句像不像：按字对子算重合度，接近 Python 侧 SequenceMatcher 的 ratio。 */
export function textSimilarity(a: string, b: string): number {
  if (!a || !b)
    return 0
  if (a === b)
    return 1
  const grams = (s: string) => {
    const map = new Map<string, number>()
    for (let i = 0; i < s.length - 1; i++) {
      const g = s.slice(i, i + 2)
      map.set(g, (map.get(g) || 0) + 1)
    }
    return map
  }
  const ga = grams(a)
  const gb = grams(b)
  let hit = 0
  ga.forEach((n, g) => {
    const m = gb.get(g)
    if (m)
      hit += Math.min(n, m)
  })
  return (2 * hit) / (Math.max(a.length - 1, 1) + Math.max(b.length - 1, 1))
}

function sentences(texts: string[]): string[] {
  const out: string[] = []
  for (const t of texts) {
    if (!t)
      continue
    for (const part of String(t).split(/[。！？…；]/)) {
      const s = part.trim()
      if (s)
        out.push(s)
    }
  }
  return out
}

export function checkCopy(input: {
  topic?: string
  hook?: string
  points?: string[]
  cta?: string
  narration?: string[]
}): CopyWarn[] {
  const hook = squash(input.hook || '')
  const topic = squash(input.topic || '')
  const cta = squash(input.cta || '')
  const parsed = (input.points || []).map(splitPoint)
  const bodies = parsed.map(([title, body]) => squash(body || title))
  const spoken = (input.narration || []).map(squash).filter(Boolean)

  const scoped: Array<[CopyScope, string, number | undefined]> = [
    ['hook', hook, undefined],
    ['topic', topic, undefined],
    ['cta', cta, undefined],
    ['points', bodies.join(' '), undefined],
    ['voice', spoken.join(' '), undefined],
  ]

  const warns: CopyWarn[] = []
  const add = (w: CopyWarn) => {
    if (warns.some(x => x.code === w.code && x.scope === w.scope && x.index === w.index))
      return
    warns.push(w)
  }

  for (const [scope, text] of scoped) {
    if (!text)
      continue
    for (const phrase of TEMPLATE_PHRASES) {
      if (text.includes(phrase))
        add({ code: 'template_phrase', scope, params: { phrase } })
    }
    for (const phrase of EXPLAINER_PHRASES) {
      if (text.includes(phrase))
        add({ code: 'explainer_tone', scope, params: { phrase } })
    }
  }

  const list = sentences([hook, topic, cta, ...bodies, ...spoken])
  const lengths = list.map(s => s.length)
  if (lengths.length) {
    const min = Math.min(...lengths)
    const max = Math.max(...lengths)
    if (max - min < 5)
      add({ code: 'flat_rhythm', scope: 'all', params: { min, max } })
    if (min > 10)
      add({ code: 'no_short_line', scope: 'all', params: {} })
    const longs = list.filter(s => s.length > 26)
    if (longs.length)
      add({ code: 'long_line', scope: 'all', params: { count: longs.length, first: longs[0].slice(0, 16) } })
  }

  spoken.forEach((s, i) => {
    if (s.length > 28)
      add({ code: 'long_spoken', scope: 'spoken', index: i + 1, params: { count: s.length } })
  })

  parsed.forEach(([, body, said], i) => {
    const flatBody = squash(body)
    if (body && flatBody.length > 18)
      add({ code: 'point_body_long', scope: 'point', index: i + 1, params: { count: flatBody.length } })
    const flatSaid = squash(said)
    if (flatBody.length >= 4 && flatSaid.length >= 4) {
      if (flatBody.includes(flatSaid) || flatSaid.includes(flatBody) || textSimilarity(flatBody, flatSaid) > 0.5)
        add({ code: 'card_echoes_voice', scope: 'point', index: i + 1, params: {} })
    }
  })

  return warns
}
