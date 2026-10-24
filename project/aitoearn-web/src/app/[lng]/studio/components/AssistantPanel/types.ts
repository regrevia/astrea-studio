/**
 * AssistantPanel 私有类型。
 * 会话、消息、步骤的接口模型来自 @/api/production/production.types，这里只放视图层类型。
 */

/** 空态快捷提示的 key 后缀，展示顺序由 utils 里的 ASSISTANT_QUICK_PROMPTS 决定。 */
export type AssistantQuickPromptKey = 'news' | 'tweak' | 'export' | 'publish'

/** AssistantPanel 的 props。 */
export interface AssistantPanelProps {
  /** 外层附加样式（Studio 页用来留卡片间距）。 */
  className?: string
}
