# 竖屏手绘简报短片：生产手册

纸面引擎出 1080×1920 竖屏短片：真截图 + 手绘 SVG 面板交替，HTML 排版 → 逐帧截图 → ffmpeg 编码。
下文命令都在仓库根目录执行。`paper_render_engine.py` 正在拆模块、行号会漂，对不上时按符号名搜：
`.head` / `.pbody` / `.sketch` / `_RGH` / `_SKETCHES` / `_sheet_schematic()` / `_html_doc_schematic()` / `renderFrame` / `gapSk`。

## 1. 一分钟出片

先备一条 spec 和几张截图，然后一条命令直出：

```bash
cd /Users/apple/Desktop/work/Studio/creator-studio
mkdir -p ~/CreatorStudio/deliver ~/CreatorStudio/work

/usr/bin/python3 deploy/schematic_preview.py deploy/specs/envoy_real.json \
  --out ~/CreatorStudio/deliver/envoy-real-schematic.mp4 \
  --workdir ~/CreatorStudio/work/envoy-real \
  --shots ~/CreatorStudio/materials/shots/envoy-head.png#838#contain#396 \
          sketch:timeline \
          ~/CreatorStudio/materials/shots/envoy-points.png#838#contain#396 \
          sketch:gap \
          ~/CreatorStudio/materials/shots/envoy-cli.png#838#contain#396 \
          sketch:install \
  --caps "网页抬头，只读得到 title" "一条链接怎么变成材料" \
         "材料页带时间戳和页码" "没字幕的空档要标出来" \
         "CLI 装完就能跑" "三行装完"
```

六个版位 = 开场 + 四条结论 + 收尾（`spec` 里 4 条 `points`，每屏一行）。
`--caps` 不给就用文件名主干（`deploy/schematic_preview.py:38`），两者按屏序一一配对；`caps` 少给的那几屏角标留空，多给的直接丢掉（`deploy/schematic_preview.py:39-40`）。

平台链路（走服务端队列、带回归、出片落到 `~/CreatorStudio/deliver/`）：

```bash
STUDIO_SKIP_PREFLIGHT=1 bash deploy/submit_spec.sh deploy/specs/envoy_real.json 1
```

`STUDIO_SKIP_PREFLIGHT=1` 只跳过出片前回归，不跳过接口校验（`deploy/submit_spec.sh:18-22`）。
脚本自己轮询 `/api/production/runs`，每 15s 打一行 `dur=/qa=/findings=/copy=`，最多 60 次。

服务端队列本身的往返用 `deploy/p2_timeline_e2e.py` 验（它像 UI 一样打公开 API，断言
**run 的 materialId 和每个平台的对象**，不看 job 自报的 done）：

```bash
# 三段素材，两条路都验：advanced=自己写完整时间轴；friendly=只给文案/素材/画幅，服务端展开
python3 deploy/p2_timeline_e2e.py --assets /tmp/demo_c/asset --platforms douyin --timeout 600
python3 deploy/p2_timeline_e2e.py --assets /tmp/demo_c/asset --platforms douyin --mode friendly
```

`--assets` 下要 `a.mp4/b.mp4/c.mp4`（`p2_timeline_e2e.py:clips_under`），可选 `bgm.m4a`。
2026-10-27 两条路都 PASS：`advanced` run `6ae097e0cca7f12f29fa7e66`、
`friendly` run `6ae098a5cca7f12f29fa8130`，均 9s 母版 + douyin 变体 `ok=True 1080x1920`
+ draft 附着 + materialId 回写；草稿路径看 job log 的 `DRAFT_PATH=` 行
（后端 `completeJob` 白名单不收 `draftPath`，只能落日志）。

