# Creator Studio — 项目交接概览

> 本文件由 Hermes 于 2026-10-24 生成，用于把「剪映原生能力接入创作者中心平台」这条工作线
> 完整移交给后续接手者（人或其他 agent）。全部结论来自本机实测，非推断。

---

## 1. 一句话说清做成了什么

在**创作者中心工作台内**（不改用它、不去剪映/抖音网页操作）完成
「脚本 → 分镜 → 剪映工程草稿 → 人工导出 → 发布」链路的自动化，
并让生成的草稿真正带上**剪映原生资源**（转场/滤镜/特效/文字动画，带真实 resource_id）。

平台侧提交一个 `spec`（JSON），worker 领单，产出剪映草稿目录，人在剪映里一键导出。

---

## 2. 架构与数据流

```
用户 / agent
   │  提交 spec (JSON)
   ▼
aitoearn-server (NestJS, 容器内 3002)
   │  engine === "jianying" → job.kind = "jianying_draft"
   ▼
MongoDB productionJob  (queued)
   │  mac-1 worker 轮询领取（claimJob 读 productionWorker 的 capabilities 快照）
   ▼
mac-worker (LaunchAgent local.creatorstudio.worker)
   │  integrations/mac-worker/jianying_engine.py
   ▼
VectCutAPI  (HTTP 127.0.0.1:9100, Flask)
   │  create_draft → add_text/add_* → save_draft
   ▼
~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<draft_name>/
   │  draft_content.json (明文)
   ▼
剪映 11.6 打开 → 人工点导出 → 平台回填视频
```

**关键点**：整条链路的"交付物"是**草稿目录路径**，不是 mp4。原因见第 6 节。

---

## 3. 三个候选开源仓库的真实状态（实测，2026-10）

你给的那份清单有误导，结论如下：

| 仓库 | 实测结果 |
|---|---|
| `luoluoluo22/jianying-editor-skill` | **404，仓库已删除或转私有**。搜索结果里的 "3.8k★" 是搜索引擎旧索引。同账号下只剩 `AIcut`(113★, Remotion 自建编辑器) 和 `JianAI`(6★, Electron 全量编辑器)，**都不驱动剪映** |
| `YGtemple/jianying-editor-skill` | **1★ / 50KB / 纯 Markdown，零代码**。它自己引用的 `references/community-tools.md` 都不存在。是 `isYangs` 的文档 fork，不可作为实现 |
| `isYangs/jianying-editor-skill` | **257★ / MIT / 5.8MB / 2.9 万行 Python / v1.5.0（2026-10-15 仍在推）**，唯一真实可用的 skill。但无 REST API，且自动导出仅 Windows |

真正被选为接入主体的，是它们共同提到的第四个项目：

| `sun-guannan/VectCutAPI` | **2322★ / Apache-2.0 / 28 个 REST 路由 + 内置 MCP server** |

选它的理由：**它是服务形态**（HTTP API），天然适合被平台调用；而三个 skill 都是"给 agent
读的说明书 + 本地脚本"，无法被后端服务直接消费。

---

## 4. 为什么这套能跑通（决定性技术事实）

**剪映 11.6 的 `draft_info.json` 已经加密了**（base64 乱码，`json.loads` 必失败），
但 **`draft_content.json` 仍然是明文 JSON**。

VectCutAPI 的 `jianying_pro_10` profile 写的正是 `draft_content.json`
→ 所以对本机 11.6 **完全兼容**。

推论（很重要）：**永远只读写 `draft_content.json`，不要试图解析 `draft_info.json`。**
剪映自己能读它，我们不需要。

---

## 5. 已落地的代码改动

### 新增文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `integrations/mac-worker/jianying_engine.py` | 320 | 剪映引擎主体：spec → VectCutAPI 调用序列 |
| `deploy/recreate_server.sh` | 53 | 应急：从本地镜像直推容器（绕开 compose 网络问题） |
| `~/CreatorStudio/jianying/install_vcut.sh` | 45 | 幂等安装 VectCutAPI：建 venv、装依赖、写 config、查素材库 |
| `~/CreatorStudio/motion/JIANYING_INTEGRATION_DECISION.md` | 81 | 当时的调研结论存档 |

### 改动的既有文件

| 文件 | 改了什么 |
|---|---|
| `integrations/mac-worker/worker.py` | 新增 `elif kind == "jianying_draft"` 分支 + `do_jianying()`，import `jianying_engine` |
| `libs/mongodb/src/schemas/production.schema.ts` | job kind 枚举加 `JIANYING_DRAFT = 'jianying_draft'` |
| `apps/aitoearn-server/src/core/production/production.service.ts` | `enqueueRender` 加 `engine === 'jianying'` 分支；capabilities 列表加入新 kind |
| `deploy/render_config.py` | 修 `pull_policy` 硬编码（见第 8 节） |
| `docker-compose.yml`（仓库内） | 全量镜像改本地名 + `pull_policy: never` + 头部警告注释 |

