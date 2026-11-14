//
// PhotoEncoding.swift
//
// The backend accepts JPEG/PNG/WebP/GIF up to 5 MB. Library photos are often HEIC and larger,
// so they are redrawn as a downscaled JPEG before upload.
//

import UIKit

enum PhotoEncoding {
  static func uploadJPEG(from data: Data, maxSide: CGFloat = 1280) -> Data? {
    guard let image = UIImage(data: data) else { return nil }
    let scale = min(1, maxSide / max(image.size.width, image.size.height))
    let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
    let format = UIGraphicsImageRendererFormat.default()
    format.scale = 1
    let resized = UIGraphicsImageRenderer(size: size, format: format).image { _ in
      image.draw(in: CGRect(origin: .zero, size: size))
    }
    return resized.jpegData(compressionQuality: 0.85)
  }
}