**口播文本取自哪里**：`points` 每条是 `标题::卡面正文::口播`，TTS 念的是**第 3 段**
（`export/vertical_short.py:1584` 取 `split("::")[2]`；`factcard._parse_point` 落成 `say`）。
`narration.points[i]` 只是兜底：当某条 `point` 连两个 `::` 都没有时，`build_spec` 才把它拼成第 3 段
（`export/vertical_short.py:944-956`）。两处写不一样时，以第 3 段为准——想改口播就改第 3 段。
卡面上印的是第 2 段（`raw_bodies`，`vertical_short.py:1018-1019`），纸面屏的正文字块用的是
`narration.points[i]`（`export/paper_short.py:45`）。

## 2. 版式与画面约束

画布 1080×1920、30fps（`paper_render_engine.py:26-28`）。以下数值全部来自 `_CSS_SCHEMATIC`
（`paper_render_engine.py:126` 起），改风格时改这里，别在 spec 里覆盖：

| 元素 | 数值 |
|---|---|
| 安全边距 `.pad` | 左/右 92，上 128，下 104 |
| 标题 `.head` | 600 61px/1.28，最大宽 858，`#ECF3FA`；强调 `.em` 用 `#5ED3BE` |
| 波浪下划线 `.ul` | 228×16，上边距 14，`stroke-width:3`，`#5ED3BE`，path 是 `M3 11 q13 -8 26 0` 后接 8 段 `t26 0` |
| 正文 `.body` | 400 31px/1.62，上边距 24，最大宽 846，`#A2B8CB`；加粗 `#EAF2FA` |
| 截图面板 `.panel` | 左/右 58（内宽 964），默认 top 396、高 838，底 `#0C1826`，`rotate(-.85deg)`，圆角 3 |
| 面板说明 `.cap` | 500 23px 等宽，`letter-spacing:.08em`，贴面板上沿 `bottom:calc(100% + 14px)` |
| 手绘面板 `.sketch` | 左/右 18，top 430，高 545（内部 viewBox 900×470） |
| 页脚 `.foot` | 400 22px 等宽，`letter-spacing:.18em`，`#6C8BA8`，下 52 |

人物叠层：`width=696, x=520, y=1014`，整片钉死不动（`export/vertical_short.py:288-294`，注释写着
「二十几屏里人物从 x500/y940 挪到 x520/y1014…整片钉同一处」）。素材画布 2048 见方、人物占高
78–82%（`deploy/presenter/ASSET-SPEC-whale-girl.md`），缩到 696 宽后画布占位 y≈1014–1710，
人物实体约 y≈1130–1660，右侧出画到 x=1080。文字区按 y≤1000 划（`video_qa.py` 的
`TEXT_BOX=(124,470,1004,1000)`），和人物实体之间留约 130px 气口——这就是「人物不压字」的量化标准。

**刻意不做的四件事**：

- 序号 kicker：`.kicker{display:none}`、`.accent{display:none}`（`paper_render_engine.py:138,142`）。
  `01 / 04` 这种编号一上屏就变成幻灯片页码，观众开始数「还有几屏」。
- 网格背景：舞台只有一层径向渐变（`radial-gradient(96% 64% at 46% 8%, …)`，`paper_render_engine.py:131`）
  加两道 1px 斜纹假颗粒（`.grain`，opacity .5）。网格会把留白量出来，人一眼看出这是模板。
- 光效：环境光整层删掉过，注释写着「卡面自己已经有底光，不再加任何会动的光」
  （`export/vertical_short.py:1509-1513`）。动的光会被读成「背景那盏灯在动」，一切画面就假。
- 虚线导轨：虚线在 `_SKETCHES` 里只表示「未覆盖」这一个语义
  （`_SKETCHES["gap"]` 里那两条 `stroke-dasharray="7 8"`）。当装饰用，
  观众会以为那条线也有信息量。

面板本体：`.pbody` 是深色卡纸，靠 `::before/::after` 两块斜胶带压住四角；右缘和底缘各有一层
向 `#0C1826` 收的暗角（`linear-gradient(90deg, … 74px …, rgba(6,13,21,.88) 100%)`，
`paper_render_engine.py:158-160`），作用是让亮截图不硬切在纸上。

