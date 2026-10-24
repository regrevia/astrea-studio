# 剪映专业版 11.6.0（macOS）内置 AI Agent 与全链路能力盘点

> 只读静态探测报告。**未启动、未激活、未对 App 做任何 GUI 操作**，所有证据来自安装包磁盘文件与用户数据目录的只读读取。
> 环境：macOS，App Store 渠道包。

## 0. 探测对象

| 项 | 值 | 证据 |
|---|---|---|
| 安装路径 | `/Applications/VideoFusion-macOS.app` | `ls /Applications` |
| Bundle ID | `com.lemon.lvpro` | `Contents/Info.plist` `CFBundleIdentifier` |
| 版本 | `CFBundleShortVersionString=11.6.0`，`CFBundleVersion=490` | 同上 |
| 可执行文件 | `Contents/MacOS/VideoFusion-macOS` | 同上 `CFBundleExecutable` |
| 渠道 | `Channel Name = App Store` | `Contents/PackageConfig.plist` |
| 架构 | Qt 6.2.2 + QML + CEF 混合；可执行体为 universal (x86_64+arm64) | `otool -L Contents/MacOS/VideoFusion-macOS` |

> 注意：本机**不存在** `/Applications/剪映专业版.app`，真实路径是 `/Applications/VideoFusion-macOS.app`。

### 工具能力限制（影响证据形式，必须知悉）

本机 REA 6.7.0 的**原生二进制分析 provider 全部不可用**，因此本报告对 dylib 的证据采用**有界 `strings`/`grep`**，而非 REA 反编译。这是可复现的环境限制，不是放弃分析：

```bash
$HOME/.dsh/skills/rea-cli/scripts/rea.sh providers
# → ghidra: not_configured (GHIDRA_INSTALL_DIR unset)
#   且 architecture_unsupported: "Ghidra v1 cannot enforce the selected architecture
#   when importing a universal Mach-O; analyze a thinned Mach-O slice instead."
# → hopper / ida / kuna: not_configured
# 结果：rea search <dylib> ... → provider_unavailable / Analysis failed
```

REA 的 **JS 制品分析（`analyze-javascript-application`）可用**，已用于 chat-bot 前端包。

---

## 1. 结论摘要

### ① 剪映 11.6 确实有**内置对话式 AI Agent**，且是产品级功能 —— **能**

不是"AI 按钮集合"，而是一个有会话、技能、工具调用、checkpoint 回滚的完整 agent。

- 二进制：`Contents/Frameworks/libDeepAgentsService.dylib`（29 MB）——agent 核心服务
- C API 层：`Contents/Frameworks/libdeepagents_capi.dylib`（110 MB）
- 17 个专属云端点，均以 `/agent_edit_api/` 为前缀：

```
/agent_edit_api/answer_faq                 /agent_edit_api/query_skills
/agent_edit_api/chat/completions           /agent_edit_api/retrieve_deeplinks
/agent_edit_api/commerce_capability_check  /agent_edit_api/retrieve_tools
/agent_edit_api/common_task/{new,query,cancel}  /agent_edit_api/rough_cut_plan_generation
/agent_edit_api/copilot_sug                /agent_edit_api/run_draft_modify_intent
/agent_edit_api/get_commerce_capability    /agent_edit_api/run_model_classify
/agent_edit_api/link_extract/pc            /agent_edit_api/run_model_gate
/agent_edit_api/supplement_roi            /agent_edit_api/talking_head_script_generation
/agent_edit_api/model_config               /agent_edit_api/tool_config
/agent_edit_api/fornax/observability/traces/ingest
/agent_edit_api/query_ai_package_text_styles
```

- UI 文案（`Contents/Resources/po/zh-Hans.po`）：`AI剪辑助手`、`剪映 AI 助手`、`Agent`、`展开/隐藏 Agent`、`新建对话`、`创建技能`、`技能`、`技能广场`、`新增技能广场，去看看`、`免费对话轮数已用完`、`成为 SVIP 后即可继续对话`、`小助手努力剪辑中…`、`已切换至新对话分支`、`删除此轮对话`
- 运行时开关（用户数据 `MMKV/settings_json`，明文 JSON）：`"agent_editor_lv":{"enable":true}`、`"agent_checkpoint_recover":true`

**入口形态**（po 文案）：Agent 可"从素材库、轨道或 Agent 会话内点选素材引用"，可"上屏圈选画面或选中片段快速发起对话"，即**以当前草稿/选区为上下文的对话式剪辑**。

