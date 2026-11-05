import Foundation
import SessionKit
import Testing

@MainActor
struct ConnectionFailureTests {
    #if DEBUG && targetEnvironment(simulator)
    @Test func unavailableLocalServiceExplainsHowToRecoverAndCanRetry() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(
            databaseURL: directory.appending(path: "client.sqlite"), address: "http://127.0.0.1:18496")
        let unavailable = Task { try await sessions.refresh() }
        await fixture.next().fail()
        await #expect(throws: SessionFailure.unreachable) { try await unavailable.value }
        #expect(sessions.connection == .unavailable("无法连接本地服务，请确认后端已启动。"))
        #expect(sessions.sessionList == nil)
        let retry = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        await fixture.next().reply(body: Self.emptyList)
        #expect(try await retry.value.sessions.isEmpty)
        #expect(sessions.connection == .connected)
    }
    #endif

    @Test func historyDetectsNewDatasetAndRejectsAnOlderRefresh() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        await fixture.next().reply(body: Self.emptyList)
        _ = try await initial.value
        let old = Task { try await sessions.refresh() }
        let oldSnapshot = await fixture.next()
        let history = Task { try await sessions.history(sessionID: "existing") }
        await fixture.next().reply(body: Self.page.replacingOccurrences(of: "old-dataset", with: "new-dataset"))
        await #expect(throws: SessionFailure.obsolete) { try await history.value }
        oldSnapshot.reply(body: "invalid old snapshot")
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.connection == .connecting)
        #expect(sessions.service == nil)
    }

    @Test func sameAddressDatasetChangeInvalidatesPendingHistory() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        await fixture.next().reply(body: Self.emptyList)
        _ = try await initial.value
        let old = Task { try await sessions.history(sessionID: "existing") }
        let oldPage = await fixture.next()
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot.replacingOccurrences(of: "old-dataset", with: "new-dataset"))
        await fixture.next().reply(body: Self.emptyList)
        _ = try await refresh.value
        oldPage.reply(body: Self.page)
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.service?.streamID == "new-dataset")
        #expect(sessions.connection == .connected)
    }

    @Test func changedServiceRejectsLateSuccessfulSnapshot() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let old = Task { try await sessions.refresh() }
        let snapshot = await fixture.next()
        try sessions.configure(address: "https://localhost:18490")
        snapshot.reply(body: Self.snapshot)
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.connection == .connecting)
        #expect(sessions.service == nil)
    }

    @Test func latestRefreshWinsOverOlderFailure() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let old = Task { try await sessions.refresh() }
        let oldSnapshot = await fixture.next()
        let newer = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        await fixture.next().reply(body: Self.emptyList)
        #expect(try await newer.value.sessions.isEmpty)
        oldSnapshot.reply(body: "unreadable")
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.connection == .connected)
        #expect(sessions.service?.streamID == "old-dataset")
    }

    @Test func lateHistoryFailureCannotReplaceNewerGlobalConnection() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        sessions.watchConversation(sessionID: "existing")
        let old = Task { try await sessions.history(sessionID: "existing") }
        let delayed = await fixture.next()
        let latest = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        await fixture.next().reply(body: Self.emptyList)
        _ = try await latest.value
        delayed.fail()
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.connection == .connected)
        #expect(sessions.conversationFailure == nil)
    }

    @Test func lateListFailureCannotReplaceNewerHistoryConnection() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let old = Task { try await sessions.refresh() }
        await fixture.next().reply(body: Self.snapshot)
        let delayed = await fixture.next()
        let history = Task { try await sessions.history(sessionID: "existing") }
        await fixture.next().reply(body: Self.page)
        _ = try await history.value
        delayed.fail()
        await #expect(throws: SessionFailure.unreachable) { try await old.value }
        #expect(sessions.connection == .connected)
    }

    @Test(arguments: [200, 503])
    func invalidResponseNeverBecomesEmptyHistory(status: Int) async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let reading = Task { try await sessions.history(sessionID: "existing") }
        await fixture.next().reply(status: status, body: "{\"turns\":[]}")
        let expected: SessionFailure = status == 200 ? .invalidResponse : .http(503)
        await #expect(throws: expected) { try await reading.value }
        guard case .unavailable = sessions.connection else {
            Issue.record("解码或 HTTP 失败必须明确展示，不能返回空历史")
            return
        }
        #expect(sessions.service == nil)
    }

    @Test func changedServiceIgnoresLateDecodingFailure() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let old = Task { try await sessions.refresh() }
        let snapshot = await fixture.next()
        try sessions.configure(address: "https://localhost:18490")
        snapshot.reply(body: "{\"unexpected\":true}")
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(sessions.connection == .connecting)
        #expect(sessions.service == nil)
    }

    static let snapshot = """
        {"schema_version":1,"stream_id":"old-dataset","accepting":true,"stopping":false}
        """
    static let emptyList = """
        {"schema_version":1,"sessions":[],"next_cursor":null}
        """
    static let page = """
        {"schema_version":1,"stream_id":"old-dataset","session":{"session_id":"existing","title":"旅行讨论","updated_at":"2026-11-04T08:00:00Z"},"turns":[],"next_before":null}
        """
}
