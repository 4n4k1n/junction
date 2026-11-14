//
// GlassesManager.swift
//
// Registration, device list, the DeviceSession and the Display capability, following the
// official DisplayAccess sample (WearablesViewModel + DisplayViewModel). Also owns the
// simulated glasses (MockDeviceKit) used when no physical pair is available.
//

import Foundation
import MWDATCore
import MWDATDisplay
import MWDATMockDevice
import Observation

struct GlassesDevice: Identifiable, Equatable {
  let id: DeviceIdentifier
  let name: String
  let type: String
  let link: String
  let supportsDisplay: Bool
}

enum GlassesError: LocalizedError {
  case simulatedDeviceNotReady
  case displayNotReady

  var errorDescription: String? {
    switch self {
    case .simulatedDeviceNotReady: "Timed out waiting for the simulated glasses to connect."
    case .displayNotReady: "Timed out waiting for the display to become ready."
    }
  }
}

@Observable
@MainActor
final class GlassesManager {
  private(set) var registrationState: RegistrationState
  private(set) var devices: [GlassesDevice] = []
  private(set) var isConnecting = false
  private(set) var isDisplayReady = false
  private(set) var isSimulated = false
  private(set) var isSwitchingSimulation = false
  private(set) var mockDisplay: (any MockDisplayKit)?
  private(set) var requiresGlassesAppUpdate = false
  var errorMessage: String?

  /// The running session, shared with CameraService. Nil while disconnected.
  @ObservationIgnored private(set) var session: DeviceSession?

  @ObservationIgnored private let wearables: any WearablesInterface
  @ObservationIgnored private var selector: any DeviceSelector
  @ObservationIgnored private var display: Display?
  @ObservationIgnored private var currentCard: FlexBox?
  @ObservationIgnored private var cardNeedsSending = false
  @ObservationIgnored private var cardSender: Task<Void, Never>?
  @ObservationIgnored private var displayStateToken: (any AnyListenerToken)?
  @ObservationIgnored private var linkStateTokens: [any AnyListenerToken] = []
  @ObservationIgnored private var registrationTask: Task<Void, Never>?
  @ObservationIgnored private var devicesTask: Task<Void, Never>?
  @ObservationIgnored private var sessionStateTask: Task<Void, Never>?
  @ObservationIgnored private var sessionErrorTask: Task<Void, Never>?
  @ObservationIgnored private var readinessTimeoutTask: Task<Void, Never>?
  @ObservationIgnored private var mockGlasses: (any MockGlasses)?

  init(wearables: any WearablesInterface) {
    self.wearables = wearables
    self.registrationState = wearables.registrationState
    // Created up front: AutoDeviceSelector needs time to see devices before createSession.
    self.selector = AutoDeviceSelector(wearables: wearables, filter: { $0.supportsDisplay() })
    observeWearables()
  }

  var isRegistered: Bool { registrationState == .registered }
  var hasSession: Bool { session != nil }

  // MARK: - Registration

  func register() async {
    guard registrationState != .registering else { return }
    do {
      try await wearables.startRegistration()
    } catch {
      errorMessage = error.description
    }
  }

  func unregister() async {
    do {
      try await wearables.startUnregistration()
    } catch {
      errorMessage = error.description
    }
  }

  /// Callback from the Meta AI app (registration and permission flows).
  func handleCallback(_ url: URL) async {
    guard
      let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
      components.queryItems?.contains(where: { $0.name == "metaWearablesAction" }) == true
    else {
      return
    }
    do {
      _ = try await wearables.handleUrl(url)
    } catch {
      errorMessage = error.description
    }
  }

  func openGlassesAppUpdate() async {
    do {
      try await wearables.openDATGlassesAppUpdate()
    } catch {
      errorMessage = error.description
    }
  }

  private func observeWearables() {
    registrationTask = Task { [weak self] in
      guard let wearables = self?.wearables else { return }
      for await state in wearables.registrationStateStream() {
        guard let self else { return }
        self.registrationState = state
        if state == .available || state == .unavailable {
          self.resetAfterRegistrationChange()
        }
      }
    }
    devicesTask = Task { [weak self] in
      guard let wearables = self?.wearables else { return }
      for await identifiers in wearables.devicesStream() {
        guard let self else { return }
        self.watchLinkStates(of: identifiers)
        self.refreshDevices()
      }
    }
  }

