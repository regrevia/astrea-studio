// winlist.swift — 列出 CGWindow 层级的窗口：window id / 属主 / 标题 / 位置大小 / 是否在屏。
//
// 用途：拿到 window id 后用 `screencapture -l <id>` 截**该窗口自身内容**，
// 即使它被别的窗口挡住也能截到，从而在不抢前台的前提下读面板。
//
// 用法: winlist [--owner 子串] [--all]
// 输出: id \t layer \t onscreen \t owner \t name \t x,y wxh

import Foundation
import CoreGraphics

let args = CommandLine.arguments
var ownerFilter: String? = nil
var includeAll = false
var i = 1
while i < args.count {
    if args[i] == "--owner", i + 1 < args.count { ownerFilter = args[i + 1]; i += 2; continue }
    if args[i] == "--all" { includeAll = true }
    i += 1
}

let opts: CGWindowListOption = includeAll ? [.optionAll] : [.optionOnScreenOnly, .excludeDesktopElements]
guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
    FileHandle.standardError.write("cannot read window list\n".data(using: .utf8)!)
    exit(3)
}

for w in list {
    let owner = (w[kCGWindowOwnerName as String] as? String) ?? ""
    if let f = ownerFilter, !owner.localizedCaseInsensitiveContains(f) { continue }
    let wid = (w[kCGWindowNumber as String] as? Int) ?? 0
    let layer = (w[kCGWindowLayer as String] as? Int) ?? 0
    let onscreen = (w[kCGWindowIsOnscreen as String] as? Bool) ?? false
    let name = (w[kCGWindowName as String] as? String) ?? ""
    var rect = CGRect.zero
    if let d = w[kCGWindowBounds as String] as? [String: Any],
       let x = d["X"] as? CGFloat, let y = d["Y"] as? CGFloat,
       let ww = d["Width"] as? CGFloat, let hh = d["Height"] as? CGFloat {
        rect = CGRect(x: x, y: y, width: ww, height: hh)
    }
    let line = [String(wid), String(layer), onscreen ? "1" : "0", owner, name,
                "\(Int(rect.minX)),\(Int(rect.minY)) \(Int(rect.width))x\(Int(rect.height))"].joined(separator: "\t")
    print(line)
}