## 3. 手绘质感调参

粗糙感全靠一个位移滤镜 `_RGH`（`paper_render_engine.py` 顶部）：

```text
feTurbulence fractalNoise baseFrequency=.012 numOctaves=2 seed={seed}
→ feDisplacementMap scale=1.05，xChannelSelector=R / yChannelSelector=G
滤镜区 x/y 各放宽 8%（x="-8%" width="116%"）
```

只留一层：`scale` 超过 1.5 笔画开始发毛、细线（.5）会被抖断；低于 .8 就看不出手绘。
原来的第二层高频扰动（`baseFrequency=.075 scale=.55`）已删除——它在 1080p 下只贡献噪点，
把小字边缘啃出毛刺。

每张面板在 `_sketch_svg()` 里按名字固定 seed（`link=11, timeline=23, gap=37, install=51`，其他名字落 7）。
seed 一改整张笔画全变、版式不动——想换手感只动 seed，别动坐标。

**别加光。** 立绘周围那圈冷蓝 halo 来自 `jianying_engine._stage_canvas()` 的高斯叠色，逐帧渲染时
它还会随人物缩放一起被放大，首尾两屏最明显——已经整段删掉，只留脚下一条接触阴影
（`alpha≈96`，`blur=cw//26`）。背景同样不留中线高光与网格：`#stage` 只用单向下深渐变。
判断标准是「环带亮度差」：人物周围环形带与背景的亮度差要 < 1，改完用 `/tmp/glow_check.py` 那套量一遍再出片。

**暗色纸面的当前配方**（示图页 `_SVG_STAGE`，2026-10 换掉旧版）：旧的 104° 重复斜纹
（`repeating-linear-gradient` 1px/3px）在 crf16 下会编码成条纹噪点，是「脏」的主要来源，已删；
现为四层，全部只压暗、不补亮——低频墨面浓淡（`feTurbulence baseFrequency=".0016 .011"` 3 octaves，
rect `opacity .03`）、横向笔刷（`.009 .06` 2 octaves + `feDisplacementMap scale 20` + `feGaussianBlur 2.6`，
六条宽 30–66 的成对深浅笔扫，`opacity .035–.075`）、四角磨损（`.02 .05` 3 octaves + displacement 30，
四块 `#03070C` `.22–.30`）、`#vign` 径向 + 上下渐变暗角。`#stage` 仍是单向下深渐变。
两条硬约束：纹理只能压暗，任何亮纤维/亮斑在暗底上会读成漏光；笔扫要落在内容区之外（标题上方一条、
面板下方两三条），落在标题旁会像污渍。落地前实测整屏 mean/std 不得高于上一版。

**精修 / 变糙的方向**：

| 想要 | 改法 |
|---|---|
| 更糙、更像炭笔 | 第二层 `scale` 抬到 .9–1.2，`baseFrequency` 抬到 .1 |
| 更干净、接近矢量 | 两层 `scale` 降到 .9 / .35 |
| 抖动太规律（看得出波纹） | 换 `seed`，或把第一层 `numOctaves` 提到 3 |
| 笔画粗细不匀 | 别调滤镜，按层级改 `stroke-width` |

线宽按信息层级分四档，四档之外不要再造新值（`_SKETCHES` 里的实际取值）：

```text
T1  1.5    外框、终端窗、整块卡纸
T2  1.0    结构线：标题栏、分栏、胶片带、药丸
T3  .7-.8  内容行、刻度、树状条目
T4  .5-.6  发丝线：分隔、影线、滚动条、参考线（配 opacity .18-.4）
accent 1.1-1.4  只剩青线 / 琥珀虚线两种语义色
```

四档是层级不是装饰：同一张图里 T1 只能出现在最外层 1-2 条，T4 可以几十条。
层级一乱（比如列表行用 1.15、外框用 1.5）画面就回到「PPT 描边」的观感。

