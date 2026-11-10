import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("Usage: x-vault-ocr IMAGE\n".utf8))
    exit(2)
}

do {
    let image = URL(fileURLWithPath: CommandLine.arguments[1])
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.automaticallyDetectsLanguage = true
    try VNImageRequestHandler(url: image).perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    try FileHandle.standardOutput.write(contentsOf: JSONEncoder().encode(lines))
} catch {
    FileHandle.standardError.write(Data("Apple Vision OCR failed: \(error.localizedDescription)\n".utf8))
    exit(1)
}
