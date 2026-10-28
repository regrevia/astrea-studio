# 竖屏短片素材从哪来：六条通道 + 一条命令抓包

以前只有一条路：人肉开 Envoy、截图、存进 `~/CreatorStudio/materials/shots/`，再在命令行手抄
`--shots .../envoy-head.png#838#contain#396`。换主题就重来一遍。
现在素材有了「包」的概念：一条命令抓一批图，收进 `materials/packs/<slug>/`，spec 里只写短名字。
下文命令都在仓库根目录执行，产物路径都是本机实测过的。

| 通道 | 能拿什么 | 一条命令 | 实测产物 |
| --- | --- | --- | --- |
| 网页截图（主力） | 竖屏整页/分段 PNG | `grab_material.py --urls …` | `materials/packs/envoy-demo/shots/*.png` |
| Envoy 直读 | 正文文字、block 结构 | MCP `envoy_read` | `materials/probe/envoy-read/enigma.md` |
| PDF 页渲染 | 文档页 PNG | `Chrome --print-to-pdf` + `qlmanage -t` | `materials/probe/pdf/doc-page1.png` |
| 视频抽帧 | 单帧 PNG | `ffmpeg -ss … -frames:v 1` | `materials/probe/frame/demo_bg-2s.png` |
| 剪映素材库 | **不可程序化枚举** | 只能 `ls Cache/effect` | `materials/probe/jianying/effect-dirs.txt` |
| 本地相册/录屏 | 截屏 PNG、现成录屏 | `screencapture -x` | `materials/probe/screen/desktop.png` |

## 1. 网页截图：`grab_material.py`（主力通道）

`integrations/mac-worker/tools/grab_material.py` 是 Python 外壳，真正驱动 Chrome 的是同目录
`cdp_grab.mjs`（Node 内建 `WebSocket` 直连 CDP，无第三方依赖，从 `paper/cdp_shots.mjs` 那套结果复用）。

```bash
cd <repo>

# 一串 URL，竖屏 1080x1920，每个页面抓 2 张（超出视口就分段下滚）
python3 integrations/mac-worker/tools/grab_material.py \
  --pack envoy-demo \
  --urls https://github.com/regrevia/envoy https://example.com \
  --viewport 1080x1920 --n 2

# 按主题搜（DuckDuckGo html 版，失败自动换 Bing），取前 4 条
python3 integrations/mac-worker/tools/grab_material.py \
  --pack hand-drawn --topic "手绘动画 分镜" --max-urls 4

# 长页面等前端渲染完再拍，并先下滚 600px
python3 integrations/mac-worker/tools/grab_material.py \
  --pack long --urls "https://en.wikipedia.org/wiki/Enigma_machine" \
  --wait-for "main" --scroll 600 --n 3

# 2x 清晰图（1080x1920 版位下出 2160x3840），给大屏/封面用
python3 integrations/mac-worker/tools/grab_material.py --pack hd --urls https://example.com --scale 2
```

实测（`--urls https://github.com/regrevia/envoy --n 2`）：7.8s 出 2 张，
242950 / 410615 字节，页面高 8571px，`index.json` 里 `count=2 failed=0`。

`--topic` 的搜索后端按 Bing → DuckDuckGo → 维基百科依次试，实测情况：

- Bing 能用，但结果链接是站内跳转 `bing.com/ck/a?...&u=a1<base64>`，要解出真实地址（已实现）；
- DuckDuckGo html / lite 在本机被 TLS 层掐断（`SSL: UNEXPECTED_EOF_WHILE_READING`），当成不可用；
- 维基百科搜索 API 兜底，有正文有图，但只对百科类主题对得上。

所以主题模式是「够用」不是「好用」：实测 `手绘动画 分镜` 能出知乎、论坛页，技术类主题
（`Envoy MCP protocol`）会漂到不相关页面。靠谱的做法是给 `--urls`，或先手动搜一轮再喂链接。

