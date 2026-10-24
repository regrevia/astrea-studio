// ax_dump.swift — 把某个进程的 Accessibility 树打成 TSV，用于判断 Qt 面板暴露到什么程度。
//
// 用法: ax_dump <进程名> [最大深度] [--filter 子串]
// 输出: depth \t role \t subrole \t title \t value \t desc \t x,y,w,h \t enabled \t actions
//
// 编译: swiftc -O ax_dump.swift -o ax_dump

import ApplicationServices
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: ax_dump <process-name> [max-depth] [--filter text]\n".data(using: .utf8)!)
    exit(2)
}
let procName = args[1]
var maxDepth = 8
var filter: String? = nil
var i = 2
while i < args.count {
    if args[i] == "--filter", i + 1 < args.count { filter = args[i + 1]; i += 2; continue }
    if let d = Int(args[i]) { maxDepth = d }
    i += 1
}

func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
    var v: AnyObject?
    let err = AXUIElementCopyAttributeValue(el, name as CFString, &v)
    return err == .success ? v : nil
}

func str(_ el: AXUIElement, _ name: String) -> String {
    guard let v = attr(el, name) else { return "" }
    if let s = v as? String { return s }
    if let n = v as? NSNumber { return n.stringValue }
    return ""
}

func point(_ el: AXUIElement, _ name: String) -> String {
    guard let v = attr(el, name) else { return "" }
    var p = CGPoint.zero
    var s = CGSize.zero
    if name == kAXPositionAttribute as String {
        if AXValueGetType(v as! AXValue) == .cgPoint, AXValueGetValue(v as! AXValue, .cgPoint, &p) {
            return "\(Int(p.x)),\(Int(p.y))"
        }
    } else {
        if AXValueGetType(v as! AXValue) == .cgSize, AXValueGetValue(v as! AXValue, .cgSize, &s) {
            return "\(Int(s.width)),\(Int(s.height))"
        }
    }
    return ""
}

func actions(_ el: AXUIElement) -> String {
    var names: CFArray?
    guard AXUIElementCopyActionNames(el, &names) == .success, let list = names as? [String] else { return "" }
    return list.joined(separator: "|")
}

guard let app = NSWorkspace.shared.runningApplications.first(where: { $0.localizedName == procName || $0.bundleIdentifier == procName || ($0.executableURL?.lastPathComponent == procName) }) else {
    // 退回按 pid 找
    FileHandle.standardError.write("process not found: \(procName)\n".data(using: .utf8)!)
    exit(3)
}

let root = AXUIElementCreateApplication(app.processIdentifier)
var count = 0

func walk(_ el: AXUIElement, _ depth: Int) {
    if depth > maxDepth || count > 4000 { return }
    count += 1
    let role = str(el, kAXRoleAttribute as String)
    let sub = str(el, kAXSubroleAttribute as String)
    let title = str(el, kAXTitleAttribute as String)
    let value = str(el, kAXValueAttribute as String)
    let desc = str(el, kAXDescriptionAttribute as String)
    let help = str(el, kAXHelpAttribute as String)
    let pos = point(el, kAXPositionAttribute as String)
    let size = point(el, kAXSizeAttribute as String)
    let en = str(el, kAXEnabledAttribute as String)
    var line = [String(depth), role, sub, title, value, desc.isEmpty ? help : desc, "\(pos) \(size)", en, actions(el)].joined(separator: "\t")
    if line.count > 600 { line = String(line.prefix(600)) }
    if let f = filter {
        if line.contains(f) { print(line) }
    } else {
        print(line)
    }
    guard depth < maxDepth else { return }
    if let kids = attr(el, kAXChildrenAttribute as String) as? [AXUIElement] {
        for k in kids { walk(k, depth + 1) }
    }
}

print("pid\t\(app.processIdentifier)\twindows\t\(str(root, kAXWindowsAttribute as String))")
if let wins = attr(root, kAXWindowsAttribute as String) as? [AXUIElement] {
    for w in wins {
        walk(w, 0)
        print("---")
    }
} else {
    walk(root, 0)
}
FileHandle.standardError.write("elements: \(count)\n".data(using: .utf8)!)