### 运行的进程

| 名称 | 类型 | 端口/作用 |
|---|---|---|
| `local.creatorstudio.worker` | LaunchAgent | 领单执行渲染 |
| `local.creatorstudio.capabilities` | LaunchAgent | 8899 能力上报 |
| `local.creatorstudio.logindock` | LaunchAgent | 8898 登录坞 |
| VectCutAPI | 普通进程 | **127.0.0.1:9100** |
| 剪映 11.6 | App | 9333 (CDP)，bridge 49603 |

---

## 6. 硬限制（别浪费时间的地方）

1. **macOS 无法自动导出剪映。**
   `auto_exporter` 只在 Windows + 剪映 ≤5.9 可用（依赖 `uiautomation`）；
   VectCutAPI 的 `reload_desktop` 在 macOS 直接返回 `unsupported_platform`。
   → 所以平台交付 `draftPath`，人工点导出。
   → 想全自动导出，唯一路子是 `isYangs` 的 `windows_export_agent.py` + `remote_host`，**需要一台 Windows**。

2. **剪映内置 AI 无对外接口。**
   它是完整的 agent 运行时（`libdeepagents_capi.dylib` 110MB，有 `da_agent_create/run_async` 等），
   但 bridge 所有路由都返回 `Unknown Infinite Canvas native bridge route.`。只能在 App 内手动用。

3. **剪映登录态不要碰。**
   需要用 login cookie + `x-signature` 签名（算法在 1.5GB dylib 里），升级即失效，
   且属于用户本人凭证。本项目**明确不做**。

4. **Pinterest 在本机彻底不可达**（`ERR_CONNECTION_CLOSED`，含改 DNS 复测），已放弃。

5. **发布必须人工**（用户明确划的边界）。SAU 只做登录与 cookie 校验，**不得自动提交真实作品**。

---

## 7. 原生资源调用的坑（踩过的）

### 7.1 必须双源查询

`isYangs` 的 CSV 和 `pyJianYingDraft` 0.3.1 的枚举**版本不同步**：

- `黑板擦除` 只在 CSV 里有，枚举的 361 个 `Transition_type` 里没有
- `描边填充` 两边都有，但 CSV 的分类名是 **`text_animations`**（不是 `text_intro_animations`）
- `描边粉笔` **两边都没有**（这是当初的误判，已纠正）

→ `jianying_engine.py` 的 `_asset_lookup` 做了**三级回退**：
CSV 库 → pyJianYingDraft 枚举 → 原名透传。

### 7.2 文字动画的正确写法

- 参数名是 **`intro_animation`**，不是 `text_animation`
- 写进草稿后落在 **`materials.material_animations`**（不是 `text_animations`）
- 结构：
  ```json
  {"type":"sticker_animation","animations":[
    {"name":"复古打字机","resource_id":"7253888335163167291","type":"in","duration":500000}]}
  ```
- 验证时必须查 `material_animations`，查 `text_animations` 会误判为 0

### 7.3 时间单位

`add_text` / `add_video` / `add_audio` 的 `start` / `end` / `target_start`
**全部是秒，不是微秒**。传错会得到 3,000,000,000,000μs（≈34 天）的离奇时长。

---

## 8. 平台集成的三个必踩坑

### 8.1 worker capabilities 是 Mongo 快照

`claimJob()` 只读 `db.productionWorker{name:"mac-1"}` 的 `capabilities`，
改后端代码或 `config.py` **都不生效**。必须：

```javascript
db.productionWorker.updateOne({name:"mac-1"}, {$addToSet:{capabilities:"jianying_draft"}})
```

如果 job 一直 `queued`，八成就是这个原因。

连 Mongo：
```bash
PW=$(grep "^STUDIO_MONGO_ROOT_PASSWORD=" ~/CreatorStudio/config/compose.env | cut -d= -f2-)
docker exec creatorstudio-mongodb-1 mongosh "mongodb://aitoearn:${PW}@localhost:27017/aitoearn?authSource=admin" --quiet --eval '...'
```

### 8.2 compose 是两层文件，别改错、别跑错

- **运行时**用的是 `~/CreatorStudio/config/compose.yml`
- 但它由 `deploy/render_config.py` 从仓库 `docker-compose.yml` **生成**
- 仓库那份是**上游模板**，带固定 `container_name: aitoearn-*`

**在仓库目录直接 `docker compose up` 会建出 `aitoearn-*` 容器，和在跑的 `creatorstudio-*` 抢 6379/8080 端口。** 我踩过一次，已清理。

