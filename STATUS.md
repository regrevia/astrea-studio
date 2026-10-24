# STATUS — 创作者工作台(Creator Studio)单一事实来源

> 维护者:studio-lead(唯一负责人,2026-10-24 起接管 Hermes 全部工作)
> 这份文件取代零散交接记录。有冲突时**以本文件 + 仓库代码为准**,
> `HANDOFF-jianying.md` 保留为历史存档(其中 §4 已被第 4 节更正)。

---

## 1. 三个目录的职责(别再混用)

| 路径 | 定位 | 允许做的事 |
|---|---|---|
| `~/Desktop/work/Studio/creator-studio` | **唯一真源**:源码、部署脚本、文档、本文件 | 改代码、改 compose 模板、提交基线 |
| `~/CreatorStudio` | **运行时**:`config/compose.env`、`state/`、`logs/`、`deliver/`、`materials/`、`jianying/VectCutAPI` | 只放运行产物与凭据;**不放源码** |
| `~/Desktop/DSH/CreatorLab` | **研究归档**:notes/、prototypes/、monitor/ | 探查脚本、逆向笔记;结论要回填到本仓库 |

VectCutAPI(剪映草稿 HTTP 服务,`127.0.0.1:9100`)装在 `~/CreatorStudio/jianying/VectCutAPI`,
其 `pyJianYingDraft` 是**仓库内打过补丁的副本**(import 命中它,不是 site-packages),
所以升级 pip 包不会生效,改格式要改这份副本。

---

## 2. 在跑的组件

| 组件 | 形态 | 端口 |
|---|---|---|
| aitoearn-server / web / ai | docker compose,项目名 `creatorstudio` | 经 nginx |
| nginx | 同上 | `8080`(平台 API/页面)、`9000`(S3 反代) |
| mongodb / redis / rustfs | 同上 | 仅容器内 |
| mac-worker | LaunchAgent `local.creatorstudio.worker` | 轮询 Mongo 领单 |
| capabilities / logindock | LaunchAgent | `8899` / `8898` |
| VectCutAPI | 普通进程(worker 会按需拉起) | `9100` |
| 剪映 11.6 | App | CDP `9333`,bridge 动态端口 |

compose 是**两层**:仓库 `docker-compose.yml` 是模板 → `python3 deploy/render_config.py`
渲染出 `~/CreatorStudio/config/compose.yml` → 在 `~/CreatorStudio/config` 用
`--project-name creatorstudio` 启动。**禁止在仓库目录直接 `docker compose up`**(会与在跑的容器抢端口)。

---

## 3. 真实进度

**已打通**:spec(JSON) → 平台 `productionJob` → worker 领单 → 剪映草稿目录落盘,
且草稿里带剪映原生资源(转场/滤镜/文字动画,带真实 `resource_id`)。
三种引擎分流 `slides` / `motion` / `jianying` 已实现。

**未打通(= 本阶段真正的工作)**:

1. **剪映 11.6 是否认这些草稿的时间线** —— 存疑。平台产出的 `studio_*` 草稿只写
   明文 `draft_content.json`;而 11.6 自己创建的草稿**只写加密的 `draft_info.json`**
   (base64 包流密码,熵 ≈7.998),两者结构不同。用户在 11.6 里遇到
   「无法导出,时间线为空」。
2. **导出闭环**:macOS 上 `auto_exporter` 不可用;AX 已能点到「导出面板弹出」,
   面板内按钮 Qt 自绘、AX 不可见,需坐标标定 + 产物轮询。
3. **配音/TTS**:`spec.voice` 已有 `sami:BV411_streaming` 这类标识,但未闭环到草稿音轨。

---

## 4. 对历史结论的两处更正

**(a) 「11.6 的 draft_info.json 已加密,所以只写 draft_content.json」——只对一半。**
实测(详见 `HANDOFF-jianying.md` §4 与本仓库 `docs/` 记录):

- 剪映 11.6 **自建**草稿:`draft_info.json` 是密文,`10月24日 (1)`(11:47 新建)连
  `draft_meta_info.json` 也一起加密;**没有** `draft_content.json`。
- **web/云来源**草稿(`10月24日`):`draft_info.json` 与 `draft_meta_info.json`
  **都是明文**,`version 400000 / 127.0.0.0`、`platform.os = "web"`、
  `draft_is_web_article_video = true`,11.6 能正常打开、编辑,保存后仍是明文。
- ⇒ 11.6 **按草稿类别分派解析器**,明文 web 类草稿可读。这是目前**最短的可行路线**:
  不必破解加密格式,直接按 web 格式写明文 `draft_info.json`。
  探针已生成:`rea_probe_web01`(5 段文字 / 19s / 明文),脚本见
  `integrations/mac-worker/probes/jianying_webdraft_probe.py`。

**(b) 验收证据不合格**:过去用 `draft_acion_watch.json` 里的 `copy_draft_external errno:0`
证明「剪映认了」——那条只证明**文件被复制进草稿根目录**,与内容能否渲染无关。
新的合格证据必须是:①草稿在 11.6 里时间线非空;②能导出成 mp4(或明确的导出错误)。

---

## 5. 下一阶段计划(按优先级,每步都有判定标准)

**P0 — 定格式路线(卡在人工一步)**
在剪映里刷新草稿列表,打开 `rea_probe_web01`,看是否有 5 段文字。
- 有 5 段 → 走明文 web 格式,引擎改造点明确,后续全部解锁;
- 出现但空 → 迭代 `draft_meta_info.json` 字段(已有明文样本可对照);
- 根本不出现 → 只认自家加密格式,转 P3 的 AX/CDP 驱动路线。

**P1 — 修引擎三处硬伤(不依赖 P0,可并行)**
1. `studio_6adc23ad…` / `studio_6adc2526…` / `jy_native_demo` 三个草稿**共用同一个
   timeline UUID `91E08AC5-…`**(模板克隆没换 id)→ 每草稿生成唯一 id。
2. `draft_meta_info.json` 里 `tm_duration = 0`、`draft_timeline_materials_size_ = 0`、
   缺 `draft_cover` → 补齐,避免列表显示 0 秒/无封面。
3. `draft_fold_path` 写死 `~/Movies/...`,与容器真实路径
   `~/Library/Containers/com.lemon.lvpro/Data/Movies/...` 不一致 → 统一以容器路径为准。

**P2 — 改写验收口径**:把 `copy_draft_external` 从验收项里删掉,换成
「时间线非空 + 导出成功」的可测信号(导出产物 = `~/Movies/JianyingPro/.../export/` 下的 mp4 + 时长/分辨率校验)。

**P3 — 导出闭环**:AX 菜单 → 导出面板 → 坐标点「导出」→ 轮询产物。
需要一次人工坐标标定;备选是 Windows 远程导出(要一台 Windows)。

**P4 — TTS/配音**:把 `spec.voice` 映射到 SAMI 音色,产出音频并落到草稿音轨。

**P5 — 按场景精准改片**:`POST /studio/scene/:id/revise` → 只重建该场景段。

---

## 6. 常用命令

```bash
# 渲染客户端配置(改仓库模板后必做)
cd ~/Desktop/work/Studio/creator-studio && python3 deploy/render_config.py

# 起/看运行时栈
cd ~/CreatorStudio/config && docker compose --env-file compose.env -f compose.yml \
  --project-name creatorstudio up -d

# 健康检查(平台/worker/S3/剪映)
cd ~/Desktop/work/Studio/creator-studio && python3 deploy/doctor.py

# 验收清单
python3 deploy/acceptance.py
```
