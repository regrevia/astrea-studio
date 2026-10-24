# creator-studio-timeline

用一个 JSON 时间轴出片：**母版 mp4 → 各平台合规规格**。纯 ffmpeg，不装剪映、不开 GUI、不抢前台。

```bash
python3 timeline_render.py --spec spec.json --out master.mp4      # 母版（H.264/AAC，CRF 16）
python3 deliver.py --master master.mp4 --platform douyin,bilibili --outdir out/
```

两步都是普通后台进程：可以在 CI 里跑、可以在服务器上跑、可以 10 个规格一次出完。

## 为什么又造一套

因为「渲染」和「交付」经常被绑在一个必须有人点的客户端里：

- 剪辑软件导出面板一次只能出一个规格，分辨率只能按草稿等比缩，还要占着前台；
- 用现成模板 API，就得把素材和成片都交给别人；
- 直接调 `ffmpeg`，规格逻辑（该给 B 站多大、该给抖音多大、比例不一致时怎么办）会散落一地。

这个仓库只做两件事，且做死：**时间轴怎么讲**（spec）和**每个平台要什么规格**（profile）。
它不做转场、不做关键帧动画、不解析任何剪辑软件的私有工程格式。

## 环境

| 依赖 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.9+ | 只用标准库 |
| ffmpeg / ffprobe | 在 9.0.2 上验证过；需要 `libx264`、`aac`、`overlay`、`concat`、`amix`、`loudnorm` | 4.x 大概率可用但没测 |
| Pillow | 9+ | **只有画面里有文字时才需要**；无文字可完全不装 |
| 字体 | 任一 CJK ttf/ttc | 可用 `TIMELINE_FONT=/path/to/font.ttc` 指定；否则按候选列表找 Noto/Hiragino/STHeiti |

```bash
pip install pillow          # 需要字幕时
brew install ffmpeg         # macOS
apt install ffmpeg          # Debian/Ubuntu
```

环境变量覆盖二进制路径：`FFMPEG=/path/to/ffmpeg`、`FFPROBE=/path/to/ffprobe`。

## 五分钟跑通

```bash
# 1) 合成演示素材（三支 3 秒竖屏片段 + 一段 BGM，不下载任何东西）
python3 examples/make_demo_assets.py --out examples/assets

# 2) 出母版（9 秒 1080x1920，三条字幕，一条背景乐）
python3 timeline_render.py --spec examples/spec.json --out /tmp/master.mp4 --json

# 3) 出抖音 + B 站两个规格
python3 deliver.py --master /tmp/master.mp4 --platform douyin,bilibili --outdir /tmp/out
#    -> /tmp/out/master__douyin__1080x1920.mp4     1080x1920@30
#    -> /tmp/out/master__bilibili__1920x1080.mp4   1920x1080@30（竖母版转横版，默认补模糊背景）
#    文件名 = <母版名>__<平台>__<宽x高>.mp4；用 --stem 换主体名

# 只看决策不编码（秒级，适合每个 PR）
python3 deliver.py --master /tmp/master.mp4 --platform all --dry-run
```

## spec 格式

一个 JSON 文件，四个顶层字段：`canvas` / `video` / `text` / `audio`。
相对路径的 `src` **相对 spec 文件所在目录**解析，所以整包（spec + 素材）可以整体搬走。

```json
{
  "canvas": { "width": 1080, "height": 1920, "fps": 30 },
  "video": [
    { "src": "assets/a.mp4", "in": 0, "out": 3 },
    { "src": "assets/b.mp4", "in": 0, "out": 3, "fit": "contain" }
  ],
  "text": [
    { "text": "第一行", "start": 0.4, "end": 2.8, "y": 0.16, "size": 0.05 },
    { "text": "第二行\n第二段", "start": 3.4, "end": 5.6, "y": 0.5,
      "size": 0.06, "box": "#00000099", "radius": 0.35, "fade": 0.2 }
  ],
  "audio": [
    { "src": "assets/bgm.m4a", "start": 0, "volume": 0.3, "loop": true, "fade": 0.6 }
  ]
}
```

### canvas

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `width` / `height` | 1920 / 1080 | **必须是偶数**（h264 + yuv420p） |
| `fps` | 30 | 输出帧率，切片会被统一到它 |

### video[]（至少 1 段，按数组顺序硬切拼起来）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `src` | 必填 | 视频路径 |
| `in` | 0 | 起始秒 |
| `out` | **必填** | 结束秒，必须大于 `in`。不猜时长——猜错会静默出片 |
| `fit` | `cover` | `cover`=铺满裁切；`contain`=等比缩放后补黑边。**没有拉伸选项** |
| `speed` | 1.0 | 变速；总时长按 `(out-in)/speed` 计 |

