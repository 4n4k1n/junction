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
  /// After a finished build: what the user wants to add or change next.
  var extensionText = ""
  /// The backend asked what a new part should do. The answer is sent together with the first request.
  private(set) var extensionQuestion: String?
  /// Why the last follow-up request cannot be built. Shown with ideas that would work.
  private(set) var extensionProblem: String?
  /// Example answers to that question, for a user who has no idea yet.
  private(set) var extensionIdeas: [String] = []
  private var showingIdeas = false
  @ObservationIgnored private var extensionBase = ""
  /// A question typed on the phone during the build.
  var questionText = ""
  /// The answer to the last question, shown until the user goes back to the step.
  private(set) var stepAnswer: StepAnswer?
  private(set) var isAnswering = false
  /// The user chose "Change this build" or "Add a part" on a finished build.
  private(set) var isExtending = false
  /// The parts list of a new project was shown and the user said they have the parts.
  private(set) var partsAcknowledged = false
  /// The step on which "the code is being written in the background" is shown. Said once per plan.
  private(set) var noticeStep: Int?
  @ObservationIgnored private var noticeGivenFor = ""
  /// The user is asked for a photo of the part they want to add, before saying what they want.
  private(set) var awaitingPartPhoto = false
  /// The photo of a part to add is looked at by the backend while the user says what they want.
  @ObservationIgnored private var partPhotoTask: Task<Void, Never>?
  var backendURL: String {
    didSet { UserDefaults.standard.set(backendURL, forKey: Self.backendURLKey) }
  }
  private(set) var backendStatus = "not checked"
  private(set) var backendInfo: BackendInfo?
  private(set) var guide: GuideStatus?
  private(set) var generation: GenerationResult?
  /// What the backend is doing while the sketch is generated, e.g. "Compiling for the Arduino UNO Q".
  private(set) var generationStage: String?
  /// Why the last upload failed. Shown next to the approve button, which doubles as "retry".
  private(set) var deployError: String?
  /// True until the user has read the "how it works" card that precedes the upload approval.
  /// A failed step can be retried where it failed instead of starting the whole build over.
  private(set) var canRetry = false
  @ObservationIgnored private var retryAction: (@MainActor () async -> Void)?
  @ObservationIgnored private var retryFlow: SessionPhase?
  /// A log line from the board that proves the build reacts; set when the test confirmed itself.
  private(set) var boardProof: String?
  /// After a spoken follow-up request, the app continues by itself unless the user speaks again.
  private(set) var autoGoSeconds = 0
  @ObservationIgnored private var autoGoTask: Task<Void, Never>?
  /// True only while a question about the step is being recorded (the Ask button).
  private(set) var isAskingByVoice = false
  /// Read instructions aloud.
  var readAloud: Bool {
    didSet {
      UserDefaults.standard.set(readAloud, forKey: "readAloud")
      if !readAloud { speaker.stop() }
    }
  }
  /// Say "next" or "back" during wiring instead of pressing a button (needs glasses speech).
  var handsFree: Bool {
    didSet {
      UserDefaults.standard.set(handsFree, forKey: "handsFree")
      if handsFree { startCommandLoop() } else if glassesSpeech.isListening && !isAskingByVoice { glassesSpeech.cancel() }
    }
  }
  @ObservationIgnored private let speaker = SpeechOutputService()
  @ObservationIgnored private var commandLoop: Task<Void, Never>?
  /// Glasses speech has worked once in this run. Hands-free listening only starts after that, so it
  /// can never be the thing that sends the user to the Meta AI app for a permission.
  @ObservationIgnored private var glassesSpeechWorks = false
  private(set) var build: Build?

  /// What the flow is doing. `.connected` here means "idle"; `phase` folds in the glasses link.
  private var flow: SessionPhase = .connected

  @ObservationIgnored private let camera: CameraService
  @ObservationIgnored private var pollTask: Task<Void, Never>?
  @ObservationIgnored private var phoneSpeechTimeout: Task<Void, Never>?

  init(wearables: any WearablesInterface) {
    self.glasses = GlassesManager(wearables: wearables)
    self.camera = CameraService(wearables: wearables)
    self.glassesSpeech = GlassesSpeechService(wearables: wearables)
    self.readAloud = UserDefaults.standard.object(forKey: "readAloud") as? Bool ?? true
    self.handsFree = UserDefaults.standard.bool(forKey: "handsFree")
    self.backendURL = UserDefaults.standard.string(forKey: Self.backendURLKey) ?? Self.defaultBackendURL
  }

  var phase: SessionPhase {
    if flow == .connected {
      return glasses.isDisplayReady ? .connected : .disconnected
    }
    return flow
  }

  var isIdle: Bool { flow == .connected }
  /// The step on screen. "Back" looks at earlier steps without undoing them (`stepsBack` > 0).
  var stepIndex: Int { max((guide?.currentStep ?? 0) - stepsBack, 0) }
  private(set) var stepsBack = 0
  var isLookingBack: Bool { stepsBack > 0 }
  var canGoBack: Bool { stepIndex > 0 }
  var currentStep: WiringStep? {
    guard let steps = guide?.wiringSteps, steps.indices.contains(stepIndex) else { return nil }
    return steps[stepIndex]
  }
  var isLastStep: Bool { !isLookingBack && stepIndex >= (guide?.wiringSteps.count ?? 0) - 1 }
  var pendingConfirmation: PendingConfirmation? { guide?.pendingConfirmations.first }
  /// A new project starts with its parts list: one "I have them" instead of a question per part.
  var showsPartsList: Bool {
    guard flow == .confirmingParts, !partsAcknowledged, let guide, !guide.partsNeeded.isEmpty else { return false }
    return guide.pendingConfirmations.contains { $0.kind == "presence" }
  }
  var partsListText: String { "You need: " + (guide?.partsNeeded ?? []).joined(separator: ", ") + "." }
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
    if glassesSpeech.isListening && !isAskingByVoice && flow == .guiding {
      glassesSpeech.cancel()  // hands-free listening gives way to an explicit question
    }
    guard isIdle || flow == .success || flow == .guiding, !isListening else { return }
    cancelAutoGo()
    speaker.stop()  // do not record our own voice
    if flow == .success { extensionProblem = nil }  // a new attempt replaces the last rejection
    isAskingByVoice = flow == .guiding
    if let session = glasses.session, !glasses.isSimulated {
      let started = await glassesSpeech.start(on: session) { [weak self] text in
        self?.heard(text)
        Task { await self?.pushCard() }
      }
      if started {
        glassesSpeechWorks = true
        await pushCard()
        return
      }
    }
    await speech.start()
    // The phone microphone does not end by itself: stop after a while, like the glasses do.
    phoneSpeechTimeout?.cancel()
    phoneSpeechTimeout = Task { [weak self] in
      try? await Task.sleep(for: .seconds(15))
      guard let self, !Task.isCancelled, self.speech.isRecording else { return }
      await self.finishVoiceInput()
    }
    await pushCard()
  }

  /// Ends any voice input without using what was heard. Called when the flow moves on, so a
  /// recording that was never finished cannot linger into later steps.
  private func stopListening() {
    phoneSpeechTimeout?.cancel()
    phoneSpeechTimeout = nil
    isAskingByVoice = false
    glassesSpeech.cancel()
    if speech.isRecording { speech.stop() }
  }

  func finishVoiceInput() async {
    phoneSpeechTimeout?.cancel()
    phoneSpeechTimeout = nil
    if glassesSpeech.isListening {
      glassesSpeech.finish()
    } else {
      speech.stop()
      if !speech.transcript.isEmpty { heard(speech.transcript) }
    }
    await pushCard()
  }

  /// Spoken text is the goal before a build, and the follow-up request after a finished one.
  func heard(_ text: String) {
    isAskingByVoice = false
    if flow == .success {
      extensionText = text
      startAutoGo()  // continue by itself; "Speak again" stops it
    } else if flow == .guiding {
      Task { await ask(text) }  // during wiring, speech is a question about the step
    } else {
      goal = text
    }
  }

  /// Shows the previous step again. Nothing is undone; "Next" comes back to the open step.
  func back() async {
    guard flow == .guiding, canGoBack else { return }
    stepAnswer = nil
    stepsBack += 1
    await pushCard()
  }

  // MARK: - Questions during the build

  /// Asks the backend a question about the current step. The step itself does not change.
  func ask(_ question: String) async {
    let question = question.trimmingCharacters(in: .whitespacesAndNewlines)
    guard let guide, !question.isEmpty, !isAnswering else { return }
    isAnswering = true
    stepAnswer = nil
    await pushCard()
    do {
      let answer = try await api.ask(guide.sessionId, question: question)
      stepAnswer = answer
      questionText = ""
      say(answer.answer)
    } catch {
      glasses.errorMessage = error.localizedDescription
    }
    isAnswering = false
    await pushCard()
  }

  /// Back to the step card.
  func dismissAnswer() async {
    stepAnswer = nil
    await pushCard()
  }

  // MARK: - Developing a finished build further

  /// Sends the follow-up request. A new part leads to its confirmation and wiring steps; a behaviour
  /// change goes straight to new code. Either way the new sketch needs a new approval.
  func submitExtension() async {
    cancelAutoGo()
    let answer = extensionText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard flow == .success, let guide, !answer.isEmpty else { return }
    // An answer to "what should it do?" is sent together with the request it belongs to.
    let request = extensionQuestion == nil ? answer : extensionBase + ". " + answer
    await partPhotoTask?.value  // the backend should know which part the photo showed
    partPhotoTask = nil
    do {
      let status = try await api.extend(guide.sessionId, instruction: request)
      extensionText = ""
      if !status.extensionProblem.isEmpty {
        // Cannot be built: say why, and offer requests that would work. Nothing was changed.
        extensionProblem = status.extensionProblem
        extensionIdeas = status.suggestions
        extensionQuestion = nil
        extensionBase = ""
        showingIdeas = false
        say(status.extensionProblem + (status.suggestions.isEmpty ? "" : " Here are some ideas that would work."))
        await pushCard()
        return
      }
      extensionProblem = nil
      if !status.clarification.isEmpty {
        // The part is clear but not its job: ask, and keep the finished build as it is.
        extensionQuestion = status.clarification
        extensionIdeas = status.suggestions
        showingIdeas = false
        extensionBase = request
        await pushCard()
        return
      }
      extensionQuestion = nil
      extensionIdeas = []
      showingIdeas = false
      extensionBase = ""
      build = nil
      generation = nil
      isExtending = false
      partsAcknowledged = true  // an added part is asked for by itself, without a parts list
      await apply(status)
    } catch {
      // Not fatal: the finished build stays as it is. Say what went wrong on the glasses too.
      glasses.errorMessage = error.localizedDescription
      extensionProblem = "That did not go through: " + error.localizedDescription
      extensionText = ""
      extensionQuestion = nil
      extensionIdeas = []
      showingIdeas = false
      extensionBase = ""
      await pushCard()
    }
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
        await fail(error.localizedDescription, resume: .connected) { [weak self] in await self?.captureAndStart() }
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
      await fail(error.localizedDescription) { [weak self] in await self?.analyze(photo: photo) }
    }
  }

  /// Moves the UI to wherever the backend's session state machine is.
  private func apply(_ status: GuideStatus) async {
    if status.currentStep != guide?.currentStep { stepsBack = 0 }  // a new open step: show it
    guide = status
    if status.state == .wiring || status.state == .needsConfirmation {
      var spoken = status.spokenText  // the step or the question, read aloud
      let plan = "\(status.sessionId)-\(status.wiringSteps.count)"
      if status.state == .wiring, !status.backgroundNotice.isEmpty, noticeGivenFor != plan {
        // Once per wiring plan: the code is being written while the user wires.
        noticeGivenFor = plan
        noticeStep = status.currentStep
        spoken = status.backgroundNotice + " " + spoken
      }
      let listsParts = status.state == .needsConfirmation && !partsAcknowledged && !status.partsNeeded.isEmpty
        && status.pendingConfirmations.contains { $0.kind == "presence" }
      say(listsParts ? "You need: " + status.partsNeeded.joined(separator: ", ") + ". Do you have them?" : spoken)
      if handsFree { startCommandLoop() }
    }
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

  /// "I don't know": show or hide the example answers.
  func showIdeas(_ show: Bool) async {
    showingIdeas = show
    await pushCard()
  }

  /// Picks one of the example answers and continues with it.
  func chooseIdea(_ index: Int) async {
    guard extensionIdeas.indices.contains(index) else { return }
    extensionText = extensionIdeas[index]
    showingIdeas = false
    await submitExtension()
  }

  /// Drops a follow-up request that was waiting for an answer.
  func cancelExtension() async {
    extensionIdeas = []
    showingIdeas = false
    extensionQuestion = nil
    extensionProblem = nil
    extensionBase = ""
    extensionText = ""
    isExtending = false
    awaitingPartPhoto = false
    partPhotoTask = nil
    cancelAutoGo()
    if isListening { stopListening() }
    await pushCard()
  }

  // MARK: - Photo of an added part

  /// "Continue building" after a finished build. The first thing asked is a photo of a new part,
  /// before the user says what they want; it is sent to the backend once the request is known.
  func beginExtension() async {
    guard flow == .success, !isExtending else { return }
    isExtending = true
    awaitingPartPhoto = true
    partPhotoTask = nil
    await pushCard()
  }

  /// "Change this build": same parts, new behaviour. No photo; the user says what should change.
  func beginChange() async {
    guard flow == .success, !isExtending else { return }
    isExtending = true
    awaitingPartPhoto = false
    partPhotoTask = nil
    await startVoiceInput()
    await pushCard()
  }

  /// Takes the photo of the new part with the glasses camera, then asks what to add or change.
  func takePartPhoto() async {
    guard awaitingPartPhoto, flow == .success else { return }
    guard canCaptureFromGlasses else {
      await skipPartPhoto()
      return
    }
    await setFlow(.capturing)
    let photo = await glassesPhoto(fatal: false)
    await setFlow(.success)  // on a failed capture the photo card is shown again
    if let photo {
      await keepPartPhoto(photo)
    }
  }

  /// Keeps a photo of the new part (from the glasses or the iPhone camera) for the request that follows.
  func keepPartPhoto(_ jpeg: Data) async {
    guard awaitingPartPhoto, let guide else { return }
    awaitingPartPhoto = false
    // Sent right away: it tells the backend which part "this sensor" is. If it fails, the part is
    // named in words and confirmed by questions alone.
    let api = self.api
    partPhotoTask = Task {
      _ = try? await api.analyze(guide.sessionId, photoJPEG: jpeg, confirmedInventory: nil, purpose: "part")
    }
    await startVoiceInput()  // next: say what to add or change
    await pushCard()
  }

  /// Continue without a photo, for a behaviour change or when the camera is not available.
  func skipPartPhoto() async {
    guard awaitingPartPhoto else { return }
    awaitingPartPhoto = false
    await startVoiceInput()
    await pushCard()
  }

  // MARK: - Confirming parts

  /// The answer to the parts list. "Yes" confirms that every listed part is on the desk; the checks
  /// on each physical part (pin labels) are still asked one by one afterwards.
  func answerPartsList(_ yes: Bool) async {
    guard showsPartsList, let guide else { return }
    guard yes else {
      await fail("This build needs exactly those parts. Start over when you have them.")
      return
    }
    var ids: [String] = []
    for pending in guide.pendingConfirmations where pending.kind == "presence" && !ids.contains(pending.componentId) {
      ids.append(pending.componentId)
    }
    partsAcknowledged = true
    do {
      let items = ids.map { InventoryItem(componentId: $0, confirmations: []) }
      await apply(try await api.analyze(guide.sessionId, photoJPEG: nil, confirmedInventory: items))
    } catch {
      partsAcknowledged = false
      await fail(error.localizedDescription, resume: .confirmingParts) { [weak self] in
        await self?.answerPartsList(true)
      }
    }
  }

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
      await fail(error.localizedDescription, resume: .confirmingParts) { [weak self] in
        await self?.answerConfirmation(true)
      }
    }
  }

  // MARK: - Guiding

  /// The user confirms the current wiring step. After the last one the sketch is generated.
  func next() async {
    guard flow == .guiding, let guide, let index = guide.currentStep else { return }
    stepAnswer = nil
    if stepsBack > 0 {
      // Moving forward again through steps that are already confirmed.
      stepsBack -= 1
      await pushCard()
      return
    }
    do {
      await apply(try await api.confirmStep(guide.sessionId, index: index, confirmed: true))
    } catch {
      await fail(error.localizedDescription, resume: .guiding) { [weak self] in await self?.next() }
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
    generationStage = nil
    await setFlow(.generating)
    // Generation takes a while with real AI. Show each stage on the glasses instead of a silent wait.
    let progress = Task { [weak self] in
      while !Task.isCancelled {
        try? await Task.sleep(for: .seconds(1))
        guard let self, !Task.isCancelled, self.flow == .generating else { return }
        guard let stage = try? await self.api.status(guide.sessionId).generationProgress.last else { continue }
        if stage != self.generationStage, !Task.isCancelled, self.flow == .generating {
          self.generationStage = stage
          await self.pushCard()
        }
      }
    }
    defer { progress.cancel() }
    do {
      let result = try await api.generate(guide.sessionId, sensorActiveLevel: sensorActiveLevel)
      generation = result
      build = try await api.getBuild(result.buildId)
      await setFlow(.awaitingApproval)
      say(result.displaySummary + " Start the flash when you are ready.")
    } catch {
      await fail(error.localizedDescription) { [weak self] in
        await self?.generateSketch(sensorActiveLevel: sensorActiveLevel)
      }
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
      await fail(error.localizedDescription, resume: .awaitingApproval) { [weak self] in await self?.approve() }
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
        // A line from the board that only appears when the build reacts (anything but READY) is proof.
        if let proof = latest.deploy?.observedEvents.first(where: {
          // READY and NEW_GAME are printed at power-up, without the user doing anything.
          $0 != "READY" && $0 != "NEW_GAME" && latest.expectedSerialEvents.contains($0)
        }) {
          self.boardProof = proof
          await self.reportTest(passed: true)
          return
        }
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
        say(boardProof == nil ? "It works." : "The board confirms it. It works.")
      } else {
        await fail("The behavior test failed. Check the wiring and the sensor's trim pot, then start over.")
      }
    } catch {
      await fail(error.localizedDescription, resume: .testing) { [weak self] in
        await self?.reportTest(passed: passed)
      }
    }
  }

  func reset() async {
    pollTask?.cancel()
    pollTask = nil
    build = nil
    generation = nil
    deployError = nil
    extensionText = ""
    isExtending = false
    awaitingPartPhoto = false
    partPhotoTask = nil
    stepAnswer = nil
    stepsBack = 0
    questionText = ""
    extensionQuestion = nil
    extensionIdeas = []
    showingIdeas = false
    extensionBase = ""
    extensionProblem = nil
    retryAction = nil
    canRetry = false
    boardProof = nil
    partsAcknowledged = false
    noticeStep = nil
    goal = ""  // "Build something new" starts with the question again
    cancelAutoGo()
    speaker.stop()
    guide = nil
    await setFlow(.connected)
  }

  // MARK: - Glasses card

  private func setFlow(_ newFlow: SessionPhase) async {
    if newFlow != flow, isListening {
      stopListening()
    }
    flow = newFlow
    await pushCard()
  }

  /// Shows an error. With `retry`, the user can repeat just the failed step: the flow goes back to
  /// `resume` (default: where it was) and the step runs again.
  private func fail(_ message: String, resume: SessionPhase? = nil,
                    retry: (@MainActor () async -> Void)? = nil) async {
    retryAction = retry
    retryFlow = resume ?? flow
    canRetry = retry != nil
    await setFlow(.error(message))
  }

  /// Repeats the step that failed, keeping everything that was already confirmed.
  func retryLast() async {
    guard case .error = flow, let action = retryAction, let resume = retryFlow else { return }
    retryAction = nil
    canRetry = false
    flow = resume
    await action()
  }

  private func say(_ text: String) {
    if readAloud { speaker.speak(text) }
  }

  // MARK: - Continuing by itself after a spoken follow-up request

  private func startAutoGo() {
    cancelAutoGo()
    autoGoSeconds = 3
    autoGoTask = Task { [weak self] in
      while let self, !Task.isCancelled, self.autoGoSeconds > 0 {
        try? await Task.sleep(for: .seconds(1))
        guard !Task.isCancelled else { return }
        self.autoGoSeconds -= 1
        await self.pushCard()
      }
      guard let self, !Task.isCancelled, self.flow == .success else { return }
      await self.submitExtension()
    }
  }

  private func cancelAutoGo() {
    autoGoTask?.cancel()
    autoGoTask = nil
    autoGoSeconds = 0
  }

  // MARK: - Hands-free: "next" and "back" by voice during wiring

  /// While wiring with hands-free on, keeps listening through the glasses for "next" or "back".
  private func startCommandLoop() {
    guard commandLoop == nil else { return }
    commandLoop = Task { [weak self] in
      while let self, !Task.isCancelled, self.handsFree {
        try? await Task.sleep(for: .milliseconds(700))
        guard self.glassesSpeechWorks, self.flow == .guiding, !self.isListening, !self.isAnswering, self.stepAnswer == nil,
          !self.speaker.isSpeaking, let session = self.glasses.session, !self.glasses.isSimulated
        else { continue }
        _ = await self.glassesSpeech.start(on: session) { [weak self] text in
          Task { await self?.handleCommand(text) }
        }
      }
      self?.commandLoop = nil
    }
  }

  private func handleCommand(_ text: String) async {
    let words = Set(text.lowercased().split { !$0.isLetter }.map(String.init))
    if !words.isDisjoint(with: ["next", "continue", "done", "weiter", "fertig"]) {
      await next()
    } else if !words.isDisjoint(with: ["back", "previous", "zurück"]) {
      await back()
    }
    // Anything else is ignored: questions go through the Ask button.
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
          text: "What do you want to build today?",
          primary: action("Speak") { await $0.startVoiceInput() }
        )
      }
      // No photo is needed to start; it stays possible as an extra check of the parts.
      return GlassesCards.card(
        caption: demo + "You want to build",
        text: goal,
        primary: action("Start") { await $0.start(withPhoneJPEG: nil) },
        secondary: action("Speak again") { await $0.startVoiceInput() },
        tertiary: canCaptureFromGlasses ? action("Check with photo") { await $0.captureAndStart() } : nil
      )
    case .capturing:
      return GlassesCards.card(caption: "Capturing", text: "Look at your parts and hold still.")
    case .analyzing:
      return GlassesCards.card(caption: demo + "One moment", text: "Checking which parts you need…")
    case .confirmingParts:
      if showsPartsList {
        return GlassesCards.card(
          caption: demo + "Parts for this build",
          text: partsListText + " Do you have them?",
          primary: action("Yes") { await $0.answerPartsList(true) },
          secondary: action("No") { await $0.answerPartsList(false) }
        )
      }
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
      if let stepAnswer {
        return GlassesCards.card(
          caption: "Answer",
          text: stepAnswer.answer,
          primary: action("Back to step") { await $0.dismissAnswer() },
          secondary: action("Ask again") { await $0.startVoiceInput() }
        )
      }
      // The step stays on screen while a question is heard and answered; only the caption changes.
      // Next is the default; Back looks at earlier steps; Ask takes a question about this step.
      let position = "Step \(stepIndex + 1) of \(guide.wiringSteps.count)"
      // On the first step of a plan: the code is being written meanwhile.
      let writing = noticeStep == stepIndex && !guide.backgroundNotice.isEmpty ? " · writing the code meanwhile" : ""
      let caption = isAskingByVoice ? "Listening… ask your question" : isAnswering ? "Thinking…" : demo + position + writing
      return GlassesCards.card(
        caption: caption,
        text: step.displayText,
        primary: action(isLastStep ? "Wiring done" : "Next") { await $0.next() },
        secondary: canGoBack ? action("Back") { await $0.back() } : nil,
        tertiary: isAskingByVoice
          ? action("Done") { await $0.finishVoiceInput() }
          : action("Ask") { await $0.startVoiceInput() }
      )
    case .generating:
      return GlassesCards.card(caption: demo + "Generating", text: (generationStage ?? "Starting") + "…")
    case .awaitingApproval:
      // Flashing still needs an explicit yes, but it can be given on the glasses. The code itself
      // stays readable on the phone for anyone who wants to check it first.
      // One card: how the build works, and the explicit yes to flash it.
      let compiled = generation?.compile.status == "ok" ? "Code is ready and compiled." : "Code is ready (not compiled)."
      let summary = generation?.displaySummary ?? ""
      return GlassesCards.card(
        caption: demo + (deployError == nil ? "Ready to flash" : "Upload failed"),
        text: deployError == nil ? (summary.isEmpty ? compiled + " Flash it to the board?" : summary)
          : "The upload did not succeed. Try again?",
        primary: action(deployError == nil ? "Start flash" : "Retry flash") { await $0.approve() },
        secondary: action("Cancel") { await $0.reject() }
      )
    case .deploying:
      return GlassesCards.card(caption: "Deploying", text: "Uploading to the board…")
    case .testing:
      let seen = build?.deploy?.observedEvents ?? []
      return GlassesCards.card(
        caption: isSimulatedDeploy ? "Test it · SIMULATED upload" : demo + "Test it",
        text: seen.isEmpty ? "Try it out. Does it work?" : "Board reports: " + seen.joined(separator: ", "),
        primary: action("Yes") { await $0.reportTest(passed: true) },
        secondary: action("No") { await $0.reportTest(passed: false) }
      )
    case .success:
      if isListening {
        return GlassesCards.card(
          caption: "Listening…",
          text: "Say what to add or change.",
          primary: action("Done") { await $0.finishVoiceInput() }
        )
      }
      if !extensionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        return GlassesCards.card(
          caption: autoGoSeconds > 0 ? "Continuing in \(autoGoSeconds) s" : demo + "Next",
          text: extensionText,
          primary: action(autoGoSeconds > 0 ? "Go now" : "Go") { await $0.submitExtension() },
          secondary: action("Speak again") { await $0.startVoiceInput() }
        )
      }
      if let extensionProblem, !showingIdeas {
        // The request cannot be built: the reason, and a way to requests that would work.
        return GlassesCards.card(
          caption: "That doesn't work",
          text: extensionProblem,
          primary: extensionIdeas.isEmpty
            ? action("Speak again") { await $0.startVoiceInput() }
            : action("Ideas") { await $0.showIdeas(true) },
          secondary: extensionIdeas.isEmpty ? nil : action("Speak again") { await $0.startVoiceInput() },
          tertiary: action("Cancel") { await $0.cancelExtension() }
        )
      }
      if extensionQuestion != nil || extensionProblem != nil, showingIdeas {
        return GlassesCards.choices(
          caption: extensionProblem == nil ? "Ideas" : "These would work",
          text: "Pick one, or go back and say your own.",
          options: extensionIdeas.indices.map { index in
            action(extensionIdeas[index]) { await $0.chooseIdea(index) }
          },
          primary: action("Back") { await $0.showIdeas(false) },
          secondary: action("Cancel") { await $0.cancelExtension() }
        )
      }
      if let extensionQuestion {
        if showingIdeas {
          return GlassesCards.choices(
            caption: "Ideas",
            text: "Pick one, or go back and say your own.",
            options: extensionIdeas.indices.map { index in
              action(extensionIdeas[index]) { await $0.chooseIdea(index) }
            },
            primary: action("Back") { await $0.showIdeas(false) },
            secondary: action("Cancel") { await $0.cancelExtension() }
          )
        }
        return GlassesCards.card(
          caption: "One question",
          text: extensionQuestion,
          primary: action("Answer") { await $0.startVoiceInput() },
          secondary: extensionIdeas.isEmpty
            ? action("Cancel") { await $0.cancelExtension() }
            : action("Ideas") { await $0.showIdeas(true) }
        )
      }
      if awaitingPartPhoto {
        return GlassesCards.card(
          caption: demo + "Continue building",
          text: "Adding a part? Look at it and take a photo. Otherwise skip.",
          primary: action("Take photo") { await $0.takePartPhoto() },
          secondary: action("Skip") { await $0.skipPartPhoto() },
          tertiary: action("Cancel") { await $0.cancelExtension() }
        )
      }
      if isExtending {
        return GlassesCards.card(
          caption: demo + "Continue building",
          text: "What do you want to add or change?",
          primary: action("Speak") { await $0.startVoiceInput() },
          secondary: action("Cancel") { await $0.cancelExtension() }
        )
      }
      return GlassesCards.card(
        caption: isSimulatedDeploy ? "Success · SIMULATED upload" : demo + "Success",
        text: isSimulatedDeploy ? "Flow complete. No board was flashed."
          : (boardProof.map { "The board reported \($0). " } ?? "It works. ") + "What next?",
        primary: action("Change this build") { await $0.beginChange() },
        secondary: action("Build something new") { await $0.reset() },
        tertiary: action("Add a part") { await $0.beginExtension() }
      )
    case .error(let message):
      if canRetry {
        return GlassesCards.card(
          caption: "Error",
          text: message,
          primary: action("Retry") { await $0.retryLast() },
          secondary: action("Start over") { await $0.reset() }
        )
      }
      return GlassesCards.card(
        caption: "Error",
        text: message,
        primary: action("Start over") { await $0.reset() }
      )
    }
  }
}
