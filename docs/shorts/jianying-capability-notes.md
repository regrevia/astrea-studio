# 剪映能力探测笔记（给字幕短片引擎用）

本机结论建立在 **剪映 11.6 + VectCutAPI（`127.0.0.1:9100`）** 的实测上：把每项能力打到一份最小草稿里，再回读 `draft_content.json` 核对字段。
标记约定：**【已验证】** = API 返回 success 且草稿 JSON 里能读到对应字段；**【推断】** = 只在剪映自身二进制/草稿 schema 里读到字段，没有 GUI 观感确认。

探测产物（可丢弃，别进仓库）：

| 路径 | 内容 |
| --- | --- |
| `/tmp/jy-exp/probe.py` | 能力面探测脚本（建草稿 → 逐项加 → 存盘 → 打印） |
| `/tmp/jy-exp/inspect.py` | 回读 `draft_content.json` 的字段结构 |
| `/tmp/jy-exp/dfd_cat_*/` | 实验草稿 1（转场/动画/蒙版/关键帧/特效/文本/变速） |
| `/tmp/jy-exp/adv.py` + `/tmp/jy-exp/adv/*/` | 实验草稿 2（缓动曲线/速度曲线/文本循环/节拍，手写字段） |

## 1. 跑起来

服务端已经常驻；只要 `curl` 能通就不用重启：

```bash
curl -m 2 -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9100/     # 404 = 活着（根路由没定义）
curl -m 5 -s http://127.0.0.1:9100/get_mask_types                      # 目录都用 GET
cd ~/CreatorStudio/jianying/VectCutAPI && ./.venv/bin/python capcut_server.py   # 需要时才起
```

重跑探测（每步都有 120s 超时，全程不到 5 秒）：

```bash
cd /tmp/jy-exp && ~/CreatorStudio/jianying/VectCutAPI/.venv/bin/python probe.py | tail -40
```

**踩过**：`/docs`、`/openapi.json`、`/health`、`/get_draft_status` 全是 404，`grep '@app.(get|post)'` 也搜不到——
服务端是 **Flask**（`capcut_server.py:4`），路由写法是 `@app.route('/x', methods=['POST'])`。想拿全量路由直接：

```bash
grep -oE "^@app\.route\('[^']+'" ~/CreatorStudio/jianying/VectCutAPI/capcut_server.py | sort -u
```

## 2. 能力总表

引擎现在只用了 7 个端点（`create_draft` / `add_image` / `add_video_keyframe` / `add_text` / `add_effect` / `add_audio` / `save_draft`）；
VectCutAPI 一共 27 个，剩下的入口和它们的真实参数如下。

