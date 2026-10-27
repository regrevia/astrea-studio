# 一轮出片 checklist

一轮 = 写稿 → 预算场景时长 → 渲染 → `video_qa` → `lipsync_qa`（逐场）→ 交付。
每条都能机器判，判据写在 `lipsync-paradigm.md`（§1.1 第四把尺、§1.3 最小可测口径）。
顺序不能换：先字数后时长、先画面后口型，否则返工全在最后一步。

## 1. 写稿

- [ ] 口语稿按「一句一口型轨」写：一句话一条 `lip_audio`，句间留 ≥120ms 静音。
- [ ] 数字、年份、英文缩写先按读音改写成中文（口型跟拼音走，跟字形走会错）。
- [ ] 音色按 `voice-catalog.md` 选：默认 `sami:BV411_streaming`；要逐字时间就用
      `edge:zh-CN-XiaoxiaoNeural`（`rate=0.98`、`pitch=+2%`）。

## 2. 场景时长预算

- [ ] 每场时长按**拼后实测**定：`scene.seconds >= 实测 mp3 秒数 + 0.35s` 余量。
      场景 `say` 是多段拼出来的（每句一次 `_tts` + `aresample` + `aevalsrc` 填 gap + `concat`），
      **预算要按拼后实测**，不能拿单句时长或字数估——这是上一轮返工最多的一条。
- [ ] 拼过之后回读 `merged` 字符时间轴（`_retime` 已按 `tempo` 缩放），确认句尾没被截。

## 3. 渲染

- [ ] 一帧一 JPEG 只走 headless Chrome，`renderFrame(t)` 必须是纯函数（同 t 同画面）。
- [ ] 渲染完先看 `video_qa.py`：`RESIZE_MAX`、`CUT_DIFF`、`FREEZE_DIFF`、`SOLID_MIN` 无红。
- [ ] 手绘链路的嘴部尺寸/位置不许漂（`FACE_FRAC` 之外的画面元素不动）。

## 4. 口型体检（逐场，不许只跑全片）

```bash
# 每场各跑一次；全片平均合格不算过
python3 integrations/mac-worker/tools/lipsync_qa.py \
  --audio <本场>.mp3 --states <本场>.lip.json --json > qa/<场>.json
```

- [ ] `drift_ms` 在 ±一帧内（轨道长度 `= round(dur*50)`）。
- [ ] `|lag_ms| ≤ 60ms` 过；`≤ 30ms` 好档。`r < 0.30` 只打 ⚠，不判失败。
- [ ] **开场领先张开 `lead_ms_max ≤ 60ms`**（第四把尺，逐场各算）。
- [ ] 静音必闭：`lead_frames = 0`、`tail_frames = 0`、
      `false_open ≤ 5%`（分母只算 ≥120ms 静音段）、`stop_misclosed = 0`（40~80ms 塞音）。
- [ ] 成片再跑一次 `--video`：只认延迟 ≤60ms 与静音误开 ≤5%；
      渲染级数字必然高于轨级（分辨率/抽帧相位/`lip_t0`），**不得据此判轨不合格**。

## 5. 交付前

- [ ] `--json` 能直接 `json.load`（numpy bool 已清洗；退出码 0/1/2 含义不变）。
- [ ] 成片抽 3 帧贴进交付说明：起播首帧（嘴闭）、句中（开口）、句尾（收口）。
- [ ] 本地 commit，不 push；把 `qa/*.json` 的路径一起报给 lead。
