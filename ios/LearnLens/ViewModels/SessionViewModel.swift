//
// SessionViewModel.swift
//
// The LearnLens flow: goal (+ photo) -> confirm the parts -> wiring, one confirmed step at a time ->
// generated sketch -> explicit approval -> deploy -> test. Owns the UI state and keeps the glasses
// card in sync with it.
//

import Foundation
import MWDATCore
import Observation

enum SessionPhase: Equatable {
  case disconnected
  case connected
  case capturing
  case analyzing
  case confirmingParts
  case guiding
  case generating
  case awaitingApproval
  case deploying
  case testing
  case success
  case error(String)

  var title: String {
    switch self {
    case .disconnected: "Disconnected"
    case .connected: "Connected"
    case .capturing: "Capturing"
    case .analyzing: "Analyzing"
    case .confirmingParts: "Confirm your parts"
    case .guiding: "Guiding"
    case .generating: "Generating"
    case .awaitingApproval: "Awaiting approval"
    case .deploying: "Deploying"
    case .testing: "Testing"
    case .success: "Success"
    case .error: "Error"
    }
  }
}

@Observable
@MainActor
final class SessionViewModel {
  static let backendURLKey = "backendURL"
  static let defaultBackendURL = "http://localhost:8000"

  let glasses: GlassesManager
  let speech = SpeechInputService()
  let glassesSpeech: GlassesSpeechService

  var goal = ""
  var backendURL: String {
    didSet { UserDefaults.standard.set(backendURL, forKey: Self.backendURLKey) }
  }
  private(set) var backendStatus = "not checked"
  private(set) var backendInfo: BackendInfo?
  private(set) var guide: GuideStatus?
  private(set) var generation: GenerationResult?
  /// Why the last upload failed. Shown next to the approve button, which doubles as "retry".
  private(set) var deployError: String?
  private(set) var build: Build?

  /// What the flow is doing. `.connected` here means "idle"; `phase` folds in the glasses link.
  private var flow: SessionPhase = .connected

  @ObservationIgnored private let camera: CameraService
  @ObservationIgnored private var pollTask: Task<Void, Never>?

  init(wearables: any WearablesInterface) {
    self.glasses = GlassesManager(wearables: wearables)
    self.camera = CameraService(wearables: wearables)
    self.glassesSpeech = GlassesSpeechService(wearables: wearables)
    self.backendURL = UserDefaults.standard.string(forKey: Self.backendURLKey) ?? Self.defaultBackendURL
  }

  var phase: SessionPhase {
    if flow == .connected {
      return glasses.isDisplayReady ? .connected : .disconnected
    }
    return flow
  }

  var isIdle: Bool { flow == .connected }
  var stepIndex: Int { guide?.currentStep ?? 0 }
  var currentStep: WiringStep? {
    guard let steps = guide?.wiringSteps, steps.indices.contains(stepIndex) else { return nil }
    return steps[stepIndex]
  }
  var isLastStep: Bool { stepIndex >= (guide?.wiringSteps.count ?? 0) - 1 }
  var pendingConfirmation: PendingConfirmation? { guide?.pendingConfirmations.first }
  var isDemoMode: Bool { backendInfo?.demoMode == true }
  /// True when the upload was simulated by an agent in --mock mode: no board was flashed.
  var isSimulatedDeploy: Bool { build?.deploy?.mock == true }
  var canCaptureFromGlasses: Bool { glasses.isDisplayReady && !glasses.isSimulated }

  private var api: APIClient { APIClient(baseURL: backendURL) }

  // MARK: - Backend

  func checkBackend() async {
    backendStatus = "checking…"
    do {
      backendInfo = try await api.health()
      backendStatus = "reachable"
    } catch {
      backendInfo = nil
      backendStatus = error.localizedDescription
    }
    await pushCard()
  }

  // MARK: - Voice input from the glasses

  var isListening: Bool { glassesSpeech.isListening || speech.isRecording }

