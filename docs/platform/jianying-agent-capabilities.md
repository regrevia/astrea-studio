# Agent 通过平台能对剪映做什么

问题：不碰键盘鼠标的前提下，平台（`127.0.0.1:8899` 任务队列 + `integrations/mac-worker` 工具）对
本机剪映 11.6.0（build 490）到底能做哪些操作、卡在哪。以下每条都标了「怎么做到」和「实证到哪一步」，
没做到的写进 §4，不写成「支持」。

## 1. 能做的

| 能力 | 入口 | 怎么做到 | 前提 / 代价 | 实证 |
| --- | --- | --- | --- | --- |
| 生成剪映草稿 | `jobs.py` → `kind:"jianying"`；`jianying_engine.py` | 直接写 `draft_info.json`（VectCutAPI 模板字段）+ `studio_export_meta.json` 侧车 | 无 GUI；1080×1920 / 30fps / 单批 ≤2 条 | round 43/44 落盘并被 11.6 打开 |
| 打开指定草稿 | `export/open_draft.py`、`kind:"jianying_open"` | 首页 AX 树读 `HomePageDraftTitle:<名>` 卡片矩形 → 真实坐标双击 → 等 `MainWindowTitleBarExportBtn` | 剪映窗口必须在当前桌面；首页只渲染最新 8 张卡 | round 43 真机打开成功 |
| 把旧草稿顶到首页 | `export/draft_listing.py surface` | 改 `root_meta_info.json` 里该条 `tm_draft_modified` → 重启剪映 | 会动草稿索引（自动备份，可 `restore`） | round 43 实证：只改草稿目录里的 meta 无效 |
| 关闭/重启剪映 | `open_draft.py restart_app` | `osascript quit` + 重新 `open -a` | 会打断人正在剪的会话，需说明 | round 43/44 使用 |
| 驱动导出面板 | `export/jianying_export.py` | AX 读面板控件（`ExportOkBtn`/`ExportFileNameInput`/`ExportPathInput`…）→ 三级通道阶梯 `ax → pid → hid`，用 `verify()` 判有效 | **草稿必须已打开**；面板是 Qt，AXPress 对部分控件无效 ⇒ 最终落到 `hid`（借真实指针）；空闲门禁不过直接 rc 9 | round 43 面板探针/参数回读通过；完整导出未跑通（见 §4） |
| 认领产物 | `export/jianying_export.py --claim <路径>` | ffprobe 校验时长/画幅/帧率 → 落库 + 台账 | 只读认领；必须显式给路径、`--job-id`、`--revision`、`--platform`，拒绝「目录里最新文件」，参数缺一即 `rc=2` | 历史轮次 12/12；本轮真机：手动导出后认领 rc 0（11.215s / 1080×1920），入库并进交付清单 |
| 加密草稿的自救 | `export/plain_guard.py` | 打开前存明文快照；回写成密文后先备份密文再写回明文；`status` 报 `stale`（快照后明文又改过）与 `changedSinceSnapshot` | 只对「手里还有明文」的草稿有效；密文状态不算过期（那正是快照存在的理由） | round 45 真机：识别 r1 为密文、对明文 r1 存快照成功；本轮补离线用例 |
| 平台侧发布 | `worker.py` → `publish_sau` | social-auto-upload 驱动浏览器发布并回读 `remotePostId` | 账号已登录、发布内容齐全、**必须人工点头** | 泳道探针 4/4 通过；真发未做 |
| 探针/巡检 | `kind:"probe"`、`tools/check_all.py` | AX 窗口/控件探针、10 行离线+真机自检 | 无 | round 45 `--offline` 10/10 |

## 2. 靠的是哪几条通道（为什么只能做到这些）

- **AX（Accessibility）**：Qt 自绘控件能读 id/矩形，`AXPress` 对一部分按钮无效（详情面板、导出按钮
  历史上都要退回真实点击）。
- **`pid` 通道**：向剪映进程投递事件的尝试，面板类控件上实测无效。
- **`hid`（真实指针/键盘）**：本机唯一对导出面板全有效的通道（`input_tiers.py` 阶梯最后一级）。
  代价是借用户的指针——所以有 `ax_tool idle` 空闲门禁：用户一动键鼠这轮就 rc 9 退出。
- **CEF 里的首页**：Chromium 121 渲染，AX 只暴露可见的 8 张卡（`HomePageDraftTitle:`）；CDP
  端口 9222/9223/9229/8315 全关，滚轮注入（`ax_tool scroll` 的 `.line`）也滚不动它 ⇒ 首页
  既不能读全量草稿，也不能翻页。

## 3. 一次完整链路现在长什么样

```
jianying 任务 → 明文草稿落盘 → plain_guard 快照 → jianying_open 打开（首页双击；起进程前
   worker 再走一次 ensure_plain，失败只记日志不拦打开）→ 导出面板出现
   → （导出这一步目前由人点，见 §4）→ --claim 认领产线
   → publish 泳道（人工点头后）→ 回读 remotePostId
```

## 4. 现在还做不到（不要在外面说成能做）

| 缺口 | 现状 | 影响 |
| --- | --- | --- |
| 无人值守导出 | `export` 分支参数设置+面板探针已通，整段导出没在一次运行里跑到产物落盘；`allowGui=true` 从未实证 | 「从生成到成片」最后一跳仍需人点一次导出 |
| 读加密草稿时间线 | 11.6 回写后 `draft_info.json` 是密文；解密方案已评估并放弃（见 `jianying-version-adoption.md` §4） | 已被回写的草稿不能再被引擎增量修改，只能重渲染或从快照还原 |
| 首页全量草稿 | 只暴露 8 张可见卡，无 CDP、滚轮无效 | 草稿多了只能靠顶时间戳 + 重启来把目标挪进第一屏 |
| 批量 | 单批 ≤2 条 | 多草稿场景要排队 |
| 无窗口运行 | 剪映没有 CLI 导出参数，全部依赖 GUI | 必须有个登录着的桌面会话 |
| 跨社交平台发布差异 | 抖音/DOM 选择器随站点改版 | 发布泳道要定期跑探针 |

## 5. 与人协作的边界

- 借指针前先看 `ax_tool idle`；人在忙就等，不抢焦点。
- 「导出」这一个点击目前归人：平台不替人按，也不在人没授权时关/杀剪映。
- 任何会改草稿索引、重启剪映、发布到平台的动作，先在对话里说清代价再做。