| 能力 | 路由 + 关键参数 | 落到草稿的字段 | 状态 |
| --- | --- | --- | --- |
| 建草稿 | `POST /create_draft` `{width,height}` | `canvas_config` `{1080,1920,ratio:"9:16"}`，`fps:30` | 已验证 |
| 画面入场/出场 | `/add_image` `intro_animation`(=`video_intro_animations`) + `intro_animation_duration`，`outro_animation` 同理 | `materials.material_animations[].animations[]`：`type:"in"\|"out"`、`name`、`resource_id`、`start`/`duration`(µs)、`material_type:"video"` | 已验证 |
| 转场 | `/add_image` `transition`(=`transition_types`) + `transition_duration` | `materials.transitions[{name,duration:500000,is_overlap:true,resource_id}]`，段里 `extra_material_refs` 挂上它 | 已验证 |
| 蒙版 | `/add_image` `mask_type`(=`mask_types`) `mask_center_x/y` `mask_size` `mask_feather` `mask_rotation` `mask_invert` `mask_rect_width` `mask_round_corner` | `materials.masks[{name:"圆形",resource_type:"circle",config:{centerX,centerY,width,height,feather,rotation,roundCorner,invert,aspectRatio}}]` | 已验证 |
| 画面特效 | `POST /add_effect` `{effect_type, effect_category:"scene"\|"character", start, end, params[]}` | `materials.video_effects[{name,effect_id,resource_id}]` + 一条 `effect` 轨，`render_index:10000` | 已验证 |
| 关键帧 | `POST /add_video_keyframe` `{track_name, property_types[], times[], values[]}`（单点时用 `property_type/time/value`） | 段里 `common_keyframes[{property_type:"KFTypeScaleX", keyframe_list:[{time_offset,values,curveType,left_control,right_control}]}]` | 已验证（部分属性） |
| 关键帧缓动 | **无参数**，只能手写 | `keyframe_list[].curveType` ∈ `Line` / `FreeCurveIn` / `FreeCurveOut` / `FreeCurveInOut`，配 `left_control`/`right_control` 贝塞尔控制点 | 推断（枚举来自 `libvideoeditor.dylib` 内嵌 Lua 的降级检查） |
| 文本 + 文本动画 | `POST /add_text` `{text,start,end,font,font_size,bold,intro_animation,outro_animation,intro_duration,outro_duration,transform_x/y,alignment,line_spacing,letter_spacing,background_*,shadow_*,border_*}` | `materials.texts[].content` 是**嵌套 JSON 字符串**（`styles[].font.id/path`、`range`、`fill`），动画走 `material_animations`，`material_type:"sticker"` | 已验证 |
| 文本循环动画 | **没有参数**（只有 `/get_text_loop_anim_types` 给名字） | 手写 `material_animations[].animations[]` 里 `type:"loop"`，再挂进文本段 `extra_material_refs` | 推断 |
| 音频变速 | `/add_audio` `{audio_url,start,target_start,speed,volume,effect_type,effect_params}` | 段上 `speed:1.25`，`target_timerange` 自动按倍率缩短（2.0s → 1.6s） | 已验证 |
| 变速曲线（曲线变速） | **没有参数** | `materials.speeds[]` 里 `mode:1` + `curve_speed:{name,type:"curve_speed",speed_points:[{x,y}...]}`（pyJianYingDraft 只写 `null`） | 推断 |
| 节拍 / 卡点 | **没有入口** | `materials.beats[{enable_ai_beats,mode,gear_count,beat_infos:[{time,type}]}]`；`mode:1` + `gear_count:5` 是五档变速，需草稿版本 ≥ 80.0.0 | 推断 |
| 混合动画 | `/add_image` `combo_animation`(=`combo_animation_types`) + `combo_animation_duration` | 同 `material_animations` | 已验证（目录可见，未逐项回读） |
| 贴纸 | `POST /add_sticker` `{sticker_id,transform_x/y,alpha,rotation,scale_x/y,flip_*}` | `materials.stickers[]` + `sticker` 轨 | 未测 |
| 字幕（SRT 整段） | `POST /add_subtitle` `{srt, font, font_size, bold, time_offset}` | 批量写 `texts` | 未测 |
| 字体表 | `GET /get_font_types` | — | 未测 |
| 后台模糊 | `/add_image` `background_blur` 1–4 | `materials.canvases` | 未测 |
| 项目读写 | `/list_projects` `/read_project` `/query_draft_status` `/generate_draft_url` `/reload_desktop` | — | 未测（`/reload_desktop` 要开 GUI） |

## 3. 各能力的实测细节

### 3.1 动画、转场、蒙版：都是一条 `extra_material_refs`

图片段本身只写 `clip`（`alpha/scale/transform/rotation/flip`）+ `uniform_scale` + `render_index`；
动画/转场/蒙版/变速全部作为独立 material 存在 `materials.*` 里，段上只留 id：

```json
"extra_material_refs": ["<speeds id>", "<material_animations id>", "<transitions id>", "<masks id>"]
```

所以**给已有段补能力 = 追加一个 material + 往 `extra_material_refs` 里塞 id**，不用碰段结构。这条对「后期补一个入场动画」特别省事。

### 3.2 蒙版坐标是偏移量，不是归一化中心

`mask_center_x=0.5` 被写成 `config.centerX = 0.000925...`。真实语义是 **相对画面中心的偏移**（−0.5…0.5），
**要画面正中就传 0**。`mask_size=0.8` → `width:1.422 / height:0.8`，`height` 是相对画面高度、`width` 由 `aspectRatio` 推出，所以改宽度只能换蒙版形状或 `mask_rect_width`。

### 3.3 关键帧：属性名要选对，缓动没暴露

`times` 是**绝对时间（秒）**，落到 `time_offset` 是 µs，且必须落在段的 `target_timerange` 内。
实测可用：`uniform_scale`（→`KFTypeScaleX`）、`rotation`（→`KFTypeRotation`）、`alpha`（→`KFTypeAlpha`）；
`position_x` 报错 `Failed to add keyframe #2`——位移类属性名不走 `position_x`。
全部可用属性在剪映二进制里有 233 个 `KFType*`（`Scale/ScaleX/ScaleY/PositionX/PositionY/Rotation/Alpha/Volume/Zoom/Stretch/Fade/Hue/LUT/...`），提取命令：