圆头圆角、`fill:none`，影线一律 45° 短斜线、`stroke-width:.6` 配 `opacity:.16-.2`——
影线只做「这块没读到 / 这块是附件」的弱提示，不能比正文重。

**密度底线**（一张面板在 1080 宽下至少要有三层信息，缺一层就显空）：

```text
1  结构层：外框 + 标题栏 + 分栏（T1/T2）
2  内容层：≥12 条 T3 行/格，行距 26-30，单行 ≤300 单位
3  标注层：mono caption 一条（≥13）+ 中文结论一条（≥18，≤20 字）
```

空区控制：任何一块连续留白不要超过 100×100 单位；四角里至少两角有内容压住。
刻度 / 帧号 / 滚动条这类「无信息但表意」的 T4 细节，是让手绘图像真页面的关键，
每条都便宜（一个 `path`），但别超过两层嵌套，否则动画时线太多会糊。

**描边生长（draw-on）**：所有笔画 `class="sk" pathLength="1"`，CSS 里
`stroke-dasharray:1; stroke-dashoffset:1`（`paper_render_engine.py:166`），由 JS 逐条放出来：

```js
const gapSk = sk.length > 12 ? 0.86 / sk.length : 0.030;   // 笔画多就压间隔，总时长封在 ~1s
const q = EASE((local - 0.14 - k * gapSk) / 0.46);          // 第 k 条：延后 0.14s，单条长 0.46s
el2.style.strokeDashoffset = (1 - q).toFixed(3);
```

（`_html_doc_schematic()` 的 `renderFrame`，搜 `gapSk`）。那个 `0.86/sk.length` 是让 60 条笔画的图也在 1s 内画完，
不要改成固定间隔——笔画多的图会拖到下一屏。

**中文字形不可能手写化**：CSS 只声明 `"PingFang SC","Hiragino Sans GB",…` 和 `"SF Mono"`
（`paper_render_engine.py:139,146,161`），本机没有任何中文手写体。所谓「手绘中文」实际是系统黑体，
手绘感只能来自笔画、胶带、影线、面板，不要指望字体。要真手写只能走位图（PIL 逐字贴 PNG），
纸面引擎不支持。

## 4. 嵌入真截图

`--shots` 每个参数支持 `路径#高度#fit#top` 四段，解析在 `export/paper_short.py:139-147`：
`#` 后第 1 段是面板高度 `ph`，第 2 段是 `object-fit`（`contain`/`cover`/`fill`），第 3 段是面板顶边 `pt`。
缺省 `pt=396`、`ph=838`、`fit=cover`、`object-position:center`（`_sheet_schematic()` 开头的取值）。

```bash
# 网页长图：按高度钉死，整张缩进去，顶部对齐
--shots ~/CreatorStudio/materials/shots/page.png#838#contain#396

# 手机截图（宽高比接近竖屏）：铺满面板，裁掉两侧
--shots ~/CreatorStudio/materials/shots/app.png#838#cover#396

# 代码块 / CLI：面板矮一点，给正文留位置
--shots ~/CreatorStudio/materials/shots/cli.png#560#contain#396
```

`pt` 只是下限：落版脚本会把面板顶边抬到「正文字块底 + 34px」（`_sheet_schematic()` 的 layout pass），
正文写满四行时面板被推下去，但高度不变，于是底边溢出到人物身上。正文控制在 3 行内，或把 `ph` 调小。

文件名里不要带 `#`——`#` 是分隔符，`schematic_preview.py:39` 会把路径按 `#` 切开再拼回去，
带 `#` 的文件名会被当成版位参数切开，最后报「截图不存在」。

**给亮截图烤一层向 `#0C1826` 收的暗角**（面板底色就是 `#0C1826`，这样右缘不会白得发亮）：

