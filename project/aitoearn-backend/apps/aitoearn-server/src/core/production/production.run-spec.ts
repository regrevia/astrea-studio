/**
 * 引擎派切归一化。
 *
 * 历史坑：调用方在顶层写 `engine: 'jianying'`，但 Zod 白名单把它剥掉，
 * 而 service 只读 `spec.engine`，任务于是静默落到 MoneyPrinterTurbo 通道，
 * 用户拿到一个和剪映无关的产物，且没有任何报错。
 *
 * 归一化后 `spec` 是唯一事实来源：顶层 engine/template 覆盖进 spec。
 */
export type RunSpecInput = {
  engine?: string
  template?: string
  spec?: Record<string, unknown> | null
}

export function normalizeRunSpec(input: RunSpecInput): Record<string, unknown> {
  const spec: Record<string, unknown> = { ...(input.spec ?? {}) }
  if (input.engine) spec['engine'] = input.engine
  if (input.template) spec['template'] = input.template
  return spec
}
