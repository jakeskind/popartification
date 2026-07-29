// Vision feature extractor for the art matcher.
//
// Reads a text file of image paths (one per line), and for each image emits:
//   - the Vision feature-print vector (Apple's on-device image embedding —
//     captures overall structure/content; the same tech Photos uses for
//     similarity search)
//   - detected human-figure bounding boxes (normalized), for figure-layout
//     scoring
//
// Output: one JSON object per line: {"path": ..., "v": [...], "figures": [[x,y,w,h],...]}
// Failures emit {"path": ..., "error": "..."} and processing continues.
//
// Build: swiftc -O Scripts/artmatch_vision.swift -o <out>
// Run:   <out> /path/to/list.txt

import Foundation
import Vision

guard CommandLine.arguments.count >= 2,
      let listData = FileManager.default.contents(atPath: CommandLine.arguments[1]),
      let list = String(data: listData, encoding: .utf8) else {
    FileHandle.standardError.write("usage: artmatch_vision <paths.txt>\n".data(using: .utf8)!)
    exit(1)
}

let paths = list.split(separator: "\n").map(String.init).filter { !$0.isEmpty }

for path in paths {
    autoreleasepool {
        let url = URL(fileURLWithPath: path)
        let handler = VNImageRequestHandler(url: url)
        let featureRequest = VNGenerateImageFeaturePrintRequest()
        // Faces, not bodies, for the "figures" layout signal: Vision's face
        // detection works remarkably well on paintings. Includes head pose
        // (yaw/roll/pitch) so gaze direction can be matched.
        let faceRequest = VNDetectFaceRectanglesRequest()
        // Full body-pose skeletons — the exactness signal: limb angles are
        // what make a pairing read as "literally the same pose".
        let poseRequest = VNDetectHumanBodyPoseRequest()

        var output: [String: Any] = ["path": path]
        do {
            try handler.perform([featureRequest, faceRequest, poseRequest])
            if let observation = featureRequest.results?.first {
                let count = observation.elementCount
                var floats = [Float](repeating: 0, count: count)
                observation.data.withUnsafeBytes { raw in
                    let buffer = raw.bindMemory(to: Float.self)
                    for i in 0..<count { floats[i] = buffer[i] }
                }
                output["v"] = floats
            }
            let figures = (faceRequest.results ?? []).map { obs -> [Double] in
                let b = obs.boundingBox
                return [Double(b.origin.x), Double(b.origin.y),
                        Double(b.size.width), Double(b.size.height),
                        obs.yaw?.doubleValue ?? 0,
                        obs.roll?.doubleValue ?? 0,
                        obs.pitch?.doubleValue ?? 0]
            }
            output["figures"] = figures
            let poses = (poseRequest.results ?? []).map { obs -> [String: [Double]] in
                var joints: [String: [Double]] = [:]
                if let points = try? obs.recognizedPoints(.all) {
                    for (name, point) in points where point.confidence > 0.25 {
                        joints[name.rawValue.rawValue] =
                            [Double(point.location.x), Double(point.location.y),
                             Double(point.confidence)]
                    }
                }
                return joints
            }
            output["poses"] = poses
        } catch {
            output["error"] = "\(error)"
        }

        if let json = try? JSONSerialization.data(withJSONObject: output),
           let line = String(data: json, encoding: .utf8) {
            print(line)
        }
    }
}