```bash
strings -a /Applications/VideoFusion-macOS.app/Contents/Frameworks/libvideoeditor.dylib \
  | grep -aoE 'KFType[A-Za-z0-9_]+' | sort -u > /tmp/kf.txt && wc -l < /tmp/kf.txt
```

缓动是最大缺口：pyJianYingDraft 硬写 `curveType:"Line"`、`left_control/right_control` 全 0（`keyframe.py:26-28`），
引擎里 Ken Burns 的推拉因此是**匀速直线**，这就是「PPT 感」的根因之一。
剪映自己认 4 种：`Line` / `FreeCurveIn` / `FreeCurveOut` / `FreeCurveInOut`（dylib 内嵌 Lua 的 73→72 降级检查会专门拦 `FreeCurve*`），
控制点语义与 CSS `cubic-bezier` 同构。实验 2 已验证**字段能写进去、剪映不挑格式**，但**观感需开 GUI 看一次**。

### 3.4 文本

- `materials.texts[].content` 是字符串包 JSON，改字号/颜色要解析再写回，不能当普通字段改。
- `font_name` 在外层是空串，真正的字体在 `content.styles[0].font.id/path`；模板里默认 `C:/思源粗宋.ttf` 是 Windows 路径，**剪映靠 `font.id` 命中本机字体**，`path` 不用管。
- 文本段 `render_index:15000`（压在特效 10000 之上）。
- 文本只有 `intro`/`outro` 两个参数，**循环动画没有入口**，要手写 `type:"loop"`。

### 3.5 音频

`speed` 一给，`target_timerange` 立刻按倍率重算，所以**先定速度再加字幕**，否则字幕对不上。
`effect_type`/`effect_params` 对的是 `audio_effect` 目录（`Tone` 类，如「台湾小哥」「广告男声」= 变声/音色，不是 EQ）。
节拍检测剪映自己有算法（`libvideoeditor.dylib` 里 `music_beats` runner、`beat_infos`/`beats_json`、`AudioEventDetectionManager` 分 speech/sing/music），
但**草稿只吃结果不吃请求**：要么外部算 BPM 后写 `materials.beats.beat_infos`，要么开 GUI 手动踩点。

## 4. 坑

1. **目录接口只吃 GET**，用 POST 打一律 `405 METHOD NOT ALLOWED`（我第一次就是这么空手回来的）。
2. **`/save_draft` 的 `draft_folder` 是输出根**，会给 `draft_folder/<draft_id>/` 整套目录（`draft_content.json` + `draft_meta_info.json` + `Timelines/project.json` + `assets/`）。
   不传 `draft_folder` 才落 `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft`；探测时传 `/tmp/jy-exp` 就不会污染草稿箱。
3. **`draft_id` 在内存里**（`draft_cache.DRAFT_CACHE`），进程重启就没了；`/save_draft` 之后要再改，得重新 `create_draft` 或者直接改 JSON。
4. 草稿元数据带 `platform:{app_version:"10.2.0", os:"windows"}`、`new_version:"110.0.0"`（pyJianYingDraft 模板遗留）。
   剪映 11.6 打开不报错是实测过的，但 `FreeCurve*` 之类高版本特性理论上有降级风险，`new_version` 别手改小。
5. 时间单位混着来：草稿里全是 **µs**，API 参数是 **秒（float）**，`transition_duration:0.5` → `500000`。
6. `stats` 里那套 `skipped` 兜底是对的：关键帧/角色失败不该毁整条片，探测时同样别让单个能力失败中断整轮。

## 5. 对我们引擎最有用的 Top 5，可直接落地

1. **给 Ken Burns 补缓动（改 `jianying_engine.py` 的关键帧那一小段，最快见效）**
   现在 `_post("/add_video_keyframe")` 走的全是 `curveType:"Line"`，等于匀速推（Ken Burns 在 `jianying_engine.py:725`，角色呼吸在 `:824`）。落地点：这两次调用之后，
   对刚写入的 `common_keyframes` 做一次 JSON 后处理，把 `curveType` 改 `FreeCurveInOut`、控制点按「起手慢—中段快—收尾缓」给 `(0.42,0)/(0.58,1)`。
   风险最低（只动字段值），收益是静态卡最扎眼的 PPT 感直接消失。**需 GUI 确认一次观感**。