### ② LLM 推理在**云端**，本地无推理引擎 —— **不能离线用**

- 端点：`agent_edit_api/chat/completions`（OpenAI 兼容形态，经字节服务端代理）
- 实测到的模型标识（`MMKV/settings_json`）：
  - `"default_model":"doubao-1.5-pro"` ← 字节豆包
  - `"default_model":"Gemini-3.1-flash-lite"`
  - `"agent_alias_id":"minimax_h3_pc_agent"` / `"seedance_2.5_pc_agent"` / `"seed_audio_1.0"`
- 调用走向：`HostHttpTransportCreateThunk` / `HostHttpTransportDestroyThunk`、`deepagent_child_http_thunks`、`deepagent_child_session_bridg*` —— agent 的 HTTP **经宿主进程转发**（Mojo IPC），不是 agent 自己直连
- 隐私文案自证（`po/zh-Hans.po`）："您所上传的素材、输入的指令**可能会上传至云端处理及存储**"
- 超时配置：`"agent_llm_timeout_config":{"first_response_timeout_ms":3600000}` —— 首字节超时 1 小时，符合长任务云端推理
- 反向证据：全包内**无** `volces`/ARK 直连端点，**无**本地 LLM 权重；`Resources/models/` 里 68 个模型全是视觉/音频 CV 模型（人像分割、插帧、抠像、美颜），无 transformer LLM

> 结论：**必须联网 + 必须登录**。离线状态下 Agent 不可用（推定，未做断网实测 → 见"未解之谜"）。

### ③ Agent 的**工具清单与技能清单由云端下发**，静态无法枚举 —— **半能**

- `query_skills` / `retrieve_tools` / `tool_config` / `model_config` 均为**服务端接口**，客户端只是消费者
- 本地只落了**框架**，没有工具表：
  - `deepagents::mojom_bridge::child::HostToolCallAsyncThunk(Pv16da_string_view_sS6_S6_P22da_tool_call_context_sPFvS5_iS6_ES5_)`
  - `BuildHostCallbacks(ModelHostRemoteAdapter, ToolHostRemoteAdapter, EventHostRemoteAdapter, FornaxTraceHostRemoteAdapter, ExecutionGateHostRemoteAdapter)` 返回 `Result<vector<ToolSpec>>`
  - `tool_call_id` / `tool_calls` / `tool_call_emit_event` / `tool_call_cancel` / `tool_call_async` / `tool_call_fail_count`
- 架构语义：Agent 引擎（`Fornax`）在**子进程/子会话**里跑，**模型、工具、事件、trace、执行闸门全部由宿主以 adapter 注入** —— `ExecutionGateHostRemoteAdapter` 说明工具执行前有**宿主侧权限闸门**
- 服务端下发的技能确实存在，且带 key：`settings_json` 中可见 `"skill_key":1000000001 … 1000000025`、`"skill_icon"`、`"tips_config"`（含 `data_source_url:"agent_edit_api/query_ai_package_text_styles"`）

> **具体有哪些工具、每个工具能改草稿的哪个字段 —— 未验证**（需登录且抓到 `retrieve_tools` 响应）。

### ④ 技能支持 **md / zip 上传**与**自然语言创建** —— 能，但依赖账号与灰度

`po/zh-Hans.po` 直证：

```
msgid "pc_hub_createskill_tip_format"    msgstr "支持md、zip等文件格式"
msgid "pc_hub_createskill_tip_language"  msgstr "自然语言创建skill技能"
msgid "pc_hub_createskill_btn_upload"    msgstr "本地文件上传"
msgid "pc_hub_createskill_btn_create"    msgstr "创建技能"
msgid "pc_hub_createskill_btn_agent"     msgstr "对话创建"
```

即技能有**三种创建方式**：对话创建 / 创建技能 / 本地文件上传。

配套灰度开关（`libDeepAgentsService.dylib`）：

```
custom_agent_bifrost_enabled              custom_agent_local_skill_debug_enabled
custom_agent_debug_enabled               custom_agent_native_video_diagnostics_enabled
custom_agent_environment                 custom_agent_ppe_channel / _ppe_enabled
custom_agent_inspector_enabled           custom_agent_ttnet_v2_debug_enabled
custom_agent_uploadsdk_ttnet_transfer_enabled
```

