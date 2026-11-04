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
tools/promo/build.sh                  # 幂等：配音/人物层/spec/成片哪一步的产物够新就跳过
tools/promo/build.sh --from tts        # 改了文案（promo_envoy.py 里的 SCENES）从头做
tools/promo/build.sh --from spec       # 只改了卡面排版
tools/promo/build.sh --force           # 全量重做（含立绘层，约 40 秒）
```

它依次做：配音（`tools/promo/tts_envoy.py`，文案直接 import `promo_envoy.SCENES`，
念 `say` 字段，句末标点=换气位；同一步里就产出 `a0X.words.json` 逐字轴与
`a0X.beats.json` 拍边界，所以不需要另跑对齐工具）→
卡面 + 立绘层 + 时间线（`deploy/presenter/promo_envoy.py`）→ 渲染（`timeline_render.py`）
→ 响度（线性增益 + `alimiter`）→ 交付（`tools/verify_delivery.py` 16 KB 冷读拷贝 + 回读校验）。

目录默认 `~/CreatorStudio/work/envoy-promo`（`PROMO_WORK` 可改），交付默认
`/Volumes/US202/CreatorStudio/deliver/envoy-promo.mp4`（`PROMO_DELIVER` 可改，
`--no-deliver` 只出到工作目录）。文案只有一个源：`deploy/presenter/promo_envoy.py` 的
`SCENES[*]["say"]`，TTS 和卡面都读它，不要再抄第二份。

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
