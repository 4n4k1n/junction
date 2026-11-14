// Typecheck stub: signatures copied from MWDATMockDevice 1.0.1 swiftinterface (subset; createPreviewView omitted, UIKit).
import Foundation
import MWDATCore
public enum GlassesModel : Swift.String, Swift.CaseIterable, Swift.Sendable { case rayBanMeta, oakleyMetaHSTN, oakleyMetaVanguard, rayBanMetaOptics, metaGlasses, metaRayBanDisplay }
public protocol MockDevice : Swift.Sendable {
  var deviceIdentifier: MWDATCore.DeviceIdentifier { get }
  func powerOn()
  func powerOff()
  func don()
  func doff()
}
public enum MockDeviceKitError : MWDATCore.DatError, Swift.Equatable { case notEnabled, testServerUnavailable
  public var description: Swift.String { "" } }
public protocol MockDeviceKitInterface : Swift.Sendable {
  var isEnabled: Swift.Bool { get }
  func enable(config: MockDeviceKitConfig)
  func disable()
  func disable() async
  func pairGlasses(model: GlassesModel) throws(MockDeviceKitError) -> any MockGlasses
  func unpairDevice(_ device: any MockDevice)
  func unpairDevice(_ device: any MockDevice) async
  var pairedDevices: [any MockDevice] { get }
}
extension MockDeviceKitInterface { public func enable() {} }
public enum MockDeviceKit : Swift.Sendable { public static var shared: any MockDeviceKitInterface { fatalError() } }
@frozen public struct MockDeviceKitConfig : Swift.Sendable {
  public init(initiallyRegistered: Swift.Bool = true, initialPermissionsGranted: Swift.Bool = true) {}
}
public protocol MockDisplayKit : Swift.Sendable {
  @discardableResult
  func sendClick(identifier: Swift.String) -> Swift.Bool
}
public protocol MockGlasses : MockDevice {
  func fold()
  func unfold()
  var services: any MockGlassesServices { get }
}
public protocol MockGlassesServices : Swift.Sendable { var display: any MockDisplayKit { get } }