参数都是可选项，默认值：`--viewport 1080x1920`、`--n 1`、`--scale 1`、`--wait-ms 2500`、
`--timeout-ms 20000`、`--max-urls 8`；`--root` 或环境变量 `MATERIALS_ROOT` 改素材根目录
（默认 `~/CreatorStudio/materials`）。

产物布局（`--pack` 决定 slug；不写就从主题或域名生成，中文主题没有拼音会退化成日期戳，建议显式给 `--pack`）：

```
<materials>/packs/<slug>/
  index.json     机器读：每张图的 url/尺寸/字节/滚动位置/是否可用/失败原因
  shots/*.png    图，文件名 = 页面 slug + 序号（<name>-1.png / <name>-2.png）
  meta.md        人读：清单表格 + 能直接粘进 spec 的 --shots 行
```

`index.json` 结构（`schema: creator-studio/materials-pack/1`）：

```json
{
  "schema": "creator-studio/materials-pack/1",
  "slug": "envoy-demo",
  "created_at": "2026-10-27T08:16:51+08:00",
  "source": "urls",
  "topic": "",
  "request": {"urls": ["https://github.com/regrevia/envoy"], "viewport": [1080, 1920],
              "scale": 1.0, "n": 2, "scroll": 0, "wait_for": ""},
  "count": 2, "failed": 0,
  "shots": [{"name": "github-com-regrevia-envoy-1", "file": "shots/github-com-regrevia-envoy-1.png",
             "url": "https://github.com/regrevia/envoy", "final_url": "https://github.com/regrevia/envoy",
             "title": "GitHub - regrevia/envoy", "bytes": 242950, "scroll_y": 0, "page_height": 8571,
             "ref": "pack:envoy-demo/github-com-regrevia-envoy-1"}],
  "failures": [{"url": "…", "reason": "login_required", "reason_zh": "要登录：…", "detail": "…"}]
}
```

失败不糊弄，每张图都判一类，屏幕和 `index.json` 里都给人话：

| reason | 含义 | 下一步 |
| --- | --- | --- |
| `login_required` | 有密码框，抓到的是登录页（实测 `github.com/login` 就落这里） | 这条走 Envoy/手动，别浪费时间重试 |
| `timeout` | `--wait-for` 的选择器 20s 没出现（实测写个不存在的选择器必现） | 换选择器或调大 `--timeout-ms` |
| `empty` | 正文没字 / 截图接近纯色 | 页面是自绘 canvas 或要登录 |
| `http_error` | 主文档 4xx/5xx（实测 `example.com/nope-404`） | 换 URL |
| `nav_error` | DNS/证书/断网 | 检查网络 |
| `exception` | Chrome 侧异常 | 看 `detail` |

退出码：`0` 全成 / `3` 有失败但抓到了图 / `1` 一张都没成 / `2` 参数或搜索失败。

## 2. Envoy 直读：要文字时用它，不要截图

`mcp__envoy__envoy_read` 直接把页面读成带 block 编号的文本，比截图后 OCR 靠谱，也比自己解 HTML 稳。

```
envoy_read(source={kind:"article", url:"https://en.wikipedia.org/wiki/Enigma_machine"},
           goal="取 3-5 句可直接当素材文字的内容", budget={max_response_chars:3000})
→ material_id=mat-2c78b0148a027ac0cde1f623, 14 blocks / 2976 chars
```

预算被截断时接着 `envoy_expand(material_id=…, offset=2976, max_chars=8000)`，不要重读 URL。
实测记录和可直接用的句子存在 [enigma.md](~/CreatorStudio/materials/probe/envoy-read/enigma.md)。

边界（都实测过）：GitHub 仓库页返回 `Envoy read blocked … need authenticate · read_source`，
Envoy 不在非交互会话里弹窗，这类页只能本机带可见浏览器跑一次或导 cookies；图片 block 只给
`(no text)` 占位，要图得 `scope.include_visual` + `envoy_observe`。

