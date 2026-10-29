# 剪映版本适配的实证等级

目标：写出来的草稿能被**这台机器上这版剪映**打开，且「凭什么这么写」有据可查——不猜版本号。
三层证据，强度从高到低：

| 等级 | 判据 | 状态值 | 能不能靠它自动适配新版本 |
| --- | --- | --- | --- |
| A 行为实证 | 这版剪映**打开并回写**过我们的引擎草稿 | `accepted` | 能：升级后跑一次打开就算数 |
| B 参考草稿 | 草稿目录里有**同主版本**可读草稿的版本块 | `adopted` | 能：剪映自己存过就自动抄 |
| C 内置模板 | 只有内置表（`version 360000 / new_version 110.0.0 / platform 10.2.0 windows`） | `template` | 不能，标注为推断 |

## 1. 为什么 11.x 上 B 级证据拿不到

剪映 11.6（build 490）打开引擎写的明文草稿后，会把整份草稿**回写成自己的原生加密格式**：

- `draft_info.json`：233KB 的 JSON → 113KB 的 base64 二进制，版本字段一起被加密；
- `draft_meta_info.json`：JSON → 二进制块（`i\0\0\0i\0\0\0ei…`）；
- 目录里原本没有 `draft_version.json`，回写后也没有。

后果：`jianying_versions.scan_reference_drafts()` 跳过所有加密草稿，11.x 的参考草稿永远是 0 份，
状态一直停在 `template`。这不是没查，是**查到了但读不出来**。

## 2. A 级证据怎么采（`version_evidence.py`）

判据是行为，不看版本字段：某张带引擎侧车（`studio_export_meta.json`）的明文草稿，
现在变成了原生加密格式 ⇒ 这版剪映解析并重存成功 ⇒ 它接受我们这套 schema。

```bash
python3 integrations/mac-worker/version_evidence.py scan      # 扫草稿目录，记新证据
python3 integrations/mac-worker/version_evidence.py status    # 这版剪映有几条
python3 integrations/mac-worker/jianying_versions.py          # 支持状态里带「行为实证」
```

台账落 `docs/probe/version-evidence/`：每条一个 JSON（草稿名、App 版本/build、回写前后指纹
`sha256/size/kind`、verdict、原因），`ledger.jsonl` 汇总。同一草稿同一版本只记一次。

真机已采到两条：

- `studio_6ae13f0f791bd5bc23ed991a__r2`（10-28 17:13 回写）
- `studio_6ae20a3cb2886cd834c8b67e__r1`（10-29 15:15 回写）

⇒ 11.6.0 的 `support.status` 从 `template` 变成 `accepted`，`check_all.py` 版本行显示
「行为实证 2 条（这版剪映打开并回写过引擎草稿）」。

## 3. 边界（别把 A 级读过头）

- A 级证明的是「11.6 能**读**我们写的 10.2/windows 版本块并重存」，不是「11.6 自己的写盘 schema 长这样」。
  我们仍然用 VectCutAPI 模板字段写盘——只是因为实测可开。
- 草稿被回写后就**不再是明文**：引擎侧再想读时间线（`inspect`）要读回写前的副本，
  或从 `studio_export_meta.json` 侧车拿参数。`--claim` 走 ffprobe 检查产物，不受影响。
- 拿不到 11.x 明文参考之前，不新增任何「猜」的版本号；`BUILTIN_SCHEMAS` 只在干净机器上兜底。
- 想要 B 级证据，唯一办法是让剪映在**没接触过我们草稿**的情况下自己存一份明文草稿（需要真人在
  剪映里建空草稿、确认它仍是明文），这一步没做，写在验收缺口里。
