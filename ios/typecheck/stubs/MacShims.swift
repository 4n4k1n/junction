// macOS stand-ins for the two UIKit-only files, for typechecking only.
import MWDATMockDevice
import SwiftUI
struct MockDisplayPreview: View { let display: any MockDisplayKit; var body: some View { EmptyView() } }
struct PhoneCameraPicker: View {
  static var isAvailable: Bool { false }
  let onPhoto: (Data?) -> Void
  var body: some View { EmptyView() }
}
enum PhotoEncoding { static func uploadJPEG(from data: Data, maxSide: CGFloat = 1280) -> Data? { data } }
