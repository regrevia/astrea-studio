# 平台出片矩阵（platform matrix）

> 单一事实来源：`integrations/mac-worker/export/platform_profiles.py` 的 `PROFILES`。
> 本文是它的人读版；改代码必须同步改表（测试 `test_profiles_cover_registry_labels`
> 会守平台清单，表格一致性靠人工 review）。
>
> 平台清单真值：`integrations/mac-worker/platform_registry.py` 的 `LABELS`（17 个 id）。
> 本文不发明任何平台 id。

## TL;DR

1. 剪映导出面板**改不了宽高比**，只能缩放。竖版 9:16 必须在**建草稿时就定画布**。
2. 导出端只做两件事：分辨率下拉 → 确切标签（`480P/720P/1080P/2K/4K/8K/原始`），
   帧率下拉 → 确切标签（`24/25/29.97/30/50/59.94/60/120`）。
3. 矩阵里 **17 个平台全部是 `assumed`**：这一轮离线做（联网检索不可用），没有一个
   核到官方原文。下游按「目标参数」用，不要当硬约束。

## 平台矩阵

| 平台 id | 中文名 | 比例 | 分辨率 | fps | 容器 | 码率档 | source |
|---|---|---|---|---|---|---|---|
| douyin | 抖音 | 9:16 | 1080x1920 | 30 | mp4 | 推荐 | assumed |
| xiaohongshu | 小红书 | 9:16 | 1080x1920 | 30 | mp4 | 推荐 | assumed |
| bilibili | B站 | 16:9 | 1920x1080 | 30 | mp4 | 更高 | assumed |
| kuaishou | 快手 | 9:16 | 1080x1920 | 30 | mp4 | 推荐 | assumed |
| tencent | 视频号 | 9:16 | 1080x1920 | 30 | mp4 | 推荐 | assumed |
| weibo | 微博 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| zhihu | 知乎 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| baijiahao | 百家号 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| hupu | 虎扑 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| xueqiu | 雪球 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| alipay | 支付宝 | 9:16 | 1080x1920 | 30 | mp4 | 推荐 | assumed |
| dayu | 大鱼号 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| youtube | YouTube | 16:9 | 1920x1080 | 30 | mp4 | 更高 | assumed |
| instagram | Instagram | 9:16 | 1080x1920 | 30 | mp4 | 更高 | assumed |
| facebook | Facebook | 9:16 | 1080x1920 | 30 | mp4 | 更高 | assumed |
| facebook-page | Facebook 主页 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |
| article | 通用文章 | 16:9 | 1920x1080 | 30 | mp4 | 推荐 | assumed |

「容器」是**目标容器**：面板「格式」实测当前是 MOV，导出前必须切到 MP4
（格式下拉的完整选项集还没探完，见 `panel_probe.py` 的 `ExportFormatInput`）。

`profile_for("facebook", "16:9")` 这类覆盖是允许的：保留原档案的短边像素预算
（1080）重算宽高 → `1920x1080`，并把 `source` 降级成 `assumed`（覆盖是调用方
意图，不是平台结论）。

## 为什么导出面板改不了比例

证据（全部来自真实面板，不是推测）：

- 导出面板本行显示 **「以草稿尺寸（1920x1080）导出」**——导出尺寸是由草稿决定的；
- 草稿参数面板显示 **比例 16:9 / 分辨率 适应**——比例是草稿属性；
- 导出面板的分辨率下拉只有**像素档位**（480P…8K/原始），没有「9:16 / 16:9 / 1:1」
  这种比例项；
- 所以改分辨率 = 改缩放目标像素，画面宽高比纹丝不动。

结论与做法：

1. **比例只能由草稿画布决定**。要竖版，就必须生成 1080x1920 的草稿；在导出面板上
   无论选什么档位都变不出来。
2. **同一内容发横竖两个平台** = 生成**两份草稿**（横版 + 竖版），导出两次。
   不要指望「导一次再转比例」——那会裁掉画面或加黑边。
