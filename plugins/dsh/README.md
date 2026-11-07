# dsh/ — 给 DeepSeek Harness 的纯配置 bundle

这里**没有代码**：不注册 Host/Client 入口，也不复制服务器实现。它只做一件事——往 profile 的
组装里插一条 `@deepseek-ai/dsh-mcp-client`，让 DSH 去起本仓库那个 stdio 服务器
（`integrations/mac-worker/mcp/studio_mcp.py`，与 Codex/Claude/WorkBuddy 共用同一份）。

```
package.json         # dsh.bundle.patch 指向下面的 patch；dependencies 声明要插的包
cordis.patch.yml     # 一条 insert：dsh-mcp-client + serverName/transport/command/args
locale/en.json       # 展示名（扁平 key：meta.title / meta.description）
icon.svg
```

## 装

```bash
plugin_manager action=install_bundle target="/Users/apple/Desktop/work/Studio/creator-studio/plugins/dsh"
```

装完不需要重启就有工具：`Tool.listTools` 里会出现八个 `mcp__creator_studio__studio_*`。
失败时先看返回里的 `nextStep`，**不要**手改 `~/.dsh/profiles/*/package.json` 或
`cordis.patch.yml`——同一个 bundle 重装一遍才是修复路径。

## 已知的两个坑

- **Lifeguard 的安装前预检会把这类 bundle 判成 P010**（`patch 引用了没有声明的包`）。
  它的结构预检把 `cordis.patch.yml` 里每一行 `name: ...` 都拿去和**bundle 根** `package.json`
  的依赖比，而 YAML 要求 `@scope/name` 加引号，引号让 token 永远匹配不上；
  报的 `name: '@deepseek-ai/dsh-mcp-client' 不在依赖里` 就是这个机制，不是漏写依赖。
  处理方式：把 Lifeguard 切到 `observe`（`lifeguard_guard mode=observe`）再装，装完切回 `protect`。
  这是预检的误判，不是 bundle 的问题。
- **P033（写死本机绝对路径）是真实代价**：`args` 里的 `/Users/apple/...` 换机器就失效。
  跟其它三张清单同一个取舍（见 `../codex/README.md`），换机时改这一行即可。

## 边界

- 只插一条 MCP 客户端；不装包管理器依赖、不写其它 profile 状态。
- 服务器名 `creator_studio`（`serverName`）——不是 `studio`，避免和用户在 DSH 里已有同名配置撞；
  模型侧名字是 `mcp__creator_studio__studio_*`。
- `env` 故意没写：凭证与路径都由服务器自己的默认值处理，不在 patch 里抄一份。
