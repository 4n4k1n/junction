//
// LearnLensApp.swift
//
// Entry point. Configures the Meta Wearables DAT SDK once at launch and routes the Meta AI
// app's URL callbacks (registration, permissions) back into the SDK.
//

import MWDATCore
import SwiftUI

@main
struct LearnLensApp: App {
  @State private var model: SessionViewModel

  init() {
    do {
      try Wearables.configure()
    } catch {
      NSLog("[LearnLens] Failed to configure Wearables SDK: \(error)")
    }
    self._model = State(wrappedValue: SessionViewModel(wearables: Wearables.shared))
  }

  var body: some Scene {
    WindowGroup {
      GuideView(model: model)
        .onOpenURL { url in
          Task { await model.glasses.handleCallback(url) }
        }
    }
  }
}
