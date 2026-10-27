# 剪映深度使用：用草稿 JSON 出片

从「会点按钮」到「能批量、能进流水线」：剪映（JianyingPro 11.6）的工程就是一份 `draft_content.json`，把字段写对，片子就在那儿；GUI 只是这份 JSON 的编辑器。

本文只写**本机实测过**的东西：每条能力后面跟字段片段 + 落在哪个文件。判断标记：

- 【已验证】= API 调用成功 **且** 字段真的出现在落盘草稿里
- 【从 JSON 推断】= 只从静态字符串/ schema 读到，没有实跑
- 【需 GUI/联网】= 依赖界面操作或云端资源，脚本层拿不到

---

## 1. 结论先说

1. **草稿层能覆盖绝大多数剪辑动作**：转场、蒙版、滤镜、文字（描边/阴影/背景/入场出场动画）、画中画、音频淡入淡出、关键帧、混合模式，全部能写成字段。18 秒满配样片 = 25 个 API 调用 + 一次手写补丁，落盘 74 KB。
2. **卡住的是三件事**，不是表达能力：花字/字体等云端资源的 id 拿不到；曲线变速、自由曲线缓动、HSL 调节有字段但库没暴露（要手写）；导出这一步只能走 GUI（本轮屏幕锁定，没跑成）。
3. **能自动化的边界很清楚**：素材 → 轨道 → 效果链 → 草稿，脚本全包；剪映内导出、云端字幕识别、贴纸商城，必须人工/GUI。

---

## 2. 一条命令出片

```bash
D=~/CreatorStudio/deliver/jianying-draft
# 1) 生成草稿：6 屏素材 + 转场 + 蒙版叠层 + 滤镜 + 文字动画 + 音频淡入淡出（25 个 API 调用）
cd ~/CreatorStudio/jianying/VectCutAPI && .venv/bin/python $D/build_draft.py
# 2) 补库 dump 漏写的素材 + 写关键帧（带缓动曲线）+ 混合模式 + 画布比例
python3 $D/patch_draft.py
# 3) 导入剪映草稿库并尝试导出（真导出，区别于下面的预览片）
bash $D/export-to-jianying.sh
```

产物：

| 产物 | 路径 | 说明 |
| --- | --- | --- |
| 满配草稿 | `/Users/apple/CreatorStudio/deliver/jianying-draft/envoy-18s-full/` | 直接拖进剪映可开 |
| 探针草稿 | `/Users/apple/CreatorStudio/deliver/jianying-draft/envoy-18s-probe-bezier/` | 多写了未验证字段，只用来试 GUI 兼容性 |
| ffmpeg 预览 | `/Users/apple/CreatorStudio/deliver/jianying-demo-preview.mp4` | **不是剪映导出**，仅还原节奏/版式 |
| 生成脚本 | `deliver/jianying-draft/build_draft.py` / `patch_draft.py` | 幂等，可反复重跑 |
| 导出脚本 | `deliver/jianying-draft/export-to-jianying.sh` | 导入 + 注册 + 尝试 Cmd+E |

预览片和真导出必须分名：预览片叫 `jianying-demo-preview.mp4`，剪映导出才叫 `jianying-demo.mp4`。

---

## 3. 能力矩阵（18 秒样片实测）

