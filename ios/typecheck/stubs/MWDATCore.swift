// Typecheck stub: signatures copied from MWDATCore 1.0.1 arm64-apple-ios.swiftinterface (subset).
import Foundation
public protocol DatError : Foundation.LocalizedError { var description: Swift.String { get } }
extension DatError { public var errorDescription: Swift.String? { description } }
public typealias DeviceIdentifier = Swift.String
public enum DeviceType : Swift.String, Swift.CaseIterable, Swift.Sendable { case unknown, rayBanMeta, oakleyMetaHSTN, oakleyMetaVanguard, metaRayBanDisplay, rayBanMetaOptics, metaGlasses }
@frozen public enum LinkState : Swift.Equatable, Swift.Sendable { case disconnected, connecting, connected }
public actor ListenerTokenBag {
  public init() {}
  nonisolated public func insert(_ token: (any AnyListenerToken)?) {}
  nonisolated public func clear() {}
  public func cancelAll() async {}
}
public protocol AnyListenerToken : Swift.Sendable { func cancel() async }
extension AnyListenerToken { public func store(in bag: ListenerTokenBag) {} }
public protocol Announcer<T> {
  associatedtype T : Swift.Sendable
  func listen(_ listener: @escaping @Sendable (Self.T) -> Swift.Void) -> any AnyListenerToken
}
public enum NavigationError : Swift.Int, DatError { case metaAINotInstalled, notRegistered
  public var description: Swift.String { "" } }
public enum Permission : Swift.Sendable, Swift.CaseIterable { case camera, microphone }
public enum PermissionStatus : Swift.Sendable { case granted, denied }
public enum PermissionError : Swift.Int, DatError { case noDevice, noDeviceWithConnection, connectionError, metaAINotInstalled, requestInProgress, requestTimeout, internalError
  public var description: Swift.String { "" } }
@frozen public enum RegistrationState : Swift.Int, Swift.Sendable { case unavailable, available, registering, registered
  public var description: Swift.String { "" } }
public enum RegistrationError : Swift.Int, DatError { case alreadyRegistered, configurationInvalid, metaAINotInstalled, networkUnavailable, unknown
  public var description: Swift.String { "" } }
public enum UnregistrationError : Swift.Int, DatError { case alreadyUnregistered, configurationInvalid, metaAINotInstalled, unknown
  public var description: Swift.String { "" } }
public enum Compatibility : Swift.CaseIterable, Swift.Sendable { case undefined, compatible, deviceUpdateRequired, sdkUpdateRequired }
final public class Device : Swift.Sendable {
  final public let identifier: DeviceIdentifier
  init(identifier: DeviceIdentifier) { self.identifier = identifier }
  final public func nameOrId() -> Swift.String { "" }
  final public var linkState: LinkState { .disconnected }
  final public func addLinkStateListener(_ listener: @escaping @Sendable (LinkState) -> Swift.Void) -> any AnyListenerToken { fatalError() }
  final public func addCompatibilityListener(_ listener: @escaping @Sendable (Compatibility) -> Swift.Void) -> any AnyListenerToken { fatalError() }
  final public func deviceType() -> DeviceType { .unknown }
  final public func compatibility() -> Compatibility { .undefined }
  final public func supportsDisplay() -> Swift.Bool { false }
}
public protocol DeviceSelector : Swift.Sendable {
  var activeDevice: DeviceIdentifier? { get }
}
final public class SpecificDeviceSelector : DeviceSelector {
  final public var activeDevice: DeviceIdentifier? { nil }
  public init(device: DeviceIdentifier) {}
}
public typealias DeviceFilter = @Sendable (Device) -> Swift.Bool
final public class AutoDeviceSelector : DeviceSelector {
  final public var activeDevice: DeviceIdentifier? { nil }
  public init(wearables: any WearablesInterface, filter: DeviceFilter? = nil) {}
}
final public class DeviceSession : Swift.Sendable {
  final public let deviceId: DeviceIdentifier
  init() { deviceId = "" }
  final public var statePublisher: any Announcer<DeviceSessionState> { fatalError() }
  final public var errorPublisher: any Announcer<DeviceSessionError> { fatalError() }
  final public var state: DeviceSessionState { .idle }
  final public var device: Device? { nil }
  final public func start() throws(DeviceSessionError) {}
  final public func stop() {}
  final public func stateStream() -> AsyncStream<DeviceSessionState> { fatalError() }
  final public func errorStream() -> AsyncStream<DeviceSessionError> { fatalError() }
}
public enum DeviceSessionError : DatError, Swift.Equatable {
  case noEligibleDevice, sessionAlreadyStopped, sessionAlreadyExists, sessionIdle, capabilityAlreadyActive, capabilityNotFound
  case unexpectedError(description: Swift.String)
  case thermalCritical, thermalEmergency, peakPowerShutdown, batteryCritical, datAppOnTheGlassesUpdateRequired, dwaUnavailable, insufficientSDKVersion, dwaOutOfStuRange
  public var description: Swift.String { "" }
}
@frozen public enum DeviceSessionState : Swift.Equatable, Swift.Sendable { case idle, starting, started, paused, stopping, stopped
  public var description: Swift.String { "" } }
public enum WearablesError : Swift.Int, DatError { case internalError = 0, alreadyConfigured = 1
  public var description: Swift.String { "" } }
public enum WearablesHandleURLError : Swift.Int, DatError { case registrationError, unregistrationError
  public var description: Swift.String { "" } }
public protocol WearablesInterface : Swift.Sendable {
  var registrationState: RegistrationState { get }
  func addRegistrationStateListener(_ listener: @escaping @Sendable (RegistrationState) -> Swift.Void) -> any AnyListenerToken
  func startRegistration() async throws(RegistrationError)
  func handleUrl(_ url: Foundation.URL) async throws(WearablesHandleURLError) -> Swift.Bool
  func startUnregistration() async throws(UnregistrationError)
  func openFirmwareUpdate() async throws(NavigationError)
  func openDATGlassesAppUpdate() async throws(NavigationError)
  var devices: [DeviceIdentifier] { get }
  func addDevicesListener(_ listener: @escaping @Sendable ([DeviceIdentifier]) -> Swift.Void) -> any AnyListenerToken
  func deviceForIdentifier(_ identifier: DeviceIdentifier) -> Device?
  func checkPermissionStatus(_ permission: Permission) async throws(PermissionError) -> PermissionStatus
  func requestPermission(_ permission: Permission) async throws(PermissionError) -> PermissionStatus
  func createSession(deviceSelector: any DeviceSelector) throws(DeviceSessionError) -> DeviceSession
}
public enum Wearables {
  public static func configure() throws(WearablesError) {}
  public static var shared: any WearablesInterface { fatalError() }
}
extension WearablesInterface {
  public func registrationStateStream() -> AsyncStream<RegistrationState> { fatalError() }
  public func devicesStream() -> AsyncStream<[DeviceIdentifier]> { fatalError() }
}