  /// Started from the glasses card. Uses the glasses microphone when possible, else the iPhone's.
  func startVoiceInput() async {
    guard isIdle, !isListening else { return }
    if let session = glasses.session, !glasses.isSimulated {
      let started = await glassesSpeech.start(on: session) { [weak self] text in
        self?.goal = text
        Task { await self?.pushCard() }
      }
      if started {
        await pushCard()
        return
      }
    }
    await speech.start()
    await pushCard()
  }

  func finishVoiceInput() async {
    if glassesSpeech.isListening {
      glassesSpeech.finish()
    } else {
      speech.stop()
      if !speech.transcript.isEmpty { goal = speech.transcript }
    }
    await pushCard()
  }

  /// "Photo & start" on the glasses: glasses camera when there is one, otherwise start without a photo.
  func captureOrStart() async {
    if canCaptureFromGlasses {
      await captureAndStart()
    } else {
      await start(withPhoneJPEG: nil)
    }
  }

  // MARK: - Start

  /// Takes a photo with the glasses camera, then starts the session with it.
  func captureAndStart() async {
    guard isIdle, hasGoal() else { return }
    await setFlow(.capturing)
    guard let photo = await glassesPhoto() else { return }
    await analyze(photo: photo)
  }

  /// Starts with a photo from the phone (picker), or with no photo: then every part is confirmed by hand.
  func start(withPhoneJPEG jpeg: Data?) async {
    guard isIdle, hasGoal() else { return }
    await analyze(photo: jpeg)
  }

  private func hasGoal() -> Bool {
    goal = goal.trimmingCharacters(in: .whitespacesAndNewlines)
    return !goal.isEmpty
  }

  /// A failed capture ends the flow only when `fatal`; a failed optional review just shows a message.
  private func glassesPhoto(fatal: Bool = true) async -> Data? {
    do {
      guard let session = glasses.session else { throw CameraCaptureError.sessionNotStarted }
      return try await camera.capturePhoto(on: session)
    } catch {
      if fatal {
        await fail(error.localizedDescription)
      } else {
        glasses.errorMessage = error.localizedDescription
      }
      return nil
    }
  }

  private func analyze(photo: Data?) async {
    await setFlow(.analyzing)
    do {
      let session = try await api.createSession(instruction: goal)
      let status = try await api.analyze(
        session.sessionId, photoJPEG: photo, confirmedInventory: photo == nil ? [] : nil)
      await apply(status)
    } catch {
      await fail(error.localizedDescription)
    }
  }

  /// Moves the UI to wherever the backend's session state machine is.
  private func apply(_ status: GuideStatus) async {
    guide = status
    switch status.state {
    case .awaitingAnalysis:
      await setFlow(.connected)
    case .needsConfirmation:
      await setFlow(.confirmingParts)
    case .wiring:
      await setFlow(.guiding)
    case .wiringComplete, .codeGenerated:
      await generateSketch(sensorActiveLevel: nil)
    case .unsupported:
      await fail(status.spokenText)
    case .blocked:
      await fail((["The wiring plan failed a safety check."] + status.safetyProblems).joined(separator: "\n"))
    }
  }

  // MARK: - Confirming parts

  /// The user answers the question on screen after checking the physical part.
  func answerConfirmation(_ yes: Bool) async {
    guard flow == .confirmingParts, let guide, let pending = pendingConfirmation else { return }
    guard yes else {
      await fail("This build needs exactly that part. Start over when you have it.")
      return
    }
    let item = InventoryItem(
      componentId: pending.componentId,
      confirmations: pending.kind == "variant" ? [pending.id] : [])
    do {
      await apply(try await api.analyze(guide.sessionId, photoJPEG: nil, confirmedInventory: [item]))
    } catch {
      await fail(error.localizedDescription)
    }
  }

  // MARK: - Guiding

  /// The user confirms the current wiring step. After the last one the sketch is generated.
  func next() async {
    guard flow == .guiding, let guide, let index = guide.currentStep else { return }
    do {
      await apply(try await api.confirmStep(guide.sessionId, index: index, confirmed: true))
    } catch {
      await fail(error.localizedDescription)
    }
  }