### text[]（可选，按顺序叠加）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `text` | 必填 | 内容，`\n` 换行 |
| `start` / `end` | 0 / 总时长 | 秒；`end` 会被夹到总时长内 |
| `x` / `y` | 0.5 / 0.9 | **文字块中心**的归一化坐标（0..1），会自动夹在画面内 |
| `size` | 0.055 | 字号 = `size × 画布高度`（**相对高度**，换分辨率不会错位） |
| `color` | `#ffffff` | `#rrggbb` 或 `#rrggbbaa` |
| `stroke` | 2 | 描边粗细，单位是字号像素的百分比（`4` = 字号的 4%） |
| `stroke_color` | `#000000` | 同上颜色格式 |
| `box` | 透明 | 文字底衬颜色，`#rrggbbaa`；`alpha=0` 即不要底衬 |
| `radius` | 0.5 | 底衬圆角，`0`=直角，`1`=两端全圆 |
| `align` | `center` | `left` / `center` / `right` |
| `fade` | 0 | 淡入淡出秒数（两端各一次） |

### audio[]（可选，与画面完全独立）

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `src` | 必填 | 音频路径（mp3/m4a/wav…） |
| `start` | 0 | 在成片时间轴上的起始秒 |
| `in` | 0 | 素材内起始秒 |
| `out` | 0 | 素材内结束秒；`0` 表示取到素材结尾 |
| `volume` | 1.0 | 线性增益 |
| `loop` | false | 循环填满剩余时长（BGM 常用） |
| `fade` | 0 | 淡入淡出秒数（两端各一次） |
| `role` | `""` | 仅作标记，不参与计算 |

混音后统一走 `loudnorm=I=-16:TP=-1.5:LRA=11`，避免不同素材拼在一起忽大忽小。
画面切片自带的音轨**不参与**混音——想要原声就把它单独写进 `audio[]`。

spec 不合法（缺 `src`、缺 `out`、时间轴越界、颜色写错、素材不存在）时直接非零退出并指出第几段，
不会产出一个"看起来成功"的坏文件。

## 平台规格

`platform_profiles.py` 里 17 个平台的档案：分辨率、帧率、容器、码率档、比例。

```bash
python3 deliver.py --list-platforms
```

**17 个平台的参数目前全部标记为 `assumed`**——按平台惯例推断，**没有任何一条核到官方原文**。
`source` 字段就是这个意思：

| `source` | 含义 |
| --- | --- |
| `documented` | 官方文档明载，`notes` 里给出可核对出处 |
| `assumed` | 按惯例推断，**下游不要当硬约束** |
| `fallback` | 未知平台兜底，只保证不崩 |

所以：**请按自己账号后台的实际要求改 `platform_profiles.py`**，改完把 `source` 改成 `documented`
并写上出处。这个仓库不为任何平台的上传规格做承诺。

比例和母版不一致时，`--fit` 决定怎么取舍，三种都会显式写在输出里：

| `--fit` | 做法 | 代价 |
| --- | --- | --- |
| `blur`（默认） | 补模糊背景 | 竖化横片不丢画面，观感接近平台原生 |
| `contain` | 补黑边 | 最快最稳，黑边明显 |
| `crop` | 居中裁切 | 满屏无黑边，但会切掉画面边缘 |

码率按 `分辨率 × 帧率` 推算（0.08 bit/(像素·帧) ≈ 1080p30 的 5 Mbps 量级），
平台会二次转码，再加码率只增体积。每个平台单独校验输出：尺寸、帧率、编码、时长，
单个平台失败不影响其它平台，最后按 `status` 统计并给非零退出码。

`--dry-run` 打印每个平台将要执行的决定（不编码）；`--evidence x.json` 落盘完整证据
（含每行 `status`、`cmd`、`probe`），CI 里断言这个文件而不是渲染日志。

## 字幕为什么不用 `drawtext`

很多 ffmpeg 构建没有 `freetype` / `libass`（本仓库开发机的 ffmpeg 9 就没有
`drawtext`、`subtitles`、`ass` 滤镜）。所以文字走 **Pillow 画成 RGBA PNG → `overlay` 合成**：
不依赖系统 fontconfig，顺带拿到描边、圆角底衬、多行对齐的完全控制权。
代价是渲染时必须能 import Pillow，且要有一个 CJK 字体文件。

## 测试

```bash
python3 -m unittest discover -p 'test_*.py' -v   # 48 项：渲染 + 交付
python3 test_platform_profiles.py                # 34 项（pytest 风格，自带 __main__）
```

`test_platform_profiles.py` 是「pytest 风格函数 + 自带 `__main__`」：有 pytest 时 pytest 收，
没 pytest 时直接跑。它**故意**不被 `unittest discover` 发现，所以必须单列一行——少跑这一行，
34 项就静默消失。

渲染类测试不依赖任何私有素材，缺素材时用 `examples/make_demo_assets.py` 现场合成。

## 已知限制（写死，避免误解）

1. **不依赖剪映**，只依赖 ffmpeg；也因此没有 `drawtext`/`subtitles`/`ass` 可用时字幕走 Pillow。
2. **不解析剪辑软件的工程文件**：私有格式会变（例如剪映 11.6 会重新加密 `draft_info.json`），
   自家 spec 才是唯一事实来源。
3. **文字尺寸相对画布高度**，换分辨率不错位；这也是唯一不需要按平台改字幕的原因。
4. **平台参数是 `assumed`**，可以用，但别当官方规格。
5. v1 **没有转场**（硬切），没有关键帧动画。需要的话自己加 `xfade` 并处理时间轴重叠——
   别一上来就把这层复杂度塞进主链路。

## 许可证

MIT，见 `LICENSE`。
