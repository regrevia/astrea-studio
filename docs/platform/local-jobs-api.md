# 本机任务接口：平台自己接单出片

面板要「点一下就出片」，缺的从来不是渲染能力（`worker.py` 早就把四个引擎接好了），
而是一条**本机、不依赖外部服务**的通道：容器化那条路要 docker API + S3 凭据，本机跑不动。
`integrations/mac-worker/jobs.py` + `capabilities_server.py` 的 `/jobs` 这组路由就是补这一层。

一句话：**提交 → 后台子进程跑引擎 → 状态/日志/产物可查 → 失败留下可读原因。**

## 接口

服务默认 `http://127.0.0.1:8899`（launchd `local.creatorstudio.capabilities`，端口 `--port` 可改）。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/jobs` | 提交，`{"kind":"paper","payload":{...}}`，返回 202 + 任务对象 |
| GET | `/jobs?limit=30` | 最近任务（按创建时间倒序） |
| GET | `/jobs/<id>` | 单个任务状态机 |
| GET | `/jobs/<id>/log?tail=200` | 日志尾巴 |
| POST | `/jobs/<id>/cancel` | 取消（SIGTERM 整个进程组） |
| GET | `/deliverables?limit=50` | `$STUDIO_HOME/deliver` 下的成片清单 |
| GET | `/assets` | 资产总览：`deploy/specs/` 的规格（含可提交的 kind）、`$STUDIO_HOME/materials/shots` 的镜头素材、成片清单；纯扫目录 |

任务对象字段：`id kind status progress stage createdAt updatedAt startedAt endedAt`
+ 运行中 `pid`、成功时 `result`、失败时 `error`（一行原因）与 `traceback`（尾 2000 字）。
`status` 取 `queued | running | succeeded | failed | cancelled`。
不存在的 id 返回 404（`{"error":"没有这个任务：'xxx'"}`），未知引擎返回 500 且原因里带可选值。

## 四种引擎

`kind` 与提交的 payload（路径都用绝对路径；`spec` 可直接给对象，提交时落盘到
`$STUDIO_HOME/state/jobs/<id>/spec.json`，同时兼容 `specPath`）：

| kind | 必填 | 可选 | 产物 |
| --- | --- | --- | --- |
| `paper` | `specPath` / `spec` | `shots` `caps` `style`(`paper`/`schematic`) `slug` `out` | 手绘纸片成片 mp4 |
| `slides` | `specPath` / `spec` | 引擎自己的字段原样透传 | 幻灯片成片 |
| `jianying` | `specPath` / `spec` | `draftName` | 剪映草稿目录（可直接开） |
| `motion` | `specPath` / `spec` | `{engine, voice, meta, scenes[{id,kick,shots}]}`，见 `deploy/specs/envoy_motion.json`（**voice 只能给 SAMI 音色**，引擎内部写死 sami_tts） | 动效成片 |
| `slides` | `specPath` / `spec` | `scenes[{title,caption}]` + 可选 `chapters/logo_a/logo_b`，见 `deploy/specs/envoy_slides.json` | 卡片分镜（**1920×1080 横版**，引擎写死） |

规格文件允许两层：外层 `{"projectId","title","spec":{...}}` 或裸 spec —— 引擎拿到的
永远是内层（`points`/`narration`/`scenes` 在那一层）。这条不做的话，`factcard` 会报
「至少需要一个要点」，因为它在空对象里找 `points`。

`paper` 的 `shots` / `caps` 与 `deploy/schematic_preview.py` 的参数一一对应
（`sketch:<名字>` 或 `<图片绝对路径>#<高>#contain#<宽>`），`caps` 给等长数组、元素可为空串。

## 例子

```bash
# 手绘纸片：换稿只改 specPath，接口不变
curl -s -X POST http://127.0.0.1:8899/jobs -H 'Content-Type: application/json' -d '{
  "kind": "paper",
  "payload": {
    "specPath": "/abs/path/deploy/specs/envoy_real.json",
    "shots": ["sketch:link", "/abs/q_how_i.png#470#contain#340", "sketch:timeline"],
    "caps": ["", "", ""],
    "style": "paper",
    "slug": "envoy-f49",
    "out": "/abs/deliver/envoy-f49-paper.mp4"
  }
}'

curl -s 'http://127.0.0.1:8899/jobs?limit=3'
curl -s 'http://127.0.0.1:8899/jobs/<id>/log?tail=40'
```

