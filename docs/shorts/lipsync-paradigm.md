# 口型范式：让嘴对上「说的是哪个字」

这条片子的口型问题不是「嘴不动」，是**嘴一直在动，但和张嘴的时机、和说的是哪个字都没关系**。
现在的做法是「音量包络 → 四档开合」（`integrations/mac-worker/export/vertical_short.py:505`
`_energy_states`、`:662` `_audio_beats`，0.1 秒一格），它只回答「这一格声音大不大」，
不回答「这一格在发什么音」。结果有两种必然：

- **说错字**：中文里 `i` 是扁嘴、`u` 是圆嘴、`a` 是大张口、`m/b/p` 是闭唇重建。
  音量四档里这四种只有开合大小不同，嘴形全一样 —— 观众看到的是「一张嘴在扇动」，
  不是「在说这句话」。
- **对不上**：0.1 秒的格子比字长得多。中文一个字 180–260ms，音量谷和音素边界不是一回事，
  嘴比声音平均早半格（50ms）起，闭口又拖到下一个字。

本文件定义固定范式：**八档口型 + 逐字时间 + 协同发音 + 可测量的验收线**。
范式只约束「这一格画哪个嘴」，不约束画法（`integrations/mac-worker/mouth_art.py` 是其中一种实现）。

---

## 1. 判据：先有尺子，再谈像不像

一切结论都必须由 `integrations/mac-worker/tools/lipsync_qa.py` 量出来，不靠「看着像」。
工具量的是**互相关峰值的延迟**（嘴 vs 声音），正 = 嘴早于声音，负 = 嘴晚于声音。

| 指标 | 门槛 | 含义 |
|---|---|---|
| 对齐延迟 `|lag|` | **≤ 60ms 可用，≤ 30ms 好** | 超过 60ms 人眼读作「配音对不上」 |
| 静音误开率 | **≤ 5%** | 音频 < -45dBFS 时嘴不该张（张着嘴喘气最出戏） |
| 每 100ms 换档次数 | **≤ 1.25**（派生根） | 均值超过它说明驻留已是 50–80ms 级，读作「嘴在抖」 |
| 最短驻留 | **≥ 80ms**（派生根，理想 90–100ms） | 短于它观众读到的不是「一个字」，是「抽了一下」 |
| 全长漂移 | **≤ 60ms**（派生根，与延迟同一把尺） | 恒定偏移可以整体推一帧修；逐段增长的漂移只能返工 |

标注为「派生根」的三项是由「嘴对得上语音」推出来的可执行门槛，不是用户原话；
前两项（延迟、静音误开）是验收口径本身，**不许放宽**。工具退出码：
`0` 过、`1` 有项不过、`2` 输入有问题；`--no-strict` 恒返回 0（只用在出报告场景）。

---

## 2. 八档口型（正字面）

档名是出片、QA、素材命名的共同词汇。canonical 顺序见
`integrations/mac-worker/export/lipsync.py:37` `STATE_ORDER`；画法在
`integrations/mac-worker/mouth_art.py:18` `STATES`、`:264` `_draw_mouth`（旧档 `closed/half/round/open` 必须继续可用，
paper 链路和旧 spec 依赖它们）。

| 档 | 开口度 | 对应音（韵母/声母） | 画面读法 |
|---|---|---|---|
| `closed` | 0.00 | `m b p`、静音 | 唇线闭合，不露齿 |
| `teeth` | 0.80 | `s z c zh ch sh r j q x` | 微开露齿，齿宽 ≤ 0.6 倍嘴宽 |
| `narrow` | 0.40 | `i`、`ie in ing` | 横向扁、纵向窄 |
| `mid` | 0.52 | `e ei en eng` | 中性半开 |
| `round` | 0.58 | `o uo`、`ao ou` | 圆，但不收唇 |
| `pucker` | 0.62 | `u ü` | 收唇前突，比 `round` 更小更紧 |
| `wide` | 1.00 | `a ai an ang` | 上下张到最大 |
| `half` | 0.35 | 过渡档 | 只做收口过渡，不作稳态 |