2. **蒙版做画中画/角色抠底，替代现在的整图贴**
   `/add_image` 的 `mask_*` 一族白拿，config 里 `feather`（羽化）+ `roundCorner` 就能做圆角羽化窗口。落地点：角色轨 `_character_frame` 输出后多加一段 `mask_type:"矩形"`。
   注意 `mask_center_*` 传 **0 = 居中** 这个反直觉点。
3. **用 `material_animations` 的 `type:"loop"` 补文字常驻动效**
   现在文本只有入场/出场，长停留的标题是死的。落地点：`_scene_text` 加一条 `loop` 动画（名字从 `/get_text_loop_anim_types` 取，实测有「打字机 I」「刷屏」「字体变换」），
   只往 `extra_material_refs` 追加 id，不改段结构。
4. **音频 `speed` + 外部 BPM 写 `materials.beats`，把「纯口播」换成「卡点」**
   `/add_audio` 的 `speed` 已验证可用；节拍靠外部（librosa/ffmpeg）算完写 `beats.beat_infos[{time,type}]`。
   落地点：`_scene_duration` 之后按拍点吸附切屏边界，字幕切分能吃 `_CAPTION_BREAK` + 拍点双约束。
5. **把 27 个路由里没用上的 `/add_sticker` `/add_subtitle` 纳入能力清单**
   `/add_subtitle` 直接吃 SRT，等于把 `_caption_chunks`/`_caption_spans` 那套逐条 `add_text`（约 60 行）压成一次调用；
   `/add_sticker` 给「标签/箭头/强调块」这类装饰件开了口子，现在这些只能靠渲染进图。
   这两条**未实测**，但参数面清楚，值得先跑一次 `/add_subtitle` 对齐现有 `_caption_spans` 的时间轴。

**不要做**：想靠 `/add_text` 的 `loop_anim` 参数拿循环动画（没有这个参数）、想靠 `add_video_keyframe` 调缓动（没有这个参数）、
想让 VectCutAPI 直接出节拍（它只写草稿，不跑音频分析）。这三件事都只能落到「手写草稿 JSON」这一层。

## 6. 换新版本剪映：兼容边界与升级路径

**兼容是单向的**：新版剪映能打开旧格式草稿（实测 11.1 读得懂 0.3.0 写出的 `360000` 版草稿），
旧版打不开比它新的格式。所以「先升级 App、草稿照旧」是安全的；反过来把新版草稿发给旧版机器会直接打不开。

**别人的工具跑到哪一步**（用来划清我们的差异化位置，不是依赖）：
- pyJianYingDraft：Linux/mac 上能生成草稿、能读模板（5.9 / 6.8 版本实测可读），但**不能自动导出**，
  生成完仍要在 Windows 版剪映里手动导出 —— 也就是它解决了「写」，没解决「导」。
- 市面上一些「草稿版本修改器」靠批量改版本号让旧工程能进新版 —— 说明版本号字段确实是主要门槛，
  但它只改壳、不保证新格式的多轨道/特效字段被正确解释。
- 已知能力区间（第三方经验值）：模板草稿读写 ≤ v5.9，批量导出 < v6，素材轨道控制兼容 v5+。

**我们这边的策略（已落地 / 待落地）**
1. **不写死版本号**：`export/panel.py:app_version()` 从 bundle `Info.plist` 读真实版本（当前 `11.6.0/490`），
   导出日志与证据里都带这一行。判断依据永远是「AX 元素在不在」，不是「版本号等于几」。
2. **面板按元素名找，不按坐标找**：`open_panel()` 走应用菜单「文件 → 导出」、面板靠 `导出-<草稿名>` 前缀识别；
   新版若改控件名，失败会明确报 `export_panel_unavailable`，不会静默导错。
3. **升级验收清单**（换版本后按顺序跑，任一步失败就回退旧版本）：
   `open_draft.py --name <草稿>` 能进编辑器 → `jianying_export.py --list-options` 能开出面板 →
   `--dry-run` 能选中同档位 → 真导出 `out/<jobId>/r<rev>/` 有 mp4 且时长/画布通过体检。
4. **待落地**：草稿 sidecar 里记一份「生成时的剪映版本」，导出前比对，版本跨代时先 `--dry-run`，
   把「新版把旧草稿解释歪了」挡在导出之前。

以上 2/3 是本次已修（健康检查假阴性、草稿自动打开、盲点保护），4 是下一步。

## 7. 版本适配（2026-10-28 调研结论）

