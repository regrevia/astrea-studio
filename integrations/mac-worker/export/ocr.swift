// ocr.swift — 用 macOS Vision 把一张截图读成「带坐标的文字」。
//
// 为什么不用 read_image：剪映面板里的参数（分辨率/码率/编码/帧率）是文字，
// 我们需要的是**文字 + 位置**，不是像素图。Vision 给的 boundingBox 足够精确到控件。
//
// 用法: ocr <png> [--json] [--min-conf 0.3]
// 输出(默认 tab 分隔): text \t x,y,w,h(像素,左上原点) \t conf
//
// 编译: swiftc -O ocr.swift -o ocr

import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: ocr <png> [--json] [--min-conf 0.3]\n".data(using: .utf8)!)
    exit(2)
}
let path = args[1]
var asJSON = false
var minConf: Float = 0.3
var i = 2
while i < args.count {
    if args[i] == "--json" { asJSON = true }
    if args[i] == "--min-conf", i + 1 < args.count { minConf = Float(args[i + 1]) ?? 0.3; i += 1 }
    i += 1
}

guard let img = NSImage(contentsOfFile: path),
      let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let cg = rep.cgImage else {
    FileHandle.standardError.write("cannot load image: \(path)\n".data(using: .utf8)!)
    exit(3)
}
let W = CGFloat(cg.width), H = CGFloat(cg.height)

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "en-US"]

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([request]) } catch {
    FileHandle.standardError.write("vision failed: \(error)\n".data(using: .utf8)!)
    exit(4)
}

struct Item: Encodable {
    let text: String
    let x: Double, y: Double, w: Double, h: Double
    let conf: Double
}

var items: [Item] = []
for obs in (request.results ?? []) {
    guard let top = obs.topCandidates(1).first, top.confidence >= minConf else { continue }
    let bb = obs.boundingBox  // 归一化，左下原点
    items.append(Item(text: top.string,
                      x: Double(bb.minX * W),
                      y: Double((1 - bb.maxY) * H),
                      w: Double(bb.width * W),
                      h: Double(bb.height * H),
                      conf: Double(top.confidence)))
}
// 阅读顺序：先上后下，再左到右
items.sort { a, b in abs(a.y - b.y) < 8 ? a.x < b.x : a.y < b.y }

if asJSON {
    let enc = JSONEncoder()
    enc.outputFormatting = [.withoutEscapingSlashes]
    if let d = try? enc.encode(items), let s = String(data: d, encoding: .utf8) {
        print(s)
    }
} else {
    for it in items {
        let line = String(format: "%@\t%.0f,%.0f %.0fx%.0f\t%.2f",
                          it.text, it.x, it.y, it.w, it.h, it.conf)
        print(line)
    }
}
FileHandle.standardError.write("items: \(items.count) image: \(Int(W))x\(Int(H))\n".data(using: .utf8)!)