> 这是**唯一一个"官方留出的扩展点"**：如果要做"按要求微调/剪辑"的自定义能力，正路是写 skill（md/zip）而不是逆向内部 RPC。但**是否需要内测权限、skill 的具体 schema 是什么 —— 未验证**。

### ⑤ **没有对外可调用的本地接口**（无端口 / 无 CDP / 无 CLI 工具） —— 不能

| 通道 | 结论 | 证据 |
|---|---|---|
| 本地 HTTP/RPC 端口 | **不存在** | `libPlatinumWebView.dylib`、`libDeepAgentsService.dylib` 内无 `127.0.0.1:`/`localhost:` 监听串 |
| CDP（9222/9333） | **应用未开启** | `remote-debugging-port` 只出现在 CEF 框架二进制自身的通用开关表；逐一 grep 全 Frameworks **无任何 dylib 设置该 switch** |
| 本地 H5 服务方式 | 进程内 scheme，**非端口** | `libPlatinumWebView.dylib`：`HttpsLocalUrlSchemeFileHandler`、`HttpsSchemeFileHandler`、host = `https://localurl` |
| Agent 通信 | 进程内 Mojo IPC | `deepagent_mojom_converters`、`deepagent_child_*` |
| CLI | 无 | `Contents/MacOS/` 下仅主可执行体 |
| URL scheme | **有**：`vega`、`videocut` | `Info.plist` `CFBundleURLTypes`；`retrieve_deeplinks` 端点佐证 |
| AX 可达 | 部分 | 导出面板已验证可驱动（`integrations/mac-worker/export/jianying_export.py` + `panel.py`）；**Agent 面板 AX 可操作性 —— 未验证** |

> 关键判断：`https://localurl/...` 这个 host **只在 App 自己的 CEF 内可解析**，外部进程无法访问 → 想脚本化 AI 页面，只能走 AX 或注入，不能用 HTTP。

### ⑥ 发布：只有**抖音/西瓜分享链路**，无通用"发布到多平台" —— 半能

- 发布页 H5：`Contents/Resources/export_publish/h5_export_publish/`（`main.html`、`publish-list.html` + `modern.config.json` + webpack chunk）
- 其 JS 内端点仅三条：
  ```
  /lv/v1/pc/export_page/share_douyin_sign
  /lv/v1/pc/export_page/share_douyin_vid
  /lv/v1/pc/export_page/share_xigua
  ```
- 域名：`https://open.douyin.com`、`https://creator.douyin.com`、`https://lv-biz-api.ulikecam.com`、`https://lv-pc-api-sinfonlinea.ulikecam.com`、`https://jimeng.jianying.com`
- 关键词计数：`aweme`×7、`open_platform`×4、`AUTHORIZE`×5、`publish_list`×5
- 另有分享/协作（不是发布）：`/lv/v1/coordination/share_create`、`/lv/v1/pc/share/token_gen`、`/commerce/v1/subscription/check_share_link`

> 无视频号/小红书/快手/B站/百家号证据（grep 该 H5 包，平台名零命中）。
> **注意区分**：`commonSetting.ini` 里的 `captionPublishEnabled=false` / `textPublishEnabled=false` / `retouchTextPublishEnabled=false` 是**模板/素材投稿到剪映素材库**的开关，**不是**视频发布。

### ⑦ 配音（TTS）：**云端协议**，本地只做音频前后处理 —— 不能离线，也不建议外部复用

- 参数字面（`Contents/Frameworks/libspeechsdk.dylib`）严格对应火山引擎语音协议：
  ```
  appid  token  reqid  cluster  tts_cluster  tts_backend_cluster
  tts_address  tts_uri  tts_voice_online  tts_voice_type_online
  tts_voice_offline  tts_voice_type_offline  tts_language_online
  tts_text  tts_text_type  tts_speed  tts_volume  tts_pitch
  tts_rate  tts_encoding  tts_compression_rate  tts_with_frontend
  ```