`OPENNESS` 数值（工具里的排序值）只用来算相关和排序，**不参与出片**。

### 2.1 一个字要拆成两段

「一个字一个嘴」也对不上，因为中文音节是「声母段 + 韵母段」，前半和后半嘴形不同：

- 声母段（约前 40%）：`m/b/p` → `closed`；齿音类 → `teeth`；其余辅音 → 该字的韵母形但更小一号。
- 韵母段（其余）：按上表定形。

实现参考 `lipsync.py:193` `_char_mouths(ch)`（返回 `(lead, final)`）与
`:441` `_frames`（按字符时间把 `lead` 铺到前半、`final` 铺到后半，分界在中点）。
`_text_mouth(ch)`（`:183`）是无 pypinyin 时的降级：只按字面猜一次，不拆两段。

没装 `pypinyin` 也能跑，但那是降级路径：`lipsync.py` 里有韵母类降级表，不保证 `i/u/a` 分得开。
**要求多余音素区分度的片子，必须装 `pypinyin`。**

---

## 3. 时间来源：优先级写死，不许各段自己猜

口型长度必须等于这句配音的真实长度，且每一格对应真实时间。三条来源按可复现性排序
（`lipsync.py:431` `_timeline` 决定，`build_words` 的 meta 里 `source` 字段会写出来）：

1. **`*.words.json` sidecar（`source="words"`）** —— edge-tts 的 WordBoundary 或外部工具给的逐字表，
   同目录同名（`lipsync.py:210` `_sidecar`、`:214` `load_words`）。可复现、零重算，**首选**。
   合成时直接落盘：`lipsync.py:642` `words_json(text, audio, out)`。
2. **音频包络音节切分（`source="syllable"`）** —— `lipsync.py:329` `_syllable_timeline`：
   解码 16k 单声道 → 10ms 能量 → 找谷 → 有声段（`:289` `_spans`）→ 按字数分配 →
   `:431` 再对一次 `_first_voiced`（`:379`）。没有 sidecar 时的默认路径。
3. **按字数均分（`source="uniform"`）** —— `lipsync.py:371` `_uniform_timeline`。只在上面两条都失败时用；
   `coverage`（meta 里有）会明显偏低，这种片子只能算「能看」，不能算过验收。

覆盖率是硬信号：`build_words` 返回 `coverage = timed_seconds / duration`。
低于 0.9 说明大部分字没有真实时间，延迟指标再好也不能当通过 —— 那只是「整体偏了一点」，
逐字对不上。

---

### 3.1 音色通道与逐字侧车（sidecar）

侧车的生产者是配音模块，不是口型模块。三件事必须同时成立，逐字口型才有输入。

**① 音色写法与语速。** `spec.voice` 两种写法等价：`edge:zh-CN-XiaoxiaoNeural` 与裸
`zh-CN-*-Neural`（`slides_render.py:300` `_edge_voice` 认这两种前缀，正则见 `_EDGE_VOICE_RE`）。
`spec.rate` 只在 edge 分支生效：0.92 → `-8%`，夹在 ±30%（`slides_render.py:306` `_edge_rate`）；
SAMI 分支语速固定 1.0，写 `rate` 不生效，别指望用 rate 卡时长。
合成不在主进程做（系统 python3 没有 `edge_tts`）：走
`integrations/mac-worker/tools/edge_tts_once.py`（`slides_render.py:297` `EDGE_TTS_ONCE`），
由 MPT venv 的解释器跑子进程，主进程只 import `subprocess`。

**② 缓存键与命中条件。** 键 = `voice + rate + 原文`（`slides_render.py:287` `_tts_cache`，sha1 前 20 位）。
命中要求 **mp3 和 `<stem>.words.json` 都在**（`_edge_tts`，`:327`）：只剩 mp3 时**删缓存重合成**，
绝不静默退回包络猜口型。淘汰时两个文件一起删（`prune_tts_cache`，`:253`）。
同理：改了稿子一个字就重合成，没改就零网络复用。

