import Foundation
import SwiftUI

private struct Playback: Decodable {
    struct Target: Decodable, Hashable {
        let id: String
        let transport: Int
        let route: String
    }
    let target: Target
    let position: Int
    let duration: Int
    let playing: Bool
}

private struct Snapshot: Decodable {
    let connection: String
    let playback: Playback?

    static func request(_ arguments: [String] = ["status"]) async -> Snapshot {
        await Task.detached {
            let (process, pipe) = pythonProcess("transport", arguments: arguments)
            do { try process.run() } catch { return Snapshot(connection: error.localizedDescription, playback: nil) }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            if process.terminationStatus == 0, let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) {
                return snapshot
            }
            return Snapshot(connection: String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines),
                            playback: nil)
        }.value
    }
}

@MainActor
private final class TimelineModel: ObservableObject {
    @Published var snapshot = Snapshot(connection: "Checking connection…", playback: nil)
    @Published var editing: Playback.Target?
    @Published var draft = 0.0
    @Published var commanding = false
    private var revision = 0

    func control(_ command: String, target: Playback.Target, position: Int? = nil) {
        guard snapshot.playback?.target == target, !commanding else { return }
        revision += 1
        commanding = true
        var args = [command, "--video", target.id, "--transport", String(target.transport), "--route", target.route]
        if let position { args += ["--position", String(position)] }
        Task {
            snapshot = await Snapshot.request(args)
            commanding = false
        }
    }

    var displayed: Double {
        if let editing, editing == snapshot.playback?.target { return draft }
        return Double(snapshot.playback?.position ?? 0)
    }

    func poll() async {
        while !Task.isCancelled {
            if !commanding {
                // A poll started before a seek or play/pause command can return after it.
                let startRevision = revision
                let next = await Snapshot.request()
                if !Task.isCancelled && startRevision == revision {
                    if next.playback?.target != snapshot.playback?.target { editing = nil }
                    snapshot = next
                }
            }
            try? await Task.sleep(nanoseconds: snapshot.playback == nil ? 2_000_000_000 : 1_000_000_000)
        }
    }
}

struct PlaybackTimeline: View {
    @StateObject private var model = TimelineModel()

    private func time(_ milliseconds: Int) -> String {
        let seconds = milliseconds / 1000
        if seconds >= 3600 {
            return String(format: "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
        }
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Phone playback").font(.headline)
                Spacer()
                if let playback = model.snapshot.playback {
                    Text("\(time(Int(model.displayed))) / \(time(playback.duration))").monospacedDigit()
                }
            }
            Text(model.snapshot.connection).font(.callout).foregroundStyle(.secondary)
            if let playback = model.snapshot.playback {
                HStack {
                    Button(playback.playing ? "Pause" : "Play") {
                        model.control(playback.playing ? "pause" : "play", target: playback.target)
                    }
                    .frame(minWidth: 55)
                    Slider(value: Binding(get: { model.displayed }, set: { value in
                        model.draft = value
                        if model.editing == nil {
                            model.control("seek", target: playback.target, position: min(Int(value), playback.duration - 1))
                        }
                    }), in: 0...Double(playback.duration), onEditingChanged: { editing in
                        if editing {
                            model.editing = playback.target
                            model.draft = Double(playback.position)
                        } else if let target = model.editing {
                            model.editing = nil
                            model.control("seek", target: target, position: min(Int(model.draft), playback.duration - 1))
                        }
                    })
                    .id(playback.target)
                    .accessibilityLabel("Playback position")
                    .accessibilityValue(time(Int(model.displayed)))
                }
                .disabled(model.commanding)
            } else {
                Text("Open a video in Nachim on your connected phone.").foregroundStyle(.secondary)
            }
        }
        .task { await model.poll() }
    }
}
