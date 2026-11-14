// Typecheck stub: signatures copied from MWDATCamera 1.0.1 swiftinterface (subset).
import Foundation
import MWDATCore
final public class Camera : Swift.Sendable {
  final public let stream: Stream
  init() { fatalError() }
  final public func stop() {}
}
extension MWDATCore.DeviceSession {
  final public func addCamera(config: StreamConfiguration = StreamConfiguration()) throws(MWDATCore.DeviceSessionError) -> Camera? { nil }
}
public enum PhotoCaptureFormat : Swift.Sendable { case heic, jpeg }
public struct PhotoData : Swift.Sendable {
  public let data: Foundation.Data
  public let format: PhotoCaptureFormat
}
final public class Stream : Swift.Sendable {
  final public var state: StreamState { .stopped }
  final public var statePublisher: any MWDATCore.Announcer<StreamState> { fatalError() }
  final public var photoDataPublisher: any MWDATCore.Announcer<PhotoData> { fatalError() }
  final public var errorPublisher: any MWDATCore.Announcer<StreamError> { fatalError() }
  final public func start() {}
  final public func stop() {}
  @discardableResult
  final public func capturePhoto(format: PhotoCaptureFormat) -> Swift.Bool { false }
}
public struct StreamConfiguration : Swift.Sendable {
  public init(videoCodec: VideoCodec, resolution: StreamingResolution, frameRate: Swift.UInt) {}
  public init() {}
}
public enum StreamError : MWDATCore.DatError, Swift.Equatable {
  case internalError, timeout, videoStreamingError, audioStreamingError, permissionDenied, hingesClosed, thermalHot, batteryLow, peakPowerLimit, photoCaptureFailed
  public var description: Swift.String { "" }
}
@frozen public enum StreamState : Swift.Sendable { case stopping, stopped, waitingForDevice, starting, streaming, paused }
public enum StreamingResolution : Swift.Sendable, Swift.CaseIterable { case high, medium, low }
public enum VideoCodec : Swift.Sendable { case raw, hvc1 }
