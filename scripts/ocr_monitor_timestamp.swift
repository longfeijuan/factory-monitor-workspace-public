#!/usr/bin/swift
import Foundation
import ImageIO
import Vision

struct TextObservation: Codable {
    let text: String
    let confidence: Float
    let x: Int
    let y: Int
    let width: Int
    let height: Int
}

func recognize(_ path: String) -> [TextObservation] {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let source = CGImageSourceCreateWithURL(url, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        return []
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return []
    }
    let imageWidth = CGFloat(image.width)
    let imageHeight = CGFloat(image.height)
    return (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return TextObservation(
            text: candidate.string,
            confidence: candidate.confidence,
            x: Int(box.origin.x * imageWidth),
            y: Int((1 - box.origin.y - box.size.height) * imageHeight),
            width: Int(box.size.width * imageWidth),
            height: Int(box.size.height * imageHeight)
        )
    }
}

let encoder = JSONEncoder()
encoder.outputFormatting = [.sortedKeys]
for path in CommandLine.arguments.dropFirst() {
    let payload: [String: Any] = [
        "image": path,
        "items": recognize(path).map { observation in
            [
                "text": observation.text,
                "confidence": observation.confidence,
                "x": observation.x,
                "y": observation.y,
                "width": observation.width,
                "height": observation.height,
            ] as [String: Any]
        },
    ]
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}
