//
// BuildModels.swift
//
// Swift mirror of the backend build API (shared/schemas/build-api.schema.json).
// Decoded with .convertFromSnakeCase, so build_id arrives as buildId.
//

import Foundation

/// GET /health: what the backend is running with, so the app can label demo and simulated runs.
struct BackendInfo: Decodable, Sendable {
  let demoMode: Bool        // saved AI answers and the prevalidated sketch: no model calls
  let analyzer: String      // claude | auto | mock | demo
  let agentConnected: Bool  // a Mac deploy agent polled within the last 10 s
  let agentMock: Bool       // that agent runs with --mock: it will not flash a board
}

enum BuildStatus: String, Decodable, Sendable {
  case wiring
  case awaitingApproval = "awaiting_approval"
  case approved
  case deploying
  case awaitingTest = "awaiting_test"
  case done
  case failed
  case rejected
}

struct HardwareFact: Decodable, Identifiable, Sendable {
  let id: String
  let component: String
  let statement: String
  let source: String
  let confidence: String  // official_doc | secondary | unverified
}

struct PinRef: Decodable, Sendable {
  let component: String
  let pin: String
}

struct Connection: Decodable, Sendable {
  let fromPin: PinRef
  let toPin: PinRef
}

struct WiringStep: Decodable, Identifiable, Sendable {
  let index: Int
  let displayText: String
  let spokenText: String
  let connection: Connection?
  let factIds: [String]

  var id: Int { index }
}

struct Sketch: Decodable, Sendable {
  let filename: String
  let code: String
  let sha256: String
  let provenance: String  // mock | llm
}

struct DeployEvent: Decodable, Sendable {
  let stage: String   // validate | compile | approval | device | upload | logs
  let status: String  // started | ok | failed
  let message: String
}

struct DeployResult: Decodable, Sendable {
  let compileOk: Bool
  let uploadOk: Bool  // true only when the upload tool itself reported success
  let log: String
  let mock: Bool      // true when the agent ran in --mock mode: no board was flashed
  let failedStage: String
  let runtimeLog: [String]
  let observedEvents: [String]
}

struct Build: Decodable, Sendable {
  let buildId: String
  let goal: String
  let status: BuildStatus
  let provenance: String
  let components: [String]
  let hardwareFacts: [HardwareFact]
  let wiringSteps: [WiringStep]
  let sketch: Sketch?
  let deploy: DeployResult?
  let deployEvents: [DeployEvent]
  let testPassed: Bool?
  let error: String
}

// MARK: - Guided session (POST /sessions ...)

enum GuideState: String, Decodable, Sendable {
  case awaitingAnalysis = "awaiting_analysis"
  case needsConfirmation = "needs_confirmation"
  case wiring
  case wiringComplete = "wiring_complete"
  case codeGenerated = "code_generated"
  case unsupported
  case blocked
}

struct DetectedComponent: Decodable, Sendable {
  let componentId: String
  let label: String
  let confidence: Double
  let evidence: String
}

struct InventoryItem: Decodable, Sendable {
  let componentId: String
  let confirmations: [String]
}

struct PendingConfirmation: Decodable, Identifiable, Sendable {
  let id: String
  let componentId: String
  let kind: String  // presence | variant
  let question: String
  let reason: String
}

struct WiringReview: Decodable, Sendable {
  let observations: [String]
  let possibleIssues: [String]
  let electricallyVerified: Bool  // always false: a photo cannot prove a connection
  let disclaimer: String
}

struct GuideStatus: Decodable, Sendable {
  let sessionId: String
  let state: GuideState
  let instruction: String
  let displayText: String
  let spokenText: String
  let analysisSource: String
  let detectedComponents: [DetectedComponent]
  let confirmedInventory: [InventoryItem]
  let pendingConfirmations: [PendingConfirmation]
  let hardwareFacts: [HardwareFact]
  let wiringSteps: [WiringStep]
  let currentStep: Int?
  let confirmedSteps: [Int]
  let review: WiringReview?
  let safetyProblems: [String]
  let generationProgress: [String]  // what code generation is doing; the last entry is current
  let clarification: String         // a question back to the user before a build can be developed further
  let suggestions: [String]         // example answers to that question
  let buildId: String?
}

/// Answer to a question asked during the build (POST /sessions/{id}/ask).
struct StepAnswer: Decodable, Sendable {
  let question: String
  let answer: String
  let source: String  // llm | facts (no AI: the verified facts for the current step)
}

// MARK: - Code generation (POST /sessions/{id}/generate)

struct SerialEvent: Decodable, Sendable {
  let event: String
  let when: String
}

struct CompileReport: Decodable, Sendable {
  let status: String  // ok | failed | not_run
  let fqbn: String
  let detail: String
}

struct GenerationResult: Decodable, Sendable {
  let buildId: String
  let source: String  // llm | template
  let explanation: String
  let displaySummary: String   // short version for the glasses display
  let assumptions: [String]
  let expectedSerialEvents: [SerialEvent]
  let repairsUsed: Int
  let compile: CompileReport
  let flashed: Bool            // always false: generation never uploads
  let hardwareVerified: Bool   // always false: nothing ran on a board
}