- 剪映专业版 macOS **当前最新就是 11.6.0（2026-10-13 上架）**，没有 11.7/12.x；11.3.0→11.6.0 约 1–2 周一版。
  中文站点的「11.8/11.9」是手机版或 SEO 假版本。来源：App Store id1529999940、`itunes.apple.com/lookup?id=1529999940&country=cn`。
- 草稿主文件自 6.0 起是 `draft_info.json`；只放 `draft_content.json` 会被登记成 `draft_info.json` 而实际文件缺失 → 「草稿内容已损坏」。
  引擎探测明文/密文：**明文可开，打开后自动升级为密文**。所以「生成草稿」不受加密阻碍，只有「读模板」受密文阻碍。
- 无 macOS/11.x 可用的密文解密实现（社区 codec 仅 Windows 10.3–10.6.5 且依赖 `videoeditor.dll`）。
- 蒙版自 8.5 起静默失效（写 `materials.masks`，实际键 `common_mask`），11.4 仍未修。
- 官方 pyJianYingDraft 明确「7+ 自动导出不支持、Mac 不支持导出」——自研 AX 是唯一导出路径，**升级必须重录 AX 树**。
- **结论：保持 11.6 + 做版本探测**（11.6 已是当前最新，「升级」暂无标的）。探测三件事：App Store lookup `version`、
  本机 `CFBundleShortVersionString`/`CFBundleVersion`、草稿密文头指纹；发现 >11.6.0 再走「独立用户/VM 打开候选版本 → AX 树 diff」。
- 新旧共存不建议改 bundleId 重签（破坏公证、更新互相覆盖）；用新建 macOS 用户或 VM，HOME/容器/草稿天然隔离。

版本机制、决策顺序与升级后怎么验，见 [jianying-version-support.md](jianying-version-support.md)（本机 11.6 实测 + `resolve_schema`）。

## 8. 谁在驱动剪映：TCC 归属（关键，2026-10-28 实测）

worker 由 launchd 拉起，**没有辅助功能（Accessibility）授权**，而我的终端 shell 有。同一条命令两种结果：

| 调用方 | `ax_tool tree <剪映pid>` | CGEvent 点击 | osascript 菜单点击 |
|---|---|---|---|
| 终端 shell | 160 行完整 AX 树 | 生效（实测弹出导出面板） | 生效 |
| launchd（worker 同一上下文） | **1 行空树** | 无效 | 被 TCC 拒绝 |

推论：worker 侧 `rc=2 打不开导出面板` 不是坐标/文案问题，是权限问题；`ax_tool tree` 返回空树即为判据。
当前对策：草稿生成、卡点、渲染、入库全走无 GUI 路径；GUI 导出要么用户点一次「导出」，
要么在「系统设置 → 隐私与安全性 → 辅助功能」里把 worker 的解释器
（`~/CreatorStudio/runtime/integrations/mac-worker/.python3`，由 launchd 拉起）加入白名单，之后 worker 才可能自己点。

## 9. GUI 导出这条路当前的真实状态（2026-10-28 实测）

- **worker 上下文**：`ax_tool tree` 拿到的是 1 行空树，CGEvent 点击与菜单项都无效，
  导出面板打不开（见第 8 节的 TCC 归属）。这条路上没有任何「无授权也能点」的偏方：
  剪映的 Qt 控件对 `AXPress` 全部无效，对 `CGEvent.postToPid` 也不响应，只能把窗口
  拿到前台再投递物理光标事件。
- **终端上下文**（有辅助功能授权）：能点开导出面板，但 11.6 这版导出的 mp4 只有 1 帧，
  原因是点击导出时草稿时间轴还没铺满（草稿刚写入、剪映尚未完成媒体导入），
  导出发生在时间轴就绪之前。结论：**GUI 导出不能当成平台的成片产出通道**。
- 平台的成片通道是 worker 内的 ffmpeg `TIMELINE_RENDER`（关键帧、卡点、字幕、配音全在
  规格里，渲染可复现、可并发），剪映侧只负责「草稿」这种可交付给人工二创的中间物。
- 「不抢鼠标」的边界：因为必须前台 + 物理光标，做不到零打扰。当前实现是
  `panel.quiet_start()`——等用户空闲 N 秒、记下前台 App 与光标位置、操作结束后把光标和
  前台还回去；等不到空闲就留在队列里，不硬抢（`strict` 模式）。第二块屏挂着剪映也一样：
  点击会把剪映拉到前台，用户这段时间的输入会被打断。
