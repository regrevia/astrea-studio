---
name: creator-studio
description: >
  把选题、文案、分镜表变成能交付的竖屏成片（1080×1920），并在同一套流程里
  重渲、查任务、把分镜表转成可编辑的时间轴草稿、校验交付盘上的 mp4。
  当用户说「出片 / 做条短视频 / 把这份分镜做成视频 / 重渲这版 / 查任务到哪了 /
  校验交付盘」时用它。只有文案（topic/hook/points/cta）走 short；已经算好时间轴的
  走 timeline；写不动或不想动 ffmpeg 的时候也用它，别自己拼命令行。
metadata:
  plugin-version: "0.1.0"
---

# Creator Studio 出片

本机的一条出片链路：能力服务 `127.0.0.1:8899` 收任务，worker 渲染，成片落到交付目录。
工具面是八个 `studio_*` 动作，走 stdio MCP；模型不需要拼 `curl`、不需要轮询。

## 执行契约

- 读完这份文件再动手。**执行**它，不要把命令复述给用户让他自己去跑。
- 这是一次性 runbook：不要安装它、不要持久化它、不要拿它去改用户仓库。
- 临时文件放一个本次新建的唯一 temp 目录；除本插件目录和 `~/.codex/config.toml` 外，
  不写用户的工作区。
- 要联网、要写工作区外、要删旧安装之前先征求同意；一次只问一件事。
- 用宿主自带的短命令完成步骤，不写安装器、不写 helper 脚本、不做顺手重构。
- 用户用什么语言提问，就用什么语言回答。
- **安装、自检、首次出片是三件不同的事**，分别报状态；自检没过之前，不许说出片链路可用。

## 版本与来源

- 版本 `0.1.0`，与 `.codex-plugin/plugin.json`、本文件 `metadata.plugin-version` 一致。
- 来源是本仓库的本地 marketplace：`<repo>/plugins/codex/`，没有远程包、没有 CDN。
- 依赖只有本机能力服务；它不在线时，工具会明确回 `isError: true` 和原因。

## 安装

四个宿主共用同一个服务器：`/Users/apple/CreatorStudio/runtime/integrations/mac-worker/mcp/studio_mcp.py`
（`scripts/sync_runtime.sh` 同步出来的运行态拷贝，路径不随 git 分支变）。每个宿主只写自己那张清单：

| 宿主 | 清单在哪 | 怎么装 |
|---|---|---|
| Codex | `plugins/codex/creator-studio/`（`.codex-plugin/plugin.json` + `.mcp.json`） | `codex plugin marketplace add <repo>/plugins/codex` → `codex plugin add creator-studio@creator-studio` |
| Claude Code | `plugins/codex/.claude-plugin/marketplace.json` + `creator-studio/.mcp.json` | `claude plugin marketplace add <repo>/plugins/codex` → `claude plugin install creator-studio@creator-studio` |
| WorkBuddy | `~/.workbuddy/mcp.json` 里的一条 `mcpServers` 记录 | 手写 JSON（宿主没有 install 命令），改前先备份 |
| DeepSeek Harness | `plugins/dsh/`（纯配置 bundle，`cordis.patch.yml` 插 `@deepseek-ai/dsh-mcp-client`） | `plugin_manager action=install_bundle target=<repo>/plugins/dsh` |

- 服务器名：Codex / Claude / WorkBuddy 用 `studio`，DSH 用 `creator_studio`（`serverName`）。
  别叫 `creator-studio`：配置里已有 `[mcp_servers.creator_studio]`，归一化后撞名，插件那条会被静默丢掉。
- `.mcp.json` 写**绝对路径、不写 `cwd`**：宿主进程的 cwd 是会话目录不是插件根，相对路径会
  `can't open file` 秒退且不报错。代价是这些清单跟着这台机器走，换机要改路径。
- 模型侧的命名空间：Codex `mcp__studio`，Claude `plugin:creator-studio:studio`，WorkBuddy 由它拼前缀，
  DSH `mcp__creator_studio__studio_*`。
- 装完只报告「已安装 0.1.0」。这一步不算可用。

确认挂上了，用不看模型脸色的命令：`codex mcp get studio`（`enabled: true`；`cwd: -` 是正常的，因为参数是绝对路径）、
`claude mcp list`（`plugin:creator-studio:studio ... ✔ Connected`）、`~/.workbuddy/mcp.json` 过一遍
`python3 -m json.tool`、DSH 里 `Tool.listTools` 能看到八个 `mcp__creator_studio__*`。