```bash
ffmpeg -y -v error -i ~/CreatorStudio/materials/shots/raw.png \
  -f lavfi -i color=c=0x0C1826:s=964x838 \
  -filter_complex "[0:v]scale=964:838:force_original_aspect_ratio=increase:flags=lanczos,\
crop=964:838:0:0,format=rgba,\
geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='255*(1-min(1,max(0,(X-(W-74))/74)))'[s];\
[1:v][s]overlay=0:0:format=auto,format=yuv420p[v]" \
  -map "[v]" -frames:v 1 -update 1 \
  ~/CreatorStudio/materials/shots/page-dark.png
```

`geq` 只改 alpha：右起 74px 线性淡到 0，露出的就是垫在下面的 `#0C1826`——和 `.pbody::after`
的 CSS 暗角是同一个几何。

两个必踩的坑：

- **少了 `-update 1` 什么都不会写**。image2 复用器默认按序列模板收尾，单帧 `geq` 经常产出 0 字节
  或直接报 `Output file #0 does not contain any stream`。`-frames:v 1` 和 `-update 1` 必须一起给。
- **不要在已经烤过白渐变的 PNG 上再打深色渐变**。alpha 淡出的地方露出的是图里原有的白，
  右缘会留一块白楔，越叠越脏。必须回到原始大图重裁、重打，成品图只过一次滤镜。

截图的变换也在动：入场 `translateY(46→0) + scale(1.02→1)`，之后 8s 内缓慢推到 1.04
（`renderFrame` 里的截图段）。所以截图边缘在整屏里一直在挪，暗角要打在图里，不能靠 CSS 补。

## 5. 验收

机器体检（在 `integrations/mac-worker` 下）：

```bash
cd integrations/mac-worker
/usr/bin/python3 -m video_qa ~/CreatorStudio/deliver/envoy-real-schematic.mp4 \
  --spec ~/CreatorStudio/work/envoy-real/spec.json --json
```

阈值都是命名常量（`video_qa.py:24-36`），采样 0.25s（`STEP`），判定在 270×480 灰度帧上做：

| 常量 | 值 | 管什么 |
|---|---|---|
| `CUT_DIFF` | 45.0 | 相邻帧差超了就是硬切；命中点再用 25fps 复核到 0.01s（`_refine_cut`） |
| `FREEZE_DIFF` | 0.4 | 连续 4 帧（1.0s）几乎不动 = 冻帧 |
| `FLAT_STD` | 6.0 | 文字区标准差过低 = 这屏没字 / 字没贴上 |
| `CHAR_STATIC_DIFF` | 0.3 | 人物区连续 8 帧（2.0s）不动 = 角色像贴图 |
| `TEXT_BOX` | (124,470,1004,1000) | 文字区，判定前按 1080×1920 设计坐标缩到 270×480 |
| `CHAR_BOX` | (280,900,1040,1700) | 人物区 |

人眼还要补四件事：

1. **时长与帧率**：`ffprobe -v error -show_entries stream=width,height,r_frame_rate,nb_frames -of csv` 对 1080×1920 / 30 / 1、总帧数 ≈ 时长×30。帧数不足引擎会自己抛 `帧数不足: n < total*fps`（`render()` 尾部）。
2. **停帧抽检**：转场两侧各抽 0.1s 和 0.3s（淡入 0.40s、淡出 0.34s，`_html_doc_schematic()` 的 `fadeIn`/`fadeOut`），看中间是不是一张空纸——空一帧人眼就能看见。
3. **边缘 2× 放大**：裁右缘 270px 放大两倍，看截图暗角有没有白楔、胶带有没裁断、`#5ED3BE` 有没有偏色。
4. **口型停留**：每个口型停 8–10 帧（0.26–0.34s）。短于 8 帧像抽帧，长于 10 帧像贴图。

一秒一张的联系表（60s 片正好 6×10）：

```bash
ffmpeg -y -v error -i ~/CreatorStudio/deliver/envoy-real-schematic.mp4 \
  -vf "fps=1,scale=270:480,tile=6x10" -frames:v 1 \
  ~/CreatorStudio/deliver/envoy-real-contact.png
```

