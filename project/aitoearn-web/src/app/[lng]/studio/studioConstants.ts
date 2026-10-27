/**
 * Studio 页面本地常量与展示格式化。
 *
 * 平台中文名、画幅选项、成片体积格式化只在这里用得到，属于页面私有，
 * 不放 src/utils（全局工具目录只收跨页面复用能力）。
 */

import type { TimelineCanvas } from '@/api/production/production.types'

/** 平台 key → 中文名。成片产物与发布台账都按平台 key 展示。 */
export const PLATFORM_LABELS: Record<string, string> = {
  douyin: '抖音', // ui-copy-ignore
  bilibili: 'B站', // ui-copy-ignore
  kuaishou: '快手', // ui-copy-ignore
  xiaohongshu: '小红书', // ui-copy-ignore
  shipinhao: '视频号', // ui-copy-ignore
}

/** 新建任务可选的发布平台（顺序即表单里的展示顺序）。 */
export const PLATFORM_OPTIONS = ['douyin', 'bilibili', 'kuaishou', 'xiaohongshu', 'shipinhao'] as const

/** 默认勾选前两个平台：抖音、B站。 */
export const DEFAULT_PLATFORMS: string[] = ['douyin', 'bilibili']

/** 画幅选项。具体尺寸由后端按 canvas 展开，这里只做提示。 */
export const CANVAS_OPTIONS: Array<{ value: TimelineCanvas, label: string, hint: string }> = [
  { value: 'portrait', label: '竖屏', hint: '1080×1920' },
  { value: 'landscape', label: '横屏', hint: '1920×1080' },
  { value: 'square', label: '方形', hint: '1080×1080' },
]

/**
 * 字节数 → KiB 文本。
 *
 * 面板里体积只用于粗看成片大小，不需要精确单位换算；缺字段时显示破折号，
 * 避免 0 B 看起来像渲染失败。
 */
export function formatKib(bytes?: number) {
  if (!bytes || bytes <= 0)
    return '—'
  return `${Math.round(bytes / 1024)} KiB`
}