| 能力 | 落点 | 状态 |
| --- | --- | --- |
| 多轨时间线（视频/画中画/文字/音频） | `tracks[].type` + `render_index` | 【已验证】 |
| 图片/视频片段 | `materials.videos[]` + `target_timerange` | 【已验证】 |
| 转场（叠化等 10+） | `materials.transitions[]`，`is_overlap:true` | 【已验证】 |
| 蒙版（线性/镜面/圆形/矩形/爱心/星形） | `materials.masks[].config` | 【已验证】 |
| 滤镜 | `materials.effects[]`（`type:"filter"`） | 【已验证】 |
| 文字 + 描边/阴影 | `materials.texts[]` + `content` 里的 styles | 【已验证】 |
| 文字入场/出场动画 | `materials.material_animations[]` | 【已验证】 |
| 文字循环动画 | 同 `material_animations`，`type:"loop"` | 【从 JSON 推断】 |
| 画中画 + 蒙版 + 滤镜 + 入场出场 | 第二条视频轨，同一套字段 | 【已验证】 |
| 混合模式（10 种） | `materials.mix_modes[]` + 片段引用 | 【已验证】 |
| 关键帧（缩放/位移/旋转/透明度） | `segments[].common_keyframes[]` | 【已验证】 |
| 关键帧缓动（内置曲线） | `curveType: Line/CurveIn/CurveOut/CurveInOut` | 【从 JSON 推断】 |
| 关键帧自由曲线（贝塞尔） | `curveType:FreeCurveInOut` + `left_control/right_control` | 【从 JSON 推断】 |
| 固定变速 | `materials.speeds[].speed` | 【已验证】 |
| 曲线变速 | `speeds[].mode=1` + `curve_speed_points[]` | 【从 JSON 推断】 |
| 音频 + 音量 + 淡入淡出 | `materials.audios[]` + `audio_fades[]` | 【已验证】 |
| 音频音效/变声 | `audio_effects[]` | 【从 JSON 推断】 |
| 花字 | `texts[].text_effect` / `effects[]` 资源 id | 【需 GUI/联网】 |
| 语音转字幕 | 云端识别接口 | 【需 GUI/联网】 |
| 智能抠像/智能补帧 | `matting/` 资源 + 云端 | 【需 GUI/联网】 |
| 调节（亮度/对比/H S L 关键帧） | `KFTypeBrightness/.../HslHueIntensity` | 【从 JSON 推断】 |
| 色彩曲线 / 色轮 | `materials.color_curves[]` / `*_color_wheels[]` | 【从 JSON 推断】 |
| 片头/片尾模板 | `materials.placeholders` 类模板 | 【从 JSON 推断】 |
| 导出 MP4 | 无 API，GUI 或 AppleScript | 【需 GUI】 |

---

## 4. 字段解剖（每条都是本机草稿里的真字段）

引用草稿：`/Users/apple/CreatorStudio/deliver/jianying-draft/envoy-18s-full/draft_content.json`

### 4.1 转场：`is_overlap` 决定时间线是否被吃掉

```json
{"type":"transition","name":"叠化","duration":500000,"is_overlap":true,
 "effect_id":"322577","resource_id":"6724845717472416269","platform":"all"}
```

`duration` 单位是**微秒**（API 层传秒，`0.5` → `500000`）。`is_overlap:true` 时后一段前移半个转场，所以 6×3s 素材加 5 个 0.5s 叠化，成片约 15.5s 而不是 18s。写法：`Video_segment.add_transition(Transition_type.叠化, duration=tim("0.5s"))`（`pyJianYingDraft/video_segment.py:475`）。

### 4.2 蒙版：`config` 是归一化坐标，不是像素

```json
{"type":"mask","name":"矩形","resource_type":"rectangle",
 "config":{"centerX":0.0,"centerY":-0.0003,"width":0.9,"height":0.62,
           "feather":0.02,"rotation":0.0,"roundCorner":0.48,"invert":false}}
```

- `centerX/centerY` 是**偏移量**（0 = 居中），不是 0~1 的绝对位置——传 `mask_center_x=0.5` 会得到 `centerX≈0.0009`。
- `rect_width/round_corner` 只允许矩形蒙版，圆形传了就报错。
- 写法 `add_mask(script, Mask_type.矩形, center_x=..., size=...)`（`video_segment.py:435`），只有 6 种：线性/镜面/圆形/矩形/爱心/星形。

### 4.3 滤镜：`effects[]` 里 `type:"filter"`

```json
{"type":"filter","name":"1980","effect_id":"7127828208690433311","intensity":62.0,
 "adjust_params":[],"bloom_params":null,"source_platform":1}
```

