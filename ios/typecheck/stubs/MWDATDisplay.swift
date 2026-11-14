// Typecheck stub: signatures copied from MWDATDisplay 1.0.1 swiftinterface (subset).
import Foundation
import MWDATCore
extension MWDATCore.DeviceSession { final public func addDisplay() throws(MWDATCore.DeviceSessionError) -> Display { fatalError() } }
@frozen public enum DisplayState : Swift.Sendable { case starting, started, stopping, stopped }
final public class Display : Swift.Sendable {
  final public var statePublisher: any MWDATCore.Announcer<DisplayState> { fatalError() }
  final public var state: DisplayState { .stopped }
  final public func start() {}
  final public func stop() {}
  final public func send(_ view: some DisplayableView) async throws {}
  final public func clearDisplay() async throws {}
}
public enum DisplayError : MWDATCore.DatError, Swift.Equatable { case deviceDisconnected, invalidVideoURL, displayError(Swift.String)
  public var description: Swift.String { "" } }
public enum ButtonStyle : Swift.Sendable { case primary, secondary, outline }
public enum ActionRole : Swift.Sendable { case primary }
public struct Button : Swift.Sendable {
  public init(label: Swift.String, style: ButtonStyle = .primary, iconName: IconName? = nil, onClick: (@Sendable () -> Swift.Void)? = nil) {}
  public func actionRole(_ role: ActionRole) -> Button { self }
}
public enum ButtonGroupAlignment : Swift.Sendable { case start, center, end }
@resultBuilder public struct ButtonGroupBuilder {
  public static func buildBlock(_ components: [Button]...) -> [Button] { [] }
  public static func buildArray(_ components: [[Button]]) -> [Button] { [] }
  public static func buildOptional(_ component: [Button]?) -> [Button] { [] }
  public static func buildEither(first component: [Button]) -> [Button] { [] }
  public static func buildEither(second component: [Button]) -> [Button] { [] }
  public static func buildExpression(_ expression: Button) -> [Button] { [] }
}
public struct ButtonGroup : Swift.Sendable {
  public init(alignment: ButtonGroupAlignment = .center, @ButtonGroupBuilder content: () -> [Button]) {}
}
public struct FlexBox : Swift.Sendable {
  public init(direction: Direction = .column, spacing: CoreFoundation.CGFloat = 0, alignment: Alignment = .start, crossAlignment: Alignment = .start, wrap: Swift.Bool = false, padding: EdgeInsets? = nil, @ComponentBuilder content: () -> [any ViewComponent]) {}
  public func padding(_ value: CoreFoundation.CGFloat) -> FlexBox { self }
  public func background(_ value: Background) -> FlexBox { self }
  public func onTap(_ handler: @escaping @Sendable () -> Swift.Void) -> FlexBox { self }
}
public enum IconName : Swift.String, Swift.Sendable { case checkmark, triangleLeftVerticalLine, triangleRightVerticalLine, x }
public enum TextStyle : Swift.Sendable { case heading, body, meta }
public enum TextColor : Swift.Sendable { case primary, secondary }
public struct Text : Swift.Sendable {
  public init(_ content: Swift.String, style: TextStyle = .body, color: TextColor = .primary) {}
}
public protocol DisplayableView : Swift.Sendable {}
public protocol ViewComponent : Swift.Sendable {}
@resultBuilder public struct ComponentBuilder {
  public static func buildBlock(_ components: [any ViewComponent]...) -> [any ViewComponent] { [] }
  public static func buildArray(_ components: [[any ViewComponent]]) -> [any ViewComponent] { [] }
  public static func buildOptional(_ component: [any ViewComponent]?) -> [any ViewComponent] { [] }
  public static func buildEither(first component: [any ViewComponent]) -> [any ViewComponent] { [] }
  public static func buildEither(second component: [any ViewComponent]) -> [any ViewComponent] { [] }
  public static func buildExpression(_ expression: any ViewComponent) -> [any ViewComponent] { [] }
}
public enum Direction : Swift.Sendable { case column, row, columnReverse, rowReverse }
public enum Alignment : Swift.Sendable { case start, center, end, stretch }
public enum Background : Swift.Sendable { case none, card }
public struct EdgeInsets : Swift.Sendable { public init(all value: CoreFoundation.CGFloat) {} }
extension Button : ViewComponent {}
extension ButtonGroup : ViewComponent {}
extension FlexBox : ViewComponent {}
extension FlexBox : DisplayableView {}
extension Text : ViewComponent {}
