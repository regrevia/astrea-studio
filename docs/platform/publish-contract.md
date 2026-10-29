# 发布这一步的契约（`publish_sau`）

发布是整条链路里唯一不可逆的动作。这里的规则是：**载荷不合格不启动浏览器；浏览器给不出凭据就不算发布成功。**

## 载荷 → 命令行

`PublishSpec.from_payload()` 决定发什么，`build_argv()` 决定怎么发。抖音有「视频」和「图文」两个入口，
argv 不一样，所以发布类型必须显式带：

| 载荷 | 结果 |
| --- | --- |
| `videoPath` + `content.title` | `upload-video --file … --title … --desc … --tags a,b` |
| `content.type = note` + `content.imagePaths` | `upload-note --images … --title … [--note …] [--bgm …]` |
| `content.type = story` 或未知值 | 拒绝：`unsupported publication type` |
| 缺 `accountRef` / 缺标题 / 文件不存在 | 拒绝，且**不**启动浏览器 |

话题统一去掉 `#` 前缀再用逗号串联；`thumbnailPath` 只对视频生效，图文入口不收这个参数。

## 什么才算「发出去了」

| 浏览器输出 | 记录的状态 |
| --- | --- |
| 有提交确认行 + 可读作品 URL | `submitted_unverified`，带回 `remotePostId` / `remoteUrl` |
| 有提交确认行、读不到作品 ID | `submitted_unverified`，`reason` 说明没读到 ID |
| 退出码 0 但没有提交确认行 | `submission_unknown`（台账不会自动重投） |
| 退出码非 0 | `PublishError`，附最后 500 字符输出 |

`submitted_unverified` 这个名字是刻意的：页面说交了就记「已提交未核验」，不冒充「已发布」。

## 状态只能往前走

发布台账的状态不是随便写：`recordSubmission` 按一张转移表校验，越级直接 400。

| 从 | 允许到 |
| --- | --- |
| `planned` | `not_submitted`（放弃计划） |
| `submitting`（`beginSubmission` 先落库） | `submitted_unverified` / `submission_unknown` / `scheduled_remote` / `reviewing` / `not_submitted` / `waiting_user` / `reconciling` |
| `submission_unknown` | `reconciling` / `submitted_unverified` / `published` / `not_submitted` |
| `reconciling` | `submitted_unverified` / `published` / `not_submitted` |
| `submitted_unverified` | `published` / `rejected` / `reviewing` |

另外两条：标 `published` 必须带作品 ID（没 ID 就停在 `submitted_unverified`）；同一份结果重复
回写直接返回原记录，不追加第二份证据、不写第二条时间线。

实测（2026-10-29），attempt `6ae2ab4666b2fe676bafe43a`：

1. `planned` 直接 reconcile 到 `published` → 400 `发布状态不能从 planned 直接变成 published`
2. `beginSubmission` → `submitting`
3. reconcile `submission_unknown`（带 error 说明没真调浏览器）→ 通过，证据 1 条
4. 同一结果再回写一次 → 证据还是 1 条（总 2 条是第 5 步那次）
5. reconcile `not_submitted` 收尾 → 通过，证据 2 条

留的是诚实的「放弃提交」终态，没有伪造成功。

## 提交中断了怎么收尾

浏览器那条路走到一半进程没了（关机、崩溃、被杀），记录会停在 `submitting`，而库里没法
区分「还没开始提交」和「已经点下去但没回读」。所以不做自动重投，只做降级：

```
POST /api/production/publications/sweep
{ "olderThanMinutes": 15 }
```

超时仍为 `submitting` 的记录改成 `submission_unknown`（附 `kind: "stale_sweep"` 证据和一条
时间线事件），下一步由人工回读平台确认发没发。重复跑是幂等的；巡检撞上真实回写时以真实
回写为准，不覆盖终态、不写事件。

默认 15 分钟，可配 1～10080 分钟；一次最多处理 20 条。目前只有手动触发（没有定时任务），
因为真发一次是不可逆的，自动重投的风险比停在未确认大。

实测（2026-10-29）：新登记 attempt `6ae2ae2b6d97de3d41203a1d` → `beginSubmission` 落 `submitting`；
把 `updatedAt` 推到一小时前后巡检，第一次 `scanned:1 recovered:[…]`，第二次 `scanned:0`；
记录变成 `submission_unknown`，证据一条（`kind: "stale_sweep"`），`lastError` 写明要人工回读。

## 回读一条链接收尾

`submission_unknown` 只能靠回读平台收尾，不能靠重投。入口：

```
# 只读看结论，不写库
python3 tools/check_publish.py --readback https://www.douyin.com/video/<id> --account <账号>
# 顺便认领：拉本人最近作品（用来对照标题找回作品 ID）
python3 tools/check_publish.py --works <账号>
# 把结论写回台账（默认 dry-run，加 --apply 才发请求）
python3 tools/reconcile_publish.py --url https://www.douyin.com/video/<id> --account <账号> \
    --attempt <attemptId> --apply
```

规则：

