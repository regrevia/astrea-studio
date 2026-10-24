# AssistantPanel（AI 指挥台）

`/studio` 顶部的对话式入口：用户说一句人话（查资料 → 做片 → 改片 → 导出 → 发布），
后端把流水线拆成若干步骤，前端把每一步渲染成状态卡片贴在对话里。

本轮只做「UI + API client」，后端接口由 `production/assistant/*` 提供，未上线时面板降级为
「指挥台后端未就绪」，不影响下面现有的平台矩阵、配音音色与新建时间轴任务。

## 文件

| 文件 | 职责 |
| --- | --- |
| `index.tsx` | 面板入口：会话恢复、乐观发送、错误提示、会话切新 |
| `types.ts` | 面板私有视图类型（快捷提示 key、props） |
| `utils.ts` | localStorage 会话 id 读写、快捷提示顺序、临时消息 id |
| `components/AssistantMessageItem/index.tsx` | 单条消息 + 步骤状态卡片 |
| `README.md` | 本文件 |

接口与方法在 `src/api/production/`：`AssistantMessage` / `AssistantAction` 等类型在
`production.types.ts`，三个方法在 `production.api.ts`。

## 交互约定

- 会话 id 存 `localStorage['studio.assistant.sessionId']`，刷新后先 `GET` 恢复；
  服务端不认这个 id 时清掉并静默新建，不把用户卡在报错上。
- 发送是乐观的：立刻插入用户消息 + 一条 `running` 占位回复，真实 `reply` 到了整条替换。
- Enter 发送、Shift + Enter 换行；输入法组合态的回车不触发发送。
- 空态的 4 条快捷提示只填进输入框，不自动发送（用户通常要先改品牌名）。
- 列表内部滚动（`scrollTop`），不用 `scrollIntoView`，避免整页跳动。

## 失败处理

接口统一走 `@/utils/request` 的 `http`，三个方法都传 `silent: true`，避免后端未就绪时
弹全局网络错误 toast：

- 返回 `null`（接口 404 / 服务没起）→ 顶部灰色横幅「指挥台后端未就绪」；
- `code !== 0` → 红色横幅，内容为后端 `message`（发送失败会带 `{{message}}`）；
- 两种都保留用户已输入的历史消息，不静默吞掉。

## 复用说明

没有复用 `src/components/Chat/*`：那套组件绑定 `IActionCard`、agent store 与 SSE 会话模型，
而指挥台的契约只有 `messages + actions` 三个字段，强行复用会把两套状态耦合在一起。
这里按仓库优先级用 shadcn 基础组件（`Card` / `Button` / `Textarea` / `Skeleton`）拼装。

## 新增规则

- 步骤类型新增时，同步 `AssistantActionType`、`ACTION_ICONS` 与 `studio.json` 的
  `assistant.action.*`；不要在组件里写死中文标签。
- 所有文案走 `studio` namespace（`locales/{zh-CN,en,ja,de,fr,ko}/studio.json`），颜色只用主题 token。
- 再加大块 UI 时按目录规范下沉到 `components/<子组件>/`，`index.tsx` 保持 300 行以内。
