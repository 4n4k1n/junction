//
// SpeechOutputService.swift
//
// Reads instructions aloud, so the user can keep both hands on the wiring. The audio goes to
// wherever the iPhone is playing: the glasses' speakers when they are connected as an audio device.
//

import AVFoundation

@MainActor
final class SpeechOutputService {
  private let synthesizer = AVSpeechSynthesizer()
  private var lastSpoken = ""

  var isSpeaking: Bool { synthesizer.isSpeaking }

  /// Speaks `text`, replacing anything still being spoken. The same text is not repeated twice in a row.
  func speak(_ text: String) {
    let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty, text != lastSpoken else { return }
    lastSpoken = text
    #if os(iOS)
    let session = AVAudioSession.sharedInstance()
    try? session.setCategory(.playback, mode: .spokenAudio, options: .duckOthers)
    try? session.setActive(true)
    #endif
    synthesizer.stopSpeaking(at: .immediate)
    let utterance = AVSpeechUtterance(string: text)
    utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
    synthesizer.speak(utterance)
  }

  /// Stops at once, for example before the microphone starts listening.
  func stop() {
    synthesizer.stopSpeaking(at: .immediate)
    lastSpoken = ""
  }
}
