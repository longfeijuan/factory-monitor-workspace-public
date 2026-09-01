import Foundation
import Vision

// Run the built-in human detector on overlapping crops. People in this camera
// are small because of the fisheye view, so a single full-frame request misses
// operators standing beside the five machines.
let regions: [(String, CGRect)] = [
    ("full", CGRect(x: 0, y: 0, width: 1, height: 1)),
    ("left", CGRect(x: 0, y: 0, width: 0.62, height: 1)),
    ("right", CGRect(x: 0.38, y: 0, width: 0.62, height: 1)),
    ("upper", CGRect(x: 0, y: 0.32, width: 1, height: 0.68)),
    ("lower", CGRect(x: 0, y: 0, width: 1, height: 0.68)),
    ("center", CGRect(x: 0.18, y: 0.10, width: 0.64, height: 0.80)),
]

for rawPath in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: rawPath)
    var hits: [[String: Any]] = []
    for (name, region) in regions {
        let request = VNDetectHumanRectanglesRequest()
        request.upperBodyOnly = true
        request.regionOfInterest = region
        let handler = VNImageRequestHandler(url: url, options: [:])
        do {
            try handler.perform([request])
            for observation in request.results ?? [] {
                let box = observation.boundingBox
                hits.append([
                    "region": name,
                    "confidence": observation.confidence,
                    "box": [box.origin.x, box.origin.y, box.size.width, box.size.height],
                ])
            }
        } catch {
            hits.append(["region": name, "error": error.localizedDescription])
        }
    }
    let data = try JSONSerialization.data(withJSONObject: [
        "path": rawPath,
        "count": hits.count,
        "hits": hits,
    ])
    print(String(decoding: data, as: UTF8.self))
}
