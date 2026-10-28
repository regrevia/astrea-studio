# 宣发工作台：验收记录

一条真实链路：建项目 → 建任务（剪映草稿）→ 改稿出新版本 → 导出。下面每条都附可复跑的证据；记录只写跑过的，不写推测。

## 1. 改稿出片真的改内容

任务 `6ae009d498849e9680314e0b`（项目 `6adc2161656a13996166bbfc`）通过 `reviseProductionRun` 打 `{spec: {hook: "AI 读完一篇长文的真实成本", kicker: "Envoy 实测 · 改稿"}}`：

- 返回 `revision: 2`、`status: queued`；任务落 `succeeded / done`，草稿目录为 `studio_6ae13f0f791bd5bc23ed991a__r2`。
- 第 1 版与第 2 版的卡面不同（`s00.png` 哈希 `3d8fec50…` → `cdbae022…`）。
- OCR 第 2 版首屏卡面读到「AI 读完一篇长文的真实成本」「Envoy 实测 · 改稿」；第 1 版同位置是「给 AI 一个链接，它只读到标题」。字幕轨两版一致——改的是钩子与眉标，念白没动，符合补丁范围。

判据：改稿不是重跑同一份稿子，卡面文字必须换。

## 2. 导出不撒谎

同一任务连发导出，机器状态不同则结果不同，且都不产生假成片：

| 次 | 条件 | job 结果 | run 状态 |
| --- | --- | --- | --- |
| 1–2 | 不带 `allowGui` | `awaiting_manual_export` / `export_needs_manual_gui` | `waiting_user`，无 `videoPath` |
| 3 | 缺 `subprocess` 导入 | `failed`（worker 异常） | `failed` |
| 4 | 辅助工具在 `/tmp` 不存在 | `failed`（`FileNotFoundError`） | `failed` |
| 5 | 屏幕锁着，无自检 | 挂 100 秒后被终止 | `failed`（rc=-15） |
| 6 | 屏幕锁着，有自检 | `succeeded` / `screen_locked` | `waiting_user` |
| 7 | 打上原因归一 | `succeeded` / `screen_locked` | `waiting_user`，`output.manualExportReason = screen_locked` |

第 6 次是关键：同样的环境，修好之后 3 秒内返回，而不是等 1800 秒超时。

## 3. 界面跟着后端说话

`http://127.0.0.1:8080/zh-CN/studio?projectId=6adc2161656a13996166bbfc&runId=6ae009d498849e9680314e0b` 上，该任务显示「等你决定」「等你在剪映导出」「屏幕锁着，解锁后再点导出。」，动作是「让这台 Mac 导出」「提修改」。没有成片时不显示播放器，也不出现 `awaiting_manual_export`、`export_queued` 这类枚举原文。

## 4. 还没验收的

- 真机解锁后走 `allowGui: true` 的成功导出（面板代点 → `exported` + 真实 `videoPath`）。当前屏幕是锁的，属于人工前置条件。
- GUI 自动化的辅助功能授权（编译出的辅助工具尚未被授予 Accessibility）。
- `worker` 遇到环境类异常（缺模块、缺文件）仍把任务记成 `failed`，应改为可重试。