工具面里**故意没有发布/上传**：上传平台、选封面、点发布必须人工做，不要绕过去找别的工具。

非交互跑（`codex exec`）时 MCP 调用受审批策略管：策略是 `never` 会回
`MCP tool call requires approval`，这不是插件坏了；交互式 TUI 里点允许，或显式放宽审批再跑。

## 自检（相当于 CapCut 的「授权」那一步）

我们没有账号、没有 OAuth，需要确认的是「本机出片链路在不在」。调用 `studio_ready`：

- `isError: false` 且 `healthz.ok` 为真 ⇒ 自检 `complete`，可以往下做出片。
- `isError: true` ⇒ 服务没起来。让用户在仓库里跑一次 `./bin/studio ready`，把输出贴回来，
  按它指出的那一项修；这是自检 `pending`。
- 起来了但缺 ffmpeg/字体/交付盘 ⇒ 自检 `incomplete`，说清缺哪一项、下一步做什么。
- `/Volumes/US202` 交付盘的「大块读」✗ 是这块盘的已知缺陷，**不要**因此重渲：照常出片，
  校验一律走 `studio_verify`（16 KB 块 + `F_NOCACHE`）。`shasum` / `cp` / `ffmpeg` 在这块盘上下不了结论。

## 首次使用

按用户手上有什么选一条路：

1. **只有文案**（选题、口播稿、几个要点）→ `studio_submit kind="short"`，payload 带
   `topic/hook/points/cta`（字段看仓库 `AGENTS.md` §2 和 `deploy/specs/`），`wait=true`。
   引擎自己排时间轴、配音、出卡面、渲染，一条命令 1~2 分钟。
2. **有分镜表**（媒体写作台的 `production.json` / `state.json`，或模型自己写的
   `shots[]`：`voice`/`seconds`/`shot`/`src`）→ `studio_shots`。它只算时间轴、先不渲染：
   产物是 `spec.json`（明文可改）+ `plan.md`（为什么这么排）。
   加 `render=true` 才顺带出一条预览片。
3. **已经有一版成片要重渲 / 查状态 / 要交付** → `studio_submit kind="timeline"`（贴 spec 或
   `specPath`）/ `studio_jobs` + `studio_wait` / `studio_verify`。

关键的一点：**交付的是可编辑草稿**。`spec.json` 就是那份草稿——改一句台词、换一张图、
调一个时长，然后用 `studio_submit kind="timeline"` 重渲一遍，十几秒到一分钟。成片只是这次
渲染的快照，不是终点。这等于 CapCut 里「先生成草稿，人再收尾」，不要把第一次成片当成定稿。

## 收尾报告

固定用词，三行以内：

- 安装：`已安装 0.1.0` / `未安装`（附卡在哪一步）
- 自检：`complete` / `pending` / `incomplete`（附唯一剩下的那一步）
- 首次出片：`已出片 <路径> <时长>` / `草稿在 <spec.json 路径>，改完可重渲` / `失败：<原因>`

没到 `complete` 不许说能用；`pending`/`incomplete` 必须给出下一步，而不是「请重试」。

## 已知边界

- 服务器名固定为 `studio`（`mcp/studio_mcp.py` 的 `SERVER_NAME` 仍是 `creator-studio`，那是自报身份）。
  不要改回 `creator-studio`：它和配置里已存在的 `[mcp_servers.creator_studio]` 归一化后撞名，
  插件这条会被静默丢掉，同样表现成「工具全无」。
- `short` 的 `timeline` 是原始透传，不会替你把文案展开成时间轴；把文案按 `timeline` 提交会得到
  `spec 错误: video 至少要有 1 段`，这不是 bug。
- 字幕折行由产出方预先切好（时间轴 `text` 只认 `\n`），一行 13 字、最多 3 行；超了要把镜头拆开。
- 每个镜头必须带 `voice`；没旁白的镜头直接拒绝，不去猜。
- 口播/文案规则只有仓库里 `tools/promo/script_gen.py` 的 `COPY_RULES` 一处，这里不重复、不新抄一份。
- 出片失败先看任务里的 `error`（引擎日志最后几行）；还不够再看 `~/CreatorStudio/state/jobs/<id>/log.txt`。
