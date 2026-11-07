// 在仓库根目录运行：swift ios/Design/generate-app-icon.swift
// 输出不透明的 1024 × 1024 图标，圆角由 iOS 负责裁切。
import AppKit

let size = 1024
let context = CGContext(
    data: nil, width: size, height: size, bitsPerComponent: 8,
    bytesPerRow: size * 4, space: CGColorSpaceCreateDeviceRGB(),
    bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
)!
let blue = CGColor(red: 49 / 255, green: 92 / 255, blue: 206 / 255, alpha: 1)
context.setFillColor(blue)
context.fill(CGRect(x: 0, y: 0, width: size, height: size))

// 一条连续的旅行路线连接起点与目的地；保持小尺寸下可辨认。
context.setStrokeColor(CGColor(gray: 1, alpha: 1))
context.setLineWidth(64)
context.setLineCap(.round)
context.move(to: CGPoint(x: 286, y: 316))
context.addLine(to: CGPoint(x: 608, y: 316))
context.addCurve(to: CGPoint(x: 608, y: 510),
                 control1: CGPoint(x: 738, y: 316), control2: CGPoint(x: 738, y: 510))
context.addLine(to: CGPoint(x: 416, y: 510))
context.addCurve(to: CGPoint(x: 416, y: 704),
                 control1: CGPoint(x: 286, y: 510), control2: CGPoint(x: 286, y: 704))
context.addLine(to: CGPoint(x: 738, y: 704))
context.strokePath()

context.setFillColor(CGColor(gray: 1, alpha: 1))
context.fillEllipse(in: CGRect(x: 230, y: 260, width: 112, height: 112))
context.setFillColor(blue)
context.fillEllipse(in: CGRect(x: 662, y: 628, width: 152, height: 152))
context.setLineWidth(28)
context.strokeEllipse(in: CGRect(x: 676, y: 642, width: 124, height: 124))

let image = NSBitmapImageRep(cgImage: context.makeImage()!)
let output = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    .appendingPathComponent("../Potato/Assets.xcassets/AppIcon.appiconset/AppIcon.png")
try image.representation(using: .png, properties: [:])!.write(to: output)
print(output.standardizedFileURL.path)