**③ 多段配音的侧车必须重拼。** 一屏台词是逐句合成再 concat 的（`export/vertical_short.py:833` `_say`）：
每句各自的 WordBoundary 时间只在它自己的 mp3 里，拼成一条后必须重算 ——
`_retime(chars, tempo, offset)`（`:821`）= 「atempo 时长倒数缩放 × 段偏移累加」，
偏移是前面所有「句子时长 + 停顿」的累加（`_mp3_dur` `:784` + gap）。gag 拉慢走 atempo
（0.95/0.86，`_gag_tempo` `:757` / `_slow` `:770`），时间同样按倒数换算。
**某一段缺侧车就整条不写**（在写盘之前 `return`）—— 半截时间表比没有更糟，口型会一路跑偏。
拼好写到成片 mp3 的 sidecar（`_write_chars` `:813`），口型模块只认这个路径（`lipsync.py:210` `_sidecar`）。

**默认音色结论：`edge:zh-CN-XiaoxiaoNeural` + `rate=0.92`。**
唯一决定性理由是**只有它给 WordBoundary**（本轮实测覆盖 19/19 句，逐字时间可用）。
男声备选 `zh-CN-YunyangNeural`；要剪映播音腔用 `sami:BV411_streaming`，
但 SAMI 不给逐字边界 → 口型精度降级为包络推断（可看，不能当验收基线）。

侧车实际长相（`chars` 数组，`t` = 起点秒，`d` = 时长秒）：

```json
{"chars": [{"ch": "二", "t": 0.1043, "d": 0.1902},
           {"ch": "十", "t": 0.2945, "d": 0.1902}]}
```

## 4. 协同发音：早一点、久一点、开快闭慢

三条规则，都在 `lipsync.py:463` `_coarticulate` 里：

- **提前 20ms**（`:50` `LOOKAHEAD_MS`）：嘴比声音早一点点。再早会被读成「抢拍」，
  晚 40–60ms 就不像在说话 —— 这就是验收线 60ms 的来源。
- **开口方向立即生效，收口方向才驻留**：这张嘴是观众读「开口」的，延迟张嘴等于丢字；
  收口拖 1–2 格反而像真人在收。
- **最短驻留 90ms**（`:52` `MIN_DWELL_MS`）：短于它的段并进前一段（`:488` `_settle`）。
  50fps 下 4–5 格。工具侧对应的下限是 80ms（`MIN_DWELL_MS=80.0`），留 10ms 容差。

静音一律 `closed`（`:518` `_silence_mask`，阈值按这句自己的能量分布取，下限 0.02），
**不允许为了「嘴别停」在句间补开合** —— 那是静音误开率的主要来源。

---

## 5. 出片接线：现在谁在调用谁

```
vertical_short.py:1543  seg["lip_audio"] / :1547 seg["lip_text"] / "lip_t0"
      ↓
export/char_layer.py:263 _lip_states(seg, mp3)
      ├─ 有 lip_text → :231 _mouth_states_text → lipsync.build(text, mp3, dur, fps=50)
      │                                   → :122 _settle(min_run=4) → :160 _bridge
      └─ 没 lip_text → :176 _mouth_states → :49 _envelope → 音量四档（旧路径）
      ↓
export/char_layer.py:275 _lip_mouth(seg, t)  →  states[int((t - lip_t0) * 50)]
      ↓
mouth_art.py:401 apply(img, state)  →  画不出来就原样返回（宁可不动也不画歪）
```

### 5.1 曾经的致命缺陷：`_bridge` 插帧但不重映射时间（已修，别再改回去）

**曾经的形状**：`_bridge` 在直接开↔闭两端之间插 2 格中间档，轨变长，但 `_lip_mouth` 仍按
`i = int((t - t0) * 50)` 索引 → 口型轨被当 1.05x 慢放，漂移随时间累积到秒级。
实测那条片子 1677 格 vs 音频 1609 格，全长漂 +1356ms、r=+0.05、静音误开 59.6%。

**现在的样子（本范式要求保持）**：

