# 剪映版本适配（11.x 起，不写死 11.6）

平台生成的草稿要能被「用户这台机器上装的那版剪映」打开。版本适配层的原则是
**只采纳有实证的 schema，没有实证就沿用已验证的模板**——猜一个新版本号比留旧版本号
风险大得多。

实现：`integrations/mac-worker/jianying_versions.py`
接入点：`jianying_engine.py::_normalize_draft`（部署后、写 `draft_info.json` 前）
自检：`python3 tools/check_jianying.py` 第 5 项；`python3 jianying_versions.py` 直接看结论

## 1. 草稿里跟版本有关的字段

`draft_content.json`（同时镜像到 `draft_info.json` 与 `Timelines/<id>/…`）里：

| 字段 | 例 | 作用 |
| --- | --- | --- |
| `version` | `360000` | 整数 schema 版本 |
| `new_version` | `"110.0.0"` | 字符串 schema 版本 |
| `platform.app_id/app_source` | `3704 / "lv"` | 剪映（不是 CapCut） |
| `platform.app_version` | `"10.2.0"` | 写这份草稿的 App 版本 |
| `platform.os` | `"windows"` | 写这份草稿的系统 |
| `last_modified_platform.*` | 同上 | 最后一次修改来源 |

VectCutAPI 的模板照 **10.2.0 / windows** 写；本机 **11.6.0 (490)** macOS
实测能正常打开、能导出——说明剪映对旧版本字段向后兼容，而新版本没有承诺。

## 2. 本机实测结果（2026-10）

```
$ python3 jianying_versions.py
剪映: 11.6.0 (build 490)  ·  /Applications/VideoFusion-macOS.app
草稿 schema: version=None new_version=None source=keep-template
参考草稿: 11 份，写入过版本: ['10.2.0', '5.9.0']
支持状态: template — 沿用 VectCutAPI 模板的 10.2/windows 版本块（11.6 实测可开）
```

也就是说：当前所有可读草稿的 `platform.app_version` 都是 `10.2.0` / `5.9.0`，
没有一份是 11.6 自己写的，所以**不许盖 11.6 的章**，继续用模板字段。

## 3. 决策顺序（`resolve_schema`）

1. **同主版本参考草稿**：草稿根目录里存在 `platform.app_version` 主版本 == 本机 App
   主版本的草稿 → 抄它的 `version` / `new_version` / `platform`，`apply=True`，
   `source=reference-same-major`。只把 `app_version` / `app_build` 换成本机版本。
2. **没有实证** → `apply=False`，`source=keep-template`：草稿版本块原样保留。
3. 环境变量 `JY_SCHEMA_DRAFT=<草稿目录名>` 可以强制指定一份参考草稿（拿它做
   新版本实验，不用等它自己保存）。

`apply=True` 时部署目录会多写一份 `draft_version.json`：

```json
{"app": {"version": "12.1.0", "build": "900", ...},
 "schema": {"version": 360000, "new_version": "121.0.0",
            "source": "reference-same-major", "referenceDraft": "new"},
 "stamp": {"action": "applied", "before": {...}, "after": {...}}}
```

导出端的 `studio_export_meta.json` 会把 `schema` 一起带过去，
所以「这份片子是用哪版 schema 生成的」可以事后追溯。

## 4. 装上新版本剪映之后

不需要改代码，按这个顺序走一遍：

1. 装新版，开剪映，随便新建或打开一个草稿并 **保存一次**（让 App 自己写一份
   新 schema 的 `draft_content.json`）。
2. `python3 integrations/mac-worker/jianying_versions.py` 确认
   `source=reference-same-major`、`support.status=adopted`。
3. 平台跑一条草稿任务，看部署目录里 `draft_version.json` 的
   `schema.source` 是 `reference-same-major`，`stamp.action=applied`。
4. 在剪映里打开这条草稿 → 时间线非空、能导出，即为该版本验证通过。

如果新版**打不开**我们的草稿（列表里出现但时间线空 / 直接报错），先别改代码：

- 检查 `draft_info.json` 是不是被新版换了文件名或加了密（读首字节是否为 `{`）；
  新版若只读 `draft_info.json` 且加密，我们写入的明文会被拒写，`_write_plain_json`
  会返回 `False`，需要按该版本的实际行为补适配。
- 用 `JY_SCHEMA_DRAFT=<新版保存的草稿目录名>` 强制采纳它的 schema 再试一次。
- 仍失败就把该版本 App 的 `Info.plist` 版本号、新建草稿的 `draft_content.json`
  前 2 KB 留档进 `docs/platform/evidence/`，再动手改。

## 5. 已知边界

- 适配层只对齐 `version/new_version/platform*`，**不重排 timeline 结构**。
  若新版本的 `tracks/materials` 结构本身变了，那是另一个适配器（需要真实样本）。
- 参考草稿是本机剪映保存的，可能带用户工程的其它字段；我们只取版本块。
- 11.6 保存后会把 `draft_info.json` 加密，所以版本信息同时留在
  `draft_version.json` 与 `studio_export_meta.json` 里，不依赖解密。

## 6. 平台侧接口面

- 平台任务：`jianying_open`（只打开、不导出）与 `jianying_export`（收成片/认领）分开记账；打开成功不改 run 状态。
- REST：`POST /production/runs/:id/open-draft`，body `{"draftPath?": "...", "waitIdle?": 0}`；省略 draftPath 就用 run 记录里的草稿，
  没有就拒绝——不猜「最新草稿」。
- MCP：`open_jianying_draft`（`runId` + 可选 `draftPath`）。
- worker：`worker.py` 的 `do_jianying_open` 调 `export/open_draft.py --name <草稿目录> --wait-idle`；屏幕锁定判 `blocked/screen_locked`，
  剪映正被别人占用判 `export_user_busy`，都不会硬点 GUI。
