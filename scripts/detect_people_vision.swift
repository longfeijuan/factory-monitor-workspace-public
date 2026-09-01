#!/usr/bin/swift
import Foundation
import Vision
import ImageIO

func detect(_ path: String) -> [[String: Double]] {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let source = CGImageSourceCreateWithURL(url, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        return []
    }
    let request = VNDetectHumanRectanglesRequest()
    request.upperBodyOnly = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return []
    }
    return (request.results ?? []).map { observation in
        let box = observation.boundingBox
        return [
            "confidence": Double(observation.confidence),
            "x": box.origin.x,
            "y": box.origin.y,
            "w": box.size.width,
            "h": box.size.height,
        ]
    }
}

let encoder = JSONEncoder()
for path in CommandLine.arguments.dropFirst() {
    let payload: [String: Any] = ["image": path, "people": detect(path)]
    if let data = try? JSONSerialization.data(withJSONObject: payload),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}
