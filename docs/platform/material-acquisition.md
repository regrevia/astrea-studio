# 素材获取

## 1. 现状（可运行）

`integrations/mac-worker/materials.py`

- `materials.py search --query "city night" --kind video --n 5 [--max-mb 60]`
- `materials.py get --query ... --n 3 [--max-mb 60] [--dry-run] [--root DIR]`

免 key provider：Wikimedia Commons（`action=query&generator=search&gsrnamespace=6` + `filetype:video|bitmap`）。
需要 key 的 provider（pexels/pixabay）暂未启用：`integrations/vendor/MoneyPrinterTurbo/config.toml` 里
`pexels_api_keys` / `pixabay_api_keys` 为空，填 key 后再接。

落盘规则（与成片库同一条纪律：只增不改）

```
<root>/_assets/<provider>/<YYYY-MM>/<slug>__<provider>__<id8>.<ext>
<同上>.json   # 来源/许可证/作者/原始URL/md5/字节数/抓取时间
```

- 落盘先写 `.part` 再原子改名，且 `< 1KB` 视为失败并删掉：不留半截文件。
- **同 md5 不重复落盘**（`find_duplicate()` 查 `_assets/**/*.json` 台账，命中就回已有路径并标
  `duplicate`）；确实要副本加 `--no-dedupe`。同名加 `-2`、`-3`；绝不覆盖。
- `materials.py verify [--root DIR]`：台账体检——文件缺失 / 空文件 / md5 不符三类别出来，有问题退出码 1。
- 默认 `STUDIO_LIBRARY` = `~/CreatorStudio/library`，素材与成片同在库根下、分 `_assets/` 与 `<project>/`。
- `--max-mb`（默认 60）挡掉 Commons 上 170MB~2GB 的 4K 原始文件：下载慢、后续转码也慢。

## 2. 卡点分析

`integrations/mac-worker/beats.py`（纯 numpy + ffmpeg，不装 librosa）

```
beats.py <audio> [--json out.json] [--max-beats N]
# bpm=103.36 offset=0.36 beats=13 confidence=0.356
```

1. ffmpeg 解 22.05k 单声道 PCM；20ms 帧能量，正向差分 = onset；
2. onset 自相关，滞后区间对应 60–200 BPM；
3. 倍频纠错：在 72–165 BPM 内选自相关分数最高的倍频（不做这一步会把 92 BPM 报成 185，实测复现）；
4. 扫相位，取落在 onset 峰最多的偏移；
5. 输出 `bpm/offset/beats[]`，直接喂 timeline spec 的 `music.bpm/offset/beats`。

## 3. 已跑通的组合

在线素材 → 卡点 → 渲染 → 入库（2026-10-28 实测）：

```bash
materials.py get --query "abstract motion background" --kind video --n 2 --max-mb 30
beats.py <bgm>.mp3 --json /tmp/beats.json
# 用 beats 生成 spec（music.beats + pulse{amount,decay}），render_timeline 渲染
```

产物：`~/CreatorStudio/library/studio/2026-10/20261028-173531__studio__001__卡点关键帧测试-在线素材__test__r1.mp4`
（1080x1920@30，11.9s，3 段 Commons 素材 + 13 拍脉冲 BGM，渲染 1.6s）

## 4. 已知缺口

- **视频片段上的显式关键帧不生效**：`export/timeline_render.py` 的 `zoompan` 只在 `still`（静帧）分支里挂，
  活动视频只走 `scale/crop`，`keyframes` 被解析、被校验、但不进入滤镜图（`[vc]null[vout]`）。
  静帧上的推拉正常。要在活动素材上做推拉，需要给每段先 `split→zoompan` 再 concat。
- Commons 素材以 `.webm` 为主，剪映不吃；要进剪映草稿必须先转 mp4（`ffmpeg -c:v libx264`）。
- 免 key 只有 Commons；pexels/pixabay 需要用户填 key。Openverse API 本机不可达（curl 000）。
