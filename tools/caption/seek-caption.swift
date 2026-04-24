#!/usr/bin/env swift
// seek-caption: Extract text and scene labels from images via Vision framework.
// Usage: seek-caption <path1> [path2 ...]
// Output: one JSON line per path to stdout; errors are JSON with "error" key.
import Foundation
import Vision
import AppKit

func caption(path: String) -> [String: String] {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else {
        return ["path": path, "error": "cannot load image"]
    }

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    var parts: [String] = []

    let ocrReq = VNRecognizeTextRequest()
    ocrReq.recognitionLevel = .accurate
    ocrReq.usesLanguageCorrection = true
    try? handler.perform([ocrReq])
    let ocrText = (ocrReq.results ?? [])
        .compactMap { $0.topCandidates(1).first?.string }
        .joined(separator: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    if !ocrText.isEmpty { parts.append("text: \(ocrText)") }

    let classReq = VNClassifyImageRequest()
    try? handler.perform([classReq])
    let labels = (classReq.results ?? [])
        .filter { $0.confidence > 0.1 }
        .prefix(8)
        .map(\.identifier)
        .joined(separator: ", ")
    if !labels.isEmpty { parts.append("labels: \(labels)") }

    let caption = parts.isEmpty ? "image" : parts.joined(separator: " | ")
    return ["path": path, "caption": caption]
}

let paths = CommandLine.arguments.dropFirst()
guard !paths.isEmpty else {
    fputs("Usage: seek-caption <path1> [path2 ...]\n", stderr)
    exit(1)
}

for path in paths {
    let result = caption(path: path)
    if let data = try? JSONSerialization.data(withJSONObject: result),
       let line = String(data: data, encoding: .utf8) {
        print(line)
    }
}
