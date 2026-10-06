// Dedicated Cody state only. No generic Keychain enumeration or connector lookup.
import Foundation
import Security
import Darwin

let service = "com.dailycody.ticktick-mcp.oauth.v1"
let account = "daily-cody-read-only"
func fail(_ message: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}
let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                          kSecAttrService as String: service,
                          kSecAttrAccount as String: account]
guard CommandLine.arguments.count == 2 else { fail("Unzulaessiger Keychain-Aufruf.") }
switch CommandLine.arguments[1] {
case "load":
    guard isatty(STDOUT_FILENO) == 0 else { fail("Geheimer Zustand darf nicht im Terminal ausgegeben werden.") }
    var lookup = query
    lookup[kSecReturnData as String] = true
    lookup[kSecMatchLimit as String] = kSecMatchLimitOne
    var result: CFTypeRef?
    let status = SecItemCopyMatching(lookup as CFDictionary, &result)
    if status == errSecItemNotFound { fail("Eigener Cody-MCP-Zustand fehlt.", 2) }
    guard status == errSecSuccess, let data = result as? Data, data.count <= 65536 else {
        fail("Eigener Cody-Keychain-Zustand konnte nicht gelesen werden.")
    }
    FileHandle.standardOutput.write(data) // Private IPC pipe captured by Python, never logs.
case "save":
    guard isatty(STDIN_FILENO) == 0 else { fail("Geheimer Zustand muss ueber private stdin-Pipe kommen.") }
    let data = FileHandle.standardInput.readData(ofLength: 65537)
    guard data.count <= 65536,
          let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          payload["purpose"] as? String == "daily-cody.ticktick-mcp.v1",
          payload["scope"] as? String == "tasks:read",
          payload["resource"] as? String == "https://mcp.ticktick.com/" else {
        fail("Unzulaessiger eigener Cody-MCP-Zustand.")
    }
    let attributes: [String: Any] = [kSecValueData as String: data,
                                   kSecAttrLabel as String: "Daily Cody read-only TickTick MCP"]
    var status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
    if status == errSecItemNotFound {
        var add = query
        for (key, value) in attributes { add[key] = value }
        status = SecItemAdd(add as CFDictionary, nil)
    }
    guard status == errSecSuccess else { fail("Eigener Cody-Keychain-Zustand konnte nicht gespeichert werden.") }
default:
    fail("Unzulaessiger Keychain-Modus.")
}
