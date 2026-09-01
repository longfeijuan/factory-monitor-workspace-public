import Foundation
import Vision

for rawPath in CommandLine.arguments.dropFirst() {
    let url = URL(fileURLWithPath: rawPath)
    let request = VNDetectHumanRectanglesRequest()
    let handler = VNImageRequestHandler(url: url, options: [:])
    do {
        try handler.perform([request])
        let boxes = (request.results ?? []).map { observation in
            let box = observation.boundingBox
            return [box.origin.x, box.origin.y, box.size.width, box.size.height]
        }
        let data = try JSONSerialization.data(withJSONObject: ["path": rawPath, "count": boxes.count, "boxes": boxes])
        print(String(decoding: data, as: UTF8.self))
    } catch {
        print("{\"path\":\"\(rawPath)\",\"error\":\"\(error.localizedDescription)\"}")
    }
}
