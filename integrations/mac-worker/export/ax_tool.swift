// ax_tool.swift — 剪映 GUI 自动化底座：按 AX 语义查找控件、点击、按键。
//
// 设计原则：**坐标永远从 AX 树里取，不写死像素**。只有 AX 完全不暴露的控件
// 才退回鼠标点击，且点击点来自该控件在树里的 position/size 中心。
//
// 用法:
//   ax_tool tree   [进程名] [深度] [--filter 子串]
//   ax_tool find   [进程名] <子串> [--role ROLE]
//   ax_tool click  [进程名] <子串> [--index n] [--via ax|mouse]
//   ax_tool key    [进程名] <combo>           # cmd+e / cmd+shift+s / return / esc
//   ax_tool focus  [进程名]
//
// 编译: swiftc -O ax_tool.swift -o ax_tool

import ApplicationServices
import ScreenCaptureKit
import AppKit

// ---------------------------------------------------------------- AX 基础

func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
    var v: AnyObject?
    return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
}

func s(_ el: AXUIElement, _ name: String) -> String {
    guard let v = attr(el, name) else { return "" }
    if let x = v as? String { return x }
    if let n = v as? NSNumber { return n.stringValue }
    return ""
}

func frameOf(_ el: AXUIElement) -> CGRect? {
    var p = CGPoint.zero, sz = CGSize.zero
    guard let pv = attr(el, kAXPositionAttribute as String),
          let sv = attr(el, kAXSizeAttribute as String),
          AXValueGetType(pv as! AXValue) == .cgPoint,
          AXValueGetType(sv as! AXValue) == .cgSize,
          AXValueGetValue(pv as! AXValue, .cgPoint, &p),
          AXValueGetValue(sv as! AXValue, .cgSize, &sz) else { return nil }
    return CGRect(x: p.x, y: p.y, width: sz.width, height: sz.height)
}

func actionsOf(_ el: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(el, &names) == .success, let l = names as? [String] else { return [] }
    return l
}

// 所有候选文本：title / description / help / value
func texts(_ el: AXUIElement) -> [String] {
    [s(el, kAXTitleAttribute as String), s(el, kAXDescriptionAttribute as String),
     s(el, kAXHelpAttribute as String), s(el, kAXValueAttribute as String)].filter { !$0.isEmpty }
}

var maxElements = 4000
var seen = 0

func walk(_ el: AXUIElement, _ depth: Int, _ cb: (AXUIElement, Int) -> Bool) {
    if seen >= maxElements { return }
    seen += 1
    if !cb(el, depth) { return }
    if let kids = attr(el, kAXChildrenAttribute as String) as? [AXUIElement] {
        for k in kids { walk(k, depth + 1, cb) }
    }
}

func appRoot(_ name: String) -> AXUIElement? {
    let apps = NSWorkspace.shared.runningApplications.filter {
        $0.localizedName == name || $0.bundleIdentifier == name || $0.executableURL?.lastPathComponent == name
    }
    guard let app = apps.first else { return nil }
    return AXUIElementCreateApplication(app.processIdentifier)
}

func rootOf(_ name: String) -> (AXUIElement, pid_t)? {
    let apps = NSWorkspace.shared.runningApplications.filter {
        $0.localizedName == name || $0.bundleIdentifier == name || $0.executableURL?.lastPathComponent == name
    }
    guard let app = apps.first else { return nil }
    return (AXUIElementCreateApplication(app.processIdentifier), app.processIdentifier)
}

// ---------------------------------------------------------------- 输入注入

func postClick(_ p: CGPoint) {
    let src = CGEventSource(stateID: .hidSystemState)
    let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
    let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)
    CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
    usleep(60_000)
    down?.post(tap: .cghidEventTap)
    usleep(40_000)
    up?.post(tap: .cghidEventTap)
}

let keyCodes: [String: CGKeyCode] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31,
    "u": 32, "i": 34, "p": 35, "return": 36, "l": 37, "j": 38, "k": 40, "n": 45,
    "m": 46, "tab": 48, "space": 49, "escape": 53, "esc": 53, "left": 123, "right": 124,
    "down": 125, "up": 126, "delete": 51, "f1": 122, "f2": 120,
]

