// Typecheck stub: signatures copied from MWDATSpeech 1.0.1 swiftinterface (subset).
import Foundation
import MWDATCore
extension MWDATCore.DeviceSession {
  final public func addSpeech() throws(MWDATCore.DeviceSessionError) -> Speech? { nil }
  final public func removeSpeech() throws(MWDATCore.DeviceSessionError) {}
}
@frozen public enum SpeechState : Swift.Sendable, Swift.Equatable { case starting, started, stopping, stopped }
public enum SpeechError : MWDATCore.DatError, Swift.Equatable {
  case deviceDisconnected, invalidState, unavailable, alreadyListening, startFailed, unexpectedError
  public var description: Swift.String { "" }
}
final public class Speech : Swift.Sendable {
  init() { fatalError() }
  final public var statePublisher: any MWDATCore.Announcer<SpeechState> { fatalError() }
  final public var transcriptionPublisher: any MWDATCore.Announcer<TranscriptionResult> { fatalError() }
  final public var errorPublisher: any MWDATCore.Announcer<SpeechError> { fatalError() }
  final public var state: SpeechState { .stopped }
  final public func start() {}
  final public func stop() {}
}
public struct TranscriptionResult : Swift.Sendable, Swift.Equatable {
  public let text: Swift.String
  public let isFinal: Swift.Bool
  public let confidence: Swift.Float
}
