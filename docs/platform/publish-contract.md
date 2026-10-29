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

## 离线体检

```
cd integrations/mac-worker
<venv-python> tools/check_publish.py     # 16/16 通过，不开浏览器
```

覆盖：载荷解析与四类拒绝、视频/图文 argv 分流、URL 回读、零退出无确认、非零退出、cookie 失效不起进程；
加 `--cookie` 才额外跑一次真实 cookie 预检。
真发一版仍然要在机器空闲时人工跑，脚本不替代那一步。
