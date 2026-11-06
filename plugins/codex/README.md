# codex/ — creator-studio 的 Codex 插件

照本机已装的官方插件（`envoy@envoy`）的形状做的，不是自创格式：

```
plugins/codex/
  .agents/plugins/marketplace.json     # Codex 读的 marketplace 清单
  .claude-plugin/marketplace.json      # 同一份内容，给认这个路径的客户端
  creator-studio/
    .codex-plugin/plugin.json          # 插件清单：skills + mcpServers + interface
    .mcp.json                          # {"mcpServers": {...}}，stdio 启动本插件自带的服务器
    mcp/studio_mcp.py                  # 零依赖 JSON-RPC stdio ↔ 能力服务
    mcp/smoke.py                       # 冒烟：按真实客户端时序跑一遍
    skills/creator-studio/SKILL.md     # runbook：装 → 自检 → 出片，分三段报状态
```

## 为什么是这个形状

模型要能把活干完，需要三样东西同时在场，缺一样都退化：

| 要素 | 在这里 | 缺了会怎样 |
|---|---|---|
| 清单 | `.codex-plugin/plugin.json` | 工具装了也没描述，模型不知道该什么时候用 |
| 动作面 | `.mcp.json` → `mcp/studio_mcp.py` | 只能让模型自己拼 `curl`，错一次就中断 |
| 说明书 | `skills/creator-studio/SKILL.md` | 模型把安装步骤复述一遍就交差 |

`plugin.json` 里的 `mcpServers: "./.mcp.json"` 是官方插件在用的写法（`envoy` 同样是
`"mcpServers": "./.mcp.json"`），路径相对插件根目录，所以仓库挪位置不用改清单。

## 装

```bash
codex plugin marketplace add /Users/apple/Desktop/work/Studio/creator-studio/plugins/codex
codex plugin add creator-studio@creator-studio
```

这一步会写 `~/.codex/config.toml`（多一段 marketplace 和一条 `[plugins."creator-studio@creator-studio"]`），
属于改用户机器，**先问过人再执行**。

## 验

```bash
python3 plugins/codex/creator-studio/mcp/smoke.py     # 协议 + 工具面 + 一条真任务（不渲染）
```

冒烟覆盖：`initialize` / `tools/list`（必须 8 个、必须没有发布口子）/ `studio_ready` /
`studio_films` / `studio_shots`（真跑一次桥接，产出 `spec.json`）/ 未知工具回 `-32602`。
能力服务没起来时 `studio_ready` 回 `isError: true` 也算通过——桥的责任是回话，不是替服务背锅。

## 边界

- 工具面**没有**发布/上传：上平台必须人工。
- 交付给用户的是 `spec.json`（可编辑时间轴），不是"一锤定音"的成片。
- 这个目录不 import 引擎代码、不复制排版参数：渲染仍走 `integrations/mac-worker/export/`，
  文案规则仍只有 `tools/promo/script_gen.py` 的 `COPY_RULES` 一处。
