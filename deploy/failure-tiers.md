# 出片链路失败分级

平台链路是「表单 → API run → worker claim → 渲染 → 产物回写」。任何一步挂掉，
用户看到的都只是一句 `failed`。这张表把 worker 日志 / job log 里的真实报错串
对到五个分级，每级给一行「用户该改哪里」。排查顺序固定：先看 job log 首行，
再看 worker 日志里的 `kind=` 与异常。

job log 首行由 `integrations/mac-worker/pylocator.py` 写入，形如：

```
interpreter=~/CreatorStudio/runtime/integrations/mac-worker/.python3 source=shim:runtime python=3.11.15
```

## 1. 解释器缺失

症状（任一）：

- 进程 `exit 69`，stderr 只有 `You have not agreed to the Xcode license agreements`
- job log 首行是 `source=fallback:no-candidate-worked`
- `MPT venv missing at .../MoneyPrinterTurbo/.venv/bin/python; run deploy/bootstrap_mpt.sh`

确认：`sh deploy/py3.sh` 应打印 `.python3` shim 路径；`/usr/bin/python3 -c pass` 会 exit 69。

用户该改哪里：重装 venv（`bash deploy/bootstrap_mpt.sh`）或改用 `.python3` 启动，
不要用系统 `/usr/bin/python3`。

## 2. 依赖缺失

症状：

- `ModuleNotFoundError: No module named 'xxx'`（通常是 `pypinyin`、`loguru`、`PIL`）
- `sami tts unavailable: ...` / `VectCutAPI venv missing: ...`
- `S3 credentials are missing from config/compose.env`

确认：用 job log 首行那个解释器直接 `python -c "import xxx"`。

用户该改哪里：用该解释器装上缺的包（MPT 相关装进 MPT venv，`pypinyin` 走
`PYTHONPATH=$HOME/CreatorStudio/runtime/lib`），或补 `config/compose.env`。

## 3. 渲染超时

症状：

- `MPT exited 124`、`ffprobe rejected the output` 里带 `Invalid data found`
- `timeline spec` 渲染 `subprocess.TimeoutExpired`（`export/timeline_render.py` 默认 7200s）
- run 停在 `running`，worker 日志长时间没有新 `progress`

确认：`tail -f ~/CreatorStudio/logs/<jobId>.log`，看最后一行卡在哪个文件/哪一帧。

用户该改哪里：降 `nThreads`（`config/studio.json` 的 `render.nThreads`）或缩短
`spec.timeline` 的时长；单机并行多条只会更慢。

## 4. 素材读不到

症状：

- `assetBase 不存在: ...`
- `no such file or directory` / `ffmpeg ... Invalid data`
- `run has neither spec.script nor brief; nothing to render`
- `MPT output missing or suspiciously small`

确认：把报错里那条绝对路径直接 `ls -l` 一遍；`spec.materials[]` 是否都真实存在且可读。

用户该改哪里：换成仓库内/`~/CreatorStudio` 下的素材路径，或先把素材同步到
`assetBase` 指向的目录。

## 5. 参数不合法

症状：

- API 返回 `12850 Production project not found`（项目 id 是占位符或不属于自己）
- `timeline spec 不合法: ...`（缺 `canvas` / `scenes` 为空 / 字段类型不对）
- 表单侧 `factCard` 校验失败（`topicRequired` / `minPoints` / `pointFormat` / `noProject`）
- `jianying spec has no scenes`

确认：`curl` 拿 run 详情的 `error` 字段；对照 `deploy/specs/*.json` 的结构。

用户该改哪里：补/换 `projectId`（`deploy/submit_spec.sh` 会自动取最近项目），
按 `deploy/specs/envoy_real.json` 补齐 `spec.timeline` 必填字段。

---

## 今晚的一条可用链路（实测命令）

```bash
cd <repo>
STUDIO_SKIP_PREFLIGHT=1 bash deploy/submit_spec.sh deploy/specs/kv_cache.json 1
```

提交后 run 从 `running` 变 `succeeded`，worker 把成片路径写回 run 的
`output.videoPath` / `output.videoUrl` / `output.videoQa.video`，并登记素材
`output.materialId`。

实测（2026-10-27，runtime 副本 + launchd worker）：

- `kv_cache.json`（engine=timeline，表单「出 mp4」）= run `6adff53798849e9680310566`，
  17.152s，qa=True，母版 `~/CreatorStudio/timeline/6adff53798849e9680310568/master.mp4`（5.1 MB）
- `envoy_jianying.json`（engine=jianying，表单「出草稿」）= run `6adff60498849e96803107cf`，
  30.821s，草稿目录 `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/studio_6adff60498849e96803107d1/`

已知缺口：草稿通道的 `draftPath` 只进了 worker 日志，服务端 complete 的白名单
没有这个字段，所以 run 的 `output` 里查不到草稿路径（只有 `duration` / `copyCheck`）。
要回写需要在后端结果 DTO 里加字段，不属于本机解释器硬化范围。