滤镜 id 表在 `pyJianYingDraft/metadata/filter_meta.py:12`；但 HTTP 目录接口 **没有** `/get_filter_types`（404），只能从元数据里取。写法：`add_filter(Filter_type._1980, intensity=62.0)`（`video_segment.py:422`）。

### 4.4 文字：`content` 是一段 JSON 字符串，描边/阴影藏在 styles 里

```json
{"type":"text","content":"{\"styles\":[{\"size\":12.0,\"bold\":true,\"range\":[0,16],
  \"fill\":{\"content\":{\"solid\":{\"alpha\":1.0,\"color\":[1.0,1.0,1.0]}}},
  \"strokes\":[{\"width\":0.044,\"content\":{\"solid\":{\"alpha\":0.9,\"color\":[0.0,0.0,0.0]}}}],
  \"shadows\":[{\"diffuse\":0.0583,\"angle\":-45.0,\"distance\":6.0,\"alpha\":0.85,
               \"content\":{\"solid\":{\"color\":[0.0,0.0,0.0]}}}]}]}",
 "alignment":0,"line_spacing":0.02,"line_max_width":0.82}
```

两个容易踩的点：`content` 不是对象而是**转义后的 JSON 字符串**（改动要整体重写）；`Text_shadow` 的入参是 `smoothing`/`angle`(度)/`color`(hex)，导出时平滑度被换算成 `diffuse=smoothing/100/6`、角度保持度数——直接写 `diffuse=` 会 `TypeError`。同一段文字要分多种样式时，styles 里多条带 `range` 就能生效。

### 4.5 文字动画：一个 material 挂多个 animation

```json
{"type":"sticker_animation","animations":[
  {"type":"in","name":"冲屏位移","id":"1643884","resource_id":"7078181271393800711",
   "start":0,"duration":600000},
  {"type":"out","name":"向上擦除","id":"1644609","resource_id":"6774625752794010115",
   "start":2200000,"duration":500000}]}
```

`start`/`duration` 都是**微秒**：入场从 0 开始，出场起点由库算成「片段时长 − 出场时长」（上面这条 2.7s 文字 → 2.2s 起）。API 写法 `Text_segment.add_animation(Text_intro.冲屏位移, duration=tim("0.6s"))`，单位传错会一起错（见坑 7）。

顺带一个观察：文字片段 `extra_material_refs` 的第一个 id 在**剪映自己产出的草稿里也悬空**（解析不到任何素材），第二个才是 `material_animations`。这不是我们生成的错，别拿「引用必须全部可解析」当校验规则。**花字**是另一套资源（`text_effect`），本地缓存 config.json 里没有 `effect_id`，脚本层拿不到 id。

### 4.6 关键帧 + 缓动：库完全没有 API，只能手写

```json
{"property_type":"KFTypeScaleX","keyframe_list":[
  {"curveType":"Line","time_offset":0,"values":[1.0],
   "left_control":{"x":0,"y":0},"right_control":{"x":0,"y":0}},
  {"curveType":"FreeCurveInOut","time_offset":1500000,"values":[1.06],
   "left_control":{"x":0.42,"y":0.0},"right_control":{"x":0.58,"y":1.0}}]}
```

- 属性名必须是序列化名 `KFTypeScaleX/KFTypePositionX/...`（HTTP `/add_video_keyframe` 传 `position_x` 会 `Failed to add keyframe #2`）。
- 内置缓动：`Line / CurveIn / CurveOut / CurveInOut`；贝塞尔：`FreeCurveInOut`，控制点走 `left_control/right_control`。
- 这台机器上 `FreeCurve*` 有一段 Lua 版本闸：`'cannot downgrade because of having splited KFTypeMaskPostion','73.0.0'` ⇒ 草稿里 `new_version` 要 ≥ `73.0.0`（我们的草稿写 `110.0.0`）。
- `common_keyframes` 数组的**长度**就是关键帧组数，属性组按 `property_type` 分。