## 3. PDF 页面渲染：文档页当素材

macOS 上没有 `pdftoppm` / `mutool`，Quartz 也因 Xcode 协议没同意而 import 失败，
能用的就两条：`qlmanage` 出高清缩略图（推荐），`sips` 直转（只有 72dpi）。

```bash
# 先造/拿到 PDF：网页打成 PDF（Chrome 无头即可）
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --use-mock-keychain --disable-gpu --no-first-run \
  --user-data-dir=/tmp/ch/prof --print-to-pdf=/tmp/ch/doc.pdf https://example.com

# 第 1 页 → 2000px 长边 PNG（-s 是长边，不是宽）
qlmanage -t -s 2000 -o /tmp/ch/ql /tmp/ch/doc.pdf     # → /tmp/ch/ql/doc.pdf.png
```

实测：`doc.pdf` 59619 字节 → `doc-page1.png` **1545×2000**、103851 字节。
`qlmanage` 只出**第一页**；`sips -s format png doc.pdf --out x.png` 能跑但只有 **612×792**，
放大就是糊的，竖屏片里别用。多页要么一页一个 PDF，要么在 Chrome 里逐页截图。

产物：[doc.pdf](~/CreatorStudio/materials/probe/pdf/doc.pdf)、
[doc-page1.png](~/CreatorStudio/materials/probe/pdf/doc-page1.png)。

## 4. 视频抽帧：现成录屏/素材视频变静帧

```bash
# 第 2 秒一帧
ffmpeg -v error -y -ss 2 -i ~/CreatorStudio/materials/demo_bg.mp4 -frames:v 1 frame_2s.png

# 每 2 秒一帧，最多 4 张（做九宫格/关键帧挑选）
ffmpeg -v error -y -i ~/CreatorStudio/materials/demo_bg.mp4 \
  -vf "fps=1/2" -frames:v 4 frames-%02d.png

# 横转竖：居中裁 9:16 再缩放（源片比例不对时先裁）
ffmpeg -v error -y -ss 2 -i in.mp4 -vf "crop=ih*9/16:ih,scale=1080:1920" -frames:v 1 out.png
```

实测：`demo_bg.mp4`（1080×1920，6.000000s）取第 2 秒 → **1080×1920** PNG、10389 字节。
产物：[demo_bg-2s.png](~/CreatorStudio/materials/probe/frame/demo_bg-2s.png)。
抽帧的好处是帧序稳定、可复现，比录屏反复跑一遍强。

## 5. 剪映素材库：**不能当素材源**

结论：素材库不可程序化枚举。VectCutAPI 只有 `/add_sticker`（贴纸 id 要你自己给）和 12 个
`get_*_types` 目录接口，返回的是 `{"name":"圆形"}` 这种纯名字，没有 resource_id、没有搜索、
没有分页、没有下载；素材库/BGM/贴纸/音效都没有对应路由。详细证据见
[capability.md](<repo>/docs/probe/jianying-agent/capability.md)。

磁盘上只有「用户用过」的特效缓存，可以拿来看、不能拿来选品：

```bash
ls "$HOME/Movies/JianyingPro/User Data/Cache/effect" | head -50   # 本机 249 个资源目录
find "$HOME/Movies/JianyingPro/User Data/Cache/effect" -type f | head -20
```

实测产物：[effect-dirs.txt](~/CreatorStudio/materials/probe/jianying/effect-dirs.txt)、
[effect-files-sample.txt](~/CreatorStudio/materials/probe/jianying/effect-files-sample.txt)。
数量取决于用户用过多少，不能当素材池；`Cache/music/thirdparty`、`onlineMaterial`、`Download`、
`EMaterial`、`Resources/Lut` 在本机都是 0 文件。

## 6. 本地相册 / 录屏

