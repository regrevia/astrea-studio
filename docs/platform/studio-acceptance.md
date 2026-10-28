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

## 4. 本机环境问题不再判死任务

`failJob` 收到缺模块 / 缺文件 / 缺权限这类报错时，把 run 放回 `queued` 并补一条同 kind 的 job（最多 3 次），不再让用户看到一个「渲染失败」去手动重试；已出片或被取消的 run 不会被迟到的失败结果拉回队列。`production.service.spec.ts` 23 项通过，其中 3 项专门盯这条分支。

## 5. 一条命令重跑验收

```
node scripts/verify/e2e-promo.mjs --project <项目 ID>
```

脚本只走平台公开路径：`POST /api/production/runs` 起一条 `timeline/vertical_short` 任务 → 轮询 `GET .../runs/:id` 到终态 → 从 `output.videoUrl` 取回母版 → ffprobe 核对。核对项是「能不能直接发」的硬指标：**1080×1920**、**26–36 秒（约 30 秒）**、**有音轨**、`videoUrl` 必须存在。任一项不达标就以非零码退出，并把失败证据写成 `docs/platform/evidence/e2e-<tag>-failed.json`；通过则写 `e2e-<tag>-<runId>.json`。

最近一次跑（tag `round7b`，run `6ae14a9c3f3f9145994a22d8`）：

| 项 | 实测 |
| --- | --- |
| 分辨率 | 1080×1920 |
| 帧率 | 30/1 |
| 时长 | 28.69 s |
| 音轨 | 有 |
| 母版 | `/oss/studio/runs/6ae14a9c3f3f9145994a22d8/r1/master.mp4`（8,410,987 B） |

第一版稿子念下来 40.05 s，超出「约 30 秒」，把每屏口播压短后重跑才落到 28.69 s——说明时长是被真实配音驱动的，不是写死的。

## 6. 成片落到哪个草稿箱

制作台的项目选择器右侧新增「草稿箱」下拉：选中项目后可以直接把它绑到一个草稿箱（不选即后端默认箱），改动落到项目的 `materialGroupId` 上。后端 `attachMaterialForWorker` 早就按 `项目.materialGroupId → 用户默认分组` 的顺序解析，缺的是界面这一端——在此之前所有项目的 `materialGroupId` 都是空串。
## 7. 还没验收的

- 真机解锁后走 `allowGui: true` 的成功导出（面板代点 → `exported` + 真实 `videoPath`）。当前屏幕是锁的，属于人工前置条件。
- GUI 自动化的辅助功能授权（编译出的辅助工具尚未被授予 Accessibility）。
- 环境类失败自动重排目前只有单测证据，还没在一次真实渲染里注入失败验证过。

