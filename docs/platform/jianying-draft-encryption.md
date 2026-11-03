# 剪映回写后的草稿加密：三层防线

剪映 11.6 在「用户手动保存 / 导出」时会把 `draft_info.json`（连同 `draft_meta_info.json`）
改写成 AES-256 密文。密文对剪映自己可用，但引擎（`jianying_export.py`、`open_draft.py`）
读不了，平台下一条「打开草稿」「核对版本」都会失败。密文本体没有可用的离线解密途径
（密钥在剪映进程内，社区工具只覆盖 Windows），所以这里不做「破解」，只做三件事：
**生成即留明文底本、打开前先救、被改写了就自动救回来**。

## 三层

| 层 | 位置 | 触发时机 | 作用 |
| --- | --- | --- | --- |
| ① 生成时快照 | `worker.py` 的 `jianying_draft` 成功路径 → `plain_guard.snapshot()` | 引擎刚写完草稿 | 明文底本落在草稿目录之外的 `.studio-plain/<草稿>/` |
| ② 打开前守卫 | `worker.py` 的 `do_jianying_open` → `draft_guard.inspect()` | 每次点「打开工程」 | 已加密就先还原，再打开；结果写进任务的 `plainGuard` |
| ③ 常驻守护 | `deploy/local.creatorstudio.draftguard.plist` → `export/draft_guard.py` | 每 8 s 扫一遍草稿根 | 覆盖「用户直接在剪映里打开、保存」这条不经过平台的路径 |

①②③ 共用同一份快照与同一套状态判断，所以任何一层救回来的结果对另外两层都成立。

## 快照里有什么

`.studio-plain/<草稿>/` 存四个明文主文件的副本（`draft_info.json`、`draft_meta_info.json`、
`draft_content.json`、`draft_version.json`）加一份 `manifest.json`：每个文件的字节数与 sha256、
快照时间、以及「存快照时哪些文件已经是密文」（`encryptedAtSnapshot`）。带 sha256 是为了区分
「草稿没动」和「快照之后又被改过」——后者不能自动还原，否则会抹掉用户的新改动。

还原前会先把当前密文备份到 `<草稿>/.studio-enc-backup/<时间戳>/`，再把明文写回。密文永远不丢。

## 状态语义（`draft_guard` 的 action）

| action | 含义 | 系统接下来做什么 |
| --- | --- | --- |
| `ok` | 明文且快照是最新的 | 什么都不做 |
| `snapshot` / `would_snapshot` | 明文，存/刷新了快照 | 下一次扫描不再重复 |
| `restore` / `would_restore` | 密文，快照覆盖主文件且没过期 | 还原并备份密文 |
| `needs_review` | 密文，但快照之后草稿又被改过 | **不自动还原**，留给人决定 |
| `salvaged` | 主文件已密文，草稿里还剩别的明文 | 把残余明文抢救存快照（残缺快照） |
| `partial` | 只有残缺快照，主文件已密文 | 只能重新生成草稿 |
| `unrecoverable` | 密文且没有任何快照 | 只能重新生成草稿 |
| `busy` | 文件在 `SETTLE_SECONDS`（5 s）内被改过 | 这一轮跳过，避免和剪映抢写 |
| `missing` | 目录不在了 | 跳过 |

常驻循环只在**状态变化**时写流水账（`~/CreatorStudio/logs/draft-guard.jsonl`）。同一草稿停在
同一个结论上不会每 8 秒重复记一行——流水账的价值在于「什么时候变了」，不在于「还在」。

平台生成的草稿不会走到 `partial` / `unrecoverable`：生成成功即存快照（第①层）。这两个状态只可能
出现在「快照还没落盘就被改写」或「快照目录被外部删掉」的草稿上。真要恢复，只能按原 spec 重新生成
（`jianying_draft` 再跑一次），残存的 `draft_content.json` 与 `studio_export_meta.json`
可以给重建提供画布/时长/轨道素材依据。

## 运维命令

```bash
# 装 / 卸 / 看状态 / 重启（装完立刻生效，KeepAlive 兜住崩溃）
bash deploy/draft_guard_agent.sh install
bash deploy/draft_guard_agent.sh status
bash deploy/draft_guard_agent.sh restart
bash deploy/draft_guard_agent.sh uninstall

# 立刻扫一遍（不写状态，也不动文件）
cd ~/CreatorStudio/runtime/integrations/mac-worker
./.python3 -m export.draft_guard --once --dry-run

# 真扫一遍（该存的存、该还原的还原）
./.python3 -m export.draft_guard --once

# 单个草稿的状态与还原
./.python3 -m export.plain_guard status  --draft <草稿目录> --json
./.python3 -m export.plain_guard restore --draft <草稿目录> --dry-run
./.python3 -m export.plain_guard list
```

面板侧不用读流水账文件，直接问本机服务：

```bash
curl -s 'http://127.0.0.1:8899/guard?limit=20'
```

返回 `drafts`（每份草稿最近一次结论）和 `events`（最近若干条状态变化），字段与流水账一致。

守护进程用 `KeepAlive: true`（不是 worker 的 `SuccessfulExit: false`）：它退出的意思就是「草稿
没人看了」，必须被拉起来。`ThrottleInterval 30` 防崩溃循环反复冲撞草稿目录。

## 实测（2026-11-03，本机 11 份草稿）

- 部署后第一轮：`草稿守护：11 个草稿，would_snapshot 2` / 随后实扫存下 2 份明文快照；
- 两份 10 月底的旧草稿（`studio_6ae13f0f791bd5bc23ed991a__r2`、
  `studio_6ae20a3cb2886cd834c8b67e__r1`）主文件已是密文、且从未存过快照：救回残余
  `draft_content.json` 后稳定停在 `partial`，不再刷日志；
- 稳定态每 25 s 采样流水账行数不变（`348 -> 348`）。

## 不变量

- 快照永不导致渲染失败：全部 `try/except` + `log.warning`，和 `PG.ensure_plain` 同一姿态。
- 过期快照永不自动还原（`needs_review`），还原前永远先备份密文。
- 绝不为救不回来的草稿伪造明文主文件。
- 加密不阻塞任何一条运行路径：还原失败只记事件，不拦打开、不拦导出。