- 音色表：`Contents/Resources/tts/tone_infos.json`，speaker id 采用 `BV*_streaming` / `VOV*_bytesing3` 命名（火山 speaker id 约定），如 `BV007_streaming`(亲切女声)、`BV009_DPE_streaming`(知性女声)、`BV026_streaming`(粤语男声)、`VOV201_bytesing3_shuiguowuqu`
- 鉴权文件：`Contents/Resources/tts/tts_local_auth_code`（INI 形式，`[General]` + `token=<长 base64>`）—— **内容不在此复述；其用途未验证**
- 本地能力：`libsamicore.dylib`（SAMI 音频编解码/重采样/Opus）、`rnnoise/`（降噪）、`models/audiomodel` —— 做**处理**，不做**合成**
- 离线标记的真相：`tts_voice_offline` 仅是字段名；`libspeechsdk.dylib` 中 "offline" 的实际实现是 **ASR 离线 worker**（`asr_pact/offline/asr_off_worker_petrel.cc`），不是 TTS
- 无硬编码 TTS URL（`libsamicore`/`libspeechsdk`/`libcccreator`/`libvideoeditor`/`libAICreator` 逐一双向 grep `tts|voice|sami|speech|clone` + `/lv/…` 全空）→ 地址由服务端配置下发

> 立场：**不破解、不复用付费 API**。要配音，正路是让剪映自己念（AX 驱动 UI）或用自己的 TTS 在草稿层替换音轨。

---

## 2. 详细能力表

`能否脚本化` 三档：**能**（有稳定程序化路径）／**半能**（需 AX 或需登录人工前置）／**不能**（无可达接口）。

| 能力 | UI 入口（po 文案） | 证据 | 能否脚本化 | 建议 |
|---|---|---|---|---|
| **AI Agent 对话式剪辑** | `AI剪辑助手`、`展开/隐藏 Agent`、`新建对话` | `libDeepAgentsService.dylib` 的 `/agent_edit_api/*`；`settings_json` `agent_editor_lv.enable=true` | **不能**（无外部接口） | 仅 AX 驱动；或改走 skill 上传 |
| Agent 技能广场 / 创建技能 | `技能`、`技能广场`、`创建技能`、`本地文件上传` | `po/zh-Hans.po` `pc_hub_createskill_*`；`settings_json` `skill_key` | **半能**（需登录+灰度） | **优先路线**：写 md/zip skill |
| Agent checkpoint 回滚 | — | `settings_json` `"agent_checkpoint_recover":true` | 不能 | 人工兜底 |
| **图文成片 / 文字成片** | `图文成片`、`图文成片已全新升级为文字成片` | `Resources/article_video/text_to_video_template.json`(4.3 KB)、`aigc_text_template/`、`image_h5_smart_text{,_old}/`、`image_h5_smart_voiceover_script_v{2,3}/` | **半能** | H5 在 CEF 内 → AX |
| **智能字幕** | `智能字幕`（3 处） | `po`；端点 `/lv/edit/audio_subtitle/process_subtitle`、`/lv/edit/audio_aed_check`；本地 `models/audiomodel`、`rnnoise/`、`Resources/audiosami/`；`liblyra_cli_client` 有 `--only-request-subtitles` | **能**（草稿层直接写 subtitle 轨 + 本地 ASR） | 自建 ASR 写 draft_content.json 更可控 |
| **智能配音 / 音色** | `丰富音色自由选择`、`CapCut提供了数十种音色` | `Resources/tts/tone_infos.json`、`libspeechsdk.dylib` `tts_*` | **半能** | 云端协议，别复用；用自有 TTS 替换音轨 |
| 音色克隆 / 声音复刻 | `5秒克隆专属音色`、`克隆音色`、`仅需10秒音频` | `po`；`faceu/via_voice_clone_pc.html`（CDN） | **不能**（需付费+真人授权视频） | 不碰 |
| **数字人** | `数字人`、`定制数字人`、`上传视频定制数字人形象` | 端点 `/lv/edit/digital_human/{create_item,delete,reactivate,recustom}`；`po`（渲染/SVIP 积分/防滥用声明）；`Resources/digital_human/` 仅有 `zh_aigc_watermark.png` | **不能**（云端渲染+人脸授权） | 云端算力，无本地引擎 |
| **AI 补帧** | `AI补帧`、`AI补帧已完成`、`AI补帧排队中` | `po`；`Resources/models/interpolation`（本地模型） | **半能** | 本地模型在，但无 CLI 入口 |
| **智能抠像** | `智能抠像`（2 处） | `po`；`Resources/matting_config/`、`models/humanparsingmodel`、`models/hairparser`、`mattingBlend*`；`liblyra_cli_client` `--ai-smart-matting` | **半能** | 草稿层 `auto_cutout` 字段更省事 |
| **智能包装** | `智能包装`、`智能包装…{percentage}` | `po`；`agent_edit_api/query_ai_package_text_styles`；`po` "主轨为空，暂不支持使用智能包装" | 不能 | 依赖 agent |
| AI 扩图 / AI 生图 | `剪映 AI 助手`内 | `settings_json` `create_ai_image_model_id:"high_aes_general_v40s"`；同处 `agent_aigc_config` | 不能 | 直达 `jimeng.jianying.com` |
| AI 视频生成 | — | `create_ai_video_image_to_video_model_id:"seedance_1.0"`、`create_ai_video_multi_modal_model_id:"seedance_2.0_pc"`、`create_ai_video_transition_model_id:"dit_start_end_dreamontage"`；`dreamina_model_name` 含 Seedance 1.0→2.5 全系 | 不能 | 走即梦/火山官方 API |
| AI 脚本 / 口播稿 | `实时对话获得帮助` | `/agent_edit_api/talking_head_script_generation`、`/agent_edit_api/rough_cut_plan_generation` | 不能 | 自己调 LLM 更快 |
| **导出** | 文件→导出 | **已实现**：`integrations/mac-worker/export/jianying_export.py` + `panel.py`（AX） | **能** | 维持现状 |
| **发布到抖音/西瓜** | 导出后分享 | `/lv/v1/pc/export_page/share_douyin_sign`、`share_douyin_vid`、`share_xigua`；`open.douyin.com`、`creator.douyin.com` | **半能** | 需登录授权；建议用抖音开放平台自建 |
| 草稿协作分享 | — | `/lv/v1/coordination/share_create`、`/lv/v1/pc/share/token_gen` | 不能 | — |
| **草稿文件读写** | — | `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/*/draft_content.json`（已验证可生成） | **能** | **主力路线**，已有能力 |
| 剪映内部渲染引擎 CLI | — | `liblyra_cli_client.dylib` 含 **636 个唯一 `--flag`**（`--input`/`--output`/`--audio-output`/`--ai-smart-matting`/`--minidraft`/`--only-request-subtitles`/`--output-encode-mode`…） | **未验证** | 疑似内部 RPC client，非用户 CLI；**不要盲调** |