- 文本路径（`lipsync.build`）产出的轨**长度只许等于 `round(dur*50)`**：
  `char_layer.py:266 _mouth_states_text` 末尾用 `:249 _fit_track(states, n)` 钉死（多截少补），
  不再 `_settle` / `_bridge` —— `lipsync` 内部已经做过 20ms 预判 + 90ms 最小驻留，外层重复加工只会破坏时间映射。
- `_settle` / `_bridge` **只留给老包络路径**（没有 `lip_text` 时）。
- `_lip_mouth` 索引前先自查：`len(states)` 与 `dur*50` 差 > 5% 就改用
  `rate = len(states) / dur` 索引（插件/上游又插帧时不再整体慢放）。
- `import lipsync` 包内相对导入优先（`from . import lipsync`），失败才退裸 `import`
  —— 否则工具脚本直跑时可能静默找不到模块、白退回包络四档。

改这层任何代码后，必须重跑 §6 的第 1 步：`drift` 必须 ≤ 60ms（新基线 −2ms），否则就是又插帧了。

## 6. 验收怎么跑

工具在仓库根目录下执行（`--states` 模式不读成片，最快，出片前必跑）：

```bash
cd integrations/mac-worker

# A. 口型轨模式：有 lip.json 就能量，秒级
/usr/bin/python3 tools/lipsync_qa.py \
    --audio /tmp/f32.mp3 --states /tmp/f32-lip.json

# B. 成片模式：从画面里自己找嘴，再和音轨对齐（约 40–90s，1080x1920 @30fps）
/usr/bin/python3 tools/lipsync_qa.py \
    --video ~/CreatorStudio/deliver/envoy-f32.mp4 \
    --spec ../../deploy/specs/envoy_real.json

# C. 流水线读 JSON（--no-strict 恒 exit 0）
/usr/bin/python3 tools/lipsync_qa.py --audio a.mp3 --states lip.json --json --no-strict

# 退出码：0 过 / 1 有项不过 / 2 输入问题
echo $?
```

口型轨 JSON 契约（`--states` 只认这一种格式）：

```json
{"fps": 50, "states": ["closed", "closed", "mid", "wide", ...], "t0": 0.0}
```

- `fps` 缺省 50；`t0` 缺省 0（这段配音在成片时间轴上的起点，有偏移时必须写）。
- `states` 长度 × 1/fps 必须 ≈ 音频长度；超出部分按漂移报错。
- 档名只认第 2 节那 8 个 + 旧档 `half`，出现未定义档名直接 exit 2（不静默降级）。
- 导出方式：`sys.path.insert(0, "integrations/mac-worker/export")` 后调 `lipsync.build(text, audio, dur)`，
  或直接读 `_lip_states()` 的结果写 JSON。

输出长这样（成片模式，数字见第 7 节）：

```
口型体检 · /Users/.../envoy-f32.mp4
  片子 1080x1920 @30.0fps 32.2s · 人物框 x280-1040 y900-1700
  嘴部 36x36px @(731,1240) · 代理 red（定位 r=+0.46）
  两半复核 前 +0.39/+130ms · 后 +0.55/+190ms
  ...
  延迟 +175ms（嘴早于声音）· 峰值相关 r=+0.47（0 延迟时 r=+0.31）
  判定：不过 —— 对齐延迟 +175ms（要求 |≤60|）；静音误开率 13.6%（要求 ≤5%）
```

**成片模式的嘴部是「暗红像素块面积」代理，不是唇读。** 它先在人物框
（`video_qa.py:36` `CHAR_BOX` = 280,900,1040,1700）里按比例取脸窗，
再在脸窗内按 18×18 网格搜「和音量同步变化」的块（`lipsync_qa.py` `_face_window` / `_best_patch`），
最终指标在定位到的块上重算，并报两半（前/后 50%）的 `r` 与延迟：
**两半延迟差 > 60ms 就说明定位到的不是嘴**（或嘴在漂移），别信总延迟。
`proxy` 有 `red`（暗红，张嘴露出的口腔）和 `dark`（很暗的像素）两路，取相关性强的那个。

### 6.1 复现新基线（照抄可跑）