### 4.7 混合模式：库的导出会漏字段，要手补

```json
{"type":"mix_mode","name":"滤色","effect_id":"871339","resource_id":"6758325170760323597",
 "value":1.0,"apply_target_type":0,"platform":"all","source_platform":0}
```

10 种模式 id 在 `.venv/.../pyJianYingDraft/metadata/mix_mode_meta.py:14`（滤色/叠加/强光/柔光/正片叠底…）。VectCutAPI 自带的库 `dumps()` 素材键里**没有** `mix_modes`（`pyJianYingDraft/script_file.py:882` 一带），所以片段引用写进去了、素材数组却缺失 ⇒ 必须自己往 `materials.mix_modes` 补，并挂到片段 `extra_material_refs`。同理 `transitions/masks/effects/material_animations` 也有漏写，落盘后要用片段对象的 `export_json()` 回填。

### 4.8 沉浸音频：淡入淡出是独立素材

```json
{"type":"audio_fade","fade_in_duration":350000,"fade_out_duration":900000,"fade_type":0}
```

写法 `Audio_segment.add_fade(tim("0.35s"), tim("0.9s"))`（`audio_segment.py:189`）。音量直接 `segment.volume = 0.85`，不新建素材。

### 4.9 曲线变速：字段在，库不认

`segment.py:79` 的 `Speed` 注释就写着「目前只支持固定速度」，`export_json()` 却已经留了位置：

```json
{"type":"speed","mode":0,"speed":1.0,"curve_speed":null}
```

`mode:1` + `curve_speed_points[{time_offset,speed}]` 才是曲线变速（探针草稿里写了）。GUI 日志里有 `[UpdateVideoSpeedAction] ... curve_speed_points size cannot be 0 if the mode is curve`，说明运行时认这套字段；但没在解锁环境里打开过，标【从 JSON 推断】。

---

## 5. 剪映能做、我们的 HTML 引擎做不到的 5 条

| # | 能力 | 字段证据 | 为什么 HTML 引擎做不到 |
| --- | --- | --- | --- |
| 1 | 素材级蒙版 + 羽化 + 反转 | `masks[].config.feather/invert/roundCorner` | CSS `clip-path` 只有硬边，羽化要自己上 SVG filter + 离屏合成，且不能跟视频播放头逐帧同步 |
| 2 | 曲线变速（非匀速播放） | `speeds[].mode=1` + `curve_speed_points[]` | 播放器只能 `playbackRate` 常数；要做曲线得把视频抽帧重建时间线，等于自研解码 |
| 3 | 逐属性关键帧 + 自由贝塞尔缓动 | `common_keyframes[].keyframe_list[].{curveType,left_control,right_control}` | HTML 动画是给 DOM 属性做 CSS 过渡，视频画面本身不参与缓动；多属性不同曲线也无法统一时间轴 |
| 4 | 10 种混合模式 + 多轨 `render_index` 叠层 | `mix_modes[]`、`render_index:10000/15000` | `mix-blend-mode` 只对 DOM 生效，对 `<video>` 内帧无效；多轨叠层要 WebGL 手写合成器 |
| 5 | 花字/文字动画模板 + 音效淡入淡出 | `material_animations[]`、`audio_fades[]` | 模板是云端资源（id + 渲染逻辑都在剪映里），只能复刻样式，不能复用成品 |

反过来说，HTML 引擎强在**精确时序、批量生成、零 GUI**——见下节。

---

## 6. HTML 引擎 vs 剪映：三个判断

1. **要不要逐帧改画面？** 要（蒙版羽化、曲线变速、逐属性关键帧）→ 剪映；只是排版/切图/字幕节奏 → HTML 引擎更快更稳。
2. **是一条片子，还是几百条？** 一条精修 → 剪映 GUI 值；批量（参数化模板、每天几十条）→ 草稿 JSON + HTML 双轨，剪映只做「人审后精修」。
3. **依赖云端资源吗？** 花字、配音音色、智能字幕、智能抠像都在云上 → 要么人工在 GUI 里点，要么放弃这些能力；别指望脚本复刻。

