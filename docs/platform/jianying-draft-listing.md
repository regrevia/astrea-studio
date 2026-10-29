# 剪映首页草稿箱：为什么只看到 8 张，怎么让引擎草稿出现

导出面板只存在于**已打开的草稿**里，所以整条 GUI 链路的第一跳是「在剪映首页点开那张草稿」。
这一页记的是这块黑盒的实测规则（剪映 11.6.0 build 490，macOS，2026-10-29），以及平台侧的入口。

## 1. 首页只显示「最新的一屏」

首页草稿区是 CEF 自绘的固定一屏，AX 树里只出现**当前显示的 8 张卡**（`HomePageDraftTitle:<名字>`）。
排在后面的草稿不是隐藏，而是**根本没渲染**，读不到也点不到：

* 合成滚轮对这块区域无效（line / pixel 两种单位都试过，`wincap` 截图 md5 完全一致）；
* 首页的搜索放大镜点了不弹输入框，AX 树里也没有对应文本框；
* `open -a VideoFusion-macOS.app <草稿目录>` 不会打开草稿，仍停在首页。

## 2. 显示哪 8 张，由索引里的修改时间决定

排序读的是 `com.lveditor.draft/root_meta_info.json` 里
`all_draft_store[*].tm_draft_modified`（微秒时间戳），**从新到旧取一屏**。实测：

* 只改草稿目录里的 `draft_meta_info.json` → 首页不变（它不参与排序）；
* 只改索引里那条 `tm_draft_modified` + **重启剪映** → 草稿立刻进入第一屏。

验收草稿 `studio_6ae20a3cb2886cd834c8b67e__r1`（48 小时前的旧戳，本来读不到）就是靠这一手
变成第 7 张卡的，双击后进编辑器，`lsof` 确认打开的就是它——**真草稿可以打开了**。

## 3. 新目录：剪映启动时会自己扫描收录

复制一个原生草稿目录（4 个文件：`draft_content.json` / `draft_info.json` /
`draft_meta_info.json` / `draft_version.json`），**不动索引**，重启剪映后：

* 索引条目 47 → 50（剪映自己补写）；
* 首页多出这张卡。

所以「引擎草稿进草稿箱」不需要手工插 JSON——把目录放进
`~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/`，重启一次即可。
之前引擎草稿读不到，原因是它们的修改时间旧、被挤出了一屏，而不是没被收录。

## 4. 平台入口

* `integrations/mac-worker/export/draft_listing.py`
  * `visible_titles(tree_dump)`：首页第一屏实际显示的名字；
  * `surface(name|draft_path)`：把该草稿的 `tm_draft_modified` 顶到最新（先备份索引为
    `root_meta_info.json.studio-bak`，只改这一条，不删条目，可 `restore()` 还原）；
  * `stale_entries()` / `drop_entry(name)`：清理实验目录留下的条目，别手改 JSON。
* `integrations/mac-worker/export/open_draft.py --surface --name <草稿名>`：读不到卡片时自动
  surface → 重启剪映 → 重试双击，点击走 `panel.click_point` 的三级通道阶梯（`pid` 无效才借指针），
  成功判据是「导出按钮真的出现」，不是点击返回码。
* `open_draft.py --list`：只打印第一屏能看到的名字 + 索引里的失效条目，不点鼠标，可随时跑。

## 5. 已知边界

* `surface` 会写用户的剪映索引（留一条备份，可 restore 还原），只在这台被授权的机器上跑；
* 每次 surface 都要重启一次剪映（约 17 s），期间不要有人在用；
* 索引里 `draft_ids` 是**整数计数**不是列表，别按列表去遍历改写；
* 首页一屏 8 张的上限没有找到可调项，多草稿场景靠 surface 轮转。
