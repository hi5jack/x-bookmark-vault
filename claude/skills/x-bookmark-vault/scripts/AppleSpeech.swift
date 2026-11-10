import AVFoundation
import Foundation
import Speech

@main
struct AppleSpeech {
    struct Segment: Encodable {
        let start: Double
        let end: Double
        let text: String
    }

    static func main() async {
        guard CommandLine.arguments.count == 3 else {
            FileHandle.standardError.write(Data("Usage: x-vault-speech audio.wav locale\n".utf8))
            exit(2)
        }

        do {
            let url = URL(fileURLWithPath: CommandLine.arguments[1])
            let requested = Locale(identifier: CommandLine.arguments[2])
            guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: requested) else {
                FileHandle.standardError.write(Data("Apple Speech does not support this locale.\n".utf8))
                exit(2)
            }
            let transcriber = SpeechTranscriber(locale: locale, preset: .transcription)
            if let install = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await install.downloadAndInstall()
            }

            let file = try AVAudioFile(forReading: url)
            let analyzer = SpeechAnalyzer(modules: [transcriber])
            async let segments = readFinalSegments(transcriber)
            if let lastSample = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: lastSample)
            } else {
                await analyzer.cancelAndFinishNow()
            }
            try FileHandle.standardOutput.write(contentsOf: JSONEncoder().encode(await segments))
        } catch {
            FileHandle.standardError.write(Data("Apple Speech failed: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }

    static func readFinalSegments(_ transcriber: SpeechTranscriber) async throws -> [Segment] {
        var segments: [Segment] = []
        for try await result in transcriber.results where result.isFinal {
            let start = result.range.start.seconds
            let end = result.range.end.seconds
            let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
            if start.isFinite && end.isFinite && !text.isEmpty {
                segments.append(Segment(start: start, end: end, text: text))
            }
        }
        return segments
    }
}
