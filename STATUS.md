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

### 能不能"不占鼠标、不抢前台"？—— 现状与实测边界

结论先说：**做不到零打扰，但已经把打扰压到 12–18 秒的一次脉冲**。
下面是三条实测（不是推测），决定了这个边界：

| 能力 | 结果 | 证据 |
|---|---|---|
| 后台**读**面板像素 | ✅ 可行 | `ax_tool wincap <windowid> out.png` 用 ScreenCaptureKit 的 `desktopIndependentWindow`，Finder 在前台时照样抓到 1280×836 的面板内容，OCR 正常 |
| 后台**点击**（`CGEvent.postToPid`） | ❌ 无效 | 事件投递成功、返回 0，但剪映 Qt 完全不处理：点「关闭」后面板仍在 |
| 后台**按控件**（`AXPress` / `AXValue`） | ❌ 无效 | 参数行是无 action 的 `AXStaticText`；`ExportOkBtn` 有 `AXPress`，返回 `err=0` 但**什么都不发生**（和标题栏导出按钮同一个毛病） |

所以：**参数行必须有真点击，就必须有前台，就必须动物理光标**。能省的只是"看"——
以前每次校验都要抢前台截图，现在改用 `wincap` 后台抓，前台只留给点击。

为此加了三个开关（`export/jianying_export.py`）：

```bash
python3 jianying_export.py --platform bilibili --canvas 1920x1080 \
        --wait-idle 8          # 等用户 8 秒没键鼠输入再动手（挂后台队列用）
                               # 默认结束时会还原：前台 App + 光标位置
                               # --no-restore-focus 可关掉
```

`panel.quiet_start()` 在动手前记下前台 App 与光标，`atexit` 里还原
（用 `atexit` 是因为脚本有多条 `return`，比包 `try/finally` 更不容易漏）。
实测：Finder 在前 → dry-run → 退出后前台仍是 Finder、光标回到原位。

### 零打扰的三条路：已选 C（2026-10-24 决定）

| 路线 | 做法 | 状态 |
|---|---|---|
| A | 第二个 macOS 登录会话（FUS），GUI 跑在后台会话、独立光标 | 未做，降为**备用/高级通道**（只给必须用面板的人工工序） |
| B | macOS 虚拟机（Tart/UTM） | 未做，隔离最好但装系统+剪映授权成本高 |
| **C** | **不用剪映面板，自己渲染母版 + 自己按平台规格交付** | **已落地，见下节** |

C 的选择理由：A 解决不了并发（一台机一次只能导一条）、依赖剪映登录态/TCC/版本，
且没法容器化；C 是普通后台进程，能塞进现有 Linux 容器和 aitoearn-server 一起跑、
可重试可观测，并且复用已有的平台档案矩阵与 ffprobe 校验。

远控/无头这条路已经验证不通（`CGWindowListCreateImage` 在新 SDK 里被移除；
`postToPid` 被 Qt 丢弃；AXPress 全灭），不要再往"完全后台点击"上投时间。

### C 路线已落地：spec → 母版 → 多平台（全程无 GUI）

两个新模块（都在 `integrations/mac-worker/export/`）：

| 文件 | 职责 | 依赖 |
|---|---|---|
| `timeline_render.py` | 我们自己的 timeline spec → 母版 mp4（片段拼接/字幕/配乐/响度） | ffmpeg + Pillow |
| `deliver.py` | 母版 mp4 → 各平台规格（分辨率/帧率/码率/容器/补边策略） | 仅 ffmpeg |

链路是**两段可独立重跑**的纯后台进程，任何一段失败都不用从头来：

```bash
cd integrations/mac-worker/export
python3 timeline_render.py --spec spec.json --out master.mp4   # 9s 竖版实测 1.5s
python3 deliver.py --master master.mp4 --platform douyin,bilibili --outdir out --fit blur
```

**spec 契约**（JSON，相对路径以 spec 所在目录为基准）：

