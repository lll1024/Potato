import Foundation
import SessionKit
import Testing

@MainActor
struct PollingTests {
    @Test func polledMissingConversationPublishes404AndCanRecover() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot(active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(status: 404, body: "{\"detail\":\"not found\"}")
        _ = await clock.nextInterval()
        #expect(sessions.conversationFailure == .http(404))
        #expect(sessions.connection == .connected)
        #expect(sessions.conversation == nil)
        sessions.setForeground(false)
        await clock.advance()
        let restored = try await loadHistory(sessions, fixture, status: "completed", answer: "已保存")
        #expect(sessions.conversation == restored)
        #expect(sessions.conversationFailure == nil)
    }

    @Test func storageFailureAndOfflinePollingPreserveLastSavedRunningFact() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"dataset","active_session_id":"existing","active_turn_id":"turn",
             "accepting":false,"stopping":false,"storage_error":"轮次结果未保存",
             "unsaved_fact":{"kind":"turn.finished","turn_id":"turn"}}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(status: "running", answer: nil))
        _ = await clock.nextInterval()
        #expect(sessions.canSubmit == false)
        #expect(sessions.service?.storageError == "轮次结果未保存")
        #expect(sessions.conversation?.turns.first?.answer == nil)
        await clock.advance()
        await fixture.next().fail()
        _ = await clock.nextInterval()
        guard case .unavailable = sessions.connection else {
            Issue.record("网络失败须标明暂时无法核对")
            sessions.setForeground(false)
            await clock.advance()
            return
        }
        #expect(sessions.conversation?.turns.first?.status == "running")
        #expect(sessions.conversation?.turns.first?.answer == nil)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func foregroundPollingReadsPersistedCompletionWithoutTreatingAcceptanceAsCompletion() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: snapshot(active: true))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await refresh.value
        sessions.watchConversation(sessionID: "existing")
        _ = try await loadHistory(sessions, fixture, status: "running", answer: nil)
        guard sessions.conversation?.turns.first?.status == "running" else {
            Issue.record("共享能力应发布当前对话的真实持久状态")
            return
        }
        sessions.setForeground(true)
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot(active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(status: "completed", answer: "完整保存的回答"))
        #expect(await clock.nextInterval() == .seconds(2))
        #expect(sessions.service?.activeTurnID == nil)
        #expect(sessions.conversation?.turns.first?.answer == "完整保存的回答")
        sessions.setForeground(false)
        await clock.advance()
        #expect(await fixture.receivedCount == 6)
    }

    @Test func backgroundCancelsSlowReadAndForegroundImmediatelyRefreshesOnce() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        let late = await fixture.next()
        sessions.setForeground(false)
        sessions.setForeground(true)
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot(active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(status: "completed", answer: "离开期间保存的完整结果"))
        #expect(await clock.nextInterval() == .seconds(2))
        late.reply(body: snapshot(active: true))
        #expect(sessions.conversation?.turns.first?.answer == "离开期间保存的完整结果")
        #expect(sessions.canSubmit)
        sessions.setForeground(false)
        await clock.advance()
        #expect(await fixture.receivedCount == 4)
    }

    private func loadHistory(_ sessions: any SessionAccess, _ fixture: HTTPFixture,
                             status: String, answer: String?) async throws -> ConversationPage {
        let read = Task { try await sessions.history(sessionID: "existing") }
        await fixture.next().reply(body: page(status: status, answer: answer))
        return try await read.value
    }

    private func snapshot(active: Bool) -> String {
        """
        {"schema_version":1,"stream_id":"dataset","active_session_id":\(active ? "\"existing\"" : "null"),
         "active_turn_id":\(active ? "\"turn\"" : "null"),"accepting":true,"stopping":false,"map_paused":true}
        """
    }

    private func page(status: String, answer: String?) -> String {
        """
        {"schema_version":1,"stream_id":"dataset","session":{"session_id":"existing","title":"旅行讨论","updated_at":"now"},
         "turns":[{"turn_id":"turn","input":"杭州","status":"\(status)","answer":\(answer.map { "\"\($0)\"" } ?? "null")}]}
        """
    }
}

/// 时间替身只控制两轮之间的等待，HTTP 与能力状态仍经公开入口观察。
actor PollClock {
    private var sleeper: CheckedContinuation<Void, Never>?
    private var intervals: [Duration] = []
    private var observer: CheckedContinuation<Duration, Never>?
    func wait(_ interval: Duration) async throws {
        await withCheckedContinuation { continuation in
            sleeper = continuation
            if let observer { self.observer = nil; observer.resume(returning: interval) }
            else { intervals.append(interval) }
        }
        try Task.checkCancellation()
    }
    func nextInterval() async -> Duration {
        if !intervals.isEmpty { return intervals.removeFirst() }
        return await withCheckedContinuation { observer = $0 }
    }
    func advance() { sleeper?.resume(); sleeper = nil }
}

