//
// PhoneCameraPicker.swift
//
// Fallback for the glasses camera: take the photo of the parts with the iPhone's own camera.
//

import SwiftUI
import UIKit

struct PhoneCameraPicker: UIViewControllerRepresentable {
  /// False in the iOS Simulator, which has no camera. Use the photo picker there.
  static var isAvailable: Bool { UIImagePickerController.isSourceTypeAvailable(.camera) }

  /// Called with JPEG data, or nil when the user cancels.
  let onPhoto: (Data?) -> Void

  func makeCoordinator() -> Coordinator { Coordinator(onPhoto: onPhoto) }

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let picker = UIImagePickerController()
    picker.sourceType = .camera
    picker.delegate = context.coordinator
    return picker
  }

  func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

  final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    let onPhoto: (Data?) -> Void

    init(onPhoto: @escaping (Data?) -> Void) {
      self.onPhoto = onPhoto
    }

    func imagePickerController(
      _ picker: UIImagePickerController,
      didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
    ) {
      onPhoto((info[.originalImage] as? UIImage)?.jpegData(compressionQuality: 0.9))
    }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
      onPhoto(nil)
    }
  }
}
