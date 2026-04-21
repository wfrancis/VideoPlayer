import AppKit

let outputPath = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "/Users/william/video-player-build/AppIcon-1024.png"

let canvas = NSSize(width: 1024, height: 1024)
let image = NSImage(size: canvas)

image.lockFocus()

guard let ctx = NSGraphicsContext.current?.cgContext else {
    fatalError("Could not create graphics context")
}

ctx.setAllowsAntialiasing(true)
ctx.setShouldAntialias(true)
ctx.interpolationQuality = .high

let clearRect = CGRect(origin: .zero, size: canvas)
NSColor.clear.setFill()
clearRect.fill()

let baseRect = CGRect(x: 74, y: 74, width: 876, height: 876)
let basePath = NSBezierPath(roundedRect: baseRect, xRadius: 198, yRadius: 198)

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -20), blur: 48, color: NSColor(calibratedWhite: 0, alpha: 0.22).cgColor)
NSColor.black.setFill()
basePath.fill()
ctx.restoreGState()

ctx.saveGState()
basePath.addClip()

let baseGradient = NSGradient(colors: [
    NSColor(calibratedRed: 0.35, green: 0.45, blue: 0.63, alpha: 1.0),
    NSColor(calibratedRed: 0.12, green: 0.18, blue: 0.34, alpha: 1.0),
    NSColor(calibratedRed: 0.04, green: 0.52, blue: 1.0, alpha: 1.0),
])!
baseGradient.draw(in: baseRect, angle: -72)

let topGlowRect = CGRect(x: 120, y: 520, width: 760, height: 360)
let glowGradient = NSGradient(colorsAndLocations:
    (NSColor(calibratedWhite: 1.0, alpha: 0.22), 0.0),
    (NSColor(calibratedWhite: 1.0, alpha: 0.0), 1.0)
)!
glowGradient.draw(in: topGlowRect, relativeCenterPosition: NSPoint(x: 0.0, y: 0.3))

let lowerShadeRect = CGRect(x: 120, y: 120, width: 780, height: 260)
let lowerShade = NSGradient(colorsAndLocations:
    (NSColor(calibratedWhite: 0.0, alpha: 0.20), 0.0),
    (NSColor(calibratedWhite: 0.0, alpha: 0.0), 1.0)
)!
lowerShade.draw(in: lowerShadeRect, angle: 90)

ctx.restoreGState()

NSColor(calibratedWhite: 1.0, alpha: 0.10).setStroke()
basePath.lineWidth = 6
basePath.stroke()

let panelRect = CGRect(x: 182, y: 210, width: 660, height: 604)
let panelPath = NSBezierPath(roundedRect: panelRect, xRadius: 118, yRadius: 118)

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 22, color: NSColor(calibratedWhite: 0.0, alpha: 0.18).cgColor)
NSColor(calibratedWhite: 0.02, alpha: 0.34).setFill()
panelPath.fill()
ctx.restoreGState()

let panelGradient = NSGradient(colors: [
    NSColor(calibratedWhite: 0.10, alpha: 0.74),
    NSColor(calibratedWhite: 0.03, alpha: 0.82),
])!
panelGradient.draw(in: panelPath, angle: 90)

NSColor(calibratedWhite: 1.0, alpha: 0.12).setStroke()
panelPath.lineWidth = 4
panelPath.stroke()

let playCircleRect = CGRect(x: 314, y: 354, width: 396, height: 396)
let playCirclePath = NSBezierPath(ovalIn: playCircleRect)

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -14), blur: 28, color: NSColor(calibratedRed: 0.03, green: 0.41, blue: 0.88, alpha: 0.40).cgColor)
let playCircleGradient = NSGradient(colors: [
    NSColor(calibratedRed: 0.12, green: 0.62, blue: 1.0, alpha: 1.0),
    NSColor(calibratedRed: 0.03, green: 0.49, blue: 0.98, alpha: 1.0),
])!
playCircleGradient.draw(in: playCirclePath, angle: 90)
ctx.restoreGState()

NSColor(calibratedWhite: 1.0, alpha: 0.18).setStroke()
playCirclePath.lineWidth = 5
playCirclePath.stroke()

let triangle = NSBezierPath()
triangle.move(to: NSPoint(x: 455, y: 634))
triangle.line(to: NSPoint(x: 455, y: 470))
triangle.line(to: NSPoint(x: 596, y: 552))
triangle.close()

ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -6), blur: 10, color: NSColor(calibratedWhite: 0.0, alpha: 0.18).cgColor)
NSColor.white.setFill()
triangle.fill()
ctx.restoreGState()

let trackRect = CGRect(x: 312, y: 246, width: 400, height: 48)
let trackPath = NSBezierPath(roundedRect: trackRect, xRadius: 24, yRadius: 24)
NSColor(calibratedWhite: 1.0, alpha: 0.12).setFill()
trackPath.fill()

let progressRect = CGRect(x: 330, y: 258, width: 230, height: 24)
let progressPath = NSBezierPath(roundedRect: progressRect, xRadius: 12, yRadius: 12)
let progressGradient = NSGradient(colors: [
    NSColor(calibratedRed: 0.10, green: 0.62, blue: 1.0, alpha: 1.0),
    NSColor(calibratedRed: 0.44, green: 0.82, blue: 1.0, alpha: 1.0),
])!
progressGradient.draw(in: progressPath, angle: 0)

let knobRect = CGRect(x: 540, y: 240, width: 56, height: 56)
let knobPath = NSBezierPath(ovalIn: knobRect)
ctx.saveGState()
ctx.setShadow(offset: CGSize(width: 0, height: -4), blur: 12, color: NSColor(calibratedWhite: 0.0, alpha: 0.20).cgColor)
NSColor.white.setFill()
knobPath.fill()
ctx.restoreGState()

NSColor(calibratedRed: 0.16, green: 0.61, blue: 1.0, alpha: 0.95).setStroke()
knobPath.lineWidth = 4
knobPath.stroke()

image.unlockFocus()

guard
    let tiff = image.tiffRepresentation,
    let rep = NSBitmapImageRep(data: tiff),
    let png = rep.representation(using: .png, properties: [:])
else {
    fatalError("Could not encode PNG")
}

try png.write(to: URL(fileURLWithPath: outputPath))