### Agent 内部架构（供参考）

```
主进程 (Qt/CEF)
  ├─ libdeepagents_capi.dylib  ── C API 边界
  ├─ libDeepAgentsService.dylib ── Fornax 引擎 + mojom_bridge
  │     └─ HostCallbacks: Model / Tool / Event / FornaxTrace / ExecutionGate  (5 个 HostRemoteAdapter)
  │           └─ HostHttpTransport  →  经宿主转发 HTTPS  →  ♥ lv-api*.ulikecam.com/agent_edit_api/*
  └─ 持久化：<draft>/deepagent/deepagent.db（加密）、<draft>/agent/<session_id>/
```

泄漏的构建路径佐证该模块位置：`VideoFusion-win/Capability/DeepAgentsShell/local_agent_tool_result_projector.cpp`。

### 内置 webview 承载的 AI 页面（用户重点关心）

本机随包分发的 AI 页面**全部是本地资源**（已确认磁盘存在），通过 CEF 的自定义 scheme 在**进程内**提供，**不是**线上 URL，也**不占端口**。

`https://localurl/` 已确认存在，且它不只是静态文件服务——`libPlatinumWebView.dylib` 里有请求级日志：

```
https://localurl/
[localurl] url:            [localurl] method:
[localurl] request headers begin / end
[localurl] post data begin
```

配合 `HttpsLocalUrlSchemeFileHandler`，说明它是一个**进程内请求拦截处理器**（能处理 method/headers/body），外部进程无法访问。

| 页面 | 本地路径（已确认存在） | 承载 host（**未验证**逐一对应） |
|---|---|---|
| AI 对话剪辑 bot | `Contents/Resources/image_h5_smart_edit_chat_bot/chat-bot.html` | `https://localurl/…` |
| 文字成片（新/旧） | `image_h5_smart_text/`、`image_h5_smart_text_old/` | 同上 |
| 智能字幕 | `image_h5_smart_lyrics/` | 同上 |
| 智能配音文案 v1/v2/v3 | `image_h5_smart_voiceover_script{,_v2,_v3}/` | 同上 |
| 导出/发布 | `export_publish/h5_export_publish/` | 同上 |
| 素材/贴纸/文字模板投稿 | `image_h5_material_publish/`、`image_h5_sticker_publish/`、`image_h5_text_template_publish/` | 同上 |

