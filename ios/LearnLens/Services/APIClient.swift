//
// APIClient.swift
//
// Talks to the LearnLens FastAPI backend. No API keys live in the app: the backend holds them.
//

import Foundation

enum APIError: LocalizedError {
  case badURL(String)
  case server(status: Int, message: String)
  case unreachable(String)

  var errorDescription: String? {
    switch self {
    case .badURL(let url): "Backend URL is not valid: \(url)"
    case .server(let status, let message): "Backend error \(status): \(message)"
    case .unreachable(let reason): "Backend not reachable: \(reason)"
    }
  }
}

struct APIClient: Sendable {
  let baseURL: String

  func health() async throws -> BackendInfo {
    var req = try request("GET", "/health")
    req.timeoutInterval = 5  // a wrong address should fail fast
    return try await decode(req)
  }

  // MARK: Guided session: instruction + photo -> confirmed parts -> wiring steps

  func createSession(instruction: String) async throws -> GuideStatus {
    try await decode(jsonRequest("/sessions", ["instruction": instruction]))
  }

  /// Multipart upload. Before wiring it detects the parts and plans; during wiring it reviews the photo.
  /// `confirmedInventory` carries what the user confirmed; an empty list means "no photo, ask me".
  func analyze(_ id: String, photoJPEG: Data?, confirmedInventory: [InventoryItem]?) async throws -> GuideStatus {
    let boundary = "learnlens-\(UUID().uuidString)"
    var body = Data()
    if let confirmedInventory {
      let items = confirmedInventory.map { ["component_id": $0.componentId, "confirmations": $0.confirmations] as [String: Any] }
      let json = String(data: try JSONSerialization.data(withJSONObject: items), encoding: .utf8) ?? "[]"
      body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"confirmed_inventory\"\r\n\r\n\(json)\r\n")
    }
    if let photoJPEG {
      body.append("--\(boundary)\r\nContent-Disposition: form-data; name=\"image\"; filename=\"photo.jpg\"\r\n")
      body.append("Content-Type: image/jpeg\r\n\r\n")
      body.append(photoJPEG)
      body.append("\r\n")
    }
    body.append("--\(boundary)--\r\n")

    var req = try request("POST", "/sessions/\(id)/analyze")
    req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
    req.httpBody = body
    return try await decode(req)
  }

  func confirmStep(_ id: String, index: Int, confirmed: Bool) async throws -> GuideStatus {
    try await decode(jsonRequest("/sessions/\(id)/confirm-step", ["step_index": index, "confirmed": confirmed]))
  }

  func status(_ id: String) async throws -> GuideStatus {
    try await decode(request("GET", "/sessions/\(id)/status"))
  }

  /// Writes, checks and compiles the sketch for the confirmed wiring. Never uploads.
  /// `sensorActiveLevel` ("HIGH"/"LOW") flips the sensor polarity when regenerating.
  func generate(_ id: String, sensorActiveLevel: String? = nil) async throws -> GenerationResult {
    var req = try jsonRequest("/sessions/\(id)/generate", sensorActiveLevel.map { ["sensor_active_level": $0] } ?? [:])
    req.timeoutInterval = 300  // up to three model calls and compiles
    return try await decode(req)
  }

  // MARK: Build: sketch approval, deploy, test

  func getBuild(_ id: String) async throws -> Build {
    try await decode(request("GET", "/builds/\(id)"))
  }

  /// Explicit approval of one exact sketch. The backend rejects a hash that is not the current sketch.
  func approve(_ id: String, sketchSHA256: String) async throws -> Build {
    try await decode(jsonRequest("/builds/\(id)/approve", ["sketch_sha256": sketchSHA256]))
  }

  func reject(_ id: String) async throws -> Build {
    try await decode(request("POST", "/builds/\(id)/reject"))
  }

  func reportTest(_ id: String, passed: Bool, note: String) async throws -> Build {
    try await decode(jsonRequest("/builds/\(id)/test-result", ["passed": passed, "note": note]))
  }

  // MARK: - Plumbing

  private func request(_ method: String, _ path: String) throws -> URLRequest {
    let trimmed = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
    guard let url = URL(string: trimmed + path), url.scheme != nil, url.host != nil else {
      throw APIError.badURL(baseURL)
    }
    var req = URLRequest(url: url)
    req.httpMethod = method
    req.timeoutInterval = 60
    return req
  }

  private func jsonRequest(_ path: String, _ payload: [String: Any]) throws -> URLRequest {
    var req = try request("POST", path)
    req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    req.httpBody = try JSONSerialization.data(withJSONObject: payload)
    return req
  }

  private func decode<T: Decodable>(_ req: URLRequest) async throws -> T {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(T.self, from: try await send(req))
  }

  private func send(_ req: URLRequest) async throws -> Data {
    let data: Data
    let response: URLResponse
    do {
      (data, response) = try await URLSession.shared.data(for: req)
    } catch {
      throw APIError.unreachable(error.localizedDescription)
    }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(status) else {
      throw APIError.server(status: status, message: Self.detail(from: data))
    }
    return data
  }

  /// FastAPI errors look like {"detail": "..."} or {"detail": {...}}.
  private static func detail(from data: Data) -> String {
    if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let detail = object["detail"]
    {
      return (detail as? String) ?? String(describing: detail)
    }
    return String(data: data, encoding: .utf8) ?? "no details"
  }
}

extension Data {
  fileprivate mutating func append(_ string: String) {
    append(Data(string.utf8))
  }
}