```json
{"canvas": {"width":1080,"height":1920,"fps":30},
 "video": [{"src":"a.mp4","in":0,"out":3,"fit":"cover|contain","speed":1.0}],
 "text":  [{"text":"多行\n字幕","start":0.4,"end":2.8,"x":0.5,"y":0.78,
            "size":0.045,"color":"#ffffff","stroke":2.5,"stroke_color":"#000000",
            "box":"#00000055","radius":0.5,"align":"center","fade":0.25}],
 "audio": [{"src":"bgm.m4a","start":0,"volume":0.18,"loop":true,"fade":1.0}]}
```

四条设计取舍，都是踩过坑才定的：

1. **文字用 Pillow 画 RGBA PNG 再 overlay**，不用 `drawtext`/`subtitles`——
   这台机器的 ffmpeg 9 构建**没有 freetype/libass**（`ffmpeg -filters` 里根本没有
   这三个滤镜），装了也画不出字。Pillow 顺带拿到描边/圆角底衬/多行居中，
   且不依赖系统 fontconfig（容器里换 Noto CJK 只需设 `TIMELINE_FONT`）。
2. **音轨与画面分离**：片段只出画面，声音全部来自 `audio[]`。
   避免 concat 要求每段都有音轨的老坑，也让"配乐 0.2 + 旁白 1.0 + 一段原声"可组合。
   混完统一过 `loudnorm=I=-16:TP=-1.5`，实测 -15.9 LUFS。
3. **尺寸全部相对画布高度**（`size` 是字号占画布高的比例），1080p/4K 视觉一致。
4. **不放大母版**：`deliver.target_dims()` 发现母版短边小于目标短边时，按短边等比缩目标，
   720p 母版投 B 站就出 1280x720 并在证据里写 `不放大`。**拉伸永远不是选项**。
5. 竖化横片默认 `--fit blur`（模糊背景填充），另有 `contain`（黑边，最快最稳）、
   `crop`（满屏但切边）；`eq=brightness` 一律不加——limited range 黑是 16，
   压暗会到 0，实测顶部条 YAVG 0 vs 中部 16，播放器上会偏色。

v1 **不做转场/花字/贴纸**：硬切等于剪映"无转场"，`xfade` 需要首尾帧重叠重算时间轴，
等真需要再加。要花字/特效就退回剪映面板（人工/高级通道），这是刻意的分工。

**验证方式（不读图片，全部数值化）**：产物一律 `ffprobe` + 亮度采样判，
字幕用"有字幕/无字幕两次渲染逐像素 diff"确认——实测三条字幕分别影响 6.6%/13.7%/7.1%
像素、差异全部落在 1358–1636 行（字幕带），最大差 199。不要用整带均值判字幕，
分辨率高时会被背景稀释（曾因此误判"字幕没进片"）。

测试：`python3 test_timeline_render.py`（18）+ `python3 test_deliver.py`（30）
+ `python3 test_platform_profiles.py`（34）+ `test_worker_timeline.py`（7）= **89 全绿，约 8 秒**。
`test_platform_profiles.py` 是 pytest 风格函数 + 自带 `__main__`，
`unittest discover` 会静默跳过它（报 0 个用例却"成功"）——CI 里必须单独敲一遍。

#### 接进平台（已激活 + 端到端跑通）

新增 job kind `render_timeline`，三处改动：

| 位置 | 改动 |
|---|---|
| `libs/mongodb/.../production.schema.ts` | `ProductionJobKind.TIMELINE_RENDER = 'render_timeline'` |
| `apps/aitoearn-server/.../production.service.ts` | `engine === 'timeline'` → 该 kind；并加进 claim 的默认能力数组 |
| `integrations/mac-worker/worker.py` | `do_timeline()` + 分派；`config.py` 能力表 |

