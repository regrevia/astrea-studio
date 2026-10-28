# FactCardForm

Studio 的「干货卡一键出片」入口：填选题、开场钩子、要点和音色，一次提交就排一条 `factcard` 渲染任务，worker 出卡 + 配音后草稿直接进剪映草稿箱。

## 字段与请求体

组件只做表单校验和请求体拼装，版式、逐屏字幕、转场、关键帧全部由 worker 的 `factcard` 模块展开。

| 表单字段 | 请求体位置 | 说明 |
| --- | --- | --- |
| 选题 | `spec.topic` | 必填，同时作为开场字幕 |
| 系列名 | `spec.kicker` | 默认「大模型面试题」，卡面左上角 |
| 开场钩子 | `spec.hook` | 可留空，留空时不传，由 worker 用默认钩子 |
| 要点 | `spec.points` | 每条 `标题::说明`（英文双冒号），2-4 条 |
| 音色 | `spec.voice` | `sami:BV405_streaming`（女声，默认）/ `sami:BV411_streaming`（男声） |
| — | `spec.cta` | 表单不暴露，用 `DEFAULT_FACT_CARD_CTA` 固定收尾 |
| — | `title` | `系列名 · 选题` |
| — | `brief` | 给人看的摘要（选题、系列、要点条数、音色） |

请求走 `createFactCardRun`（`src/api/production/production.api.ts`），`projectId` 与「新建时间轴任务」同一套取法：用项目列表第一个。

## 多条之间怎么不同

条数选 2 或 3 时出现「多条之间怎么不同」，只有「换开场要点」一个开关（默认勾选）：第 1 条按原稿，后面几条按条号轮转 `spec.points`。后端 `applyVerticalShortVariant` 会把 `spec.narration.points` 一起轮转，卡面标题和这一屏的口播不会错位；两边长度对不上时不转口播，宁可少一维也不串行。

请求走 `POST /production/runs/batch` 并带 `variants: ['opening']`；不勾时传空数组，几条除了表演种子外文案完全一样。

## 渲染通道

后端按 `run.spec.engine` 派切渲染通道，只有 `jianying` 才会入剪映草稿队列；顶层 `engine` / `template` 会被接口 DTO 的 whitelist 丢掉，所以 `spec` 里也带了一份 `engine: 'jianying'` / `template: 'factcard'`。这段约束写在 `utils.ts` 的 `buildFactCardSpec` 注释里，改动前先看后端 `production.service.ts` 的 `enqueueRender`。

## 文件

- `index.tsx`：表单 UI、错误/成功提示、提交
- `utils.ts`：常量、`标题::说明` 解析、校验、`buildFactCardSpec` / `buildFactCardRunInput`
- `copyCheck.ts`：提交前的文案体检（模板词、句子节奏、卡面与口播重复），只返回命中项，不挡提交
- 文案：`src/app/i18n/locales/{zh-CN,en}/studio.json` 的 `factCard.*`
