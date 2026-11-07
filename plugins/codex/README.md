# plugins/ — creator-studio 的客户端插件（四个宿主）

**一个服务器，四张清单。** 服务器是 `integrations/mac-worker/mcp/studio_mcp.py`：零依赖、
只说 stdio JSON-RPC，把出片能力（`127.0.0.1:8899`）包成八个 `studio_*` 动作。
四个宿主各自只写自己那张清单，都指向同一个运行态路径
`~/CreatorStudio/runtime/integrations/mac-worker/mcp/studio_mcp.py`（`scripts/sync_runtime.sh` 同步，
不随 git 分支变）。

形状照本机已装的官方插件（`envoy@envoy` / CapCut 那套）抄，不自创：清单 + 动作面 + 说明书。

```
plugins/
  codex/                                # Codex 与 Claude Code 共用的插件根（同一份源码，两套清单）
    .agents/plugins/marketplace.json    # Codex 读的 marketplace 清单
    .claude-plugin/marketplace.json     # Claude Code 读的 marketplace 清单
    creator-studio/
      .codex-plugin/plugin.json         # Codex 清单：skills + mcpServers + interface
      .claude-plugin/plugin.json        # Claude 清单（同名插件，Claude 用这份元数据）
      .mcp.json                         # {"mcpServers": {...}}，绝对路径起服务器
      skills/creator-studio/SKILL.md    # runbook：装 → 自检 → 出片，分三段报状态
  dsh/                                  # DeepSeek Harness 的纯配置 bundle（无代码）
    package.json  cordis.patch.yml  locale/en.json  icon.svg
```

## 为什么是这个形状

模型要能把活干完，需要三样东西同时在场，缺一样都退化：

| 要素 | 在这里 | 缺了会怎样 |
|---|---|---|
| 清单 | `.codex-plugin/plugin.json` / `.claude-plugin/plugin.json` / `package.json` | 工具装了也没描述，模型不知道该什么时候用 |
| 动作面 | `.mcp.json` → `studio_mcp.py`（DSH 走 `dsh-mcp-client` 插同一个脚本） | 只能让模型自己拼 `curl`，错一次就中断 |
| 说明书 | `skills/creator-studio/SKILL.md` | 模型把安装步骤复述一遍就交差 |

## 四个宿主

| 宿主 | 清单 | 模型侧名字 |
|---|---|---|
| Codex | `creator-studio/.codex-plugin/plugin.json` + `.mcp.json` | `mcp__studio` |
| Claude Code | `.claude-plugin/marketplace.json` + `creator-studio/.mcp.json` | `plugin:creator-studio:studio` |
| WorkBuddy | `~/.workbuddy/mcp.json` 里一条 `mcpServers` 记录 | 宿主自己拼前缀 |
| DeepSeek Harness | `plugins/dsh/`（`cordis.patch.yml` 插 `@deepseek-ai/dsh-mcp-client`） | `mcp__creator_studio__*` |

```bash
# Codex（会写 ~/.codex/config.toml，先问人再执行）
codex plugin marketplace add "$PWD/plugins/codex"
codex plugin add creator-studio@creator-studio

# Claude Code（会写 ~/.claude/plugins/，先问人再执行）
claude plugin marketplace add "$PWD/plugins/codex"
claude plugin install creator-studio@creator-studio

# WorkBuddy：宿主没有 install 命令，手写一条；改前备份
cp ~/.workbuddy/mcp.json ~/.workbuddy/mcp.json.bak-$(date +%Y%m%d%H%M%S)
#   "creator-studio": {"command":"python3","args":["<绝对路径>/studio_mcp.py"],"defer_loading":true}

# DeepSeek Harness：纯配置 bundle，只经由 plugin_manager 写 profile
plugin_manager action=install_bundle target="$PWD/plugins/dsh"
```

## 验（都不看模型脸色）

```bash
python3 integrations/mac-worker/mcp/smoke.py   # 协议 + 8 个工具面 + 真跑一次桥接（不渲染）
codex mcp get studio                           # enabled: true；cwd: - 是正常的（参数是绝对路径）
codex mcp list                                 # 有一条 studio
claude mcp list                                # plugin:creator-studio:studio ... ✔ Connected
python3 -m json.tool ~/.workbuddy/mcp.json     # 能解析；列表里多出 creator-studio
```

DSH 那边没有命令行自检，看模型侧工具面：`Tool.listTools` 里应出现八个
`mcp__creator_studio__studio_*`（装好后甚至当前会话就能看到，不必重启）。

冒烟只证明「服务器自己没问题」，不证明「某个宿主把它挂上了」。后者用上面那几条。

## 三条 2026-11 实测

- **`.mcp.json` 用绝对路径、不写 `cwd`。** 宿主给 MCP 子进程的 cwd 是**会话目录**不是插件根，
  相对的 `args` 会 `can't open file` 秒退且不报错，表现就是「skill 挂上了、工具一个都没有」。
  代价：这些清单跟着这台机器走，换机要改路径——不要把它说成可移植插件。
- **服务器名别叫 `creator-studio`。** 配置里已有 `[mcp_servers.creator_studio]`（写作台那条老的
  HTTP 桥），归一化后同名，插件这条会被静默丢掉。现在是 `studio`；DSH 里为了不和同名配置撞，
  `serverName` 用 `creator_studio`。
- **非交互会话里 MCP 调用受审批策略管。** `approval_policy = never` 时会回
  `MCP tool call requires approval`，那是审批不是故障；`codex exec --dangerously-bypass-approvals-and-sandbox`
  只用于本地验证，别写进给人用的命令。

DSH 的 bundle 是**纯配置**的（没有 Host/Client 入口文件），风险与约束写在
`plugins/dsh/README.md`；profile 文件一律由 `plugin_manager` 写，不要手改。

## 边界

- 工具面**没有**发布/上传：上平台必须人工。
- 交付给用户的是 `spec.json`（可编辑时间轴），不是"一锤定音"的成片。
- 这个目录不 import 引擎代码、不复制排版参数：渲染仍走 `integrations/mac-worker/export/`，
  文案规则仍只有 `tools/promo/script_gen.py` 的 `COPY_RULES` 一处。