一张图看完六屏的版式节奏：真截图和手绘面板有没有交替、每屏标题长度是否一致、面板顶边是不是
一屏比一屏低。

## 6. 坑表

| 现象 | 原因 | 处理 |
|---|---|---|
| `geq` 跑完没有任何输出文件，或 0 字节 | image2 复用器没收到「单帧覆盖」指令 | `-frames:v 1 -update 1` 一起给 |
| 截图右缘一块白楔 | 在已烤过白渐变的 PNG 上二次打深色渐变，alpha 露白 | 回原始大图重裁重打，成品只过一次滤镜 |
| 命令跑 120s 被砍，片没出 | 6 屏 60s 要渲 1800 帧，前台等必然超时 | `nohup … &` 或后台 job，再轮询日志和输出文件 |
| 口型和字头对不上 | 剪映链路按屏均分节拍（`lip_period` 默认 0.42，`jianying_engine.py:748-752`），不跟音频 | 口型交给 paper 链路的 `char_layer`（按 `lip_audio` 切口型状态，`export/char_layer.py:204-209`） |
| 想写手写中文，做不出来 | 本机无中文手写体，CSS 只有 PingFang/冬青黑 | 手绘感交给笔画与面板；中文字形只能位图贴 |
| `submit_spec.sh` 卡在回归 | 默认先跑 `preflight_copy.sh` | `STUDIO_SKIP_PREFLIGHT=1`，只跳回归 |
| 面板压到人物身上 | `pt` 是下限，正文一长面板被推下去而高度不变 | 正文 ≤3 行，或把 `ph` 调到 700 以内 |
| 截图没换，出的还是上一张 | `--shots` 数量少于屏数，多出的屏沿用最后一屏 | `shots` 数量对齐 `plan`，`--caps` 一起对齐 |
| 渲完找不到 `spec.json`，复现不了 | 没给 `--workdir`，默认 `tempfile.mkdtemp` 渲完即删 | 固定 `--workdir ~/CreatorStudio/work/<slug>` |
| 中间某屏整屏空白 | CDP 掉帧，帧数不足 | 看引擎日志的 `[paper] x.xxs / n 帧`，重跑；别在机器满载时出片 |
| 换手势那一下动作突然加速 | 两段交叠太短（按旧段 end 对齐，交叠可能只剩几十毫秒），一帧换完≈硬切 | 走 `char_layer.plan_transitions()`：按姿态语义/重心/高度差铺交叠，姿势权重用 smootherstep（两端速度为 0），只换口型时不造交叠。**交叠时长要按手的位移定**：`pose_fade_need = max(pose_fade_for(diff), 曲线峰值斜率 × 手移 / POSE_HAND_SPEED)`（`POSE_HAND_SPEED=380` 设计 px/s，肤色掩膜剔掉人脸框后逐姿势量手移，按路径+mtime 缓存）；窗口不够先提前起势（lead-in），别压 `end`。混合曲线用两端二次+中段线性，峰值斜率 1.471。改前后：手部单帧最大位移 90.4→24.3 px、加速度尖峰 225626→28088 px/s² |
| 交叠已经拉到极限，个别句子手还是快 | 那些句子本身只有 0.2–0.3 s，char_layer 内部挪不出 0.75 s 的窗口（前段短、无同姿势邻段可借） | 根治在规划层：`vertical_short.build_spec` 里合并短句、按短语组挂姿势，不在渲染层硬挤 |
| 换服装后人物忽大忽小 | 母版画布不是一块（1254² 与 2048² 混用），人物占画布比例差近 3% | 摆位宽度乘 `character_assets.sprite_ratio()` 量出的内容框比例（带缓存），基准取同动作跨服装中位数；缺图一律回 1.0，不拦出片 |
| 立绘右上角飘着一小块橙色 `hh` | 那是 `expressions/anger.png` 的漫画怒气符，按情绪叠上去的，不是脏点 | 别删素材；不想要愤怒情绪就把那一段的 expression 换掉 |
