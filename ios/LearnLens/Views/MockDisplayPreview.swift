//
// MockDisplayPreview.swift
//
// Renders what the simulated glasses show, inside the app (MockDisplayKit.createPreviewView()).
//

import MWDATMockDevice
import SwiftUI

struct MockDisplayPreview: UIViewRepresentable {
  let display: any MockDisplayKit

  func makeUIView(context: Context) -> UIView {
    display.createPreviewView()
  }

  func updateUIView(_ uiView: UIView, context: Context) {}
}