@MainActor
struct StopTests {
    @Test func stopsDesktopTurnAndWaitsForRealReleaseBeforeSending() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let connecting = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "desktop-turn"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await connecting.value
        let target = try #require(sessions.stopTarget)
        #expect(!sessions.canSubmit)
        sessions.watchConversation(sessionID: "another-page")
        let stopping = Task { try await sessions.stop(target: target) }
        let request = await fixture.next()
        #expect(request.protocolRequest.request.url?.path == "/api/turns/desktop-turn/stop")
        #expect(request.protocolRequest.request.httpMethod == "POST")
        #expect(sessions.stopState == .requesting(target))
        request.reply(body: "{\"schema_version\":1,\"turn_id\":\"desktop-turn\",\"status\":\"running\",\"stopping\":true}")
        try await stopping.value
        #expect(sessions.stopState == .waiting(target))
        #expect(!sessions.canSubmit)
        let stillStopping = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "desktop-turn", stopping: true))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await stillStopping.value
        #expect(!sessions.canSubmit)
        let released = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: nil))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await released.value
        #expect(sessions.stopState == .idle)
        #expect(sessions.canSubmit)
        #expect(await fixture.receivedCount == 7)
    }

    @Test func capturedOldTargetNeverStopsLaterTurnOrChangesWithNavigation() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "old"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await initial.value
        let original = try #require(sessions.stopTarget)
        let newer = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "new"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await newer.value
        sessions.watchConversation(sessionID: "other")
        let stop = Task { try await sessions.stop(target: original) }
        let request = await fixture.next()
        #expect(request.protocolRequest.request.url?.path == "/api/turns/old/stop")
        request.reply(body: "{\"schema_version\":1,\"turn_id\":\"old\",\"status\":\"completed\",\"stopping\":false}")
        try await stop.value
        let verify = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "new"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await verify.value
        #expect(sessions.stopState == .idle)
        #expect(sessions.service?.activeTurnID == "new")
        #expect(sessions.service?.stopping == false)
        #expect(!sessions.canSubmit)
    }

    @Test(arguments: ["offline", "bad-json", "wrong-turn", "http-error"])
    func unknownStopDoesNotInventTerminalState(kind: String) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "turn"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await initial.value
        let target = try #require(sessions.stopTarget)
        let stop = Task { try await sessions.stop(target: target) }
        let request = await fixture.next()
        switch kind {
        case "offline": request.fail()
        case "wrong-turn": request.reply(body: "{\"schema_version\":1,\"turn_id\":\"other\",\"status\":\"completed\",\"stopping\":false}")
        case "http-error": request.reply(status: 503, body: "{}")
        default: request.reply(body: "invalid")
        }
        await #expect(throws: SessionFailure.stopUnknown) { try await stop.value }
        #expect(sessions.stopState == .uncertain(target))
        #expect(sessions.service?.activeTurnID == "turn")
        #expect(sessions.service?.stopping == false)
        #expect(!sessions.canSubmit)
        let verify = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: nil))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await verify.value
        #expect(sessions.canSubmit)
    }

    @Test func staleIdleSnapshotCannotReleaseStopAndChangedServiceRejectsLateStop() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: state(turn: "turn"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await initial.value
        let target = try #require(sessions.stopTarget)
        let old = Task { try await sessions.refresh() }
        let stale = await fixture.next()
        let stop = Task { try await sessions.stop(target: target) }
        let response = await fixture.next()
        stale.reply(body: state(turn: nil))
        await #expect(throws: SessionFailure.obsolete) { try await old.value }
        #expect(!sessions.canSubmit)
        try sessions.configure(address: "https://other.example.test")
        response.reply(body: "{\"schema_version\":1,\"turn_id\":\"turn\",\"status\":\"running\",\"stopping\":true}")
        await #expect(throws: SessionFailure.obsolete) { try await stop.value }
        #expect(sessions.stopState == .idle)
        #expect(sessions.service == nil)
        await #expect(throws: SessionFailure.obsolete) { try await sessions.stop(target: target) }
        #expect(await fixture.receivedCount == 4)
    }

    private func state(turn: String?, stopping: Bool = false) -> String {
        """
        {"schema_version":1,"stream_id":"dataset","active_session_id":"desktop-session",
        "active_turn_id":\(turn.map { "\"\($0)\"" } ?? "null"),"accepting":true,"stopping":\(stopping)}
        """
    }
}