线上依赖域名（AI 相关，来自 `libDeepAgentsService.dylib`）：

```
https://lv-api.ulikecam.com                 生产 API
https://lv-pc-api.ulikecam.com              生产 PC API
https://lv-pc-api-sinfonlinea.ulikecam.com
https://lv-biz-api.ulikecam.com
https://www.capcut.cn  /  https://www.jianying.com
https://jimeng.jianying.com                 AI 生图/生视频
https://mlv-actx.editorjianying.com
https://xyq.jianying.com
https://open.douyin.com  /  https://creator.douyin.com
CDN: https://p1-faceu.byteimg.com, https://lf3-beecdn.bytetos.com,
     https://lf26-cdn-tos.draftstatic.com
```

### 登录要求

**AI 能力全部要求登录剪映/抖音账号，且多数有付费墙。**

- 登录端点：`/passport/web/auth/{login,login_authorize,authorize,bind}`、`/passport/auth/login/`、`/passport/token/beat/v2/`、`/passport/transfer/{get_token,token_auth}`
- 登录标识：`douyin_cn_login_pc`、`douyin_login_pc`、`UserService_IsLogin`、`UserService_GetLoginStatus`、`RESTORE_LOGIN_STATUS`
- 付费墙文案（`po`）：`成为 SVIP 后即可继续对话`、`免费对话轮数已用完`、`为保证数字人技术不被滥用…需开通SVIP后支付积分使用`、`使用克隆音色朗读文本，需按照1积分=%1字扣除积分`
- 计费/能力校验端点：`/agent_edit_api/get_commerce_capability`、`/commerce_capability_check`、`/commerce/v1/subscription/check_share_link`

---

## 3. 未解之谜（全部**未验证**，不做推测）

1. **`retrieve_tools` / `query_skills` 的实际响应内容未验证** —— 需要登录态会话抓包。因此"Agent 到底能做哪些剪辑动作"的完整清单**未验证**。
2. **MCP 的真实范围未验证** —— 仅有 `mcp_service_auto_start` / `mcpServiceAutoStart{Changed}` 三个串，**未找到**任何 MCP server 列表、配置或 `mcp.json`；**无法确认**剪映会连接外部 MCP server。
3. **`agent_alias_id` 的语义未验证** —— 见到 `minimax_h3_pc_agent`、`seedance_2.5_pc_agent`、`seed_audio_1.0`，但无法确定它是"对话 agent 背后的 LLM"还是"生成模型的别名"。
4. **skill 的 schema 未验证** —— 已知支持 `md`/`zip`，但 zip 内目录结构、frontmatter 字段、能调用哪些本地能力，**均未验证**。
5. **`deepagent.db` 不可读** —— 3 个草稿内均有 `deepagent/deepagent.db`（380 KB ~ 1 MB），文件头非 SQLite（`76 0a 4b 8a …`），`sqlite3` 报 `file is not a database`，`strings` 全为二进制噪声 → **加密存储，未验证内容**。
6. **Agent 面板的 AX 可达性未验证** —— 导出面板已可 AX 驱动，但 Agent 对话区（CEF 渲染）是否暴露 AX tree **未验证**；CEF 内容常对 AX 不透明，这是最大风险点。
7. **`vega://` / `videocut://` 的参数未验证** —— 只知道 scheme 注册了、存在 `retrieve_deeplinks` 端点；**能否用 deeplink 直接触发某个 AI 能力，未验证**。
8. **`tts_local_auth_code` 的用途未验证** —— 是本地代理鉴权、还是降级离线鉴权，无法确定。
9. **TTS 实际服务端地址未验证** —— 推断为火山引擎（参数命名 + speaker id 约定高度吻合），但**无硬编码 URL**，地址应来自服务端配置。
10. **`liblyra_cli_client.dylib` 的 636 个 flag 是否可从外部调用未验证** —— 无对应可执行体，推测为进程内 RPC client；**不建议尝试**。
11. **离线是否可用未验证** —— 从架构判断不能，但未做断网实测（需用户授权启动 App）。
12. **是否所有账号都有 Agent 入口未验证** —— `custom_agent_*` 一堆灰度开关，实际放量策略未知。

---

## 4. 复现命令

