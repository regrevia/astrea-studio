# AGENTS.md

这份文件写给「接下来要在这个仓库里干活的人或 agent」。读它能省掉重复摸索：
一次竖屏成片在这里是一条命令的事；卡住人的通常不是渲染代码，而是下面前四节。

## 1. 这是什么

`creator-studio` 是这台机器上的**出片平台**，不是前端工程（上游 AiToEarn 的 Nx/Next 那套已经不在本仓库路径里）。
它由三层组成：

| 层 | 在哪 | 干什么 |
|---|---|---|
| CLI | `bin/studio` | agent 唯一该用的入口，只说 HTTP，不自己拼 ffmpeg |
| 能力服务 | `integrations/mac-worker/capabilities_server.py`（`127.0.0.1:8899`） | 收 `POST /jobs`，排队、落盘、回状态 |
| 引擎 | `integrations/mac-worker/export/*.py` | 真正渲染：`timeline_render.py`、`vertical_short.py`、`char_layer.py` |

线上跑的是 `~/CreatorStudio/runtime` 那份**拷贝**，不是这个仓库。改完仓库必须
跑 `scripts/sync_runtime.sh`（它会 rsync 并重启 launchd 服务），否则线上还是旧逻辑。
这是本仓库最容易白干半小时的一条。

## 2. 出片就一条命令

```bash
./bin/studio ready                      # 先自检：服务/worker/解释器/ffmpeg/字体/交付盘
./bin/studio jobs submit short deploy/specs/envoy_promo.json --slug demo --wait
```

`short` = 竖屏脚本直出（`topic/hook/kicker/points/cta` 这层文本 → 旁白 → 卡片 → 立绘 → mp4），
一条命令 1~2 分钟出 1080×1920 成片；产物落在交付目录，结果 JSON 里有 `files` / `duration` / `storage`。

`kind` 怎么选：

- `short`：只有文案，时间轴让引擎自己算（首选）
- `timeline`：已经算好时间轴的 spec，只渲一遍（`promo_envoy.py` 这类生成器产出的）
- `paper` / `slides` / `motion` / `jianying`：横屏和其它引擎
- `materials`：只找素材，不出片

`timeline` 是原始透传，**不会**帮你把文案展开成时间轴；把 `short` 的脚本按 `timeline`
提交会得到 `spec 错误: video 至少要有 1 段`，这不是 bug。

### 2.1 宣传片（Envoy promo）也有一条命令

`short` 管的是「要点列表」版式；宣传片要「一屏一句话 + 一个具体事实」的自定义卡面，
所以那条链路在 `tools/promo/`，入口是 `tools/promo/build.sh`：

```bash
tools/promo/build.sh                  # 幂等：文案/配音/人物层/spec/成片哪一步的产物够新就跳过
tools/promo/build.sh --from tts        # 改了配音逻辑、稿子没换
tools/promo/build.sh --from spec       # 只改了卡面排版
tools/promo/build.sh --force           # 全量重做（含立绘层，约 40 秒）

# 让流水线自己出稿（不过手写 SCENES），稿子先过 copy_lint 才允许配音：
PROMO_ANGLE=pain PROMO_SEED=0 tools/promo/build.sh
PROMO_ANGLE=proof PROMO_SEED=2 PROMO_WORK=~/CreatorStudio/work/envoy-promo-gen \
  ./tools/promo/build.sh --no-deliver    # 试另一版，不碰默认交付
```

它依次做：**文案（可选，见下）** → 配音（`tools/promo/tts_envoy.py`，文案 import
`promo_envoy.SCENES`，念 `say` 字段，句末标点=换气位；同一步里就产出 `a0X.words.json`
逐字轴与 `a0X.beats.json` 拍边界，所以不需要另跑对齐工具）→
卡面 + 立绘层 + 时间线（`deploy/presenter/promo_envoy.py`）→ 渲染（`timeline_render.py`）
→ 响度（线性增益 + `alimiter`）→ 交付（`tools/verify_delivery.py` 16 KB 冷读拷贝 + 回读校验）。

目录默认 `~/CreatorStudio/work/envoy-promo`（`PROMO_WORK` 可改），交付默认
`/Volumes/US202/CreatorStudio/deliver/envoy-promo.mp4`（`PROMO_DELIVER` 可改，
`--no-deliver` 只出到工作目录）。默认仍是 `deploy/presenter/promo_envoy.py` 的
`SCENES[*]["say"]` **手写稿**：不设 `PROMO_SCRIPT`/`PROMO_ANGLE`/`PROMO_SEED` 时输出与
改流水线之前完全一致，TTS 和卡面都读同一份，不要再抄第二份。