```bash
cd integrations/mac-worker

# 1) 从成片抽音轨（QA 只认单声道 wav/mp3，采样率不限）
ffmpeg -v error -y -i ~/CreatorStudio/deliver/envoy-f32.mp4 -vn -ac 1 -ar 16000 /tmp/qa.wav

# 2) 导出这一屏的口型轨（有 words.json 就走逐字路径；没有就退音节切分）
/usr/bin/python3 - <<'EOF'
import json, sys
sys.path.insert(0, ".")
from export import char_layer as CL
d = json.load(open("/tmp/saytest/a00.words.json"))
text = "".join(c["ch"] for c in d["chars"])
seg = {"lip_audio": "/tmp/saytest/a00.mp3", "lip_text": text, "lip_dur": 0.0, "lip_t0": 0.0}
st = CL._lip_states(seg, seg["lip_audio"])
json.dump({"fps": 50, "states": st}, open("/tmp/qa.lip.json", "w"))
EOF

# 3) 量（--json 出机读；--no-strict 恒 exit 0）
/usr/bin/python3 tools/lipsync_qa.py --audio /tmp/saytest/a00.mp3 --states /tmp/qa.lip.json
echo "exit=$?"
```

`--video --spec` 模式下口径不变、尺子一致：从人物框里按比例取脸窗，在脸窗内搜「随音量同步变化」
的像素块作为嘴部代理（`red` / `dark` 两路取强者），再对它和音轨做互相关；
**延迟 30ms 内算好档、60ms 内算通过**，并强制报前/后两半的延迟（差 > 60ms 说明定位错了）。
它量的是时间，不是嘴形 —— 嘴形要靠 `--states` + 人工看 3 帧。

---

## 7. 当前基线（实测，2026-10-27）

### 7.1 修复前的成片 `~/CreatorStudio/deliver/envoy-f32.mp4`（留作对照，别当基线）

旧管线（0.1 秒分段 + 音量四档，`vertical_short.py:505`/`:662`）产出的片子：

| 指标 | 实测 | 门槛 | 结论 |
|---|---|---|---|
| 对齐延迟 | **+175ms**（嘴早于声音） | ≤ 60ms | 不过，超 3 倍 |
| 峰值相关 `r` | +0.47（0 延迟时 +0.31） | 派生 ≥ 0.30 | 弱相关 |
| 静音误开率 | **13.6%** | ≤ 5% | 不过 |
| 每 100ms 换档 | 0.14（p95 1.0） | ≤ 1.25 | 过 |
| 分段延迟 | +75ms ～ +250ms，极差 175ms | 漂移 ≤ 60ms | 不稳 |
| 嘴部定位 | 36×36px @(731,1240)，脸窗内 r=+0.46 | — | 可信（两半 +130 / +190ms 同向） |

读法：**嘴整体早 175ms、且句间不闭嘴**。早的原因是 0.1 秒格子量化 + 开口只看音量上升沿；
静音误开的原因是没有闭口约束。这两条都不是「调参数」能修的，是词汇里没有 `closed` 以外的信息。

### 7.2 新管线口型轨（漂移修好后，`/tmp/saytest/a00.mp3`，24 字真实配音）

`edge` 合成、带 `a00.words.json` 侧车，`char_layer._lip_states` 现版导出：

| 指标 | 实测 | 门槛 | 结论 |
|---|---|---|---|
| 帧数 | 464 格 = `round(9.282*50)`，与音频 **delta 0** | 必须等长 | 过 |
| 全长漂移 | **−2ms** | ≤ 60ms | 过 |
| 对齐延迟 | **+10ms**（嘴早于声音） | 30ms 好档 / 60ms 通过 | 好档 |
| 峰值相关 | **r=+0.83**（0 延迟同值 +0.83） | 派生 ≥ 0.30 | 强 |
| 分段延迟（每 2s） | +20 / −10 / +0 / +5，极差 30ms，斜率 −2ms/s | 极差 ≤ 60ms | 过 |
| 静音误开率 | **2.0%** | ≤ 5% | 过 |
| 最短驻留 | 100ms（中位 180ms，p05 100ms，33 段） | ≥ 80ms | 过 |
| 每 100ms 换档 | 均值 0.34、p95 1.0、最大 1 | ≤ 1.25 | 过 |