  /// Optional photo review of the wiring. It lists things to re-check; it never verifies a connection.
  func reviewWiring(phoneJPEG: Data?) async {
    guard flow == .guiding, let guide else { return }
    var photo = phoneJPEG
    if photo == nil {
      photo = await glassesPhoto(fatal: false)
    }
    guard let photo else { return }
    do {
      await apply(try await api.analyze(guide.sessionId, photoJPEG: photo, confirmedInventory: nil))
    } catch {
      glasses.errorMessage = error.localizedDescription
    }
  }

  /// Asks the backend to write, check and compile the sketch. Nothing is uploaded by this.
  private func generateSketch(sensorActiveLevel: String?) async {
    guard let guide else { return }
    await setFlow(.generating)
    do {
      let result = try await api.generate(guide.sessionId, sensorActiveLevel: sensorActiveLevel)
      generation = result
      build = try await api.getBuild(result.buildId)
      await setFlow(.awaitingApproval)
    } catch {
      await fail(error.localizedDescription)
    }
  }

  /// Regenerates with the other sensor polarity, for a module whose output idles the other way.
  func regenerate(sensorActiveLevel: String) async {
    guard flow == .awaitingApproval else { return }
    await generateSketch(sensorActiveLevel: sensorActiveLevel)
  }

  // MARK: - Approval and deploy

  /// The only way anything gets flashed: the user approves, on the phone or with the glasses button.
  func approve() async {
    guard flow == .awaitingApproval, let build, let sketch = build.sketch else { return }
    deployError = nil
    do {
      self.build = try await api.approve(build.buildId, sketchSHA256: sketch.sha256)
      await setFlow(.deploying)
      pollDeploy(build.buildId)
    } catch {
      await fail(error.localizedDescription)
    }
  }

  func reject() async {
    guard flow == .awaitingApproval, let build else { return }
    _ = try? await api.reject(build.buildId)
    await reset()
  }

  private func pollDeploy(_ id: String) {
    pollTask?.cancel()
    pollTask = Task { [weak self] in
      for _ in 0..<180 {
        try? await Task.sleep(for: .seconds(1))
        guard let self, !Task.isCancelled else { return }
        guard let latest = try? await self.api.getBuild(id) else { continue }
        self.build = latest
        switch latest.status {
        case .awaitingTest:
          await self.setFlow(.testing)
          self.pollBoardLog(id)
          return
        case .failed:
          // A failed compile or upload can be retried: approving again signs a fresh approval.
          self.deployError = [latest.error, latest.deploy?.log ?? ""].joined(separator: "\n")
          await self.setFlow(.awaitingApproval)
          return
        default:
          continue
        }
      }
      guard let self, !Task.isCancelled else { return }
      await self.fail("No deploy result after 3 minutes. Is the Mac deploy agent running?")
    }
  }

  /// While the user tests, keep reading the build: the agent adds lines it reads from the board.
  private func pollBoardLog(_ id: String) {
    pollTask?.cancel()
    pollTask = Task { [weak self] in
      for _ in 0..<120 {
        try? await Task.sleep(for: .seconds(1))
        guard let self, !Task.isCancelled, self.flow == .testing else { return }
        guard let latest = try? await self.api.getBuild(id) else { continue }
        let changed = latest.deploy?.runtimeLog.count != self.build?.deploy?.runtimeLog.count
        self.build = latest
        if changed { await self.pushCard() }
      }
    }
  }

  // MARK: - Test

  func reportTest(passed: Bool) async {
    guard flow == .testing, let build else { return }
    do {
      self.build = try await api.reportTest(build.buildId, passed: passed, note: "")
      if passed {
        await setFlow(.success)
      } else {
        await fail("The behavior test failed. Check the wiring and the sensor's trim pot, then start over.")
      }
    } catch {
      await fail(error.localizedDescription)
    }
  }

  func reset() async {
    pollTask?.cancel()
    pollTask = nil
    build = nil
    generation = nil
    deployError = nil
    guide = nil
    await setFlow(.connected)
  }

  // MARK: - Glasses card

  private func setFlow(_ newFlow: SessionPhase) async {
    flow = newFlow
    await pushCard()
  }

  private func fail(_ message: String) async {
    await setFlow(.error(message))
  }

