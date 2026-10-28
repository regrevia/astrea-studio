# 成片库：导出产物去哪、叫什么

平台自己出的片子只允许落在**成片库**一个地方。任务中转目录（`out/<jobId>/r<rev>/`）
是临时的，入库成功当场清掉，不留散件。

## 1. 目录骨架

根目录 `STUDIO_LIBRARY`，默认 `~/CreatorStudio/library`。

```
library/
  index.jsonl                       # 追加式台账，一行一条，永不改写
  .index.lock                       # 台账写入锁
  <项目名slug>__<id8>/              # 项目目录：人看得懂的名字 + 稳定短 id
    <YYYY-MM>/                      # 入库月
      <YYYYMMDD-HHMMSS>__<project>__<seq3>__<slug>__<platform>__r<rev>.mp4
      <同名>.json                   # sidecar：来源与体检结论
```

- 项目目录：`<项目名slug>__<id8>`，例如 `端到端验收-一体化宣发工作台__6ae235e6`。
  只写 id 认不出是哪个项目，只写名字在改名/重名时会撞到别人的作品，两个都要。
  晚于 2026-10-29 之前入库的旧目录（纯 id、或早期一律落 `studio/`）保持原样，
  台账里照旧可查，新入库一律走新命名。
- `YYYY-MM` 用**入库时间**，不是拍摄/生成时间；按时间找片子只看这一个维度。
- 同一次任务的不同修订版是不同文件（`r1`、`r2`…），旧版永久保留，回滚就是找旧文件。

## 1.1 旧目录迁移

早期入库不分项目、一律落 `studio/`，后来又只按 ObjectId 建目录。`library.py migrate` 做一次性搬迁：

```
# 纯 id 目录 → 项目名__id8
python library.py migrate --dir 6ae235e6aa561aad282987e0 \
    --project-name "端到端验收 · 一体化宣发工作台" --project-id 6ae235e6aa561aad282987e0

# studio/ 按 runId 找到所属项目后逐条搬（run_map: {runId: {name, id}}）
python library.py migrate --dir studio --run-map /tmp/legacy_run_map.json

# 找不到归属的散件归到 未归属/
python library.py migrate --dir studio --project-name "未归属"
```

- 先加 `--dry-run` 看清单；搬迁同时改文件名里的项目段、sidecar 的 `path/project/projectId/projectDir`，并回写台账路径字段（台账"永不改写"的唯一例外）。
- 找不到 runId / 项目已删的文件留在原地，最后统一收进 `未归属/`，不静默丢。

## 2. 命名规则

```
<YYYYMMDD-HHMMSS>__<project>__<seq3>__<slug>__<platform>__r<rev>.<ext>
```

| 段 | 含义 | 规则 |
| --- | --- | --- |
| `YYYYMMDD-HHMMSS` | 入库时间 | 本地时区，秒级；`ls` 天然按时间排序 |
| `project` | 项目目录名 | `<项目名slug>__<id8>`；名字部分小写、保留中英文数字，其余折成 `-`，≤24 字符 |
| `seq3` | 同秒防撞序号 | 三位补零，从 `001` 起；同一项目同标题同秒才有机会 >001 |
| `slug` | 片子标题 | 同上字符集，≤32 字符；重复时后缀 `-02`、`-03` |
| `platform` | 目标平台 | `douyin` / `xiaohongshu` / `bilibili` / `generic` |
| `r<rev>` | 修订版 | 整数，跟随任务的 revision |
| `ext` | 容器 | `mp4` / `mov`，由导出配置决定 |

分隔符固定双下划线 `__`，段内不出现下划线（下划线会被转成 `-`），因此
`cut -d_` 之外的任何解析都不会切错。

## 3. sidecar

与视频同名的 `.json`，字段固定：

```
assetId path project projectId projectDir title slug platform revision
runId jobId draft draftPath duration bytes md5 container
landedAt meta{checks, profile, panel}
```

- `md5` 是入库时的实算值，用于跨机校验与去重。
- `meta.checks` 是导出体检结论（时长/分辨率/帧率/编码），不入库就不算成片。
- `runId`/`jobId` 是回到任务链路的唯一入口，`draftPath` 是回到剪映工程的入口。

## 4. 台账

`index.jsonl` 只追加，不改写、不排序。每行与 sidecar 同构。
`library.py verify` 对账：缺文件、大小不符、缺 sidecar 三类问题都会列出来。

## 5. 谁能写入

只通过 `integrations/mac-worker/library.py` 的 `land()` 落库：

- worker 的 `jianying_export` 与 `render_timeline` 成功分支自动调用，`output` 里带 `libraryPath` / `libraryAssetId` / `md5`（timeline 通道还带各平台规格的 `libraryEntries`）；
- 人工补录：`library.py land --video … --project … --title … --platform …`；
- 禁止直接把文件 `cp` 进库，也不要在 `out/` 里长期留成片。

## 6. 保留策略

| 位置 | 保留 | 谁清 |
| --- | --- | --- |
| `library/` | 永久 | 不自动清 |
| `out/<jobId>/r<rev>/` | 入库成功即删 | worker 自动 |
| `~/Movies/<draft>.mp4` | 剪映导出原始落点 | 入库后由导出驱动删除/覆盖无妨 |
| 剪映草稿 `assets/` | 跟随草稿 | 草稿删了才删 |

## 7. 与任务系统的关系

- 任务（`ProductionRun`）只认 `output.libraryPath`，前端展示库里的片子，不再拼 `out/` 路径。
- 一次导出 = 一个 `assetId`；同一 run 的多次导出按 `revision` 递增，不覆盖。
- 发布（`PUBLISH_SAU`）从库里取文件，不再回捞 `~/Movies`。