**判定：通过（exit 0）。这是当前基线**，替换掉修复前那组
「1677 格 / 漂移 +1356ms / r=+0.05 / 静音误开 59.6%」。

对照意义：同一套 8 档词汇 + 逐字时间，修掉时间轴缺陷后 r 从 +0.05 到 +0.83、
延迟从 +250ms 到 +10ms。说明**「对不上」的主因从来是时间轴，不是嘴形画得不够多**。

### 7.3 已量到、尚未修的事

1. **`teeth` / `wide` 的外观**：`mouth_grid` 里 `teeth` 画成「两点 + 竖线」、`wide` 留了一条唇高光带。
   **已指派给 char-rig**，本文件不追踪实现，只保留判据（见 §2 表 + §8 末条）。
2. **素材重跑**：`mouth_art.STATES` 已扩到 9 名、`_draw_mouth`（`:264`）已分支，
   但旧素材走 `_locate`（`:94`）找嘴、找不到就原样返回 —— 档位扩容后必须重跑一遍素材，
   确认每档都真出图，否则某些字会静默保持上一帧形状（看着像卡住）。
3. **静音判定**：`_mouth_states` 的 `_LIP_CLOSED=0.20` 是「三成时间闭嘴」，和
   「静音误开率 ≤ 5%」不是同一件事 —— 静音段必须由 `lipsync.py:518` `_silence_mask` 显式判，
   不能靠音量阈值顺带。新基线 2.0% 是逐字路径给的结果，老包络路径没这个保证。

## 8. 哪些做法是错的（踩过的坑，别再回来）

- **固定周期开合**（正弦 / 「每 0.4 秒张一次」）：和语音完全无关，是最典型的「嘴在扇风」。
- **只开合、不换形**（四档只按音量分大小）：`i/u/a/m` 全画成同一个椭圆 —— 这就是
  「说错字」。观众说不出哪不对，但会说「不像在说话」。
- **用音量代替音素**：音量回答「多大」，口型回答「哪个音」。两者正交，不能互推。
- **嘴晚 60ms 以上**：这是「配音对不上」的硬线；「早」比「晚」好，所以预取 20ms 是允许的、
  滞后补 40ms 不行。
- **在 24px 宽的嘴上画高光**：嘴部在成片里只有 30–40px 宽，任何高光、牙齿细节都会变成噪点。
  嘴形靠**轮廓宽度/高度比**和开口度表达，不靠内部细节。
- **句间补开合「让嘴别停」**：静音误开是第一眼出戏的原因，句间必须 `closed`。
- **逐字时间用「按字数均分」还当通过**：`coverage < 0.9` 的片子只能算能看，
  延迟指标会被均分掩盖（整体对齐、逐字全错）。
- **补过渡帧改变轨长**：见 5.1。过渡要占**已有格子的时间**，不能凭空延长轨——修过一次的坑，回归即返工。
- **拿成片代理指标当唇读结论**：成片模式量的是像素面积变化，能判「时间对不对」，
  不能判「嘴形对不对」。嘴形要用 `--states` 模式量 + 人工看 3 帧。

---

## 9. 落地顺序（出片前检查单）

1. 生成口型轨 → 导出 JSON → `lipsync_qa.py --audio ... --states ...`，`|lag| ≤ 60ms`、静音误开 ≤ 5%。
2. 确认 5.1 的时间轴约束没被破坏（口型轨格数 = `round(dur*50)`、`drift ≤ 60ms`）；回归就返工，别调参糊过去。
3. 每条新片跑一次成片模式，确认两半延迟同向、极差 ≤ 60ms。
4. 随机抽 3 帧人工核嘴形（`i` 扁 / `u` 圆 / `a` 大张 / `m` 闭）—— 工具量不出这个。
5. 全部通过才进成片队列；`exit 1` 的片子不进队列，不靠人工「看着还行」放行。