func postKey(_ combo: String) {
    let parts = combo.lowercased().split(separator: "+").map(String.init)
    guard let last = parts.last, let code = keyCodes[last] else {
        FileHandle.standardError.write("unknown key: \(combo)\n".data(using: .utf8)!)
        exit(4)
    }
    var flags: CGEventFlags = []
    for m in parts.dropLast() {
        switch m {
        case "cmd", "command": flags.insert(.maskCommand)
        case "shift": flags.insert(.maskShift)
        case "alt", "option": flags.insert(.maskAlternate)
        case "ctrl", "control": flags.insert(.maskControl)
        default: break
        }
    }
    let src = CGEventSource(stateID: .hidSystemState)
    let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true)
    let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false)
    down?.flags = flags
    up?.flags = flags
    down?.post(tap: .cghidEventTap)
    usleep(30_000)
    up?.post(tap: .cghidEventTap)
}

/// 用 CGEvent 注入任意 Unicode 文本（中文、斜杠、点号都行）。
///
/// 为什么需要它：剪映的「名称」「导出至」是 Qt 自绘控件，AX 只能给出矩形和
/// automation id，不能 setValue，也没有 AXPress 到可编辑态；而这台机器没装
/// cliclick。CGEvent.keyboardSetUnicodeString 单次最多 20 个 UTF-16 单元，
/// 所以按 20 切块发，块之间留 12ms 让 Qt 事件循环消化。
func postText(_ text: String) {
    let src = CGEventSource(stateID: .hidSystemState)
    let units = Array(text.utf16)
    var i = 0
    while i < units.count {
        let chunk = Array(units[i..<min(i + 20, units.count)])
        let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true)
        let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false)
        chunk.withUnsafeBufferPointer { buf in
            guard let base = buf.baseAddress else { return }
            down?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: base)
            up?.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: base)
        }
        down?.post(tap: .cghidEventTap)
        usleep(12_000)
        up?.post(tap: .cghidEventTap)
        usleep(12_000)
        i += 20
    }
}

func focusApp(_ pid: pid_t) {
    if let app = NSRunningApplication(processIdentifier: pid) {
        app.activate(options: [.activateIgnoringOtherApps])
    }
}

// ---------------------------------------------------------------- 命令

let argv = CommandLine.arguments
guard argv.count >= 2 else {
    FileHandle.standardError.write("usage: ax_tool tree|find|click|key|focus <proc> ...\n".data(using: .utf8)!)
    exit(2)
}
let cmd = argv[1]
var proc = "VideoFusion-macOS"
var rest: [String] = []
var flag: [String: String] = [:]
var i = 2
while i < argv.count {
    let a = argv[i]
    if a.hasPrefix("--") {
        let k = String(a.dropFirst(2))
        if i + 1 < argv.count && !argv[i + 1].hasPrefix("--") {
            flag[k] = argv[i + 1]; i += 2
        } else { flag[k] = "1"; i += 1 }
        continue
    }
    rest.append(a); i += 1
}
if let first = rest.first, !first.hasPrefix("--"), cmd != "key", cmd != "focus", cmd != "tree" {
    // 第一个位置参数若是已知进程名则当进程名
    if first.contains("VideoFusion") || first.contains(".")  { proc = first; rest.removeFirst() }
}
if cmd == "tree", let f = rest.first, f.contains("VideoFusion") { proc = f; rest.removeFirst() }

guard let (root, pid) = rootOf(proc) else {
    FileHandle.standardError.write("process not found: \(proc)\n".data(using: .utf8)!)
    exit(3)
}