- 抖音对普通 HTTP 请求只回一段 JS 检测壳，没有内容；所以回读走无头 chromium（只读打开，不点任何按钮、不抢前台），复用该账号的 SAU cookie，私密作品也看得到。
- 结论只有三档：`published`（页面给出作品）、`not_found`（平台明确说作品不存在）、`unknown`（登录墙、验证码、网络失败、页面结构变了）。只有前两档会写台账：`published` 必须带作品 ID 和链接，`not_submitted` 只能由 `not_found` 推出；`unknown` 一律不动。
- 分类只看渲染出来的文字，不看 HTML 源码：脚本里本来就带「验证码」等字样，看源码会把真作品误判成登录墙。
- 拉作品列表抽不到 ID 时返回空列表，**不能据此判定「没发出去」**。

回读也做成了 worker 作业：`publish_readback`（lane `publish`），payload `{url, account?, attemptId?}`。
worker 只做三件事——渲染回读、翻成台账能收的结论、调用
`POST /api/production/worker/publications/:id/readback` 写回（x-worker-token 认证，绑定 token 的 userId）。
没有结论或没给 `attemptId` 时，作业照样成功返回 `{state, applied:false, reason}`，**不写台账**。
后端只收 `published`（必须带作品 ID）和 `not_submitted`，其余状态直接拒（12853）。

界面上不用手搓作业：对回执不明的记录点「回读链接」，前端调
`POST /api/production/publications/:id/readback-job`（用户态），后端把记录翻成 `reconciling`、
排一条 `publish_readback` 作业，返回 `{jobId, attempt, url, status}`。几个边界：

- 只有 `submission_unknown` 能排：已有结论的记录再排一次等于白读一遍链接，直接拒（12853，`只有回执不明的提交需要回读；其他状态已有结论`）。
- 正在回读（`reconciling`）时再点一次也拒（`这条提交正在回读，等结论回来`），`reconciling` 本身就是重复点击的锁。
- 没有链接也没有作品 ID 的记录拒掉（`这条记录没有可回读的链接；有作品 ID 才能按 ID 去平台核对`），不排空转作业。
- 回读作业不等同于渲染：它不写 run 的 `output`、不把 run 翻成 `succeeded`（`completeJob` 走 `settleReadbackJob` 分支）。
- 回读没给出结论时，记录退回 `submission_unknown` 并记一条 `readback_no_conclusion` 证据——核对失败不等于有了结论，不能让记录停在 `reconciling` 装成还在查。

实测（2026-10-29，账号 `yefen`，记录 `6ae2b7f9c7f59fe6681559e2` / 不存在的作品 `7456000000000000000`）：
`POST publications/:id/readback-job` → 作业在 `publish` 泳道被领取并跑完（21s）→ `result.applied=false, state=unknown` →
记录回到 `submission_unknown`，证据两条（`readback_enqueued`、`readback_no_conclusion`），run 状态不动。

实测（2026-10-29，账号 `yefen`）：`--works yefen` 读到 4 条真实作品；对真实作品 `7602658672556153993` 回读得 `published` + 标题；对不存在的 `7456000000000000000` 回读得 `unknown`（页面里读不到作品标记），`reconcile_publish.py` 判为「没有明确结论，台账不动」。后端接口侧新增 `applySubmissionReadback`，单测覆盖「published 带 ID 收下 / published 无 ID 拒 / 越级状态拒」。

## Cookie

发布前先跑 `sau douyin check --account <account>`；失效直接报错并提示重新登录，
不进入上传流程——避免半路失败留下「不知道发没发」的状态。

这条检查跑的是无头 chromium（`DOUYIN_COOKIE_AUTH_HEADLESS=true`），不弹窗、不抢前台，
但会联网。可以单独预检：

```
<venv-python> tools/check_publish.py --cookie yefen --timeout 90
```

输出两种结局：`cookie 文件在` + `cookie 有效` → 可发；文件不在或 `sau douyin check` 不通过 →
直接给重新登录的命令。实测 `yefen`：文件在、`sau douyin check` 通过（2026-10-29）。

## 台账记哪一个文件

`planPublication` 往 `content.assetKey` 写的是「这次真要发的那份文件」，优先级：

1. 该平台重新交付过的成片（`output.platforms[platform].key`）
2. **认领入库那一份**（`output.libraryPath`）——本机保证存在、过过指纹闸
3. 母版 key（`output.videoPath`）——产物目录里的临时路径，可能已被清掉

实测：run `6ae237a72a5e71e140145c49` 登记成 attempt `6ae2ab4666b2fe676bafe43a`，
`assetKey` 是 `library/…/20261029-095802__…__douyin__r1.mp4`（188927 B，文件在），
同一载荷再登记一次 `deduped:true`，没有产生第二行。

## 离线体检

```
cd integrations/mac-worker
<venv-python> tools/check_publish.py     # 16/16 通过，不开浏览器
```

覆盖：载荷解析与四类拒绝、视频/图文 argv 分流、URL 回读、零退出无确认、非零退出、cookie 失效不起进程；
加 `--cookie` 才额外跑一次真实 cookie 预检。
真发一版仍然要在机器空闲时人工跑，脚本不替代那一步。
