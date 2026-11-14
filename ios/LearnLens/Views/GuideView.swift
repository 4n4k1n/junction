//
// GuideView.swift
//
// The whole phone UI on one screen: glasses status, the simulated display, and the controls
// for the current phase. Functional on purpose; polish comes after the pipeline works.
//

import PhotosUI
import SwiftUI

struct GuideView: View {
  @Bindable var model: SessionViewModel
  @State private var pickedPhoto: PhotosPickerItem?
  @State private var pickedReviewPhoto: PhotosPickerItem?
  @State private var showPhoneCamera = false

  private var glasses: GlassesManager { model.glasses }

  var body: some View {
    NavigationStack {
      Form {
        modeBanner
        glassesSection
        if let display = glasses.mockDisplay {
          Section("Simulated glasses display") {
            MockDisplayPreview(display: display)
              .id(glasses.isSimulated)
              .frame(maxWidth: .infinity)
              .aspectRatio(1, contentMode: .fit)
              .background(.black)
              .clipShape(RoundedRectangle(cornerRadius: 16))
          }
        }
        phaseSection
        backendSection
      }
      .navigationTitle("LearnLens · \(model.phase.title)")
      .sheet(isPresented: $showPhoneCamera) {
        PhoneCameraPicker { data in
          showPhoneCamera = false
          guard let data else { return }
          Task {
            let jpeg = PhotoEncoding.uploadJPEG(from: data)
            if model.awaitingPartPhoto, let jpeg {
              await model.keepPartPhoto(jpeg)  // photo of a part that is being added
            } else {
              await model.start(withPhoneJPEG: jpeg)
            }
          }
        }
      }
      .task { await model.checkBackend() }
      .task { await model.pushCard() }
      .onChange(of: pickedPhoto) { _, item in
        guard let item else { return }
        pickedPhoto = nil
        Task {
          let data = try? await item.loadTransferable(type: Data.self)
          await model.start(withPhoneJPEG: data.flatMap { PhotoEncoding.uploadJPEG(from: $0) })
        }
      }
      .onChange(of: pickedReviewPhoto) { _, item in
        guard let item else { return }
        pickedReviewPhoto = nil
        Task {
          let data = try? await item.loadTransferable(type: Data.self)
          guard let jpeg = data.flatMap({ PhotoEncoding.uploadJPEG(from: $0) }) else { return }
          await model.reviewWiring(phoneJPEG: jpeg)
        }
      }
    }
  }

  // MARK: - Mode

  /// Says, before anything else, when this is not a real run.
  @ViewBuilder
  private var modeBanner: some View {
    if let info = model.backendInfo, info.demoMode || info.agentMock || !info.agentConnected {
      Section {
        if info.demoMode {
          Label("DEMO MODE: saved AI answers and prevalidated code. No model is called.", systemImage: "play.rectangle")
            .foregroundStyle(.orange)
        }
        if info.agentConnected && info.agentMock {
          Label("SIMULATED upload: the deploy agent runs with --mock. No board will be flashed.", systemImage: "bolt.slash")
            .foregroundStyle(.orange)
        }
        if !info.agentConnected {
          Label("No deploy agent connected. Start it on the Mac before approving.", systemImage: "exclamationmark.triangle")
            .foregroundStyle(.red)
        }
      }
    }
  }

  // MARK: - Glasses

  private var glassesSection: some View {
    Section("Glasses") {
      LabeledContent("Registration", value: glasses.registrationState.description)
      ForEach(glasses.devices) { device in
        LabeledContent(device.name, value: "\(device.type) · \(device.link)")
      }
      LabeledContent("Display", value: displayStatus)

      if !glasses.isSimulated {
        if glasses.isRegistered {
          Button("Unregister from Meta AI", role: .destructive) { Task { await glasses.unregister() } }
        } else {
          Button("Register with Meta AI") { Task { await glasses.register() } }
        }
      }
      if glasses.hasSession {
        Button("Disconnect display") { glasses.disconnect() }
      } else {
        Button("Connect display") { Task { await glasses.connect() } }
          .disabled(!glasses.isRegistered || glasses.isConnecting)
      }
      Toggle(
        "Simulated glasses (no hardware)",
        isOn: Binding(
          get: { glasses.isSimulated },
          set: { enabled in Task { await glasses.setSimulated(enabled) } }
        )
      )
      .disabled(glasses.isSwitchingSimulation || !model.isIdle)

      if glasses.requiresGlassesAppUpdate {
        Button("Update the app on the glasses") { Task { await glasses.openGlassesAppUpdate() } }
      }
      if let message = glasses.errorMessage {
        Text(message).foregroundStyle(.red)
        Button("Dismiss") { glasses.errorMessage = nil }
      }
    }
  }

