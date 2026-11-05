import Foundation
import SessionKit
import Testing

@MainActor
struct PaginationTests {
    @Test func deletedEarlierPageClearsHistoryAndRejectsAnOlderSuccessfulRefresh() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3))
        _ = try await sessions.history(sessionID: "hangzhou")
        let history = Task { try await sessions.history(sessionID: "hangzhou") }
        let delayed = await fixture.next()
        await fixture.enqueue(status: 404, body: "{}")
        await #expect(throws: SessionFailure.http(404)) { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        #expect(sessions.conversation == nil)
        #expect(sessions.conversationFailure == .http(404))
        delayed.reply(body: page([3, 4], cursor: 10, before: 3))
        await #expect(throws: SessionFailure.obsolete) { try await history.value }
        #expect(sessions.conversation == nil)
    }

    @Test(arguments: ["switch", "configuration", "dataset", "deleted"])
    func obsoleteEarlierPageCannotRestoreAnInvalidTarget(change: String) async throws {
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3))
        _ = try await sessions.history(sessionID: "hangzhou")
        let earlier = Task { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        let delayed = await fixture.next()
        switch change {
        case "switch":
            sessions.watchConversation(sessionID: "suzhou")
            await fixture.enqueue(body: page([1], cursor: 20, session: "suzhou"))
            _ = try await sessions.history(sessionID: "suzhou")
        case "configuration": try sessions.configure(address: address + ":443")
        case "dataset":
            await fixture.enqueue(body: snapshot.replacingOccurrences(of: "dataset", with: "replacement"))
            await fixture.enqueue(body: list([]))
            _ = try await sessions.refresh()
        default:
            await fixture.enqueue(status: 404, body: "{}")
            await #expect(throws: SessionFailure.http(404)) { try await sessions.history(sessionID: "hangzhou") }
        }
        delayed.reply(body: page([1, 2], cursor: 10))
        await #expect(throws: SessionFailure.obsolete) { try await earlier.value }
        #expect(sessions.conversation?.session.id != "hangzhou")
        #expect(!sessions.isLoadingEarlierTurns)
        #expect(sessions.earlierTurnsFailure == nil)
    }

    @Test func paginationFailureRetainsContentAndAllowsExplicitRetry() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3))
        _ = try await sessions.history(sessionID: "hangzhou")
        await fixture.enqueue(status: 503, body: "{}")
        await #expect(throws: SessionFailure.http(503)) { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        #expect(sessions.conversation?.turns.map(\.id) == ["turn-3", "turn-4"])
        #expect(sessions.conversation?.nextBefore == 3)
        #expect(sessions.earlierTurnsFailure == .http(503))
        await fixture.enqueue(body: page([1, 2], cursor: 11))
        #expect(try await sessions.loadEarlierTurns(sessionID: "hangzhou").turns.count == 4)
        #expect(sessions.earlierTurnsFailure == nil)
    }

    @Test func lateListPageCannotUndoARefreshAndCursorIsPassedUnmodified() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        await fixture.enqueue(body: snapshot)
        await fixture.enqueue(body: list([("a", "旧标题")], next: "opaque+/=cursor"))
        _ = try await sessions.refresh()
        let more = Task { try await sessions.loadMoreSessions() }
        let delayed = await fixture.next()
        let parameters = URLComponents(url: delayed.protocolRequest.request.url!, resolvingAgainstBaseURL: false)?.queryItems
        #expect(parameters?.contains(URLQueryItem(name: "cursor", value: "opaque+/=cursor")) == true)
        await #expect(throws: SessionFailure.busy) { try await sessions.loadMoreSessions() }
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: snapshot)
        delayed.reply(body: list([("a", "旧标题"), ("deleted", "已删除")]))
        _ = try await more.value
        // 第一页和原游标完全相同，删除只发生在下一页。
        await fixture.next().reply(body: list([("a", "旧标题")], next: "opaque+/=cursor"))
        await fixture.enqueue(body: list([("b", "电脑已改名")]))
        _ = try await refresh.value
        #expect(sessions.sessionList?.sessions.map(\.title) == ["旧标题", "电脑已改名"])
        #expect(sessions.sessionList?.nextCursor == nil)
        #expect(sessions.connection == .connected)
        #expect(sessions.moreSessionsFailure == nil)
    }

    @Test func staleHistoryCannotReplaceNewCompletion() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3, running: 4))
        _ = try await sessions.history(sessionID: "hangzhou")
        let earlier = Task { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        let delayed = await fixture.next()
        await fixture.enqueue(body: page([3, 4], cursor: 20, before: 3))
        _ = try await sessions.history(sessionID: "hangzhou")
        delayed.reply(body: page([1, 2, 3, 4], cursor: 10, running: 4))
        _ = try await earlier.value
        #expect(sessions.conversation?.turns.last?.status == "completed")
        #expect(sessions.conversation?.turns.last?.answer == "第4轮完整回答")
        #expect(sessions.conversation?.cursor == 20)
    }

    @Test func lateEarlierPagePreservesNewFactsAndLeavesNewGapReachable() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3, running: 4))
        _ = try await sessions.history(sessionID: "hangzhou")
        let earlier = Task { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        let delayed = await fixture.next()
        let parameters = URLComponents(url: delayed.protocolRequest.request.url!, resolvingAgainstBaseURL: false)?.queryItems
        #expect(parameters?.contains(URLQueryItem(name: "before", value: "3")) == true)
        #expect(sessions.isLoadingEarlierTurns)
        await #expect(throws: SessionFailure.busy) { try await sessions.loadEarlierTurns(sessionID: "hangzhou") }
        await fixture.enqueue(body: page([8, 9], cursor: 30, before: 8))
        _ = try await sessions.history(sessionID: "hangzhou")
        delayed.reply(body: page([1, 2, 3, 4], cursor: 10, running: 4))
        _ = try await earlier.value
        #expect(sessions.conversation?.turns.map(\.id) == ["turn-1", "turn-2", "turn-3", "turn-4", "turn-8", "turn-9"])
        #expect(sessions.conversation?.nextBefore == 8)
        await fixture.enqueue(body: page([3, 4, 5, 6, 7], cursor: 31, before: 3))
        let filled = try await sessions.loadEarlierTurns(sessionID: "hangzhou")
        #expect(filled.turns.map(\.id) == ["turn-1", "turn-2", "turn-3", "turn-4", "turn-5", "turn-6", "turn-7", "turn-8", "turn-9"])
        #expect(filled.nextBefore == nil)
        #expect(filled.turns[3].answer == "第4轮完整回答")
        #expect(filled.cursor == 31)
    }

    @Test func sessionListLoadsOpaqueCursorAndRefreshReflectsDesktopRenameAndDeletion() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        await fixture.enqueue(body: snapshot)
        await fixture.enqueue(body: list([("a", "杭州"), ("b", "上海")], next: "opaque+/=cursor"))
        _ = try await sessions.refresh()
        await fixture.enqueue(body: list([("b", "旧上海"), ("c", "苏州")]))
        let more = try await sessions.loadMoreSessions()
        #expect(more.sessions.map(\.id) == ["a", "b", "c"])
        #expect(more.sessions[1].title == "上海")
        #expect(more.nextCursor == nil)
        await fixture.enqueue(body: snapshot)
        await fixture.enqueue(body: list([("c", "苏州改名"), ("a", "杭州")]))
        let refreshed = try await sessions.refresh()
        #expect(refreshed.sessions.map(\.id) == ["c", "a"])
        #expect(refreshed.sessions.first?.title == "苏州改名")
    }

    @Test func earlierTurnsUseServerBoundaryAndKeepCompleteReadHistory() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        await fixture.enqueue(body: page([3, 4], cursor: 10, before: 3))
        _ = try await sessions.history(sessionID: "hangzhou")
        await fixture.enqueue(body: page([1, 2], cursor: 10))
        let result = try await sessions.loadEarlierTurns(sessionID: "hangzhou")
        #expect(result.turns.map(\.id) == ["turn-1", "turn-2", "turn-3", "turn-4"])
        #expect(result.nextBefore == nil)
    }

    @Test func refreshingHistoryPreservesAlreadyReadTurnsAndUpdatesCompletedAnswer() async throws {
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: temporaryDatabase())
        sessions.watchConversation(sessionID: "hangzhou")
        let initial = Task { try await sessions.history(sessionID: "hangzhou") }
        await fixture.next().reply(body: page([1, 2], cursor: 10, running: 2))
        _ = try await initial.value
        let refresh = Task { try await sessions.history(sessionID: "hangzhou") }
        await fixture.next().reply(body: page([2, 3], cursor: 20, before: 2))
        let result = try await refresh.value
        #expect(result.turns.map(\.id) == ["turn-1", "turn-2", "turn-3"])
        #expect(sessions.conversation?.turns.first?.input == "第1轮完整输入")
        #expect(sessions.conversation?.turns[1].answer == "第2轮完整回答")
        #expect(sessions.conversation?.nextBefore == nil)
    }

    private func temporaryDatabase() -> URL {
        FileManager.default.temporaryDirectory.appending(path: UUID().uuidString).appending(path: "client.sqlite")
    }

    private var snapshot: String {
        """
        {"schema_version":1,"stream_id":"dataset","accepting":true,"stopping":false}
        """
    }

    private func list(_ sessions: [(String, String)], next: String? = nil) -> String {
        let rows = sessions.map { "{\"session_id\":\"\($0.0)\",\"title\":\"\($0.1)\",\"updated_at\":\"now\"}" }
        return """
        {"schema_version":1,"sessions":[\(rows.joined(separator: ","))],"next_cursor":\(next.map { "\"\($0)\"" } ?? "null")}
        """
    }

    private func page(_ ordinals: [Int], cursor: Int, before: Int? = nil, running: Int? = nil,
                      stream: String = "dataset", session: String = "hangzhou") -> String {
        let turns = ordinals.map { ordinal in
            """
            {"turn_id":"turn-\(ordinal)","ordinal":\(ordinal),"input":"第\(ordinal)轮完整输入",
             "answer":\(ordinal == running ? "null" : "\"第\(ordinal)轮完整回答\""),
             "status":"\(ordinal == running ? "running" : "completed")"}
            """
        }.joined(separator: ",")
        return """
            {"schema_version":1,"stream_id":"\(stream)","cursor":\(cursor),
             "session":{"session_id":"\(session)","title":"杭州旅行","updated_at":"now"},
             "turns":[\(turns)],"next_before":\(before.map(String.init) ?? "null")}
            """
    }
}