```bash
# 当前屏幕一帧（实测可用；没给屏幕录制权限会失败或返回黑图）
screencapture -x ~/CreatorStudio/materials/probe/screen/desktop.png

# 相册：本机 ~/Pictures 里只有 Photos Library.photoslibrary，散图 0 张
find ~/Pictures -maxdepth 3 -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.heic' \)
```

实测：`screencapture` 出 71227 字节 PNG，见
[desktop.png](~/CreatorStudio/materials/probe/screen/desktop.png)。
相册要走 Photos.app 导出（GUI/需要自动化授权），脚本别指望；现成录屏 `.mp4` 一律走第 4 条抽帧。

## 7. 接进 spec：`pack:<slug>/<name>`

解析层有两处，都认同一套写法：`integrations/mac-worker/tools/material_packs.py`（命令行出片，
`deploy/schematic_preview.py` 在参数解析阶段换成绝对路径）和
`integrations/mac-worker/export/vertical_short.py` 的 `resolve_materials()`（平台的
`spec.materials` 通道，见 §7.1）。渲染层不碰字符串，拿到的一律是绝对路径。

```bash
# 命令行：包内引用 + 版位后缀（#高#contain#宽）
MATERIALS_ROOT=~/CreatorStudio/materials ~/CreatorStudio/.motionvenv/bin/python \
  deploy/schematic_preview.py deploy/specs/envoy_intro.json \
  --shots "pack:envoy-demo/github-com-regrevia-envoy-1#838#contain#396" \
  --caps "仓库页第一屏" --out /tmp/render-smoke/pack-ref.mp4 --workdir /tmp/render-smoke/work
```

已实测跑通：上面这条命令 35.06s 出 6 屏片（1052 帧，1859 KB 静音轨 + 人物层），
抓下来的 GitHub 仓库截图确实贴进了首屏版位（角标「抓下来的图」），
成片 [pack-ref.mp4](~/CreatorStudio/materials/probe/render/pack-ref.mp4)、
抽帧对照 [pack-ref-frames.png](~/CreatorStudio/materials/probe/render/pack-ref-frames.png)。

spec JSON 顶层也能写，命令行 `--shots` 为空时生效（临时换图不用改 spec）：

```json
{"spec": {"…": "…", "shots": ["pack:envoy-demo/github-com-regrevia-envoy-1#838#contain#396",
                              "sketch:timeline"]}}
```

规则：

- `pack:<slug>/<name>` → `<materials>/packs/<slug>/shots/<name>.png`，带不带 `.png` 都行；- `pack:<slug>` 只写包名 → 取 `index.json` 里第一张可用的；
- 后缀 `#高#contain#宽` 原样透传；非 `pack:` 的写法（绝对路径、`~`、`sketch:`）行为不变；
- 写错立刻报人话：`素材包不存在：nope（现有包：errs, long, smoke）`、
  `素材包 smoke 里没有 nope。包里可用：example-com`；
- `MATERIALS_ROOT` 决定根目录，默认 `~/CreatorStudio/materials`。

### 7.1 平台 `spec.materials`：给竖屏成片插真素材

`POST /api/production/runs` 的 `spec.materials` 收一组字符串，一行一个引用；
制作台「素材（可选）」文本框写的就是它。解析规则与命令行一致：`pack:` 走素材包，
其余按绝对路径（`~` 会展开，相对路径拼在 `MATERIALS_ROOT` 下），后缀 `#高#填充#最大高` 先剥掉。

```json
{"engine": "timeline", "mode": "vertical_short", "materials": [
  "pack:envoy-demo/github-com-regrevia-envoy-1",
  "pack:envoy-demo/github-com-regrevia-envoy-2#838#contain#396"]}
```

落位规则：第 i 条素材进第 i 屏——卡面先讲要点，这一屏收尾前插入真图（0.9–1.6 s，
按屏长取 `min(1.6, max(0.9, 屏长-1.0))`），下一页再切走；口播起点不重排。
第一帧永远是卡面，封面标题不会被素材顶掉。