  private var displayStatus: String {
    if glasses.isDisplayReady { return glasses.isSimulated ? "ready (simulated)" : "ready" }
    return glasses.isConnecting ? "connecting…" : "not connected"
  }

  // MARK: - Phase

  @ViewBuilder
  private var phaseSection: some View {
    switch model.phase {
    case .disconnected, .connected:
      goalSection
    case .capturing:
      progress("Taking a photo with the glasses…")
    case .analyzing:
      progress("Sending to the backend…")
    case .confirmingParts:
      confirmSection
    case .guiding:
      guidingSection
    case .generating:
      progress((model.generationStage ?? "Generating the sketch") + "…")
    case .awaitingApproval:
      approvalSection
    case .deploying:
      progress(deployLine)
    case .testing:
      testingSection
    case .success:
      Section("Success") {
        Text(model.isSimulatedDeploy ? "Flow complete with a SIMULATED upload. No board was flashed." : "The build works.")
        if let proof = model.boardProof {
          Label("Confirmed by the board: it reported \(proof).", systemImage: "checkmark.seal").foregroundStyle(.green)
        }
        if !model.isExtending {
          // Keep developing the same build: add a known part, or change what it does.
          Button("Continue building") { Task { await model.beginExtension() } }
          Button("New build") { Task { await model.reset() } }
        } else if model.awaitingPartPhoto {
          // Asked right away, before the request: a photo of the part that should be added.
          Text("Adding a part? Take a photo of it. Otherwise skip.").font(.title3)
          Button("Take photo with the glasses") { Task { await model.takePartPhoto() } }
            .disabled(!model.canCaptureFromGlasses)
          Button("Take photo with the iPhone") { showPhoneCamera = true }
            .disabled(!PhoneCameraPicker.isAvailable)
          Button("Skip the photo") { Task { await model.skipPartPhoto() } }
          Button("Cancel", role: .destructive) { Task { await model.cancelExtension() } }
        } else {
          if let problem = model.extensionProblem {
            // The last request cannot be built: why, and requests that would work instead.
            Text(problem).foregroundStyle(.red)
            ForEach(Array(model.extensionIdeas.enumerated()), id: \.offset) { index, idea in
              Button(idea) { Task { await model.chooseIdea(index) } }
            }
          }
          if let question = model.extensionQuestion {
            Text(question).font(.title3)  // the backend needs to know what the new part should do
            // Iterates over the values, not the indices: the list is emptied when an idea is chosen, and
            // a row that still looked up its old index would crash the app.
            ForEach(Array(model.extensionIdeas.enumerated()), id: \.offset) { index, idea in
              Button(idea) { Task { await model.chooseIdea(index) } }
            }
          }
          TextField(model.extensionQuestion == nil ? "Add a part or change what it does" : "Your answer",
                    text: $model.extensionText, axis: .vertical)
            .onSubmit { Task { await model.pushCard() } }
          Button("Go") { Task { await model.submitExtension() } }
            .disabled(model.extensionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
          Button("Cancel", role: .destructive) { Task { await model.cancelExtension() } }
        }
      }
    case .error(let message):
      Section("Error") {
        Text(message).foregroundStyle(.red)
        if model.canRetry {
          Button("Retry this step") { Task { await model.retryLast() } }  // keeps what was confirmed
        }
        Button("Start over") { Task { await model.reset() } }
      }
    }
  }

  private func progress(_ text: String) -> some View {
    Section(model.phase.title) {
      HStack {
        ProgressView()
        Text(text)
      }
    }
  }

  private var goalSection: some View {
    Section("What do you want to build?") {
      TextField("Build a sound level meter", text: $model.goal, axis: .vertical)
        .onSubmit { Task { await model.pushCard() } }  // show the typed goal on the glasses too

      Label(
        model.speech.isRecording ? "Listening… release to stop" : "Hold to talk",
        systemImage: model.speech.isRecording ? "waveform" : "mic"
      )
      .frame(maxWidth: .infinity)
      .padding(.vertical, 8)
      .foregroundStyle(model.speech.isRecording ? .red : .accentColor)
      .contentShape(Rectangle())
      .onLongPressGesture(minimumDuration: 0, perform: {}) { pressing in
        if pressing {
          Task { await model.speech.start() }
        } else {
          model.speech.stop()
        }
      }
      .onChange(of: model.speech.transcript) { _, text in
        if !text.isEmpty { model.heard(text) }
      }
      if let message = model.glassesSpeech.errorMessage ?? model.speech.errorMessage {
        Text(message).font(.footnote).foregroundStyle(.secondary)
      }

      Button("Capture with glasses and start") { Task { await model.captureAndStart() } }
        .disabled(!model.canCaptureFromGlasses)
      Button("Take a photo with the iPhone and start") { showPhoneCamera = true }
        .disabled(!PhoneCameraPicker.isAvailable)
      PhotosPicker("Pick a saved photo and start", selection: $pickedPhoto, matching: .images)
      Button("Start without a photo") { Task { await model.start(withPhoneJPEG: nil) } }
    }
  }

  private var confirmSection: some View {
    Section("Confirm your parts") {
      if let guide = model.guide {
        // What the vision model thinks it saw: a suggestion, shown with its confidence.
        ForEach(Array(guide.detectedComponents.enumerated()), id: \.offset) { _, part in
          VStack(alignment: .leading, spacing: 2) {
            Text("\(part.label) · \(Int(part.confidence * 100))%").font(.footnote)
            Text(part.evidence).font(.caption2).foregroundStyle(.secondary)
          }
        }
        if guide.detectedComponents.isEmpty {
          Text("No parts were detected from a photo (\(guide.analysisSource)).").font(.footnote)
        }
      }
      if let pending = model.pendingConfirmation {
        Text(pending.question).font(.title3)
        Text(pending.reason).font(.footnote).foregroundStyle(.secondary)
        Button("Yes") { Task { await model.answerConfirmation(true) } }
        Button("No", role: .destructive) { Task { await model.answerConfirmation(false) } }
      }
    }
  }

  private var guidingSection: some View {
    Section("Wiring") {
      if let guide = model.guide, let step = model.currentStep {
        Text("Step \(model.stepIndex + 1) of \(guide.wiringSteps.count)")
          .font(.footnote)
          .foregroundStyle(.secondary)
        Text(step.displayText).font(.title3)
        if let connection = step.connection {
          Text(
            "\(connection.fromPin.component) \(connection.fromPin.pin) → \(connection.toPin.component) \(connection.toPin.pin)"
          )
          .font(.body.monospaced())
        }
        // Hardware facts come from the backend's database, not from a model. Show their confidence.
        ForEach(guide.hardwareFacts.filter { step.factIds.contains($0.id) }) { fact in
          VStack(alignment: .leading, spacing: 2) {
            Text(fact.statement).font(.footnote)
            Text("Hardware fact · \(fact.confidence)")
              .font(.caption2)
              .foregroundStyle(fact.confidence == "official_doc" ? Color.secondary : Color.orange)
          }
        }
      }
      Button(model.isLookingBack ? "Next" : model.isLastStep ? "I did this. Wiring done" : "I did this. Next") {
        Task { await model.next() }
      }
      Button("Back") { Task { await model.back() } }
        .disabled(!model.canGoBack)

      // A question about this step, answered from the wiring plan and the verified facts.
      TextField("Ask a question about this step", text: $model.questionText, axis: .vertical)
      Button(model.isAnswering ? "Thinking…" : "Ask") { Task { await model.ask(model.questionText) } }
        .disabled(model.isAnswering || model.questionText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
      if let answer = model.stepAnswer {
        VStack(alignment: .leading, spacing: 2) {
          Text(answer.question).font(.caption2).foregroundStyle(.secondary)
          Text(answer.answer).font(.footnote)
        }
      }

      Button("Check my wiring with a glasses photo") { Task { await model.reviewWiring(phoneJPEG: nil) } }
        .disabled(!model.canCaptureFromGlasses)
      PhotosPicker("Check my wiring with a phone photo", selection: $pickedReviewPhoto, matching: .images)
      if let review = model.guide?.review {
        ForEach(review.observations, id: \.self) { Text($0).font(.footnote) }
        ForEach(review.possibleIssues, id: \.self) { Text("Re-check: \($0)").font(.footnote).foregroundStyle(.orange) }
        Text(review.disclaimer).font(.caption2).foregroundStyle(.secondary)
      }
    }
  }

  private var approvalSection: some View {
    Section("Approve this sketch?") {
      if let sketch = model.build?.sketch {
        Text("\(sketch.filename) · generated by: \(sketch.provenance)").font(.footnote)
        ScrollView(.horizontal) {
          Text(sketch.code).font(.caption.monospaced())
        }
        if let generation = model.generation {
          Text(generation.explanation).font(.footnote)
          ForEach(generation.assumptions, id: \.self) { Text("Assumes: \($0)").font(.caption2).foregroundStyle(.secondary) }
          // Compiling is not running: say plainly what was and was not checked.
          Text(compileLine(generation)).font(.caption2)
            .foregroundStyle(generation.compile.status == "ok" ? Color.secondary : Color.orange)
        }
        Text("Nothing is uploaded to the board until you approve.").font(.footnote).foregroundStyle(.secondary)
      }
      if let deployError = model.deployError {
        Text("The last upload did not succeed:\n\(deployError)").font(.footnote).foregroundStyle(.red)
      }
      Button(model.deployError == nil ? "Approve and upload to the board" : "Approve again and retry the upload") {
        Task { await model.approve() }
      }
      Button("Reject", role: .destructive) { Task { await model.reject() } }
    }
  }

  /// Latest status line streamed from the Mac deploy agent.
  private var deployLine: String {
    guard let event = model.build?.deployEvents.last else {
      return model.backendInfo?.agentConnected == false
        ? "No deploy agent is connected. Start it on the Mac: ./scripts/start.sh"
        : "Waiting for the Mac deploy agent…"
    }
    return "\(event.stage): \(event.message)"
  }

  private func compileLine(_ generation: GenerationResult) -> String {
    switch generation.compile.status {
    case "ok": "Compiled for \(generation.compile.fqbn). Not yet run on a board."
    case "failed": "Did not compile."
    default: "Not compiled on the backend: \(generation.compile.detail)"
    }
  }

  private var testingSection: some View {
    Section("Test it") {
      if model.build?.deploy?.mock == true {
        Text("Simulated upload: no board was flashed.").foregroundStyle(.orange)
      }
      if let deploy = model.build?.deploy, !deploy.mock {
        // Only lines the agent actually read from the board are listed.
        Text(deploy.runtimeLog.isEmpty ? "No log output read from the board yet." : "Board log: " + deploy.runtimeLog.joined(separator: ", "))
          .font(.footnote)
        if deploy.observedEvents.contains("SOUND_DETECTED") {
          Label("The board reported the clap.", systemImage: "checkmark.circle").foregroundStyle(.green)
        }
      }
      Text("Try the behavior now. If the board reports that it reacts, this confirms itself. Otherwise: does it work?")
      Button("Yes, it works") { Task { await model.reportTest(passed: true) } }
      Button("No") { Task { await model.reportTest(passed: false) } }
    }
  }

  // MARK: - Backend

  @ViewBuilder
  private var backendSection: some View {
    Section("Options") {
      Toggle("Read steps aloud", isOn: $model.readAloud)
      Toggle("Voice commands while wiring: \"next\", \"back\" (experimental)", isOn: $model.handsFree)
    }
    Section("Backend") {
      TextField("http://<mac-ip>:8000", text: $model.backendURL)
        .autocorrectionDisabled()
      LabeledContent("Status", value: model.backendStatus)
      Button("Check connection") { Task { await model.checkBackend() } }
    }
  }
}