  private func resetAfterRegistrationChange() {
    disconnect()
    if !isSimulated {
      selector = AutoDeviceSelector(wearables: wearables, filter: { $0.supportsDisplay() })
    }
  }

  private func watchLinkStates(of identifiers: [DeviceIdentifier]) {
    // Dropping a token cancels its listener.
    linkStateTokens = identifiers.compactMap { identifier in
      wearables.deviceForIdentifier(identifier)?.addLinkStateListener { [weak self] _ in
        Task { @MainActor [weak self] in self?.refreshDevices() }
      }
    }
  }

  private func refreshDevices() {
    devices = wearables.devices.compactMap { identifier in
      guard let device = wearables.deviceForIdentifier(identifier) else { return nil }
      let link =
        switch device.linkState {
        case .connected: "connected"
        case .connecting: "connecting"
        case .disconnected: "disconnected"
        }
      return GlassesDevice(
        id: identifier,
        name: identifier == mockGlasses?.deviceIdentifier ? "Simulated glasses" : device.nameOrId(),
        type: device.deviceType().rawValue,
        link: link,
        supportsDisplay: device.supportsDisplay()
      )
    }
  }

  // MARK: - Session and display

  /// Creates and starts a DeviceSession, then attaches Display once the session is started.
  func connect() async {
    guard session == nil else { return }
    errorMessage = nil
    requiresGlassesAppUpdate = false
    isConnecting = true

    do {
      let newSession = try wearables.createSession(deviceSelector: selector)
      session = newSession

      let states = newSession.stateStream()
      let errors = newSession.errorStream()
      sessionStateTask = Task { [weak self] in
        for await state in states {
          guard let self, !Task.isCancelled else { return }
          switch state {
          case .started:
            self.attachDisplay(to: newSession)
          case .stopping:
            self.isDisplayReady = false
          case .stopped:
            self.clearSession()
          case .idle, .starting, .paused:
            break
          @unknown default:
            break
          }
        }
      }
      sessionErrorTask = Task { [weak self] in
        for await error in errors {
          guard let self, !Task.isCancelled else { return }
          self.requiresGlassesAppUpdate = error == .datAppOnTheGlassesUpdateRequired
          self.errorMessage = error.localizedDescription
        }
      }

      startReadinessTimeout(for: newSession)
      try newSession.start()
    } catch DeviceSessionError.datAppOnTheGlassesUpdateRequired {
      clearSession()
      requiresGlassesAppUpdate = true
      errorMessage = DeviceSessionError.datAppOnTheGlassesUpdateRequired.localizedDescription
    } catch {
      clearSession()
      errorMessage = "Could not start a glasses session: \(error.localizedDescription)"
    }
  }

  private func attachDisplay(to session: DeviceSession) {
    guard display == nil else { return }
    do {
      let capability = try session.addDisplay()
      display = capability
      displayStateToken = capability.statePublisher.listen { [weak self] state in
        Task { @MainActor [weak self] in self?.handleDisplayState(state) }
      }
      capability.start()
    } catch {
      session.stop()
      clearSession()
      errorMessage = "Could not start the display: \(error.localizedDescription)"
    }
  }

  private func handleDisplayState(_ state: DisplayState) {
    switch state {
    case .starting:
      break
    case .started:
      readinessTimeoutTask?.cancel()
      readinessTimeoutTask = nil
      isConnecting = false
      isDisplayReady = true
      // Content sent before the display was ready is drawn now.
      if let currentCard {
        Task { await show(currentCard) }
      }
    case .stopping:
      isDisplayReady = false
    case .stopped:
      isDisplayReady = false
      displayStateToken = nil
      display = nil
      session?.stop()
    }
  }

  private func startReadinessTimeout(for target: DeviceSession) {
    readinessTimeoutTask?.cancel()
    readinessTimeoutTask = Task { [weak self] in
      do {
        try await Task.sleep(for: .seconds(10))
      } catch {
        return
      }
      guard let self, self.session === target, !self.isDisplayReady else { return }
      target.stop()
      self.clearSession()
      self.errorMessage = GlassesError.displayNotReady.localizedDescription
    }
  }

