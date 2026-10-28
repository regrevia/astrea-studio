# NewRunForm - 口播出片表单

Studio 面板里「填稿子就出片」的入口。用户给主题、一行一屏的口播稿、音色和平台，
提交时由 `utils.ts` 组装成 `timeline` 竖屏脚本 spec，渲染进程按稿子现场出卡面、
立绘和逐屏配音。用户不用写 JSON，也不用自己算时间轴。

## 结构

| 文件 | 作用 |
| --- | --- |
| `index.tsx` | 表单 UI、音色清单加载、提交与错误提示（`'use client'`） |
| `utils.ts` | 纯函数：口播稿解析、音色候选、时长估算、校验、spec 组装 |
| `components/NarrationRunResult/index.tsx` | 提交后的状态轮询、成片下载、失败原因与日志尾巴 |

## 口播稿格式

一行一屏，三段用 `::` 分隔：

```
标题::镜头注解::口播句
```

- 第一段上屏做卡面标题；第二段是这一屏的画面提示；第三段就是这一屏念的句子，直接进 TTS。
- 口播句里可以再出现 `::`，第三段之后的内容都算口播句。
- 首屏标题同时作开场钩子，末屏标题同时作收尾句；`kicker`（左上角系列名）取主题。

## 表单字段 → 请求体

提交 `POST production/runs`：

```json
{
  "projectId": "prj_xxx",
  "title": "Envoy 为什么能把链路看清",
  "brief": "Envoy 为什么能把链路看清",
  "spec": {
    "engine": "timeline",
    "mode": "vertical_short",
    "template": "factcard",
    "topic": "Envoy 为什么能把链路看清",
    "hook": "为什么都用 Envoy",
    "points": ["为什么都用 Envoy::镜头推近面板::Envoy 把服务发现、限流、灰度收进一层"],
    "cta": "不换代码就能接",
    "voice": "edge:zh-CN-XiaoxiaoNeural@rate=0.98",
    "kicker": "Envoy 为什么能把链路看清",
    "materials": ["pack:envoy/hero"],
    "platforms": ["douyin", "bilibili"]
  }
}
```

- `engine`/`mode`/`template` 是通道开关：`timeline` + `vertical_short` + `factcard` 才会走到逐屏配音分支。
- `voice` 是一个完整字符串，`@rate=0.98` 是渲染进程已支持的内联语速语法；SAMI 音色可选，但没有逐字时间，口型按句子估。
- 音色下拉优先读本机能力服务（`/inventory` 的 `voices.edge`），服务不在线时用兜底清单，不阻塞表单。
- `materials` 只在填了内容才传，前端只校验是不是绝对路径或 `pack:` 前缀，存在性由渲染进程判断。
- 素材包来自能力服务 `/inventory` 的 `materials.packs`：每张图渲染成「第 N 张」按钮，点一下往文本框追加一行
  `pack:<包>/<名字>`（再点一次撤掉），不用记包名；服务不在线时这一块不出现，仍可手写绝对路径。
- `platforms` 是勾选的发布平台，渲染进程按每个平台各出一版；一个都不勾只出母版。
- 时长上限是**提交前**的整片估算（3.4 字/秒，停顿标点每个 0.18 秒），超了直接拦住。渲染进程没有这个参数，所以不写进 spec。

## 成功后

`createRun` 只回 `runId`。表单把它交给父组件（`StudioOverview` 的 `onCreated`）接入面板轮询，
同时自己在下方渲染 `NarrationRunResult`：每 3 秒 `getRun` 到状态落定，给出母版和各平台成片链接；
失败时把 `run.lastError` 原文和最后 20 行任务日志摊开。

## 多条母版怎么错开

母版条数 > 1 时出现「多条之间怎么不同」，三个维度各管一件事（第 1 条永远按原稿）：

| 维度 | 竖屏口播稿（本表单） | `timeline` 直接给规格时 |
|---|---|---|
| 运镜方向 | 换渲染进程的表演种子：勾上每条手势/运镜按 `runId` 各演各的，取消则几条表演一致 | 倒序每段关键帧的 `zoom`（推镜变拉镜，时间点不动）；只勾这维时第 2 条镜像、第 3 条回原稿 |
| 开场镜头 | 按序号轮转 `points`，素材跟着屏一起转，换开场那屏 | 把首段素材挪到最后 |
| 开头字幕 | 按序号取 `spec.hook_variants` 里的一条（没给变体文案就不动） | 取 `text[0].hookVariants` 里的一条 |

提交时 `variants: ['mirror','opening','hook']` 的选中子集跟着请求走；后端把它们落到每条
`spec` 上，所以渲染出来的母版两两不同（实测三条 1080×1920 成片 md5 两两不同）。
