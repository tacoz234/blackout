import AppKit
// Draws the Blackout icon: a pure black rounded square with one glowing line through the
// middle, like a CRT screen collapsing to a line as it switches off.
//   swift make_icon.swift <dir>.iconset        macOS iconset (build.sh turns it into AppIcon.icns)
//   swift make_icon.swift --ico <file>.ico     Windows icon; the result is committed as windows/Blackout.ico

func render(_ px: Int) -> NSBitmapImageRep {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let cg = NSGraphicsContext.current!.cgContext
    let s = CGFloat(px)
    let inset = s * 100 / 1024                         // Apple icon grid: 824pt body in 1024 canvas
    let body = NSRect(x: inset, y: inset, width: s - 2 * inset, height: s - 2 * inset)
    let w = body.width, cx = body.midX, cy = body.midY
    let shape = NSBezierPath(roundedRect: body, xRadius: w * 0.225, yRadius: w * 0.225)
    NSColor.black.setFill()
    shape.fill()
    shape.addClip()

    let space = CGColorSpaceCreateDeviceRGB()
    func rgba(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat) -> CGColor {
        CGColor(colorSpace: space, components: [r, g, b, a])!
    }
    func gradient(_ stops: [(CGFloat, CGColor)]) -> CGGradient {
        CGGradient(colorsSpace: space, colors: stops.map { $0.1 } as CFArray, locations: stops.map { $0.0 })!
    }
    // Soft glow: a radial gradient squashed into an ellipse rx wide and ry tall.
    func glow(rx: CGFloat, ry: CGFloat, _ stops: [(CGFloat, CGColor)]) {
        cg.saveGState()
        cg.translateBy(x: cx, y: cy)
        cg.scaleBy(x: 1, y: ry / rx)
        cg.drawRadialGradient(gradient(stops), startCenter: .zero, startRadius: 0,
                              endCenter: .zero, endRadius: rx, options: [])
        cg.restoreGState()
    }
    let blue = (r: CGFloat(0.13), g: CGFloat(0.45), b: CGFloat(1.0))

    // Faint wide haze, then a tighter glow around the line.
    glow(rx: w * 0.45, ry: w * 0.11, [(0, rgba(blue.r, blue.g, blue.b, 0.30)), (1, rgba(blue.r, blue.g, blue.b, 0))])
    glow(rx: w * 0.24, ry: w * 0.05, [(0, rgba(blue.r, blue.g, blue.b, 0.55)), (1, rgba(blue.r, blue.g, blue.b, 0))])

    // The line: a lens shape tapering to points, white at the center and blue toward the tips.
    // Small sizes get a thicker, longer line so it still reads as a line rather than a dot.
    let half = max(w * 0.0065, s <= 32 ? 0.9 : 0.5)
    let reach = w * (s <= 32 ? 0.40 : 0.36)
    let lens = CGMutablePath()
    lens.move(to: CGPoint(x: cx - reach, y: cy))
    lens.addQuadCurve(to: CGPoint(x: cx + reach, y: cy), control: CGPoint(x: cx, y: cy + 2 * half))
    lens.addQuadCurve(to: CGPoint(x: cx - reach, y: cy), control: CGPoint(x: cx, y: cy - 2 * half))
    cg.saveGState()
    cg.addPath(lens)
    cg.clip()
    cg.drawLinearGradient(gradient([
        (0.00, rgba(blue.r, blue.g, blue.b, 0)),
        (0.18, rgba(blue.r, blue.g, blue.b, 0.95)),
        (0.40, rgba(0.55, 0.78, 1.0, 1)),
        (0.50, rgba(1, 1, 1, 1)),
        (0.60, rgba(0.55, 0.78, 1.0, 1)),
        (0.82, rgba(blue.r, blue.g, blue.b, 0.95)),
        (1.00, rgba(blue.r, blue.g, blue.b, 0)),
    ]), start: CGPoint(x: cx - reach, y: cy), end: CGPoint(x: cx + reach, y: cy), options: [])
    cg.restoreGState()

    // Hot white core where the last of the picture collapses.
    glow(rx: max(w * 0.13, 2), ry: max(w * 0.045, 1.2), [
        (0.00, rgba(1, 1, 1, 1)),
        (0.30, rgba(0.75, 0.88, 1.0, 0.9)),
        (0.60, rgba(0.30, 0.60, 1.0, 0.45)),
        (1.00, rgba(blue.r, blue.g, blue.b, 0)),
    ])
    glow(rx: max(w * 0.05, 1), ry: max(w * 0.018, 0.8), [(0, rgba(1, 1, 1, 1)), (0.6, rgba(1, 1, 1, 0.9)), (1, rgba(1, 1, 1, 0))])

    NSGraphicsContext.restoreGraphicsState()
    return rep
}

func png(_ px: Int) -> Data { render(px).representation(using: .png, properties: [:])! }

let args = CommandLine.arguments
if args.count == 3 && args[1] == "--ico" {
    // PNG-compressed .ico with the sizes Windows uses for the taskbar, Explorer and shortcuts.
    let sizes = [16, 24, 32, 48, 64, 256]
    let images = sizes.map(png)
    var ico = Data()
    func u16(_ v: Int) { ico.append(contentsOf: [UInt8(v & 0xff), UInt8(v >> 8 & 0xff)]) }
    func u32(_ v: Int) { u16(v & 0xffff); u16(v >> 16) }
    u16(0); u16(1); u16(sizes.count)
    var offset = 6 + 16 * sizes.count
    for (size, image) in zip(sizes, images) {
        ico.append(contentsOf: [UInt8(size & 0xff), UInt8(size & 0xff), 0, 0])  // 256 is stored as 0
        u16(1); u16(32); u32(image.count); u32(offset)
        offset += image.count
    }
    images.forEach { ico.append($0) }
    try! ico.write(to: URL(fileURLWithPath: args[2]))
} else {
    let dir = args[1]
    try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
    for base in [16, 32, 128, 256, 512] {
        for scale in [1, 2] {
            let name = scale == 1 ? "icon_\(base)x\(base).png" : "icon_\(base)x\(base)@2x.png"
            try! png(base * scale).write(to: URL(fileURLWithPath: "\(dir)/\(name)"))
        }
    }
}
