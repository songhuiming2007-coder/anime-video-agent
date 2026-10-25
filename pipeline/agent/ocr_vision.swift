// D141：用 macOS 自带 Vision 框架识别图片里的文字，一行一个识别结果打到 stdout。
// 用法：swift ocr_vision.swift <图片>。不装任何依赖；只在 macOS 上可用（本项目只管 macOS）。
import Foundation
import Vision

let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
      let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    FileHandle.standardError.write("读不了图片：\(url.path)\n".data(using: .utf8)!)
    exit(2)
}
let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
// 不开语言纠错：它会把识别结果往词典词上改，核的是「图上画了什么」，不要它替我们猜
req.usesLanguageCorrection = false
req.recognitionLanguages = ["zh-Hans", "zh-Hant", "ja-JP", "en-US"]
do {
    try VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
} catch {
    FileHandle.standardError.write("Vision 识别失败：\(error)\n".data(using: .utf8)!)
    exit(3)
}
for o in req.results ?? [] {
    if let t = o.topCandidates(1).first { print(t.string) }
}