---

## 7. 七个坑（都踩过）

1. **包名打架**：`pip` 装的 `pyJianYingDraft` 0.3.1 是 snake_case，VectCutAPI 自带的是 CamelCase。必须 `cwd=VectCutAPI` 或用 `sys.path.insert(0, ...)`，否则 `Script_file` / `add_mask(script, ...)` 全找不到。
2. **目录接口只认 GET**：`POST /get_*_types` 一律 `405`；`/get_filter_types` 直接 `404`。滤镜 id 只能查元数据。
3. **素材漏写**：库的 `dumps()` 不会把 transition/mask/filter/animation 全写进 `materials`，要在 dump 后从片段对象 `export_json()` 回填 + 挂 `extra_material_refs`；否则剪映打开是「有转场但没效果」。
4. **单位两套**：草稿 JSON 全是微秒，Python API 参数是秒（float）。`tim("0.5s")` 是安全写法。
5. **轨道类型锁死**：`track.py` 的 `add_segment` 会校验类型，音频段塞进视频轨直接 `TypeError`。建轨时就要分好 `video/audio/text`。
6. **`Video_material` 第一个位置参数是 `material_type`**：图片要写 `Video_material("photo", path)`（photo 会跳过 ffprobe，并给默认 3 小时时长）。
7. **`trange()` 的数字是微秒，不是秒**：`trange(0, 3)` 不报错，但整条时间线会塌成 3 微秒（剪映里 6 个片段全挤在 0.000003 秒内，动画/关键帧偏移也跟着错）。正确写法 `trange(f"{start}s", f"{dur}s")` 或 `trange(tim("0.3s"), tim("3.0s"))`——这个 bug 生成时不报任何错，只能检查落盘 JSON 发现。

---

## 8. 边界与未验证

- **导出没跑通**：本机会话屏幕锁定（`CGSSessionScreenIsLocked=Yes`），`screencapture` 全黑、`System Events` 点不到按钮。`export-to-jianying.sh` 的第 3 步（Cmd+E + 点「导出」）**未实测**，快捷键也不保证是这个。
- 剪映 11.6 是 Qt6 + CEF，**没有** `--remote-debugging-port`，内嵌浏览器抓不到 DOM；自带 AI agent 走 Mojo IPC + `/passport/*` 鉴权，没有本地 HTTP 口。
- 花字/字体/音效的云端 id 抓不到，样片里用「描边 + 阴影 + 背景 + 字号」替代花字。
- 曲线变速、`FreeCurve*` 缓动、HSL 关键帧、`beats`（卡点）都只做到「字段写对」，没在 GUI 里验证渲染结果——所以它们只在 `envoy-18s-probe-bezier/`，不进出口草稿，保证主草稿一定能打开。
- 转场曲线（转场自身的缓动曲线）在草稿里**没有**找到对应字段，按 GUI-only 处理。

---

## 9. 复现清单

```bash
# 依赖：VectCutAPI 服务在 127.0.0.1:9100（ensure_service 自动拉起）
curl -m 5 -s http://127.0.0.1:9100/health -o /dev/null -w "%{http_code}\n"   # 404 = 活着
ls ~/CreatorStudio/materials/shots/envoy/                                    # 6 张主图素材
python3 -c "import json;d=json.load(open('/Users/apple/CreatorStudio/deliver/jianying-draft/envoy-18s-full/draft_content.json'));print(len(d['tracks']),{k:len(v) for k,v in d['materials'].items() if isinstance(v,list) and v})"
```

生成脚本已放进交付目录：`build_draft.py`（25 个 API 调用，自带合法骨架 `skeleton/`）+ `patch_draft.py`（回填素材 / 关键帧 / 混合模式 / 画布比例），两次跑出来字节数一致，可安全重跑。要长期用应搬进 `integrations/mac-worker/`（本轮未动，避免和他人改动冲突）。