switch cmd {
case "focus":
    focusApp(pid)
    print("focused \(proc) pid=\(pid)")

case "tree":
    let depth = Int(rest.first ?? "") ?? Int(flag["depth"] ?? "") ?? 8
    let filter = flag["filter"]
    var count = 0
    let windows = attr(root, kAXWindowsAttribute as String) as? [AXUIElement] ?? [root]
    for w in windows {
        walk(w, 0) { el, d in
            let f = frameOf(el)
            let line = [String(d), s(el, kAXRoleAttribute as String), s(el, kAXSubroleAttribute as String),
                        s(el, kAXTitleAttribute as String), s(el, kAXDescriptionAttribute as String),
                        s(el, kAXHelpAttribute as String), s(el, kAXValueAttribute as String),
                        f.map { "\(Int($0.minX)),\(Int($0.minY)) \(Int($0.width)),\(Int($0.height))" } ?? "",
                        actionsOf(el).joined(separator: "|")].joined(separator: "\t")
            if let fl = filter, !line.contains(fl) { return true }
            print(line)
            count += 1
            return d < depth
        }
        print("--- window end ---")
    }
    FileHandle.standardError.write("elements: \(count)\n".data(using: .utf8)!)

case "find":
    guard let needle = rest.first else {
        FileHandle.standardError.write("usage: ax_tool find <needle>\n".data(using: .utf8)!); exit(2)
    }
    let roleFilter = flag["role"]
    let windows = attr(root, kAXWindowsAttribute as String) as? [AXUIElement] ?? [root]
    var hits = 0
    for w in windows {
        walk(w, 0) { el, d in
            let role = s(el, kAXRoleAttribute as String)
            if let rf = roleFilter, role != rf { return true }
            let blob = texts(el).joined(separator: " ")
            if blob.contains(needle) {
                hits += 1
                let f = frameOf(el)
                print([String(d), role, s(el, kAXDescriptionAttribute as String).isEmpty ? s(el, kAXTitleAttribute as String) : s(el, kAXDescriptionAttribute as String),
                       f.map { "\(Int($0.midX)),\(Int($0.midY))" } ?? "",
                       f.map { "\(Int($0.width))x\(Int($0.height))" } ?? "",
                       actionsOf(el).joined(separator: "|")].joined(separator: "\t"))
            }
            return true
        }
    }
    FileHandle.standardError.write("hits: \(hits)\n".data(using: .utf8)!)

case "click":
    guard let needle = rest.first else {
        FileHandle.standardError.write("usage: ax_tool click <needle> [--index n] [--via ax|mouse]\n".data(using: .utf8)!); exit(2)
    }
    let idx = Int(flag["index"] ?? "0") ?? 0
    let via = flag["via"] ?? "ax"
    var matches: [(AXUIElement, CGRect, String)] = []
    let windows = attr(root, kAXWindowsAttribute as String) as? [AXUIElement] ?? [root]
    for w in windows {
        walk(w, 0) { el, _ in
            if texts(el).joined(separator: " ").contains(needle), let f = frameOf(el), f.width > 0 {
                matches.append((el, f, s(el, kAXRoleAttribute as String)))
            }
            return true
        }
        if !matches.isEmpty { break }
    }
    guard matches.count > idx else {
        FileHandle.standardError.write("no match for '\(needle)' (found \(matches.count))\n".data(using: .utf8)!); exit(5)
    }
    let (el, f, role) = matches[idx]
    if flag["no-focus"] == nil {          // --no-focus：不激活 App，只投 AX 动作，用来测"后台能不能按"
        focusApp(pid)
        usleep(150_000)
    }
    if via == "ax", actionsOf(el).contains(kAXPressAction as String) {
        let err = AXUIElementPerformAction(el, kAXPressAction as CFString)
        print("AXPress \(role) err=\(err.rawValue)")
        if err == .success { exit(0) }
    }
    postClick(CGPoint(x: f.midX, y: f.midY))
    print("mouse click \(Int(f.midX)),\(Int(f.midY)) role=\(role) frame=\(Int(f.minX)),\(Int(f.minY)) \(Int(f.width))x\(Int(f.height))")

case "click-at":
    // 直接点屏幕坐标（双击用 --count 2）。坐标应当由 AX 树推导后传入，不要写死。
    guard rest.count >= 2, let x = Double(rest[0]), let y = Double(rest[1]) else {
        FileHandle.standardError.write("usage: ax_tool click-at <x> <y> [--count n]\n".data(using: .utf8)!); exit(2)
    }
    let n = Int(flag["count"] ?? "1") ?? 1
    focusApp(pid)
    usleep(150_000)
    let p = CGPoint(x: x, y: y)
    if n <= 1 {
        postClick(p)
    } else {
        // 双击必须用同一 event number 的连续 down/up，否则被识别成两次单击
        let src = CGEventSource(stateID: .hidSystemState)
        CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.post(tap: .cghidEventTap)
        usleep(120_000)
        for k in 0..<n {
            let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
            let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)
            down?.setIntegerValueField(.mouseEventClickState, value: Int64(k + 1))
            up?.setIntegerValueField(.mouseEventClickState, value: Int64(k + 1))
            down?.post(tap: .cghidEventTap)
            usleep(30_000)
            up?.post(tap: .cghidEventTap)
            usleep(80_000)
        }
    }
    print("click-at \(Int(x)),\(Int(y)) count=\(n)")

