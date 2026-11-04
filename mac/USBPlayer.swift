import AppKit
import SwiftUI

private let toolEnvironment: [String: String] = {
    var env = ProcessInfo.processInfo.environment
    let sdk = env["ANDROID_HOME"] ?? NSHomeDirectory() + "/Library/Android/sdk"
    env["PATH"] = [env["PATH"] ?? "/usr/bin:/bin", "/opt/homebrew/bin", "/usr/local/bin",
                   sdk + "/platform-tools"].joined(separator: ":")
    return env
}()

func pythonProcess(_ script: String, arguments: [String]) -> (Process, Pipe) {
    let process = Process()
    let output = Pipe()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    process.arguments = ["-B", Bundle.main.url(forResource: script, withExtension: "py")!.path] + arguments
    process.environment = toolEnvironment
    process.standardOutput = output
    process.standardError = output
    return (process, output)
}

private struct Update: Decodable {
    let label: String
    let percent: Double?
}

@MainActor
private final class Sender: ObservableObject {
    static let shared = Sender()
    @Published var link = ""
    @Published var status = Update(label: "Ready", percent: nil)
    @Published var busy = false
    @Published var failed = false

    func submit(captions: String, size: Int, apply: Bool = false) {
        let input = link.trimmingCharacters(in: .whitespacesAndNewlines)
        guard apply || !input.isEmpty else { return }
        run("send", ["--captions", captions, "--size", String(size)] + (apply ? ["--apply"] : []) + ["--", input])
    }

    func run(_ helper: String, _ arguments: [String]) {
        guard !busy else { return }
        busy = true
        failed = false
        status = Update(label: "Starting…", percent: nil)
        Task {
            defer { busy = false }
            let (worker, output) = pythonProcess(helper, arguments: arguments)
            do {
                try worker.run()
                for try await line in output.fileHandleForReading.bytes.lines {
                    status = (try? JSONDecoder().decode(Update.self, from: Data(line.utf8)))
                        ?? Update(label: line, percent: nil)
                }
                worker.waitUntilExit()
                failed = worker.terminationStatus != 0
            } catch {
                failed = true
                status = Update(label: error.localizedDescription, percent: nil)
            }
        }
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Command-Tab can show a generic icon even when Finder displays the bundle icon.
        if let icon = Bundle.main.image(forResource: "FrameShift") {
            NSApp.applicationIconImage = icon
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard Sender.shared.busy else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Nachim is busy"
        alert.informativeText = "Wait for the current operation to finish before quitting."
        alert.runModal()
        return .terminateCancel
    }
}

private struct Content: View {
    private static let captionLanguages: [(name: String, code: String)] = [
        ("English", "en"),
        ("Mandarin Chinese", "zh-Hans"),
        ("Hindi", "hi"),
        ("Spanish", "es"),
        ("Standard Arabic", "ar"),
        ("French", "fr"),
        ("Bengali", "bn"),
        ("Portuguese", "pt"),
        ("Indonesian", "id"),
        ("Urdu", "ur"),
        ("Russian", "ru"),
        ("German", "de"),
        ("Japanese", "ja"),
        ("Nigerian Pidgin", "pcm"),
        ("Egyptian Arabic", "arz"),
        ("Marathi", "mr"),
        ("Vietnamese", "vi"),
        ("Telugu", "te"),
        ("Swahili", "sw"),
        ("Hausa", "ha"),
    ]

    @ObservedObject var sender: Sender
    @AppStorage("captionChoice") private var choice = "off"
    @AppStorage("captionLanguage") private var language = ""
    @AppStorage("captionSize") private var size = 22

    private var captions: String {
        choice == "other" ? language.trimmingCharacters(in: .whitespacesAndNewlines) : choice
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Send to Android", systemImage: "iphone.and.arrow.forward.outward")
                .font(.title2.weight(.semibold))
            DisclosureGroup("Phone hotspot") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("For setup, plug in your unlocked phone and allow USB debugging. Turn on its password-protected hotspot and join it from this Mac.")
                    HStack {
                        Button("Set up hotspot") { sender.run("transport", ["setup"]) }
                        Button("Turn Off") { sender.run("transport", ["disable"]) }
                    }
                    Text("Send uses USB when plugged in and reconnects over the hotspot when unplugged. Turn Off closes network debugging.")
                        .foregroundStyle(.secondary)
                }
                .font(.callout)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .disabled(sender.busy)
            HStack {
                TextField("YouTube URL", text: $sender.link)
                    .textFieldStyle(.roundedBorder)
                    .disabled(sender.busy)
                Button("Send") { sender.submit(captions: captions, size: size) }
                    .keyboardShortcut(.defaultAction)
                    .disabled(sender.busy || sender.link.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Picker("YouTube captions", selection: $choice) {
                        Text("Off").tag("off")
                        Text("Original language").tag("original")
                        ForEach(Self.captionLanguages, id: \.code) { language in
                            Text(language.name).tag(language.code)
                        }
                        Text("Other language…").tag("other")
                    }
                    if choice == "other" {
                        TextField("Code, e.g. es", text: $language).frame(width: 95)
                    }
                }
                HStack {
                    Stepper("Text size  \(size) sp", value: $size, in: 12...48, step: 2)
                        .fixedSize()
                        .disabled(choice == "off")
                    Spacer()
                    Button("Apply to current video") {
                        sender.submit(captions: captions, size: size, apply: true)
                    }
                }
            }
            .disabled(sender.busy)
            Divider()
            PlaybackTimeline()
            Divider()
            if let percent = sender.status.percent {
                ProgressView(value: percent, total: 100)
                Text("\(sender.status.label)  \(Int(percent))%")
                    .monospacedDigit()
            } else {
                HStack(alignment: .center) {
                    if sender.busy { ProgressView().controlSize(.small) }
                    Text(sender.status.label)
                        .foregroundStyle(sender.failed ? Color.red : Color.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .padding(24)
        .frame(width: 510, alignment: .leading)
    }
}

@main
struct USBPlayer: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        Window("Nachim", id: "main") {
            Content(sender: Sender.shared)
        }
        .windowResizability(.contentSize)
    }
}
