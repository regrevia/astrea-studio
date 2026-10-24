# 开源计划（C 路线）

目标：把「自己的时间轴 spec → 母版 → 多平台规格」这条**全程无 GUI**的渲染/交付链路
开源出去，让别人不装剪映、不接内网服务也能跑；平台本体（AiToEarn fork）跟随上游许可证。

本文件只写事实与步骤，不写宣传。

## 1. 现状盘点

仓库是 AiToEarn 的 fork（MIT，见 `LICENSE`）。本工作区自己新增的部分：

| 目录 | 内容 | 行数 | 依赖 |
| --- | --- | --- | --- |
| `integrations/mac-worker/export/timeline_render.py` | spec → 母版（h264/aac，CRF 16，faststart） | 444 | ffmpeg 9 + Pillow（有字幕时） |
| `integrations/mac-worker/export/deliver.py` | 母版 → 各平台规格（blur/contain/crop） | 397 | ffmpeg/ffprobe |
| `integrations/mac-worker/export/platform_profiles.py` | 17 平台档案（分辨率/帧率/码率/封装） | 502 | 仅标准库 |
| `integrations/mac-worker/export/test_*.py` | 单元测试 | 922 | 标准库 `unittest` |
| `integrations/mac-worker/*.py` | 平台任务执行器（领取/渲染/上传/草稿登记） | ~4k | 标准库 + 上述模块 |
| `deploy/*` | 本机运行栈：镜像构建、容器重建、worker 安装、验收脚本 | ~2k | bash/python3 |

测试现状（全部用 `python3 xxx.py` 直接跑，不依赖 pytest）：
`test_worker_timeline.py` 7 + `test_timeline_render.py` 18 + `test_deliver.py` 30 +
`test_platform_profiles.py` 34 = **89 项通过**。

## 2. 拆成两个可独立发布的仓库

### A. `creator-studio-timeline`（先发这个）

只包含 `export/` 三件套 + 三个测试 + 一个 README。任何人都能用：

```bash
python3 timeline_render.py --spec spec.json --out master.mp4 --json
python3 deliver.py --master master.mp4 --platform douyin,bilibili --outdir out/
```

**已完成**：`bash scripts/export_timeline_repo.sh --smoke` 生成 `dist/creator-studio-timeline/`
（README + `examples/spec.json` + `examples/make_demo_assets.py` + `requirements.txt` +
`.github/workflows/ci.yml` + 生成物 `expected_labels.py` + MIT `LICENSE`
+ 三模块与其三个测试）。验证：发布包内 `unittest` 48 项 + 平台档案 34 项全绿；
真渲染 9 秒 1080x1920 母版并出 douyin 规格；把发布包整目录拷到 `/tmp` 后按 README
四步原样复现成功（缺 `--spec`、素材路径不存在均按 spec 错误 `EXIT=2`）。
打包脚本顺带做公开面自检（个人路径/内网地址/凭证），并从 `platform_registry.LABELS`
现抽 `expected_labels.py`，避免发布包里「测试和自己比对」。

- spec 格式即 README 的核心内容（画布/切片/字幕/BGM/音频），字段含义与取值范围要写死。
- 平台档案必须携带 `source` 字段并诚实标注：当前 17 个平台**全是 `assumed`**，
  没有任何官方原文核实过。公开发布时这条要写在最显眼的位置，否则就是在替平台方
  承诺参数。

### B. `creator-studio-worker`（可选，后发）

`integrations/mac-worker/` 整个目录 + `deploy/local.creatorstudio.worker.plist` 模板。
它不能独立跑（要对接平台 API），发布价值在于「一个后台进程如何在 macOS 上
安静地渲染并上传」的实现参考。

## 3. 发布前必须过的门（已具备）

```bash
./scripts/check_public_surface.sh
```

检查四类东西，任一命中即失败：

1. `个人绝对路径` —— 用户名出现在公开 diff 里就是泄漏，哪怕路径本身无害；
2. `内网地址` —— 只允许 `127.0.0.1`（作为文档里的默认值），
   不允许 `192.168/10.x/172.16-31`；
3. `疑似凭证` —— `sk-*`、长 Bearer、以及形如 `token = "<24+ 字符>"` 的赋值；
4. `禁发文件` —— `compose.env`、`*.token`、`integrations/vendor/`（第三方代码，
   有自己的许可证，且与本能力无关）、`docs/probe/export-panel/runs/`
   （剪映面板的一次性抓包证据，含专有客户端信息，已从 git 移除、只留本机）。

已经在本次整理中处理掉的问题：plist 模板（`__HOME__` 占位，安装时 `sed` 渲染，
渲染结果与已安装文件逐字节一致）、MCP 桥用法示例、平台矩阵文档与面板抓包里的
用户名。

## 4. CI

- **单元测试**：`python3 -m unittest discover`（48 项）+ `python3 test_platform_profiles.py`
  （34 项，pytest 风格、`unittest` 发现不到，必须单列一步），Linux runner 即可，无需 ffmpeg。
- **渲染冒烟**：需要 ffmpeg 9 + Pillow。素材不落库，用
  `python3 deploy/make_demo_assets.py` 现场合成（三支 3 秒 1080x1920 片段 +
  一段 BGM），再跑 `timeline_render.py` 出 9 秒母版，用 ffprobe 断言
  时长/分辨率/编码。macOS runner 才有 Pillow 预装优势，Linux 需 `pip install pillow`。
- **交付冒烟**：`deliver.py --dry-run` 断言 17 个平台的尺寸/码率/封装决策，
  不实际编码（秒级，适合每个 PR）。

不要在没有 ffmpeg 的 runner 上跑渲染类测试并跳过——"跳过"会让人以为通过。

## 5. 文档要写死的四条限制

1. **不依赖剪映**：只依赖 ffmpeg；也因此**没有** `drawtext`/`subtitles`/`ass`
   滤镜时（本机 ffmpeg 9 就没有 freetype/libass）字幕走 Pillow 出 RGBA PNG 再
   `overlay`，而不是滤镜链。
2. **不解析剪映草稿**：剪映 11.6 会重新加密 `draft_info.json`，抓它的结构等于把
   自己的主链路挂在一个会变的私有格式上。自有 spec 是唯一事实来源。
3. **文字尺寸相对画布高度**：换分辨率不会错位；这也是唯一不需要按平台改字幕的原因。
4. **平台参数是 `assumed`**：矩阵可以照着改，但别声称是官方规格。

## 6. 落地顺序

| 阶段 | 内容 | 完成判据 |
| --- | --- | --- |
| P0 | 公开面门 + 本文档 + plist 模板化 + CI 骨架 | ✅ `check_public_surface.sh` 通过；CI 见 `.github/workflows/timeline.yml`（未在真实 runner 上跑过） |
| P1 | 拆出 `creator-studio-timeline` 并发布，README 用真实 spec 示例 | 陌生机器上两条命令出片 |
| P2 | 发 `creator-studio-worker`，写清它需要什么平台接口 | 与本仓库文档一致 |
| P3 | 平台本体的时间轴任务表单/发布侧接入（见 `STATUS.md`） | ✅ 已完成：表单建任务 → 后台出片 → 平台规格 → 发布台账（run `6adc54b57aa82b66b89b9f30`） |

P1 的验收必须在一台**没装过本项目**的机器上做一次；本机能跑通只证明没忘东西，
不证明别人能跑通。