case "scroll":
    // 在指定点滚轮，不移动真实光标：滚轮事件自带 location，指针不会被挪走。
    // 剪映首页这类 CEF 列表的卡片不在 AX 树里，只能滚动之后重新读树。
    guard rest.count >= 3, let sx = Double(rest[0]), let sy = Double(rest[1]), let dy = Double(rest[2]) else {
        FileHandle.standardError.write("usage: ax_tool scroll <x> <y> <dy> [--count n]\n".data(using: .utf8)!); exit(2)
    }
    let sc = max(1, Int(flag["count"] ?? "1") ?? 1)
    let before = CGEvent(source: nil)?.location ?? .zero
    focusApp(pid)
    usleep(120_000)
    let pt = CGPoint(x: sx, y: sy)
    for _ in 0..<sc {
        if let e = CGEvent(scrollWheelEvent2Source: nil, units: .line, wheelCount: 1,
                           wheel1: Int32(dy), wheel2: 0, wheel3: 0) {
            e.location = pt
            e.post(tap: .cghidEventTap)
        }
        usleep(90_000)
    }
    if flag["keep-cursor"] == nil { CGWarpMouseCursorPosition(before) }
    let after = CGEvent(source: nil)?.location ?? .zero
    let moved = abs(after.x - before.x) > 1 || abs(after.y - before.y) > 1
    print("scroll \(Int(sx)),\(Int(sy)) dy=\(Int(dy)) x\(sc) 光标\(moved ? "被移动了" : "未移动")")

case "type":
    // 往当前焦点控件里输入任意文本。调用方先用 click-at 点中控件、需要时 key cmd+a 全选。
    let text = rest.joined(separator: " ")
    guard !text.isEmpty else {
        FileHandle.standardError.write("usage: ax_tool type <text>\n".data(using: .utf8)!); exit(2)
    }
    focusApp(pid)
    usleep(150_000)
    postText(text)
    print("type \(text.count) chars -> pid \(pid)")

case "key":
    guard let combo = rest.first else {
        FileHandle.standardError.write("usage: ax_tool key <combo>\n".data(using: .utf8)!); exit(2)
    }
    focusApp(pid)
    usleep(200_000)
    postKey(combo)
    print("key \(combo) -> pid \(pid)")

case "wins":
    // 列出该进程的窗口与 CGWindowID（后台截图要用 id）；--all 连离屏窗口一起列
    let opts: CGWindowListOption = flag["all"] != nil ? [.optionAll, .excludeDesktopElements]
                                                      : [.optionOnScreenOnly, .excludeDesktopElements]
    let info = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] ?? []
    var n = 0
    for w in info where (w[kCGWindowOwnerPID as String] as? Int32) == pid {
        let num = w[kCGWindowNumber as String] as? Int ?? -1
        let nm = w[kCGWindowName as String] as? String ?? ""
        let b = w[kCGWindowBounds as String] as? [String: CGFloat] ?? [:]
        let layer = w[kCGWindowLayer as String] as? Int ?? -1
        print("\(num)\t\(Int(b["X"] ?? 0)),\(Int(b["Y"] ?? 0)) \(Int(b["Width"] ?? 0))x\(Int(b["Height"] ?? 0))\tlayer=\(layer)\t\(nm)")
        n += 1
    }
    FileHandle.standardError.write("windows: \(n)\n".data(using: .utf8)!)

case "wincap":
    // 后台抓单个窗口像素：ScreenCaptureKit 的 desktopIndependentWindow 过滤器
    // 抓的是**窗口自己**的内容，所以窗口被别的 App 遮住、甚至不在前台也能抓到。
    // 光标不动、前台不变。需要"屏幕录制"权限。
    guard rest.count >= 2, let wid = UInt32(rest[0]) else {
        FileHandle.standardError.write("usage: ax_tool wincap <windowid> <out.png> [--scale 2]\n".data(using: .utf8)!); exit(2)
    }
    let out = rest[1]
    let scale = Double(flag["scale"] ?? "2") ?? 2
    if #available(macOS 14.0, *) {
        // ScreenCaptureKit 依赖 WindowServer 初始化；命令行 binary 里必须先碰一下 NSApplication，
        // 否则 SCShareableContent 直接 CGS_REQUIRE_INIT 断言崩溃。
        _ = NSApplication.shared
        let sem = DispatchSemaphore(value: 0)
        var msg = "timeout"
        Task {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                guard let win = content.windows.first(where: { $0.windowID == wid }) else {
                    msg = "window \(wid) not found（共 \(content.windows.count) 个）"; sem.signal(); return
                }
                let filter = SCContentFilter(desktopIndependentWindow: win)
                let cfg = SCStreamConfiguration()
                cfg.width = Int(Double(win.frame.width) * scale)
                cfg.height = Int(Double(win.frame.height) * scale)
                cfg.showsCursor = false
                cfg.ignoreShadowsSingleWindow = true
                let img = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg)
                let rep = NSBitmapImageRep(cgImage: img)
                if let png = rep.representation(using: .png, properties: [:]) {
                    try png.write(to: URL(fileURLWithPath: out))
                    msg = "wincap \(wid) -> \(out) \(img.width)x\(img.height) bytes=\(png.count)"
                } else { msg = "png encode failed" }
            } catch {
                msg = "wincap err: \(error)"
            }
            sem.signal()
        }
        _ = sem.wait(timeout: .now() + 20)
        print(msg)
        if !msg.hasPrefix("wincap ") { exit(6) }
    } else {
        FileHandle.standardError.write("需要 macOS 14+ 的 ScreenCaptureKit\n".data(using: .utf8)!); exit(6)
    }