**文案自动生成（可选，2026-11 加）**：`tools/promo/script_gen.py` 从事实库
`tools/promo/facts/envoy.json` 拼稿，只贴 `claim`/`say` 原文、自己不算任何数字；
三个叙事角度 `pain`/`proof`/`candor`，`--seed` 换说法但同一 seed 完全可复现。
产物写到 `<WORK>/script.json`，并通过 `PROMO_SCRIPT` 让 `promo_envoy.py` 用它替换
`SCENES`。稿子先过 `tools/promo/copy_lint.py` 这一关（卡面事实必须逐字等于事实库原文、
镜头里每个阿拉伯数字都能在事实库找到、收尾句逐字在位、版式长度上限），**不过关整条流水线
直接失败**，不会带着编的数字去配音。

    python3 tools/promo/script_gen.py --list
    python3 tools/promo/script_gen.py --angle candor --seed 1 --out /tmp/s.json
    python3 tools/promo/copy_lint.py /tmp/s.json        # 手写/LLM 稿也用它验收

**挑稿（2026-11 加）**：`tools/promo/copy_score.py` 给每份稿子打分（事实密度 / 具体性 /
推进力 / 冗余 / 节奏 / 钩子收尾 / 事实去重，各维度都能数出来，同一份稿永远同一分；
「冗余」不只比镜头之间，也扣**旁白把卡面标题/标签再念一遍**——卡面抢不了耳朵的活），
`PROMO_ANGLE=best` 就会扫 3 角度 × 9 seed、丢掉 lint 不过的、取总分最高那版再往下做，
候选表和落选稿留在 `<WORK>/cand/`：

    python3 tools/promo/copy_score.py --rank --top 8     # 打印候选表
    PROMO_ANGLE=best tools/promo/build.sh                # 自动挑最高分那版出片

`copy_lint.py` 另外拦两类「说了等于没说」：同一行事实在两屏重复念、中段三屏标题同字开头
（生成器会先自己换一版说法，换不掉才判失败）。

配音缓存用 `<WORK>/audio/.script_id`（稿子 sha 或 `default`）判，不看 mtime：
手写稿和生成稿混用而音频没重配，时间轴会和旁白对不上。

**写文案的硬规则**（`script_gen.COPY_RULES`，`copy_lint.py` 里有对应的 `PAD` 词表会直接判失败）：

- 每句话都要推进论证——给信息、给因果、给结论、给动作。只为「万一被质疑」而存在的句子删掉。
- 不写免责声明式语气：「仅供参考 / 如有出入以实际为准 / 我们没做人工判分 / 你自己验一下就知道」
  都是这条的反例。可信度交给标签（自测 / 未验证）、出处、原始数字，不交给形容词和自证。
- 同一个事实只标一次「自测」，不再追加一句解释它为什么可信；读者追问时再答。
- 法规/平台强制披露的（广告标识、数据来源）保留，但压到最短一句、放在该在的位置。

### 2.2 写作台的成稿也能出片（`tools/bridge/`）

外部写作台（[dsh-media-desk](https://github.com/woniuniuniu/dsh-media-desk)）出选题、标题、
封面文案、分镜表，但**不配音、不渲染**；我们的强项正好是那后半段。接缝只有一份数据，
所以这里是一层转换脚本，不是插件、不 import 对方代码：

```bash
PY=~/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python
"$PY" tools/bridge/from_media_desk.py <production.json|state.json> --out /tmp/desk            # 只算时间轴
"$PY" tools/bridge/from_media_desk.py tools/bridge/tests/production.json --out /tmp/desk --render
"$PY" tools/bridge/from_media_desk.py ~/.dsh/media-desk/state.json --id 3f2a --submit        # 排队出片
```

它读的字段、兜底规则、环境变量和已知边界写在 `tools/bridge/README.md`，改之前先看那一份。
三条要点：`shots[].voice` 缺了直接拒绝（没旁白的镜头不出片）；每镜时长取
`max(它的 seconds, 配音 + 0.35s)`，不为了凑它的数把句子切掉；没有分镜图时用
`promo_envoy.card` 的卡面兜底，不留黑屏。

只做「写作台 → 我们」单向：反向会变成两边各存一份状态的双向同步，冲突处理没有收益。
口播规则只有 `script_gen.COPY_RULES` 一处，别在别处再抄一份。

## 3. 服务契约（agent 别猜路由）

- 存活探测是 `GET /healthz`（`{"ok": true}`）。`/health` 不存在，探它会得到 `{"error":"not found"}`，
  容易被当成「服务挂了」。
- `POST /jobs {"kind": …, "payload": {…}}`；`GET /jobs`、`GET /jobs/<id>`、`GET /jobs/<id>` 的
  `error` 字段就是失败原因（现在会带回引擎日志最后几行，不再只说「详见 log」）。
- 交付目录由 `storage` 决定，默认 `/Volumes/US202/CreatorStudio/deliver`；结果里的
  `storage` 字段永远以实际落点为准。
- 任务在 `<STUDIO_HOME>/state/jobs/<id>/` 下留 `job.json`、`log.txt`，排查先看这两个。

## 4. 三个已知陷阱

**a) 交付盘的「大块读」会串数据。** 这块 USB 盘（`/Volumes/US202`）读已经冷掉的大文件时
会随机返回别的字节，块越大越容易踩：同一个 23 MB 文件连续读 8 遍，16 KB 块 8/8 正确，
64 KB 块只有 5/8 正确，128 KB 以上基本全错。症状是 `shasum` 对不上、`ffmpeg` 报 h264
解码错、`cp` 出来的副本坏——**文件本身没坏**（错数据是随机的，能反复读出同一个正确
sha256 就说明盘上字节是对的）。所以：校验/拷贝用 `tools/verify_delivery.py`（16 KB +
`F_NOCACHE`），不要用 `shasum`/`cp` 下结论；`./bin/studio ready` 会自动做冷读对比。
真正的修复是换线/换盒，不是重渲。