平台侧只要提交 run 时 `spec.engine = 'timeline'`（spec 本体放 `spec.timeline`，
或直接就是带 `canvas` 的对象），worker 自己会：
渲染母版 →（`platforms` 非空时）出各平台规格 → 母版必传 S3、平台件逐个传
（单个平台传失败不带走整批）→ 登记素材库草稿。
**草稿登记失败只记 `draftError`，不把整单判死**——片已经渲好了这个事实不能丢。
payload 不带 `runId` 就是本地试跑：不传 S3、不发草稿，只回本地路径。
每次 job 的 spec 都落盘到 `~/CreatorStudio/timeline/<jobId>/spec.json`，出问题能原样复现。

激活路径（都已执行，可重复）：
```bash
NX_DAEMON=false ./deploy/build_images.sh aitoearn-server   # 本地镜像 creator-studio/aitoearn-server:local
bash deploy/recreate_server.sh                             # 只重建 server 容器，不碰其它服务
rsync -a --delete integrations/ ~/CreatorStudio/runtime/integrations/   # worker 跑的是运行时副本
launchctl kickstart -k gui/$(id -u)/local.creatorstudio.worker
```
worker 启动与空闲(>600s)时会 `POST /api/production/worker/capabilities` 自刷新能力表，
所以新增 job kind **不再需要手改库**——这之前是踩过的坑：
claim 的过滤读的是库里的 `productionWorker.capabilities`，注册时缺 `render_timeline`
会让任务永远没人领，且 worker 看上去一切正常。

**端到端验收（2026-10-24 实测，全程无 GUI）**：
`deploy/p2_timeline_e2e.py --mode friendly` 只提交"文案+素材+画幅"（前端表单发的就是这份），
服务端在 `createRun` 里展开成完整 spec 再落库。

| 项 | 结果 |
|---|---|
| run | `6adc54b57aa82b66b89b9f30` `succeeded`，母版 9.0s 1080x1920 |
| spec 展开 | `video` 3 段 / `text` 3 条（0.2→2.8 递推）/ `audio` bgm 0.35 loop |
| `run.output.platforms` | `douyin` 1080×1920 1.66MiB、`bilibili` 1920×1080 2.12MiB |
| 母版与平台件 | 三个 key 全部 `/oss/...` 200 可回读，ffprobe 尺寸/时长一致 |
| 字幕真的进片 | 字幕带(y1600-1740)与源片逐像素 diff 均值 68 / 最大 199；对照带 0.05 |
| 响度 | 集成响度 -16.2 LUFS（目标 -16），峰值 -12.0 dBFS |
| 发布意图 | `publicationAttempt.content.assetKey` 分别指向对应平台成片，不是母版 |
| 前端 | `/studio` 页面跑出「新建时间轴任务」表单，任务列表按平台出「抖音 1080×1920 1698 KiB / B站 1920×1080 2170 KiB」 |

关键点：**发布阶段取的是平台成片而不是母版**（`planPublication` 里
`platforms[platform]?.key || output.videoPath`）。之前 `run.output` 只有
`videoPath`，多平台交付等于白做——发布永远发母版。

已知限制：① 无转场/动画模板，这类需求要 Remotion/剪映；② 平台矩阵 17 个仍全是
`assumed`；③ 交付阶段只落到本地/S3，没有回写平台的发布草稿箱
（形态是 `run.output.platforms[platform] = {key,url,width,height,bytes}`，
不是旧文档里写的 `platformFiles`）。

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

# C 路线：spec → 母版 → 平台规格（全程无 GUI）
cd ~/Desktop/work/Studio/creator-studio/integrations/mac-worker/export
python3 timeline_render.py --spec spec.json --out master.mp4 --dry-run   # 先看命令
python3 deliver.py --master master.mp4 --platform all --outdir ~/CreatorStudio/out
python3 test_timeline_render.py && python3 test_deliver.py && python3 test_platform_profiles.py

# 健康检查(平台/worker/S3/剪映)
cd ~/Desktop/work/Studio/creator-studio && python3 deploy/doctor.py

# 验收清单
python3 deploy/acceptance.py
```