`render_config.py` 里原本对 `aitoearn-ai` 硬编码 `pull_policy: missing`（会去打不可达的 Docker Hub），已改为全部 `never`。

**正确三步：**
```bash
# 1) 改仓库模板 docker-compose.yml
# 2) 重新渲染
cd ~/Desktop/work/Studio/creator-studio && python3 deploy/render_config.py
# 3) 在运行时目录启动（--project-name 不能省）
cd ~/CreatorStudio/config
docker compose --env-file compose.env -f compose.yml --project-name creatorstudio up -d
```

省略 `--project-name` 会按目录名推导项目名，容器名就对不上了。

应急可用 `deploy/recreate_server.sh`，但它建的容器**没有 compose 标签**，
后续 `compose up` 会报 `container name already in use`，需先 `docker rm -f`。

### 8.3 平台提交的 spec 会嵌套

平台可能把分镜包在 `way` 字段里：
```json
{"engine":"jianying","way":{"scenes":[...]}}
```
引擎要在 `way` / `spec` / `storyboard` / `board` 里逐层找 `scenes`。

---

## 9. 验收方法（缺一不算通过）

```bash
# 1) VectCutAPI 能列出用户现有真实草稿（证明路径对、能读 11.6）
curl -s -X POST http://127.0.0.1:9100/list_projects -d '{}' -H 'Content-Type: application/json'

# 2) 草稿内容正确
D="$HOME/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/<draft_name>"
python3 -c "import json;d=json.load(open('$D/draft_content.json'));print(d['canvas_config'], d['duration']/1e6)"

# 3) 剪映真的认了（关键证据）
grep copy_draft_external "$HOME/Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro/User Data/Log/draft_acion_watch.json" | tail -1
# 期望看到 "errno":0 "suc":true

# 4) 平台侧 job succeeded 且 result.draftPath 存在
```

最近一次成功：run `6adc252601ce2d26b52f559a` → draft `studio_6adc252601ce2d26b52f`，
1920×1080 / 19s / 5 段文案 / 5 个原生文字动画（带 resource_id），剪映日志 `errno:0 suc:True`。

---

## 10. 现有产物（可直接打开看效果）

草稿根：`~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`

| 草稿 | 内容 |
|---|---|
| `studio_6adc252601ce2d26b52f` | 平台端到端产物（最新） |
| `studio_6adc23ad1823b214a2ae` | 平台端到端产物 |
| `jy_native_demo` | 5 段文案 + 5 个剪映原生文字动画的样板 |
| `envoy_promo_v2` | 早前 49s 的 envoy 宣传片工程（6 段分屏） |

另有一支已导出的成片：`~/CreatorStudio/deliver/envoy-motion-final.mp4`
（49.333s / 1920×1080 / 1480 帧）。

---

## 11. 三种渲染引擎的分工

平台支持 `spec.engine` 分流：

| engine | 产出 | 适用 |
|---|---|---|
| `slides` | mp4 | 30s 左右的静态图文短视频 |
| `motion` | mp4 | 6–10 分钟的动画长片 |
| `jianying` | **草稿目录** | 需要在剪映里精修、或用原生转场/特效 |
| 缺省 | mp4 | MoneyPrinterTurbo（素材拼接） |

---

## 12. 下一步建议（没做的）

1. **按场景精准改片**：现在草稿已按场景分段，可做 API —— POST 单个 scene 的修订 →
   只重建该场景段，不重渲全片。这是"随时提意见精准更新某个视频"的落地形态。
2. **Windows 远程导出**：解决 macOS 不能自动导出的唯一路子。
3. **素材库扩充**：isYangs 的 `data/*.csv` 可继续挖（4021 行原生资源命名）。

---

## 13. 相关 skill 与文档

- skill `jianying-vectcut-native` —— 上面这些坑的浓缩版，agent 可自动加载
- `~/Desktop/work/Studio/creator-studio/RUNBOOK.md` —— 整体运维手册
- `~/Desktop/work/Studio/creator-studio/ACCEPTANCE.md` —— 验收清单
- `~/CreatorStudio/motion/JIANYING_INTEGRATION_DECISION.md` —— 最初调研结论

---

## 14. 凭据与安全

- 数据库密码、S3 密钥、`STUDIO_INTERNAL_TOKEN` 全在 `~/CreatorStudio/config/compose.env`
  （由 `render_config.py` 生成，删掉会重生成并**使本地登录失效**）
- 平台 API 在 **8080**（9000 是 S3，不要混）
- 本机 user token 在 docker volume `creatorstudio_init-data:/d/token.txt`
- **不要导出、打印、提交任何剪映登录态明文**（已明确不碰）