**b) 渲染 spec 的校验比看起来严。** 任何 `music` 键都必须带 `music.src`（不需要配乐就别写这个键）；
`video` 列表不能为空。这两条以前只表现成一句 `命令退出码 2`。

**c) `char_layer` 立绘是最贵的一步**，但比看起来快得多，而且现在有缓存：

| 场景 | 12 s / 360 帧 | 57 s 宣传片（1715 帧） |
|---|---|---|
| 改动前的单进程画帧 | 33.8 s（画 15.0 + 编码 4.3 + 预处理） | 约 175 s |
| `CHAR_LAYER_JOBS=8`（默认） | **21.3 s**（画 2.6 + 编码 4.3 + 预处理） | 实测 8.8 + 20.2 + 预处理 ≈ 42 s |
| 输入没变、命中缓存 | **1.5 s** | 约 2 s |

- 画帧按段/帧分给 8 个进程（`_draw_jobs()` 在 `<120` 帧时退回单进程），结果与单进程
  **逐像素一致**（实测两版可 360 帧解码回来字节完全相同）；只有 vp9 码流字节会不同，
  不影响画面。
- 缓存是**整层内容寻址**：size/fps/时长/种子/每段的 pose/mouth/outfit/lip 音频、
  outfit 资产目录签名、`char_layer` 等模块源码签名一起算 key，落在
  `<workdir>/<name>.char.json`；命中要求 key 相同 **且** webm 字节数相同，然后直接返回。
  所以任何一处改动都会让整层重画（不是逐帧增量），但「什么都没改只重渲」是秒级。
  `CL.build(..., rebuild=True)` 可强制重建。
- 剩下的固定开销是大头：约 11 s 的贴图预处理（24 张 sprite 解码/composite）与
  约 4.3 s/12 s 的单进程 vp9 编码。要再快就往这两处下手，别再怀疑画帧。
- 改文案但没改口型/姿态时，立绘层本就会命中缓存；只有姿态、口型音频、outfit 或
  `char_layer` 代码真的变了才需要重画。

## 5. 画质与合规基线

- 响度：−14 LUFS 上下，真峰 ≤ −1.5 dBTP。做法是线性增益 + `alimiter`，不要用动态 `loudnorm`（会抽气）。
- 配音：`edge_tts` 逐句合成，F0 中位数落在 190~265 Hz 才算「适合旁白」，超过 275 Hz 偏亮。
- 数字上屏必须有出处：要么来自本项目自己的评测表，要么同时标注「自测」，不要写没跑过的结论。
- 没验证过的链路（上传/预签名、真实发布、无人值守剪映导出）必须显式标注「未验证 = 0」，不要省略。

## 6. 协作约定

- 这个仓库可能同时有另一个 agent 在用：只改自己负责的文件，不要 `git add -A`，不要回滚别人的改动。
- 长任务（立绘、批量渲染）放后台并把日志写文件，不要在前台阻塞。
- 提交任务前先 `./bin/studio ready`：除「交付盘」一项外必须全 ✓。
  交付盘若报大块读 ✗，那是这块盘的环境缺陷（见 4a），照常出片，校验走小块即可，不要重渲。

## 7. 常用路径

- 仓库：`/Users/apple/Desktop/work/Studio/creator-studio`
- 线上：`~/CreatorStudio/runtime`（`sync_runtime.sh` 同步）
- 状态/任务：`~/CreatorStudio/state/jobs/`
- 交付：`/Volumes/US202/CreatorStudio/deliver`（另有一份内置盘副本 `~/CreatorStudio/deliver`）
- 解释器：`~/CreatorStudio/runtime/integrations/vendor/MoneyPrinterTurbo/.venv/bin/python`
