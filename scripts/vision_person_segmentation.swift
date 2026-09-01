import CoreVideo
import Foundation
import Vision

let maskOutputDir = ProcessInfo.processInfo.environment["MASK_OUTPUT_DIR"]
if let maskOutputDir {
    try? FileManager.default.createDirectory(atPath: maskOutputDir, withIntermediateDirectories: true)
}

for rawPath in CommandLine.arguments.dropFirst() {
    let request = VNGeneratePersonSegmentationRequest()
    switch ProcessInfo.processInfo.environment["VISION_QUALITY"] {
    case "fast": request.qualityLevel = .fast
    case "balanced": request.qualityLevel = .balanced
    default: request.qualityLevel = .accurate
    }
    request.outputPixelFormat = kCVPixelFormatType_OneComponent8
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: rawPath), options: [:])
    do {
        try handler.perform([request])
        guard let observation = request.results?.first else {
            print("{\"path\":\"\(rawPath)\",\"pixels\":0,\"ratio\":0}")
            continue
        }
        let buffer = observation.pixelBuffer
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { continue }
        let bytes = base.assumingMemoryBound(to: UInt8.self)
        var pixels = 0
        var minX = width, minY = height, maxX = -1, maxY = -1
        for y in 0..<height {
            let row = bytes.advanced(by: y * stride)
            for x in 0..<width where row[x] >= 96 {
                pixels += 1
                minX = min(minX, x); minY = min(minY, y)
                maxX = max(maxX, x); maxY = max(maxY, y)
            }
        }
        let ratio = Double(pixels) / Double(width * height)
        let bbox: [Double] = pixels > 0 ? [
            Double(minX) / Double(width), Double(minY) / Double(height),
            Double(maxX + 1) / Double(width), Double(maxY + 1) / Double(height),
        ] : []
        var maskPath = ""
        if pixels > 0, let maskOutputDir {
            let stem = URL(fileURLWithPath: rawPath).deletingPathExtension().lastPathComponent
            maskPath = URL(fileURLWithPath: maskOutputDir).appendingPathComponent(stem + ".pgm").path
            var pgm = Data("P5\n\(width) \(height)\n255\n".utf8)
            for y in 0..<height {
                pgm.append(bytes.advanced(by: y * stride), count: width)
            }
            try pgm.write(to: URL(fileURLWithPath: maskPath))
        }
        let data = try JSONSerialization.data(withJSONObject: [
            "path": rawPath, "pixels": pixels, "ratio": ratio,
            "maskWidth": width, "maskHeight": height, "bbox": bbox, "maskPath": maskPath,
        ])
        print(String(decoding: data, as: UTF8.self))
    } catch {
        print("{\"path\":\"\(rawPath)\",\"error\":\"\(error.localizedDescription)\"}")
    }
}
