//
// CameraService.swift
//
// One photo from the glasses camera, using the in-stream capture path of the official
// CameraAccess sample: add Camera to the started session, start the stream, call
// stream.capturePhoto(format:) once it is streaming, read the result from photoDataPublisher.
// The camera is stopped again afterwards so it does not hold bandwidth during guidance.
//

import Foundation
import MWDATCamera
import MWDATCore

enum CameraCaptureError: LocalizedError {
  case sessionNotStarted
  case permissionDenied
  case cameraUnavailable
  case captureNotStarted
  case timedOut
  case stream(String)

  var errorDescription: String? {
    switch self {
    case .sessionNotStarted: "Connect the glasses before taking a photo."
    case .permissionDenied: "Camera permission was denied in the Meta AI app."
    case .cameraUnavailable: "The glasses camera could not be attached. Try again."
    case .captureNotStarted: "The glasses did not start the capture. Try again in a moment."
    case .timedOut: "Timed out waiting for the photo from the glasses."
    case .stream(let message): message
    }
  }
}

private enum CaptureEvent: Sendable {
  case state(StreamState)
  case photo(Data)
  case failed(String)
  case timedOut
}

@MainActor
final class CameraService {
  private let wearables: any WearablesInterface

  init(wearables: any WearablesInterface) {
    self.wearables = wearables
  }

  /// Returns JPEG data. Opens the Meta AI app if camera permission has not been granted yet.
  func capturePhoto(on session: DeviceSession) async throws -> Data {
    guard session.state == .started else { throw CameraCaptureError.sessionNotStarted }

    if try await wearables.checkPermissionStatus(.camera) != .granted {
      guard try await wearables.requestPermission(.camera) == .granted else {
        throw CameraCaptureError.permissionDenied
      }
    }

    let config = StreamConfiguration(videoCodec: VideoCodec.raw, resolution: StreamingResolution.low, frameRate: 15)
    guard let camera = try session.addCamera(config: config) else {
      throw CameraCaptureError.cameraUnavailable
    }
    let stream = camera.stream

    let tokens = ListenerTokenBag()
    let (events, continuation) = AsyncStream.makeStream(of: CaptureEvent.self)
    stream.statePublisher.listen { continuation.yield(.state($0)) }.store(in: tokens)
    stream.photoDataPublisher.listen { continuation.yield(.photo($0.data)) }.store(in: tokens)
    stream.errorPublisher.listen { continuation.yield(.failed($0.localizedDescription)) }.store(in: tokens)
    let timeout = Task {
      try? await Task.sleep(for: .seconds(20))
      continuation.yield(.timedOut)
    }
    defer {
      timeout.cancel()
      continuation.finish()
      tokens.clear()
      camera.stop()
    }

    stream.start()

    var captureRequested = false
    for await event in events {
      switch event {
      case .state(let state):
        guard state == .streaming, !captureRequested else { continue }
        captureRequested = true
        guard stream.capturePhoto(format: .jpeg) else { throw CameraCaptureError.captureNotStarted }
      case .photo(let data):
        return data
      case .failed(let message):
        throw CameraCaptureError.stream(message)
      case .timedOut:
        throw CameraCaptureError.timedOut
      }
    }
    throw CameraCaptureError.timedOut
  }
}
