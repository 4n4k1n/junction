// Decodes every backend response recorded in docs/examples/clap-light-session.json with the app's
// own model types, using the same decoder settings as APIClient. Fails on any mismatch.
import Foundation

let path = CommandLine.arguments[1]
let entries = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [[String: Any]]
let decoder = JSONDecoder()
decoder.keyDecodingStrategy = .convertFromSnakeCase
var failures = 0

for entry in entries {
  let request = entry["request"] as! [String: Any]
  let route = request["path"] as! String
  let body = try JSONSerialization.data(withJSONObject: entry["response"]!)
  do {
    if route == "/health" {
      _ = try decoder.decode(BackendInfo.self, from: body)
    } else if route.hasSuffix("/generate") {
      _ = try decoder.decode(GenerationResult.self, from: body)
    } else if route.hasPrefix("/sessions") {
      _ = try decoder.decode(GuideStatus.self, from: body)
    } else {
      _ = try decoder.decode(Build.self, from: body)
    }
  } catch {
    failures += 1
    print("MISMATCH \(route): \(error)")
  }
}
print("\(entries.count - failures)/\(entries.count) recorded backend responses decode with the app's models")
exit(failures == 0 ? 0 : 1)