  /// Draws a card on the glasses. Before the display is ready the card is kept and drawn on connect.
  ///
  /// Returns at once: the app's flow never waits for the glasses. Cards are sent one at a time in the
  /// background, and when several arrive while one is still being sent, only the newest is drawn.
  func show(_ card: FlexBox) async {
    currentCard = card
    cardNeedsSending = true
    guard display != nil, isDisplayReady, cardSender == nil else { return }
    cardSender = Task { [weak self] in
      await self?.sendCards()
    }
  }

  private func sendCards() async {
    while cardNeedsSending, let display, isDisplayReady, let card = currentCard {
      cardNeedsSending = false
      let started = ContinuousClock.now
      do {
        try await display.send(card)
      } catch {
        errorMessage = (error as? DisplayError)?.description ?? error.localizedDescription
      }
      let took = ContinuousClock.now - started
      if took > .seconds(2) {
        NSLog("[LearnLens] the glasses took \(took) to accept a card")
      }
    }
    cardSender = nil
  }

  /// Stop Display first, then the session (teardown cascades parent to child, not the reverse).
  func disconnect() {
    if let display {
      display.stop()
    } else {
      session?.stop()
    }
  }

  private func stopSessionAndWait() async {
    guard session != nil else { return }
    disconnect()
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: .seconds(3))
    while session != nil, clock.now < deadline {
      try? await clock.sleep(for: .milliseconds(50))
    }
    clearSession()
  }

  private func clearSession() {
    readinessTimeoutTask?.cancel()
    readinessTimeoutTask = nil
    sessionStateTask?.cancel()
    sessionStateTask = nil
    sessionErrorTask?.cancel()
    sessionErrorTask = nil
    displayStateToken = nil
    display = nil
    session = nil
    isDisplayReady = false
    isConnecting = false
    cardSender?.cancel()
    cardSender = nil
  }

  // MARK: - Simulated glasses (MockDeviceKit)

  /// Pairs a simulated Meta Ray-Ban Display. The same DeviceSession.addDisplay() path then
  /// renders into `mockDisplay`'s preview view on the phone.
  func setSimulated(_ enabled: Bool) async {
    guard enabled != isSimulated, !isSwitchingSimulation else { return }
    isSwitchingSimulation = true
    defer { isSwitchingSimulation = false }
    errorMessage = nil
    await stopSessionAndWait()

    let kit = MockDeviceKit.shared
    if enabled {
      kit.enable()
      var paired: (any MockGlasses)?
      do {
        let glasses = try kit.pairGlasses(model: .metaRayBanDisplay)
        paired = glasses
        glasses.powerOn()
        glasses.unfold()
        glasses.don()
        try await waitUntilReady(glasses.deviceIdentifier)
        mockGlasses = glasses
        mockDisplay = glasses.services.display
        selector = SpecificDeviceSelector(device: glasses.deviceIdentifier)
        isSimulated = true
        refreshDevices()
        await connect()
      } catch {
        if let paired {
          await kit.unpairDevice(paired)
        }
        await kit.disable()
        errorMessage = "Could not start simulated glasses: \(error.localizedDescription)"
      }
    } else {
      if let mockGlasses {
        await kit.unpairDevice(mockGlasses)
      }
      await kit.disable()
      mockGlasses = nil
      mockDisplay = nil
      isSimulated = false
      selector = AutoDeviceSelector(wearables: wearables, filter: { $0.supportsDisplay() })
      refreshDevices()
    }
  }

  private func waitUntilReady(_ identifier: DeviceIdentifier) async throws {
    let clock = ContinuousClock()
    let deadline = clock.now.advanced(by: .seconds(3))
    while true {
      if let device = wearables.deviceForIdentifier(identifier),
        device.linkState == .connected, device.compatibility() == .compatible
      {
        return
      }
      guard clock.now < deadline else { throw GlassesError.simulatedDeviceNotReady }
      try await clock.sleep(for: .milliseconds(50))
    }
  }
}