以下命令全部只读，可逐条重跑。`$A` = `/Applications/VideoFusion-macOS.app`，`$U` = `$HOME/Movies/JianyingPro/User Data`。
**注意**：本机 REA 原生 provider 不可用（见 §0），故 dylib 证据用有界 `strings`；每条均已限定输出行数，不会打爆终端。

### 4.1 基础信息

```bash
A=/Applications/VideoFusion-macOS.app
defaults read "$A/Contents/Info.plist" CFBundleIdentifier CFBundleShortVersionString CFBundleVersion CFBundleExecutable
/usr/libexec/PlistBuddy -c "Print :'Channel Name'" "$A/Contents/PackageConfig.plist"
/usr/libexec/PlistBuddy -c "Print :CFBundleURLTypes" "$A/Contents/Info.plist"
otool -L "$A/Contents/MacOS/VideoFusion-macOS" | head -20
```

### 4.2 Agent 端点（17+ 条）

```bash
F="$A/Contents/Frameworks/libDeepAgentsService.dylib"
strings -a "$F" | grep -aoE '/agent_edit_api/[a-z0-9_/]+' | sort -u
# 期望：answer_faq, chat/completions, retrieve_tools, query_skills, tool_config, model_config,
#       run_draft_modify_intent, rough_cut_plan_generation, talking_head_script_generation,
#       fornax/observability/traces/ingest, query_ai_package_text_styles, ... 
```

### 4.3 Agent 工具调用框架 / 宿主适配器

```bash
strings -a "$F" | grep -aoE '[A-Za-z_:]*tool_call_[a-z_]+' | sort -u | head -20
strings -a "$F" | grep -a 'HostRemoteAdapter' | sort -u | head -10
strings -a "$F" | grep -aoE 'deepagents::[A-Za-z_:]+' | sort -u | head -15
strings -a "$F" | grep -a 'mcp' | sort -u | head -10
```

### 4.4 LLM 在云端 + 模型清单（**最有力的一条**）

```bash
S="$HOME/Movies/JianyingPro/User Data/MMKV/settings_json"   # 明文 JSON，16 MB
strings -a "$S" | grep -aoE '"[a-z_]*model[a-z_]*":"[^"]{2,60}"' | sort -u | head -30
strings -a "$S" | grep -aoE '"agent_alias_id":"[^"]+"' | sort -u
strings -a "$S" | grep -ao '"agent_llm_timeout_config".\{0,80\}' | head -1
strings -a "$S" | grep -ao '"agent_editor_lv":{[^}]*}' | head -1
strings -a "$S" | grep -aoE '"skill_key":[0-9]+' | sort -u | head
```

### 4.5 对话式 AI 的 UI 存在性

```bash
P="$A/Contents/Resources/po/zh-Hans.po"    # 1.4 MB，55k 行
grep -a -E '^msgstr ' "$P" | grep -aE '智能体|Agent|AI剪辑|小助手|技能|对话' | sort -u | head -40
grep -a -E '^msgstr ' "$P" | grep -aE '图文成片|智能字幕|智能抠像|AI补帧|智能包装|数字人' | sort -u | head -40
grep -a -A1 'pc_hub_createskill_tip_' "$P"
```

### 4.6 技能扩展点（md/zip）

```bash
grep -a -A1 -E '^msgid "pc_hub_createskill_(tip_format|tip_language|btn_upload)"' "$P"
strings -a "$F" | grep -aoE 'custom_?[Aa]gent[A-Za-z_]*' | sort -u | head -25
```

### 4.7 TTS

```bash
strings -a "$A/Contents/Frameworks/libspeechsdk.dylib" | grep -a -i 'tts' | sort -u | head -30
strings -a "$A/Contents/Frameworks/libspeechsdk.dylib" | grep -aE '^(appid|token|reqid|cluster)$' | sort -u
python3 -c "import json;d=json.load(open('$A/Contents/Resources/tts/tone_infos.json'));print(len(d['tone_infos']));print([t['tone_speaker'] for t in d['tone_infos']][:12])"
# 离线 TTS 证伪：
strings -a "$A/Contents/Frameworks/libspeechsdk.dylib" | grep -a 'offline' | head -5
```

### 4.8 本地接口 / 端口 / CDP 证伪

