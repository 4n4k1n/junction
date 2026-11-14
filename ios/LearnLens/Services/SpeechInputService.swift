//
// SpeechInputService.swift
//
// Push-to-talk on the iPhone microphone (SFSpeechRecognizer). The typed text field is the
// fallback when speech is unavailable or not authorized.
//

import AVFoundation
import Observation
import Speech

enum SpeechInputError: LocalizedError {
  case notAuthorized
  case unavailable
  case noMicrophone

  var errorDescription: String? {
    switch self {
    case .notAuthorized: "Speech or microphone access was not granted. Type instead."
    case .unavailable: "Speech recognition is not available right now. Type instead."
    case .noMicrophone: "No microphone input available. Type instead."
    }
  }
}

@Observable
@MainActor
final class SpeechInputService {
  private(set) var transcript = ""
  private(set) var isRecording = false
  var errorMessage: String?

  @ObservationIgnored private let recognizer = SFSpeechRecognizer()
  @ObservationIgnored private let engine = AVAudioEngine()
  @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
  @ObservationIgnored private var task: SFSpeechRecognitionTask?
  @ObservationIgnored private var tapInstalled = false

  func start() async {
    guard !isRecording else { return }
    errorMessage = nil
    transcript = ""

    do {
      guard await Self.requestAuthorization() else { throw SpeechInputError.notAuthorized }
      guard let recognizer, recognizer.isAvailable else { throw SpeechInputError.unavailable }

      #if os(iOS)
      let audioSession = AVAudioSession.sharedInstance()
      try audioSession.setCategory(.record, mode: .measurement, options: .duckOthers)
      try audioSession.setActive(true, options: .notifyOthersOnDeactivation)
      #endif

      let request = SFSpeechAudioBufferRecognitionRequest()
      request.shouldReportPartialResults = true
      self.request = request

      task = Self.recognize(with: recognizer, request: request) { [weak self] text, finished in
        Task { @MainActor [weak self] in
          guard let self else { return }
          if let text { self.transcript = text }
          if finished { self.stop() }
        }
      }
      try Self.installTap(on: engine, feeding: request)
      tapInstalled = true
      engine.prepare()
      try engine.start()
      isRecording = true
    } catch {
      stop()
      errorMessage = error.localizedDescription
    }
  }

  func stop() {
    if engine.isRunning {
      engine.stop()
    }
    if tapInstalled {
      engine.inputNode.removeTap(onBus: 0)
      tapInstalled = false
    }
    request?.endAudio()
    request = nil
    task = nil
    isRecording = false
  }

  // The helpers below are nonisolated on purpose: their callbacks run on audio and speech
  // threads and must not inherit this class's main-actor isolation.

  nonisolated private static func requestAuthorization() async -> Bool {
    let speechAllowed = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
      SFSpeechRecognizer.requestAuthorization { status in
        continuation.resume(returning: status == .authorized)
      }
    }
    guard speechAllowed else { return false }
    #if os(iOS)
    return await AVAudioApplication.requestRecordPermission()
    #else
    return true
    #endif
  }

  nonisolated private static func recognize(
    with recognizer: SFSpeechRecognizer,
    request: SFSpeechAudioBufferRecognitionRequest,
    onUpdate: @escaping @Sendable (String?, Bool) -> Void
  ) -> SFSpeechRecognitionTask {
    recognizer.recognitionTask(with: request) { result, error in
      let finished = error != nil || (result?.isFinal ?? false)
      onUpdate(result?.bestTranscription.formattedString, finished)
    }
  }

  nonisolated private static func installTap(
    on engine: AVAudioEngine,
    feeding request: SFSpeechAudioBufferRecognitionRequest
  ) throws {
    let input = engine.inputNode
    let format = input.outputFormat(forBus: 0)
    // The iOS Simulator can report an empty format when the Mac has no input device.
    guard format.sampleRate > 0, format.channelCount > 0 else { throw SpeechInputError.noMicrophone }
    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
      request.append(buffer)
    }
  }
}
