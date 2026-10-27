# 音色目录：哪些能选、哪些给逐字时间

一句话：**只有 edge 通道给逐字时间**（口型可逐字，`export/lipsync.py` 走 `source=words`）；
SAMI 的 14 个音色只拿到 `source=syllable` 的估算轨道（拿包络把字铺到有声段），且不收 rate/pitch。

所有样音同一句 19 个有音汉字（`这条短片里，嘴形要跟着字走，声音也要像真人。`），edge 用 `rate=+0%&pitch=+0Hz`，
SAMI 用默认语速；样音目录 `~/CreatorStudio/materials/voice-probe/<tag>/audio.mp3`。

| id | 通道 | 性别/年龄感 | 是否给逐字时间 | 支持 rate/pitch | 一句样音路径 |
|---|---|---|---|---|---|
| `zh-CN-YunxiNeural` | edge | 男 / 青年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-yunxineural/audio.mp3` |
| `zh-CN-XiaoxiaoNeural` | edge | 女 / 青年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-xiaoxiaoneural/audio.mp3` |
| `zh-CN-YunjianNeural` | edge | 男 / 中年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-yunjianneural/audio.mp3` |
| `zh-CN-YunyangNeural` | edge | 男 / 中年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-yunyangneural/audio.mp3` |
| `zh-CN-YunxiaNeural` | edge | 男 / 少年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-yunxianeural/audio.mp3` |
| `zh-CN-XiaoyiNeural` | edge | 女 / 青年 | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-xiaoyineural/audio.mp3` |
| `zh-CN-YunfengNeural` | edge | 男 / 中年（服务端无音频，暂不可用） | —（不可用） | —（不可用） | —（合成失败：edge 返回无音频） |
| `zh-CN-liaoning-XiaobeiNeural` | edge | 女 / 中年（东北话） | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-liaoning-xiaobeineural/audio.mp3` |
| `zh-CN-shaanxi-XiaoniNeural` | edge | 女 / 中年（陕西话） | 是 | 是 | `~/CreatorStudio/materials/voice-probe/edge-zh-cn-shaanxi-xiaonineural/audio.mp3` |
| `BV411_streaming` | sami | 男 / 青年（解说小帅） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv411-streaming/audio.mp3` |
| `BV701_streaming` | sami | 男 / 中年（沉稳解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv701-streaming/audio.mp3` |
| `BV414_streaming` | sami | 男 / 青年（赛事解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv414-streaming/audio.mp3` |
| `BV406_streaming` | sami | 男 / 青年（网文解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv406-streaming/audio.mp3` |
| `BV405_streaming` | sami | 女 / 青年（甜美解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv405-streaming/audio.mp3` |
| `BV021_streaming` | sami | 男 / 中年（东北老铁） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv021-streaming/audio.mp3` |
| `zh_male_sunwukong_clone2` | sami | 男 / 中年（猴哥） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-male-sunwukong-clone2/audio.mp3` |
| `zh_male_hougedaiyu` | sami | 男 / 青年（娇羞猴哥） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-male-hougedaiyu/audio.mp3` |
| `zh_female_xiaopengyou` | sami | 童声 / 女 | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-female-xiaopengyou/audio.mp3` |
| `zh_male_iclvop_zhangjinxiangnanzhu` | sami | 男 / 青年（磁性男声） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-male-iclvop-zhangjinxiangnanzhu/audio.mp3` |
| `ICL_zh_male_qinggandiantai` | sami | 男 / 中年（情感频道） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-icl-zh-male-qinggandiantai/audio.mp3` |
| `BV145_streaming` | sami | 女 / 青年（情感语录） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-bv145-streaming/audio.mp3` |
| `zh_male_rendongteng` | sami | 男 / 中年（纪录片解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-male-rendongteng/audio.mp3` |
| `zh_male_changtianyi_xuanyi` | sami | 男 / 中年（悬疑解说） | 否 | 否（忽略并记日志） | `~/CreatorStudio/materials/voice-probe/sami-zh-male-changtianyi-xuanyi/audio.mp3` |

## 结论

- edge 9 个音色里 **8 个可用**：`zh-CN-YunfengNeural`（云枫）在 edge 服务端返回
  `No audio was received`，重试 3 次仍失败 —— 目录里保留它但标成不可用，选音色时别选。
- SAMI 14 个音色全部可用，但没有逐字边界：`lipsync.build_words` 只能退回
  `source=syllable`（拿包络把字铺到有声段），它是估算，不是音色自带的字级时间；
  精度与 edge 的 `WordBoundary` 不是一个量级（验收线见 `docs/shorts/lipsync-paradigm.md`）。
- 逐字时间的来源只有 edge 的 `WordBoundary`；要逐字口型就必须选 edge，SAMI 用在
  「音色合适、口型可以粗一点」的地方（SAMI 侧口型轨道 `source=syllable`）。

## 选法

- 默认仍走 `DEFAULT_VOICE = sami:BV411_streaming`（历史成片基线，不动）。
- 想要逐字口型 + 可调语速/音高：`edge:zh-CN-XiaoxiaoNeural@rate=0.98`，
  `pitch` 写 `+2%`（按 200Hz 基准折成 +4Hz 再交给 edge，它只收整数 Hz）。
- SAMI 写成 `sami:BV411_streaming@rate=0.9` 不会报错，但参数被忽略并打一行 stderr 日志。

样音总表：`~/CreatorStudio/materials/voice-probe/catalog.json`；自然度矩阵见
`~/CreatorStudio/materials/voice-probe/matrix2.md`。
