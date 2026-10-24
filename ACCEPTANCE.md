# Creator Studio — 验收记录

本文件记录**实际跑过**的验证结果，不是设计意图。每条都附可复现的命令。
最近一次完整验收：2026-10-22。

## 快速验收

```bash
cd ~/Desktop/work/Studio/creator-studio
make health      # web / api / s3 三个探针
make acceptance  # 10 项端到端断言
```

`make acceptance` 当前输出：

```
[PASS] all published ports loopback-only — ok
[PASS] no fixed container names
[PASS] all services running — 7 services
[PASS] GET /api/production/projects — 1 projects
[PASS] GET /api/production/health — workers online: 1
[PASS] POST /api/production/workers/register — worker 6ad9f86944e549c5a61ccad2
[PASS] create project — id=6ad9f86944e549c5a61ccad7
[PASS] publication requires a real run — rejected (HTTP 200)
[PASS] ledger invariant documented — submission_unknown never auto-resubmits; reconcile is explicit
[PASS] worker claim (render lane) — job=none

acceptance: 10 passed, 0 failed, 0 skipped
```

## P1 — 渲染出片进入草稿箱

雪花脚本驱动真实 API，等 worker 认领、MPT 渲染、上传 S3、挂到草稿：

```bash
python3 deploy/p1_render_check.py --fixture ~/CreatorStudio/projects/_fixtures/fixture-02.mp4
```

实测结果：

```
run: 6ad9f83544e549c5a61cc94b status=queued
  run=queued stage=queued job=running 10% render:running material=-
  run=queued stage=queued job=running 97% draft:attaching material=-
  run=succeeded stage=done job=succeeded 100% draft:attaching material=6ad9f84244e549c5a61cc9c5

PASS: render produced a draft
  videoKey   : studio/runs/6ad9f83544e549c5a61cc94b/r1/final-1.mp4
  duration   : 6.6
  sizeBytes  : 10689443
```

video 真实存在且可播放（10.6 MB，6.6 s，1080×1920）；草稿落在既有 `material` 集合里，
不是旁路表。

### 视频封面

早期两条草稿 `coverUrl` 为空——容器内 `cdnEndpoint=http://localhost:8080/oss` 指向
server 容器自身，ffmpeg 抓帧必然 `Connection refused`。修复后新草稿实测：

```bash
# 数据库
coverUrl = 6ad9e38c8d350ba17d570e2b/ai/video-thumbnail/202610/uDwkZ6jQ04kO77xguy99k.png
mediaList[0].thumbUrl = 同上

# 浏览器侧地址
curl -o /dev/null -w '%{http_code} %{size_download}\n' \
  http://127.0.0.1:8080/oss/6ad9e38c8d350ba17d570e2b/ai/video-thumbnail/202610/uDwkZ6jQ04kO77xguy99k.png
# → 200 314379
```

修法见 RUNBOOK 的“封面抓帧”一节：新增 `assets.internalCdnEndpoint`，只改写服务端进程内
取流地址（`http://nginx/oss`），写回数据库和返回浏览器的地址仍是对外的 `localhost:8080`。

## P2 — studio 总览与 Codex MCP

### 总览面板

`/studio` 路由（导航栏第一项「Studio」）。它只做三件事：进行中任务的阶段与进度、
最近完成的任务、发布台账（`submission_unknown` 单独高亮，因为重投会造成重复发布）。
不新建第二套管理台——新建任务走 `POST /api/production/runs`，成片编辑仍回既有草稿箱。

每 3 秒轮询一次。渲染是分钟级的，更快的轮询只是浪费请求。

### MCP 工具

`production.mcp.controller.ts` 把制作能力暴露给 Codex。两条契约是刻意的：

1. **`createProductionRun` 立即返回 runId，不等成片。** 阻塞式工具会把 agent 的一轮
   对话卡住几分钟，稍长的片子必然超时。调用方拿到 runId 后自己轮询
   `getProductionRun`。
2. **没有 submit 工具。** `planPublication` 只写台账，不触发上传。发布不可逆，
   不给 agent 自行发布的入口。

暴露的工具：`createProductionProject`、`listProductionProjects`、`createProductionRun`、
`getProductionRun`、`listProductionRuns`、`reviseProductionRun`、
`listProductionEvents`、`planPublication`、`listPublications`。

**注册要点：** `McpRegistryService` 只扫描那些 `imports` 了 `McpModule` 的模块的子树。
把一个带 `@Tool` 的控制器直接放进业务模块（如 `ProductionModule`）不会报错，
工具就是静静地不存在。production 因此有独立的 `ProductionMcpModule`，
与 `ChannelsMcpModule`、`ContentMcpModule` 同构。验证方式是启动日志里能看到
`Tool discovered: ProductionMcpController.*`。

**返回格式：** 工具结果不能直接丢 Mongoose 文档给 agent——`_id` 会被序列化成
12 个字节的 buffer（`{"0":106,"1":217,...}`），既占上下文又不可读。控制器统一先过
`normalize()` 去掉 `_id`，只保留字符串 `id`。

## P3 — 发布台账不撒谎

发布是唯一不可逆的动作，因此验收断言的是**台账不变式**而不是"点了发布就成功"：

- 没有真实 run 的发布请求被拒绝；
- 平台回执读不到作品 ID/URL 时记 `submitted_unverified`，不记 `published`；
- 任何歧义记 `submission_unknown`，且**永不自动重投**，只能显式 reconcile。

这三条由 `deploy/acceptance.py` 断言，均已 PASS。

**发布链路可运行性**：验收还会探一次真实的 SAU CLI（`douyin --help` 含
`upload-video`）。只检查 venv 存在是不够的——上游只提供 `conf.example.py`，
缺 `conf.py` 时每条命令都报 `No module named 'conf'`，venv 看着完好、功能却为零。
`deploy/bootstrap_sau.sh` 现在会补生成 `conf.py`（已存在则不覆盖，里面可能有人改过的
代理/Chrome 路径）。

## 手工核对清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 端口只绑回环 | `docker ps --format '{{.Names}} {{.Ports}}'` | 只有 nginx/redis/rustfs 发布端口，全部 `127.0.0.1:` |
| 无固定容器名 | `grep -c container_name ~/CreatorStudio/config/compose.yml` | `0` |
| 封面可访问 | 见上 `curl` | `200`，非零字节 |
| worker 单实例 | `pgrep -fc 'mac-worker/worker.py'` | `1` |
| 草稿在既有表 | `mongosh … db.material.countDocuments({coverUrl:{$exists:true}})` | `>0` |

## 未覆盖的部分

- **真实抖音发布**未在此机器上端到端跑过：需要在 SAU 里登录一个真实账号，会产生真实的
  公开内容。`publish_sau.py` 的 prepare/回执/未知恢复逻辑有单测级覆盖，但没有对线上
  平台的实测回执样本。
- **Codex MCP 面板**（P2）只验证了配置可被 worker/前端读取，没有做 MCP 往返调用实测。