缺素材当场失败，不静默跳过：`素材不存在：pack:envoy-demo/no-such-shot（包里是 … 这些）`、
`素材不存在：pack:no-pack/x（没有这个素材包：…）`。文案故意不含 `FileNotFoundError`——
那是「本机环境问题」的判据，会被平台自动重排三次，白等三轮还是同一句错。

用了哪些素材、多大、sha256 前 16 位，落在成片工作目录 `spec.json` 的
`meta.materials[]` 里，可回溯到具体文件。

## 8. 日常怎么用

换主题 = 一条命令 + 改 spec 里几行引用，不再手工攒图：

```bash
python3 integrations/mac-worker/tools/grab_material.py --pack <主题slug> --topic "<主题词>" --n 2
# 看 meta.md 的清单，挑几张写进 spec 的 shots
```

素材包不进 git（`materials/` 在 `~/CreatorStudio/` 下，仓库外）；要留档就留 `index.json`，
它记着每张图的原始 URL 和抓取时间，能重抓。

不记得有哪些包就问指挥台：`list_platforms` 会连宿主机能力清单，把每个包的 slug、张数和前几个
`pack:` 引用一起返回（`curl -s http://127.0.0.1:8899/inventory | jq .materials` 是同一份数据）。
模型拿到引用就能直接写进 spec，不用先试错。

两种写法共用同一段解析：spec 字段那条用坏引用试过，会立刻 `素材引用有问题：素材包不存在：nope（现有包：envoy-demo）`
退出、不出片；命令行那条整条链路出过成片（见上）。

## 9. 哪种主题该用哪种途径

先按「素材最终长什么样」分流，再按可达性降级；命中最左列就照最右列做，不要六条都试一遍。

| 主题长什么样 | 首选 | 降级 | 别用 | 依据 |
| --- | --- | --- | --- | --- |
| 有公开网页（产品页、GitHub、百科、新闻） | `grab_material.py --pack <slug> --topic "<词>"`（Bing→DDG→维基） | 已知网址时 `--urls` 直给，省搜索 | 不要截图后 OCR | 实测 7.8s / 2 张 / 8571px 页面；`index.json` 留原始 URL 可重抓 |
| 要的是**文字**（原理、史实、引语） | `envoy_read` 取 block 文本（带 `goal` 收窄） | `envoy_expand` 续读，不要重读 URL | 截图再肉眼抄 | 维基实测 14 blocks / 2976 chars |
| 白皮书 / 论文 / 说明书 PDF | Chrome `--print-to-pdf` → `qlmanage -t -s 2000` | 多页就一页一个 PDF 再各出一张 | `sips`（只有 612×792，放大就糊） | 实测 1545×2000 / 103851B |
| 已有素材视频 / 录屏 | `ffmpeg -ss` 单帧 或 `-vf fps=1/2` 挑关键帧 | 先 `crop=ih*9/16:ih` 再抽，避免拉伸 | 重录一遍 | 帧序稳定可复现；实测 1080×1920 |
| 剪映素材库 / 贴纸 / BGM | 用自绘 SVG 或本地 `pack:` 替代 | 用户手动导出的成片再抽帧 | `get_*_types` 枚举（只有名字、无 id/搜索/下载） | 素材库路由不存在，见 §5 |
| 本机屏幕 / 相册 | `screencapture -x` 一帧 | 相册走 Photos.app 手动导出 | 脚本直读 `Photos Library` | 散图 0 张，需 GUI 授权 |
| 需要登录 / 付费墙 | `envoy_auth` 扫码，或本机可见浏览器导 cookies | 放弃这条，换公开源 | 反复重试 `grab`（只会落 `login_required`） | GitHub 仓库页实测被拦 |

两条纪律：

- **一条主题只留一个包**：`materials/packs/<slug>/`，重抓覆盖不追加，`index.json` 是唯一档案。
- **能自绘就别抓图**：数据图表、流程图、示意图一律 SVG，和 spec 同一套 token，比截图清楚也不掉分辨率。
