import Foundation
import SessionKit
import Testing

@MainActor
struct ReadingIntegrationTests {
    @Test func realHTTPSProvidesAllSessionsFullHistoryAndDesktopChanges() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let sessions: any SessionAccess = try SharedSessions(databaseURL: file)
        try sessions.configure(address: "https://localhost:18495")
        let first = try await sessions.refresh()
        #expect(first.sessions.count == 50)
        #expect(first.nextCursor != nil)
        let all = try await sessions.loadMoreSessions()
        #expect(all.sessions.count >= 53)
        #expect(Set(all.sessions.map(\.id)).count == all.sessions.count)
        #expect(all.nextCursor == nil)
        let reading = try #require(all.sessions.first { $0.title.hasPrefix("杭州两日旅行阅读验收第1轮") })
        sessions.watchConversation(sessionID: reading.id)
        let latest = try await sessions.history(sessionID: reading.id)
        #expect(latest.turns.count == 50)
        #expect(latest.turns.first?.ordinal == 6)
        let complete = try await sessions.loadEarlierTurns(sessionID: reading.id)
        #expect(complete.turns.count == 55)
        #expect(complete.turns.first?.ordinal == 1)
        #expect(complete.turns.last?.ordinal == 55)
        #expect(complete.nextBefore == nil)
        #expect(complete.turns.first?.input == "杭州两日旅行阅读验收第1轮：保留完整输入。")
        let answer = try #require(complete.turns.last?.answer)
        let sampleURL = try #require(Bundle(for: SnapshotTests.self).url(forResource: "reading-answer", withExtension: "md"))
        #expect(answer == (try String(contentsOf: sampleURL, encoding: .utf8)))
        #expect(answer.contains("| 日期 | 上午 | 午餐与饮食 | 午后 | 预约及交通提醒 |"))
        #expect(answer.hasSuffix("回答全文结尾：完整内容包含两日安排、饮食要求、休息时间与预约提醒。\n"))
        #expect(try await sessions.history(sessionID: reading.id).turns.count == 55)

        // 用第二个 HTTP 客户端改动一项独立会话，复用后端真实管理与保存。
        var request = URLRequest(url: URL(string: "https://localhost:18495/api/turns")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["input": "电脑端刷新验证"])
        let (data, _) = try await URLSession.shared.data(for: request)
        let receipt = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let id = try #require(receipt["session_id"] as? String)
        // 公开快照等待保存，不依赖精确模型耗时。
        for _ in 0..<100 {
            _ = try await sessions.refresh()
            if sessions.service?.activeTurnID == nil { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        request.url = URL(string: "https://localhost:18495/api/sessions/" + id)!
        request.httpMethod = "PATCH"
        request.httpBody = try JSONSerialization.data(withJSONObject: ["title": "电脑端已改名"])
        let (_, renamed) = try await URLSession.shared.data(for: request)
        #expect((renamed as? HTTPURLResponse)?.statusCode == 200)
        #expect(try await sessions.refresh().sessions.first?.title == "电脑端已改名")
        request.httpMethod = "DELETE"
        request.httpBody = nil
        let (_, deleted) = try await URLSession.shared.data(for: request)
        #expect((deleted as? HTTPURLResponse)?.statusCode == 200)
        #expect(try await sessions.refresh().sessions.contains { $0.id == id } == false)
        sessions.watchConversation(sessionID: id)
        await #expect(throws: SessionFailure.http(404)) { try await sessions.history(sessionID: id) }
        #expect(sessions.conversation == nil)
    }
}