3. 导出端只负责像素对齐：`resolution_option(profile.width, profile.height, canvas=canvas)`。
   若 `canvas` 与目标同比例但尺寸不同 → 返回档位标签（不会选「原始」）；
   若 `canvas` 恰好等于目标 → 返回「原始」（不重采样）；
   若 `canvas` 与目标是**反向**（竖目标 + 横画布）→ 照常返回档位，但 reason 里带
   ⚠ 警告：这单导出来就是带黑边/被裁的，必须重建草稿。

## 哪些值是 assumed、需要人工确认

**全部 17 个都是 assumed。** 判定标准很硬：只有「平台官方文档明载 + notes 里能给出
可核对出处」才配 `documented`。这一轮是离线做的（联网检索接口 401），所以一条都
不敢标 documented。仓库里另有 web 端发布校验规则可作**旁证**，但旁证不是官方原文。

### A 组：有仓库旁证（先确认这几个，成本最低）

| 平台 | 本表结论 | 仓库旁证 | 说明 |
|---|---|---|---|
| youtube | 16:9 / 1920x1080 | `publish.json` → `youtubeResolutionSuggestion`："建议视频分辨率：1920×1080（16:9）或 1080×1920（9:16）。" | 旁证只给两个比例，没给 fps/码率；Shorts 走 9:16 需另建草稿 |
| kuaishou | 9:16 / 1080x1920 | `publish.json` → `kwaiResolutionSuggestion`："推荐视频分辨率：1080x1920（竖屏）。" | 旁证直接给了尺寸，**最接近 documented 的一条** |
| instagram | 9:16 / 1080x1920 | `publish.json` → `instagramReelAspectRatio`："Instagram Reel 视频宽高比需在 4:5 ~ 9:16 范围内。" | 旁证是范围不是尺寸；1080x1920 落在范围内 |
| facebook | 9:16 / 1080x1920 | `brandPromotion.json` → `facebookVideoAspectRatio`："Facebook 视频宽高比需在 9:16 ~ 4:5 范围内" | 同上；主站信息流横版需 `profile_for(..., "16:9")` |
| tencent | 9:16 / 1080x1920 | `publish.json` → `wxSphVideoSize` / `wxSphVideoDuration` | 只有体积/时长旁证，**比例仍是纯惯例**，别当旁证用 |

（旁证文件：`project/aitoearn-web/src/app/i18n/locales/zh-CN/publish.json`、
`project/aitoearn-web/src/app/i18n/locales/zh-CN/brandPromotion.json`；消费方是
`project/aitoearn-web/src/components/PublishDialog/hooks/usePubParamsVerify.tsx`。
引用已按原文逐字核对，包括句末句号。）

### B 组：零仓库旁证（纯行业惯例，当成约束前必须先问/先查）

| 平台 | 需要确认什么 | 风险 |
|---|---|---|
| douyin | 竖版 1080x1920/30 是否仍是推荐档 | 主投放渠道，猜错代价最大 |
| xiaohongshu | 视频笔记是否 9:16；图文是否 3:4（1080x1440） | 视频/图文两套比例，本表只覆盖视频 |
| bilibili | 主站是否 16:9 1920x1080；竖版是否必须走竖屏分区 | 竖版投错分区 = 没有推荐流 |
| weibo | 竖版能否进推荐流，还是只横版 | 竖版可能被加边 |
| zhihu | 视频回答是否只吃横版 | 平台已边缘化，收益不确定 |
| baijiahao | 横版是否唯一形态 | 百度系信息流规则不公开 |
| hupu | 社区帖视频是否横版 | 小平台，规则不公开 |
| xueqiu | 是否横版 | 小平台，规则不公开 |
| alipay | 生活号短视频是否 9:16 | 发布面窄、文档少，风险最高 |
| dayu | 是否横版 | 平台活跃度低 |
| facebook-page | 主页是否横版、Reels 是否归 `facebook` | 仓库没区分「主页」，这条是拆分假设 |
| article | 通用文章是否涉及视频导出 | 纯图文不涉及；仓库发现平台时已显式排除它 |

