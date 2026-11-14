//
// GlassesSpeechService.swift
//
// Voice input through the glasses microphone, using the SDK's on-device speech recognition
// (MWDATSpeech, experimental in SDK 1.0). When it is not available, SessionViewModel falls back
// to the iPhone microphone (SpeechInputService).
//

import Foundation
import MWDATCore
import MWDATSpeech
import Observation

@Observable
@MainActor
final class GlassesSpeechService {
  private(set) var transcript = ""
  private(set) var isListening = false
  var errorMessage: String?

  @ObservationIgnored private let wearables: any WearablesInterface
  @ObservationIgnored private var speech: Speech?
  @ObservationIgnored private weak var attachedSession: DeviceSession?
  @ObservationIgnored private let tokens = ListenerTokenBag()
  @ObservationIgnored private var onFinal: (@MainActor (String) -> Void)?
  @ObservationIgnored private var timeoutTask: Task<Void, Never>?

  init(wearables: any WearablesInterface) {
    self.wearables = wearables
  }

  /// Starts listening on the glasses. Returns false when glasses speech cannot be used, so the
  /// caller can fall back to the phone. `onFinal` gets the recognized sentence.
  func start(on session: DeviceSession, onFinal: @escaping @MainActor (String) -> Void) async -> Bool {
    guard !isListening, session.state == .started else { return false }
    errorMessage = nil
    do {
      // Opens the Meta AI app the first time, to grant microphone access.
      if try await wearables.checkPermissionStatus(.microphone) != .granted {
        guard try await wearables.requestPermission(.microphone) == .granted else {
          errorMessage = "Microphone permission for the glasses was denied in the Meta AI app."
          return false
        }
      }
      if speech == nil || attachedSession !== session {
        tokens.clear()
        guard let capability = try session.addSpeech() else { return false }
        speech = capability
        attachedSession = session
        capability.transcriptionPublisher.listen { [weak self] result in
          Task { @MainActor [weak self] in self?.handle(result) }
        }.store(in: tokens)
        capability.errorPublisher.listen { [weak self] error in
          Task { @MainActor [weak self] in self?.fail(error.localizedDescription) }
        }.store(in: tokens)
      }
    } catch {
      errorMessage = error.localizedDescription
      return false
    }

    transcript = ""
    self.onFinal = onFinal
    isListening = true
    speech?.start()
    // Stop by itself if nobody says anything.
    timeoutTask?.cancel()
    timeoutTask = Task { [weak self] in
      try? await Task.sleep(for: .seconds(15))
      guard !Task.isCancelled else { return }
      self?.finish()
    }
    return true
  }

  /// Stops listening and delivers what was heard so far.
  func finish() {
    guard isListening else { return }
    timeoutTask?.cancel()
    timeoutTask = nil
    speech?.stop()
    isListening = false
    let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
    if !text.isEmpty {
      onFinal?(text)
    }
    onFinal = nil
  }

  /// Stops listening without delivering anything (the flow moved on).
  func cancel() {
    onFinal = nil
    transcript = ""
    finish()
  }

  private func handle(_ result: TranscriptionResult) {
    guard isListening else { return }
    transcript = result.text
    if result.isFinal {
      finish()
    }
  }

  private func fail(_ message: String) {
    errorMessage = message
    finish()
  }
}
