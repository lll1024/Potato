import Foundation
import SessionKit
import Testing

@MainActor
struct SubmissionIntegrationTests {
    @Test func phoneAndDesktopShareSingleAcceptanceFullAnswersAndPreferences() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let phone: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "phone.sqlite"))
        let desktop: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "other-client.sqlite"))
        try phone.configure(address: "https://localhost:18492")
        try desktop.configure(address: "https://localhost:18492")
        _ = try await phone.refresh()
        let receipt = try await phone.submit(input: "  杭州两日旅行，保留原始内容\n", sessionID: nil)
        #expect(phone.submission == .accepted(receipt))
        // 使用电脑 Web 同一 HTTP 重发原始请求，验证真实 SQLite 幂等边界。
        var duplicate = URLRequest(url: URL(string: "https://localhost:18492/api/turns")!)
        duplicate.httpMethod = "POST"
        duplicate.setValue("application/json", forHTTPHeaderField: "Content-Type")
        duplicate.httpBody = try JSONSerialization.data(withJSONObject: [
            "submission_id": receipt.request.id, "input": receipt.request.input])
        let (data, response) = try await URLSession.shared.data(for: duplicate)
        #expect((response as? HTTPURLResponse)?.statusCode == 202)
        let repeated = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(repeated["turn_id"] as? String == receipt.turnID)
        #expect(repeated["session_id"] as? String == receipt.sessionID)
        try await waitForCompletion(phone)
        let first = try await desktop.history(sessionID: receipt.sessionID)
        #expect(first.turns.count == 1)
        #expect(first.turns[0].input == "  杭州两日旅行，保留原始内容\n")
        #expect(first.turns[0].answer?.contains("已读取共同偏好：不吃辣。") == true)
        #expect(first.turns[0].answer?.hasSuffix("回答全文结尾") == true)
        let continuation = try await phone.submit(input: "第二天改为博物馆", sessionID: receipt.sessionID)
        #expect(continuation.request.sessionID == receipt.sessionID)
        #expect(continuation.sessionID == receipt.sessionID)
        #expect(continuation.turnID != receipt.turnID)
        try await waitForCompletion(phone)
        let fromPhone = try await phone.history(sessionID: receipt.sessionID)
        let fromDesktop = try await desktop.history(sessionID: receipt.sessionID)
        #expect(fromPhone == fromDesktop)
        #expect(fromDesktop.turns.count == 2)
        #expect(fromDesktop.turns.last?.input == "第二天改为博物馆")
        #expect(fromDesktop.turns.allSatisfy { $0.status == "completed" })
    }

    private func waitForCompletion(_ sessions: any SessionAccess) async throws {
        for _ in 0..<100 {
            _ = try await sessions.refresh()
            if sessions.service?.activeTurnID == nil { return }
            try await Task.sleep(for: .milliseconds(20))
        }
        Issue.record("真实后端未在限定时间内保存回答")
    }
}