### 怎么把某条翻成 documented

1. 找到官方原文（帮助中心/创作规范页），确认**尺寸、比例、帧率、容器**四件事；
2. 在 `PROFILES[<id>].notes` 里加 `官方出处: <文档标题> <URL>（核对于 YYYY-MM-DD）`；
3. 把该条 `source` 改成 `"documented"`；
4. 跑测试：`test_documented_requires_citation` 会强制 notes 里有出处，否则红；
5. 同步改本表 `source` 列。

**不要把旁证当官方文档。** 旁证只能让确认变快，不能让猜测变成事实。

## 分辨率映射规则（判据写死在这里，避免各处口径不一）

档位（面板实测文案）：`480P(854x480)`、`720P(1280x720)`、`1080P(1920x1080)`、
`2K(2560x1440)`、`4K(3840x2160)`、`8K(7680x4320)`，外加 `原始 = 草稿尺寸`。

| 输入 | 输出 | 理由 |
|---|---|---|
| `canvas == 目标` | `原始` | 不重采样，零画质损失、导出最快 |
| 1080x1920 / 1920x1080 | `1080P` | 短边 1080 命中；480P/720P 装不下 |
| 720x1280 | `720P` | 目标就在 720 档 |
| 1440x2560 | `2K` | 短边 1440 只有 2K 起 |
| 2160x3840 / 4320x7680 | `4K` / `8K` | 同上，逐档比 |
| 1600x900 | `1080P` | 最小的能覆盖档，**不**跳到 2K/4K |
| 1080x2400 | `2K` | 短边 1080 够，但长边 2400 > 1920，1080P 装不下 |
| 640x360 / 100x100 | `480P` | 面板最低档就是 480P，不造档 |
| 10000x5000 | `8K`（clamp） | 面板没有更大档；reason 写明实际会被缩到 7680x4320 |
| 竖目标 + 横画布 | 档位 + ⚠ | 导出只缩放不转比例，必须重建草稿 |

**判据**：档位的**长边和短边都必须 ≥ 目标的长边和短边**，取满足条件的**最小档**。
只比短边会让 1080x2400 错配到 1080P；只比长边会让 1080x1920 错配到 4K。
选最小档而不是最大档，是因为 4K/8K 的码率/体积是 1080P 的 4~16 倍，而平台会二次
转码，肉眼无差，只会让导出更慢、上传更容易超限。

## 帧率规则

| 场景 | 输出 | 理由 |
|---|---|---|
| `fps=29.97` / `59.94` | `"29.97"` / `"59.94"` | 面板就是这两个非整数档 |
| `fps=30.0` | `"30"` | 整数不带小数点 |
| `fps=48`（面板无此档） | `"50"` | 贴最近档，reason 留痕「面板没有 48」 |
| `fps=30, canvas_fps=25` | `"25"` | **保留草稿帧率**：面板写「默认使用草稿帧率，修改可能导致预览和导出画面有差异」；30→25 是重采样时间轴（丢帧/重排），损失更大 |
| `fps=30, canvas_fps=23.976` | `"24"` | 草稿帧率不在面板档位时贴最近档，reason 里保留原始值 23.976 |

要用平台帧率（例如 60），正确做法是**建草稿时按 60 定帧率**，不是在导出面板硬改。

## 复现

```bash
cd <repo>
# 仓库没装 pytest，用自带 __main__（34 个用例）
python3.11 integrations/mac-worker/export/test_platform_profiles.py
```

相关文件：

- `integrations/mac-worker/export/platform_profiles.py`（本表事实来源）
- `integrations/mac-worker/export/test_platform_profiles.py`（34 个用例）
- `integrations/mac-worker/export/jianying_export.py`（消费方：`profile_for(...).as_dict()`
  → `resolution_option` / `fps_option` → 点面板下拉 → 导完 ffprobe 校验宽高帧率）
- `integrations/mac-worker/export/panel_probe.py`（面板探测，产出 AX id ↔ 标签映射）
- `integrations/mac-worker/platform_registry.py`（17 个平台 id 的真值）