  /// Redraws the glasses for the current state. Button taps on the glasses come back here.
  func pushCard() async {
    await glasses.show(makeCard())
  }

  private func action(_ label: String, _ run: @escaping @MainActor (SessionViewModel) async -> Void) -> CardAction {
    CardAction(label: label) { [weak self] in
      Task { @MainActor [weak self] in
        guard let self else { return }
        await run(self)
      }
    }
  }

  private func makeCard() -> FlexBoxCard {
    // Demo mode is visible on the glasses too, on every card.
    let demo = isDemoMode ? "DEMO MODE · " : ""
    switch flow {
    case .disconnected, .connected:
      // The whole start can be driven from the glasses: speak the goal, then photo and go.
      if isListening {
        return GlassesCards.card(
          caption: demo + "Listening…",
          text: "Say what you want to build.",
          primary: action("Done") { await $0.finishVoiceInput() }
        )
      }
      if goal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return GlassesCards.card(
          caption: demo + "LearnLens",
          text: "What do you want to build?",
          primary: action("Speak") { await $0.startVoiceInput() }
        )
      }
      return GlassesCards.card(
        caption: demo + "Your goal",
        text: goal,
        primary: action("Photo & start") { await $0.captureOrStart() },
        secondary: action("Speak again") { await $0.startVoiceInput() }
      )
    case .capturing:
      return GlassesCards.card(caption: "Capturing", text: "Look at your parts and hold still.")
    case .analyzing:
      return GlassesCards.card(caption: demo + "Analyzing", text: "Working out the wiring…")
    case .confirmingParts:
      return GlassesCards.card(
        caption: "Check your part",
        text: pendingConfirmation?.question ?? "Confirm your parts on the phone.",
        primary: action("Yes") { await $0.answerConfirmation(true) },
        secondary: action("No") { await $0.answerConfirmation(false) }
      )
    case .guiding:
      guard let guide, let step = currentStep else {
        return GlassesCards.card(caption: "Guiding", text: "No wiring steps.")
      }
      return GlassesCards.card(
        caption: demo + "Step \(stepIndex + 1) of \(guide.wiringSteps.count)",
        text: step.displayText,
        primary: action(isLastStep ? "Wiring done" : "Done, next") { await $0.next() }
      )
    case .generating:
      return GlassesCards.card(caption: demo + "Generating", text: isDemoMode ? "Loading the prevalidated sketch…" : "Writing the sketch…")
    case .awaitingApproval:
      // Flashing still needs an explicit yes, but it can be given on the glasses. The code itself
      // stays readable on the phone for anyone who wants to check it first.
      let compiled = generation?.compile.status == "ok" ? "Code is ready and compiled." : "Code is ready (not compiled)."
      return GlassesCards.card(
        caption: demo + (deployError == nil ? "Approve upload" : "Upload failed"),
        text: deployError == nil ? compiled + " Flash it to the board?" : "The upload did not succeed. Try again?",
        primary: action(deployError == nil ? "Start flash" : "Retry flash") { await $0.approve() },
        secondary: action("Cancel") { await $0.reject() }
      )
    case .deploying:
      return GlassesCards.card(caption: "Deploying", text: "Uploading to the board…")
    case .testing:
      let seen = build?.deploy?.observedEvents ?? []
      return GlassesCards.card(
        caption: isSimulatedDeploy ? "Test it · SIMULATED upload" : demo + "Test it",
        text: seen.isEmpty ? "Make some noise. Does it react?" : "Board reports: " + seen.joined(separator: ", "),
        primary: action("Yes") { await $0.reportTest(passed: true) },
        secondary: action("No") { await $0.reportTest(passed: false) }
      )
    case .success:
      return GlassesCards.card(
        caption: isSimulatedDeploy ? "Success · SIMULATED upload" : demo + "Success",
        text: isSimulatedDeploy ? "Flow complete. No board was flashed." : "It works. Nice build!",
        primary: action("New build") { await $0.reset() }
      )
    case .error(let message):
      return GlassesCards.card(
        caption: "Error",
        text: message,
        primary: action("Start over") { await $0.reset() }
      )
    }
  }
}
