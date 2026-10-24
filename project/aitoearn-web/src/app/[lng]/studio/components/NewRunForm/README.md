# NewRunForm - 新建时间轴任务表单

Studio 面板里的「免手写 JSON」建任务入口。用户只填标题、文案、素材和画幅，
提交时由 `utils.ts` 组装成后端约定的友好 spec，后端的 timeline-spec builder
再展开成 worker 能直接渲染的 `timeline` 规格。

## 结构

| 文件 | 作用 |
| --- | --- |
| `index.tsx` | 表单 UI、提交与成功/失败提示（`'use client'`） |
| `utils.ts` | 纯函数：多行解析、轻校验、spec 组装、默认值常量 |

## 表单字段 → 请求体

提交 `POST production/runs`：

```json
{
  "projectId": "prj_xxx",
  "title": "三句文案 · 竖屏 · 抖音+B站",
  "spec": {
    "engine": "timeline",
    "mode": "script",
    "assetBase": "/private/tmp/demo_c/asset",
    "materials": ["a.mp4", "b.mp4"],
    "script": ["第一句文案", "第二句文案"],
    "canvas": "portrait",
    "clipSeconds": 3,
    "platforms": ["douyin", "bilibili"],
    "fit": "blur",
    "bgm": { "src": "bgm.m4a", "volume": 0.35 }
  }
}
```

- `clipSeconds` 固定 3 秒、`fit` 固定 `blur`，表单不暴露；需要更细的控制直接调后端 API。
- `bgm` 只在填了文件名时才传，音量留空回落到默认 0.35。
- `canvas` 只传朝向（`portrait`/`landscape`/`square`），像素尺寸由后端固定。
- 表单不拼 `timeline` 内部字段（画布/切片/字幕时序/音轨），避免前后端各写一份渲染器规格。

## 成功后

`createRun` 只回 `runId`，表单把它交给父组件（`StudioOverview` 的 `onCreated`），
父组件单独轮询 `getRun` 拿 `run.output.platforms`，因此「进行中」里能立刻看到新任务，
完成后每行还会显示各平台成片（平台名 + 画幅 + 体积）。