```bash
G="$A/Contents/Frameworks"
# 逐 dylib 找 CDP switch 的实际使用者（期望：无输出）
for f in "$G"/*.dylib; do n=$(grep -a -c 'remote-debugging-port' "$f" 2>/dev/null); [ "${n:-0}" -gt 0 ] && echo "$(basename "$f"): $n"; done
# 本地 webview scheme（期望 https://localurl，无端口）
strings -a "$G/libPlatinumWebView.dylib" | grep -aoE 'Https[A-Za-z]*Scheme[A-Za-z]*Handler' | sort -u
strings -a "$G/libPlatinumWebView.dylib" | grep -aoE 'https://[a-z0-9.-]{3,40}' | sort -u | head
strings -a "$G/libDeepAgentsService.dylib" | grep -aoE '(127\.0\.0\.1|localhost):[0-9]{2,5}' | sort -u
```

### 4.9 发布

```bash
EP="$A/Contents/Resources/export_publish/h5_export_publish"
cat "$EP"/static/js/*.js | grep -aoE '"/[a-z0-9_/]*(publish|share|douyin|partner)[a-z0-9_/]*"' | sort -u | head -20
cat "$EP"/static/js/*.js | grep -aoiE '(抖音|西瓜|视频号|小红书|快手|bilibili|百家号)' | sort | uniq -c
```

### 4.10 运行时足迹（证明 Agent 真的跑过 / 有持久化）

```bash
U="$HOME/Movies/JianyingPro/User Data"
ls "$U" | grep -iE 'agent|deep'
find "$U/Projects/com.lveditor.draft" -maxdepth 2 \( -name agent -o -name deepagent \) 2>/dev/null
file "$U/Projects/com.lveditor.draft/envoy_promo_v2/deepagent/deepagent.db"
head -c 16 "$U/Projects/com.lveditor.draft/envoy_promo_v2/deepagent/deepagent.db" | xxd
```

### 4.11 REA（JS 制品分析，可用；原生 provider 不可用）

```bash
R=$HOME/.dsh/skills/rea-cli/scripts/rea.sh
$R --version                     # 6.7.0
$R providers                     # 确认 ghidra/hopper/ida/kuna 均 not_configured
# 可用路径（本次已跑，产物已移出仓库：/tmp/jianying-rea/chatbot-rea.json 124 MB）
$R analyze-javascript-application --input "$A/Contents/Resources/image_h5_smart_edit_chat_bot" \
   --format json --filter-output > /tmp/jianying-rea/chatbot-rea.json
```

> 该 113 MB 原始产物**已移出仓库**至 `/tmp/jianying-rea/`，仓库内只保留本 markdown。
> 已知 REA 限制：`--token-limit` **不能**与 `--format json` 同用（`UNSUPPORTED_OUTPUT_COMBINATION`）。

### 4.12 严禁操作（安全边界）

- **不要** `open -a VideoFusion-macOS` / `osascript` 激活 App（会改变状态、可能触发上传）
- **不要**尝试解密 `deepagent.db` 或复用 `tts_local_auth_code`
- **不要**盲调 `liblyra_cli_client` 的 flag
- 本报告所有结论均为**静态**证据；任何需要运行时的结论都已标注"未验证"

---

## 5. 给 creator-studio 的建议（按可行性排序）

| 优先级 | 路线 | 理由 |
|---|---|---|
| **P0** | **草稿文件生成 + AX 导出**（现有能力） | 唯一已验证端到端可行；可控、可测试、无限额 |
| **P1** | 自建 ASR/TTS/LLM，结果写进 `draft_content.json` 的音轨/字幕轨 | 绕开云端额度与登录；音色与文案完全自主 |
| P2 | 写 **agent skill（md/zip）** 上传到技能广场 | 官方扩展点，但需登录+灰度，schema 未验证 |
| P3 | 用 `agent_edit_api/*` —— **不建议** | 非公开接口，参数/鉴权/trace 均耦合内部，且违反"不逆向付费 API"边界 |
| P4 | deeplink（`vega://`/`videocut://`）触发流程 —— **暂不建议** | 参数未验证 |
| ❌ | 复刻 Agent（云端 LLM + 云端工具 + 付费墙） | 成本与合规双输，且剪映侧随时可变 |

**最诚实的一句话**：剪映的"全功能 agent"是一个**闭源、云端、需登录付费**的黑盒。它能被**人**用得很好，但**不能被外部程序按需调用**。creator-studio 想做到"按要求微调、剪辑、配音、发布全链路自动化"，可行的是**自建链路 + 只把剪映当渲染器和导出器**，而不是把剪映当 agent 平台。