case "bgclick":
    // 后台点击：CGEvent.postToPid 直接把事件投递给目标进程，不移动物理光标、不切前台。
    // 坐标仍是屏幕全局坐标（由 AX 矩形推导），但只有目标进程收到事件。
    guard rest.count >= 2, let x = Double(rest[0]), let y = Double(rest[1]) else {
        FileHandle.standardError.write("usage: ax_tool bgclick <x> <y> [--count 2]\n".data(using: .utf8)!); exit(2)
    }
    let p = CGPoint(x: x, y: y)
    let n = Int(flag["count"] ?? "1") ?? 1
    let src = CGEventSource(stateID: .hidSystemState)
    CGEvent(mouseEventSource: src, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)?.postToPid(pid)
    usleep(60_000)
    for k in 0..<n {
        let down = CGEvent(mouseEventSource: src, mouseType: .leftMouseDown, mouseCursorPosition: p, mouseButton: .left)
        let up = CGEvent(mouseEventSource: src, mouseType: .leftMouseUp, mouseCursorPosition: p, mouseButton: .left)
        down?.setIntegerValueField(.mouseEventClickState, value: Int64(k + 1))
        up?.setIntegerValueField(.mouseEventClickState, value: Int64(k + 1))
        down?.postToPid(pid)
        usleep(40_000)
        up?.postToPid(pid)
        usleep(80_000)
    }
    print("bgclick \(Int(x)),\(Int(y)) count=\(n) -> pid \(pid)（光标未移动）")

case "front":
    // 当前前台 App（守护焦点用）
    if let a = NSWorkspace.shared.frontmostApplication {
        print("\(a.processIdentifier)\t\(a.localizedName ?? "")")
    }

case "activate":
    // 把指定 pid 拉回前台（配合 --restore-focus）
    guard let p = rest.first, let t = pid_t(p) else {
        FileHandle.standardError.write("usage: ax_tool activate <pid>\n".data(using: .utf8)!); exit(2)
    }
    if let app = NSRunningApplication(processIdentifier: t) {
        print(app.activate(options: [.activateIgnoringOtherApps]) ? "activated \(p)" : "activate failed \(p)")
    } else { exit(3) }

case "cursor":
    // 无参数=读光标；两个参数=直接挪回去（不产生点击事件，不触发 hover 逻辑之外的副作用）
    if rest.count >= 2, let x = Double(rest[0]), let y = Double(rest[1]) {
        CGWarpMouseCursorPosition(CGPoint(x: x, y: y))
        print("cursor -> \(Int(x)),\(Int(y))")
    } else {
        let loc = CGEvent(source: nil)?.location ?? .zero
        print("\(Int(loc.x)) \(Int(loc.y))")
    }

case "idle":
    // 距用户最后一次输入（鼠标/键盘/滚轮）多少秒。用于"等用户离开再动手"。
    let t = CGEventType(rawValue: ~0)!
    let sec = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: t)
    print(String(format: "%.2f", sec))

case "focused":
    // 当前键盘焦点落在哪个元素上（探键盘可达性用）
    var fo: CFTypeRef?
    let e0 = AXUIElementCreateApplication(pid)
    if AXUIElementCopyAttributeValue(e0, kAXFocusedUIElementAttribute as CFString, &fo) == .success,
       let fe = fo {
        let el = fe as! AXUIElement
        print("\(s(el, kAXRoleAttribute as String))\t\(s(el, kAXTitleAttribute as String))\t\(s(el, kAXDescriptionAttribute as String))\t\(s(el, kAXValueAttribute as String))\t\(frameOf(el).map { "\(Int($0.minX)),\(Int($0.minY)) \(Int($0.width))x\(Int($0.height))" } ?? "")")
    } else { print("none") }

default:
    FileHandle.standardError.write("unknown command \(cmd)\n".data(using: .utf8)!)
    exit(2)
}
