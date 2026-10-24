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

1. ~~剪映 11.6 是否认这些草稿的时间线~~ —— **根因已定位并已修(2026-10-24)**。
   剪映的草稿注册表 `<draft_root>/root_meta_info.json` 里每条记录都有
   `draft_json_file` 字段,**一律指向 `draft_info.json`**:11.6 **只读 `draft_info.json`**,
   `draft_content.json` 只是历史文件名,它根本不看。平台过去只写 `draft_content.json`,
   于是列表里草稿在、时间线恒为空(注册表里 `tm_duration`、`draft_timeline_materials_size` 也都是 0)。
   修法见 §5 P1。
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

**P1 — 引擎修复(代码已改,待一次人工确认)**
1. ✅ **输出 `draft_info.json`(明文)**:`_deploy()` 之后新增 `_normalize_draft()`,
   同时写草稿根与 `Timelines/<id>/` 两处;若目标已是剪映写的密文则**拒绝覆盖**。
2. ✅ **每草稿独立 id**:模板模式沿用模板 id,导致三个草稿共用 `91E08AC5-…`;
   现在部署后统一换新 UUID,并同步 `Timelines/` 目录名、`project.json(.bak)`、
   `timeline_layout.json`、`draft_meta_info.json.draft_id`。
3. ✅ **meta 补全**:`tm_duration`、`draft_timeline_materials_size_` 按真实值写。
4. ❌ 撤回一条曾进过计划的"硬伤":`~/Movies/JianyingPro` 与
   `~/Library/Containers/com.lemon.lvpro/Data/Movies/JianyingPro` 是**同一个目录**
   (inode 相同),不存在路径不一致问题,不必改。

**验收动作(1 分钟)**:`jy_native_demo` 已原地修好(备份 `/tmp/jy_native_demo.pre-normalize.tgz`),
在剪映里打开它,看时间线是否出现 5 段文字、总长 19s。

**P2 — 改写验收口径**:把 `copy_draft_external` 从验收项里删掉,换成
「时间线非空 + 导出成功」的可测信号(导出产物 = `~/Movies/JianyingPro/.../export/` 下的 mp4 + 时长/分辨率校验)。

**P3 — 导出闭环**:AX 菜单 → 导出面板 → 坐标点「导出」→ 轮询产物。
需要一次人工坐标标定;备选是 Windows 远程导出(要一台 Windows)。

**P4 — TTS/配音**:把 `spec.voice` 映射到 SAMI 音色,产出音频并落到草稿音轨。

**P5 — 按场景精准改片**:`POST /studio/scene/:id/revise` → 只重建该场景段。

---

## 5.5 P3 导出闭环 —— 已跑通（2026-10-24 13:27）

一条命令从"草稿已打开"到"校验过的产物 + 证据 JSON"：

```bash
cd integrations/mac-worker/export
python3 jianying_export.py --platform bilibili --canvas 1920x1080
```

实测结果（草稿 `jy_native_demo`，19s，1920x1080@30）：

| 项 | 面板前 | 面板后 | 说明 |
|---|---|---|---|
| 分辨率 | 1080P | 1080P | 幂等，已是目标值就不动 |
| 格式 | MOV | MP4 | 点行 → AX 读选项 → 点选项 → dismiss |
| 帧率 | 30 | 30 | 幂等 |
| 视频码率 | 推荐 | 更高 | B站档位 |

产物 `~/CreatorStudio/out/jy_native_demo__bilibili__1920x1080.mp4`
（h264+aac，1920×1080，30fps，**19.000s**，423KB），证据
`docs/probe/export-panel/runs/jy_native_demo__bilibili.json`，
`duration/resolution/fps` 三项校验全 OK，退出码 0。

### 三条硬约束（都写过坑）

1. **导出不能改比例**。面板原文「以草稿尺寸（1920x1080）导出，可前往草稿参数修改」，
   下拉只有像素档没有比例项。所以 `--platform douyin` 配 16:9 草稿会**直接拒绝**（退出码 7），
   不会静默导出个方形片。比例只能由建草稿时决定（`render_mpt.py` 的 `aspect`）。
2. **档位按"短边"算，朝向跟草稿走**。`1080P` 在 16:9 草稿上出 1920×1080，在 9:16 上出 1080×1920。
   校验必须用 `expected_dims(tier, canvas)`，不能拿平台档案的 width/height 直接比；
   另外档位会**下调到不超过画布**（1280×720 草稿要 1080P → 实际 720P，不放大白占体积）。
3. **剪映 11.6 保存后会把 `draft_info.json` 加密**（首字节变成 `GGHbtvxhfjg…` 密文），
   所以导出时读不到画布/时长。为此 `jianying_engine.py` 部署草稿时额外写
   `<草稿>/studio_export_meta.json`（画布/fps/时长/轨道数），导出端以它为唯一可信来源，
   没有 sidecar 又没给 `--canvas` 就直接报错，不猜。

### 面板探针的四个坑

- **ESC 会连整个模态面板一起取消**：ESC 关下拉后，后面的点击落到编辑器里（曾把面板改成 480P/24/更低）。
  现在一律点面板标题条 `dismiss()`，ESC 只用于关闭"导出成功"残留页。
- **下拉选项必须从 AX 读**，不能 OCR 弹层：Qt 弹层有透明边距，OCR 会把底下几行一起读进来
  （格式读出 `MOV, 30, 更低, H.264`）。现在 `options_from_ax()` 只取落在弹层矩形内的 `AXStaticText`。
- **`dialog_rect()` 会被 195×20 的菜单浮层劫走**（subrole 同样是 `AXDialog`），
  加了尺寸过滤 + 标题必须以「导出」开头。
- **每次截图前必须 `ensure_front()`**：`set frontmost` 是异步的，实测要 2.2–3s，
  否则 `-R` 截到的是 DSH 自己的窗口，所有值读空。窗口尺寸/位置每次都会变
  （`696,301 1168x780` / `0,30 2560x1320` / 面板 `640x720` 或 `640x418` 收起态），
  所以只存**窗口相对坐标**。

### 平台档案

`export/platform_profiles.py`：17 个平台（与 `platform_registry.py` 一一对应），
`profile_for/resolution_option/fps_option/as_dict`，测试 34/34。
**全部 17 个 source=`assumed`** —— 无官方原文可核，别当事实用；
矩阵与"怎么翻成 documented"见 `docs/probe/export-panel/platform-matrix.md`。
跑测试用 `python3 integrations/mac-worker/export/test_platform_profiles.py`
（本机 pytest 不可用：3.9 的 anyio/typing_extensions 错位）。

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
