# 应用内点击：为什么「只在那个窗口里的鼠标」不存在，以及我们怎么绕

做「在剪映里点按钮」这件事时，最想要的是一个**只作用于剪映窗口、不动用户鼠标**的输入通道。
这一页记的是实测结论和平台的接入方式，避免下一轮再从头试一遍。

## 1. 结论

macOS 只有一根硬件指针，**没有**"第二个光标"。任何真正会让 App 响应的点击，最终都要落到
HID 事件（`.cghidEventTap`）上，而 HID 事件的坐标是全局的——所以只能借用户的指针，不能造一根新的。

能省掉的不是"指针"，而是**时长和次数**：先试两条不移动指针的通道，都无效才借指针，用完立刻
还回去（焦点 + 光标位置）。平台把这套顺序做成三级阶梯，调用方只看"哪一级生效"。

## 2. 实测（2026-10-29，剪映 11.6.0 build 490）

对象：导出面板的「取消」按钮（`automationcancel`，屏幕矩形 `-128,1401 72x22`）。
判据不是退出码，而是**窗口截图逐字节对比**（`ax_tool wincap <wid>`，118232 bytes → 同前/后比对）。

| 通道 | 命令 | 返回 | 界面 |
| --- | --- | --- | --- |
| AXPress | `ax_tool click VideoFusion-macOS automationcancel --via ax` | `AXPress AXButton err=0` | 零变化，窗口仍在 |
| 进程内投递 | `ax_tool bgclick -92 1412`（`CGEvent.postToPid`，打印「光标未移动」） | 0 | 零变化，窗口仍在 |
| HID | `ax_tool click VideoFusion-macOS automationcancel --via mouse` | 0 | **窗口立即关闭**（层数 2 → 1） |

即：剪映的 Qt/CEF 混合 UI **返回成功码但什么都不做**。因此

* 不能拿返回码当"点到了"的证据，必须有 verify（截图哈希 / 窗口标题 / AX 树快照）；
* `bgclick` 保留在阶梯里只是为了换版本后能自动占位，不是可用通道；
* 真正生效的只有 HID，也就是必须短暂借指针（现有 `quiet_start/quiet_restore` 已负责空闲门、
  前台 pid 与光标位置的保存/恢复，整段 12–18 s）。

顺带否掉一条看起来更优雅的路：剪映内嵌 Chromium（子进程 user-agent
`JIANYING_PC/11.6.0 Chrome/121`），但主编辑器与导出面板是 Qt 原生控件，**不是网页**，
所以 CDP/DevTools 打不到这些按钮；`--remote-debugging-port` 只能覆盖它内嵌的素材/云页面。

## 3. 平台接入（本轮已落地）

* `integrations/mac-worker/input_tiers.py`：通道定义、命令行拼装、`ladder()` 阶梯、`receipt()`
  证据形态。`verify()` 说"没变"就继续下一级——只信返回码会把"点了没反应"记成成功。
* `integrations/mac-worker/export/panel.py`：`click_point(x, y, verify=None)` 用
  `("pid", "hid")` 两级的阶梯；面板里 4 处原本写死 `click-at` 的点按全部改走它，语义不变、
  多出一条"哪一级生效"的记录。
* `integrations/mac-worker/tests/test_input_tiers.py`：11 条离线用例，覆盖"成功码但无效果时下探"、
  "失败码不调 verify"、"全失败返回 None"、"借指针必须有空闲门"。
* 验收：`tools/check_all.py --offline` 10/10 通过（该行用例数 18 → 29）。

## 3.1 滚轮：不点、只翻（本轮新增）

列表类界面（剪映首页「本地草稿」、素材面板）不需要点击，只需要翻页。这类操作用**滚轮事件**：
`ax_tool scroll <x> <y> <dy> [--count n]`。

实测（2026-10-29）：滚轮事件带上坐标投到 HID 后，**WindowServer 会把真实光标挪到那个坐标上**
（第一版实现读回光标位置时发现了位移）。所以实现里默认先存光标、滚完 `CGWarpMouseCursorPosition`
放回原位，并输出「光标未移动 / 被移动了」作为证据；`--keep-cursor` 可关掉恢复（调试用）。

判据同样是"界面有没有真的动"，不是返回码：滚完立刻 `wincap` 比截图哈希，或比较 AX 树里的标题集合。

**一轮实测的负结果**：对剪映 11.6 首页「本地草稿」发了 2 组合成滚轮（line 单位 `dy=-12 ×8`、
pixel 单位 `dy=-900`，落点都在卡片区），两次 `wincap` 的 md5 完全一致、AX 标题集合也不变——
这个首页列表**不响应**合成滚轮（很可能是自绘的固定一屏+分页，不是可滚动容器）。结论是：
滚轮通道保留（对真滚动容器的面板有用），但"翻剪映首页草稿箱"不能靠它，要么找分页/搜索入口，
要么走索引收录（见 `docs/probe/export-panel/runs/6ae237a72a5e71e140145c49/README.md`）。

## 4. 想彻底不碰用户鼠标，只有三条真路（按代价排序）

1. **第二用户会话**（同机换账号登录，剪映跑在那边）：输入事件进的是那个会话的窗口服务器，
   用户当前会话的光标不动。需要另一套账号与自动登录；剪映一份草稿目录要跟着切。
2. **macOS 虚拟机**（Virtualization.framework / UTM / tart 镜像）：完全隔离，能进 CI，
   代价是 macOS guest 镜像（数十 GB）与 GPU 转码/渲染性能。
3. **自建 Web 界面走 CDP**：我们自己的平台页面本来就有 CDP 通道（无需外设、真正"窗口内指针"），
   所以"应用内点击"优先做成网页入口；只有剪映这类原生第三方 App 才落到借指针。

本轮不做 1/2（要用户决定成本），先把阶梯与证据固定下来：以后任何 GUI 点击都能回答
"用了哪一级、有没有验证过"。

## 5. 换版本后怎么重测

```bash
cd integrations/mac-worker
# 1) 造一个可逆界面状态：打开剪映导出面板（或任一弹层）
# 2) 记基线、点、比图
export AX_TOOL=~/CreatorStudio/bin/ax_tool
$AX_TOOL wincap <wid> /tmp/before.png
$AX_TOOL click VideoFusion-macOS automationcancel --via ax   # 期望：err=0 但界面不变
$AX_TOOL bgclick <cx> <cy>                                   # 期望：仍然不变
$AX_TOOL wincap <wid> /tmp/after2.png                        # 与 before.png 逐字节比对
$AX_TOOL click VideoFusion-macOS automationcancel --via mouse # 期望：界面变化（生效）
```

三次截图对不上就说明该版本放开了某一级——把结果更新到上面第 2 节的表，并把 `input_tiers.TIERS`
的默认顺序前移，别只改注释。