## 状态与产物落在哪

`STUDIO_HOME`（默认 `~/CreatorStudio`）下：

```
state/jobs/<id>/job.json     状态机（原子写：先 .tmp 再 replace）
state/jobs/<id>/log.txt      引擎输出 + 失败堆栈
state/jobs/<id>/result.json  成功时的结构化结果
state/jobs/<id>/spec.json    提交时的规格快照（换稿后仍能复现这一条）
state/jobs/<id>/work/        中间帧 / 草稿 / 渲染工作目录
deliver/                     成片（DELIVER_DIR 可覆盖）
```

`JOBS_DIR`、`DELIVER_DIR`、`JOBS_MAX_RUNNING`（默认 2）、`STUDIO_PY`、`NODE_BIN` 都可
用环境变量覆盖；其余一律走 `Path.home()` 与仓库相对路径，没有写死的主机路径 ——
这条由 `scripts/check_public_surface.sh` 守门。

## 失败怎么读

失败不吞：`error` 是一行能直接看懂的原因，`traceback` 给定位，`log.txt` 里有引擎自己
打印的那一行。两种典型：

- `RuntimeError: 命令退出码 2：… （详见 log）` → 看 `log.txt` 里 argparse 的原话，比如
  `unrecognized arguments: --fps 30`；
- `ValueError: factcard 至少需要一个要点（标题::说明）` → 规格没进到内层，见上文。

## 与 worker.py 的分工

| | worker.py | jobs.py |
| --- | --- | --- |
| 场景 | 容器化 / 多机 | 本机单人 |
| 传输 | S3 + docker API | 本机 HTTP + 子进程 |
| 必填 | `runId` + S3 凭据 | 只有 `kind` + `payload` |
| 产物 | 上传后给 URL | 直接落 `$STUDIO_HOME/deliver` |

两边共用同一批 `render_*` 引擎函数，行为一致；不要在本机再写一套 `ffmpeg` 拼装脚本
绕过它们 —— 那样口型/版式/响度三处校准会各自漂。

## 成片落在哪

`paper` 直接由引擎写到 `$STUDIO_HOME/deliver/`；`slides`/`motion`/`jianying` 的引擎
把产物写在自己的工作目录里，任务成功后由队列**复制**一份到
`$STUDIO_HOME/deliver/<slug>-<jobId><扩展名>`（`slug` 不写就用 `kind`），
`GET /deliverables` 和 `bin/studio films` 列的就是这里。工作目录保留原始产物供复查，
但只有 deliver 里那份算交付物——清工作目录不会丢成片。

## 自检：别让降级静默发生

两条硬约束现在都会吵：

1. **口型轨必须真的建出来。** `char_layer` 崩了以前只打一行 stderr 就退回逐段静帧——
   片子照出，口型没了，成片级质检指不出是哪一步丢的。现在默认 **抛错并让任务失败**，
   真要静帧片得显式 `STUDIO_ALLOW_STATIC_CHARACTER=1`。
   改完口型相关代码先跑符号自检（2 秒）：

   ```bash
   python integrations/mac-worker/tools/check_char_layer.py
   python integrations/mac-worker/tools/check_char_layer.py a00.mp3 "你好世界" 2.4   # 真跑一条轨
   ```

   第二种用法会顺带核对「轨长 == dur*fps」，长度不对直接非零退出——轨比音频长
   一点，画到时间轴上就是整片漂移。

2. **仓库改了要同步到线上那份。** launchd 跑的是 `$STUDIO_HOME/runtime/` 的拷贝，
   不是仓库。忘了同步 = 线上还是老逻辑（口型漂移那类修复会白做）：

   ```bash
   bash scripts/sync_runtime.sh
   launchctl kickstart -k gui/$(id -u)/local.creatorstudio.capabilities
   ```

