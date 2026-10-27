/**
 * AssistantPanel 私有类型。
 * 会话、消息、步骤的接口模型来自 @/api/production/production.types，这里只放视图层类型。
 */

/** 空态快捷提示的 key 后缀，展示顺序由 utils 里的 ASSISTANT_QUICK_PROMPTS 决定。 */
import type { ProductionRun } from '@/api/production/production.types'

export type AssistantQuickPromptKey = 'news' | 'tweak' | 'export' | 'publish'

/** AssistantPanel 的 props。 */
export interface AssistantPanelProps {
  /** 外层附加样式（Studio 页用来留卡片间距）。 */
  className?: string
  /** 页面当前选中的项目；有值时指挥台的上下文就是它。 */
  projectId?: string
  projectName?: string
  /** 当前项目范围内的任务，用来显示阶段进度。 */
  runs?: ProductionRun[]
  /** 页面选中的任务；为空时取第一个在跑的。 */
  selectedRunId?: string
}
