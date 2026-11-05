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

@MainActor
struct AnswerStreamingTests {
    @Test func receivesBatchesBeforeSavedHistoryThroughOneForegroundSubscription() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        #expect(stream.protocolRequest.request.url?.path == "/api/events")
        let query = URLComponents(url: stream.protocolRequest.request.url!, resolvingAgainstBaseURL: false)?.queryItems
        #expect(query?.contains(URLQueryItem(name: "after", value: "12")) == true)
        stream.beginStream()
        let first = event(text: "## 杭州", revision: 1)
        // 网络块可以截断 UTF-8 汉字与 SSE 行；直到完整事件才发布累计正文。
        let split = first.firstIndex(of: 0xE6)!
        stream.send(first.prefix(split + 1))
        stream.send(first.suffix(from: split + 1))
        try await observed { sessions.answerPreview?.text == "## 杭州" }
        #expect(sessions.conversation?.turns.first?.answer == nil)
        #expect(sessions.conversation?.turns.first?.status == "running")
        stream.send(event(text: "## 杭州\n\n上午游览西湖。", revision: 2))
        try await observed { sessions.answerPreview?.revision == 2 }
        #expect(sessions.answerPreview?.text == "## 杭州\n\n上午游览西湖。")
        #expect(await fixture.receivedCount == 4)
        await clock.advance()
        await fixture.next().reply(body: snapshot(revision: 3, active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(answer: "## 杭州\n\n上午游览西湖。"))
        _ = await clock.nextInterval()
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.answer == "## 杭州\n\n上午游览西湖。")
        // 完成后迟到的旧预览不能盖过已保存历史。
        stream.send(event(text: "错误的迟到正文", revision: 2))
        stream.finish()
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func backgroundAndPageExitCancelReceivingAndForegroundRestoresSnapshot() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let old = await fixture.next()
        old.beginStream()
        old.send(event(text: "离开前", revision: 1))
        try await observed { sessions.answerPreview?.text == "离开前" }
        sessions.setForeground(false)
        await clock.advance()
        let deadline = ContinuousClock.now + .seconds(5)
        while await fixture.cancelledStreams < 1, ContinuousClock.now < deadline { await Task.yield() }
        #expect(await fixture.cancelledStreams == 1)
        sessions.setForeground(true)
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot(revision: 4, text: "离开期间的累计正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let resumed = await fixture.next()
        #expect(resumed.protocolRequest.request.url?.path == "/api/events")
        #expect(sessions.answerPreview?.text == "离开期间的累计正文")
        resumed.beginStream()
        resumed.send(event(text: "恢复后的正文", revision: 5))
        try await observed { sessions.answerPreview?.revision == 5 }
        sessions.watchConversation(sessionID: nil)
        #expect(sessions.answerPreview == nil)
        #expect(await fixture.receivedCount == 8) // 全部只有 GET，没有后端停止。
        resumed.finish()
        old.finish()
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func attemptReplacementWrongOwnershipAndStreamFailureRemainObservable() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(event(text: "旧尝试", revision: 1))
        try await observed { sessions.answerPreview?.revision == 1 }
        stream.send(event(text: "新尝试", revision: 3, attempt: "second"))
        try await observed { sessions.answerPreview?.revision == 3 }
        #expect(sessions.answerPreview?.text == "新尝试")
        #expect(sessions.answerPreview?.attemptID == "second")
        stream.send(event(text: "旧次序", revision: 2))
        stream.send(event(text: "其他轮次", revision: 4, turn: "other"))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview?.text == "新尝试")
        #expect(sessions.conversation?.turns.first?.status == "running")
        try sessions.configure(address: address)
        #expect(sessions.answerPreview == nil)
        #expect(sessions.answerFailure == nil)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test(arguments: ["configuration", "conversation", "dataset", "process"])
    func changedOwnershipInvalidatesOldConnection(kind: String) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let old = await fixture.next()
        old.beginStream()
        old.send(event(text: "原归属正文", revision: 1))
        try await observed { sessions.answerPreview?.text == "原归属正文" }
        switch kind {
        case "configuration": try sessions.configure(address: "https://changed.example.test")
        case "conversation": sessions.watchConversation(sessionID: "another")
        default:
            let read = Task { try await sessions.refresh() }
            let body = kind == "dataset" ? snapshot().replacingOccurrences(of: "dataset", with: "new-dataset")
                : snapshot().replacingOccurrences(of: "process", with: "new-process")
            await fixture.next().reply(body: body)
            await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
            _ = try await read.value
        }
        #expect(sessions.answerPreview == nil)
        old.send(event(text: "失效连接迟到正文", revision: 99))
        old.finish()
        sessions.setForeground(false)
        await clock.advance()
        #expect(sessions.answerPreview == nil)
    }

    @Test func delayedSnapshotDuplicateEventsAndMissingPreviewCannotResurrectOldAttempt() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 4, text: "快照已有正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        let refresh = Task { try await sessions.refresh() }
        let delayed = await fixture.next()
        stream.send(event(text: "交接期间增加的正文", revision: 6))
        try await observed { sessions.answerPreview?.revision == 6 }
        delayed.reply(body: snapshot(revision: 5, text: "较旧快照"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await refresh.value
        #expect(sessions.answerPreview?.text == "交接期间增加的正文")
        stream.send(event(text: "重复更新不应修改正文", revision: 6))
        // 完整空事件是明确的缺失结果，同次序及更旧尝试不能将正文复活。
        stream.send(Data("event: answer.preview\ndata: {\"schema_version\":1,\"instance_id\":\"process\",\"revision\":7,\"preview\":null}\n\n".utf8))
        try await observed { sessions.answerPreview == nil }
        stream.send(event(text: "已经失效的正文", revision: 7))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview == nil)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test(arguments: ["running", "completed", "restarted"])
    func disconnectedStreamReconcilesSnapshotAndHistoryBeforeResubscribing(result: String) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "断线前正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let old = await fixture.next()
        old.beginStream()
        old.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview?.text == "断线前正文")
        await clock.advance()
        let body = result == "running" ? snapshot(revision: 4, text: "断线期间累计正文")
            : result == "completed" ? snapshot(revision: 4, active: false)
            : snapshot(active: false).replacingOccurrences(of: "process", with: "new-process")
        await fixture.next().reply(body: body)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        let history = result == "running" ? page() : result == "completed" ? page(answer: "离开期间保存的完整正文")
            : page().replacingOccurrences(of: "\"status\":\"running\"", with: "\"status\":\"terminated\",\"reason\":\"service_interrupted\"")
        await fixture.next().reply(body: history)
        _ = await clock.nextInterval()
        let resumed = await fixture.next()
        #expect(resumed.protocolRequest.request.url?.path == "/api/events")
        resumed.beginStream()
        if result == "running" {
            #expect(sessions.answerPreview?.text == "断线期间累计正文")
            resumed.send(event(text: "继续生成", revision: 5))
            try await observed { sessions.answerPreview?.text == "继续生成" }
            // 返回列表再进入相同会话，同次序的有效累计对象仍可恢复。
            sessions.watchConversation(sessionID: nil)
            sessions.watchConversation(sessionID: "existing")
            let read = Task { try await sessions.history(sessionID: "existing") }
            await fixture.next().reply(body: page())
            _ = try await read.value
            let reopened = await fixture.next()
            reopened.beginStream()
            reopened.send(event(text: "继续生成", revision: 5))
            try await observed { sessions.answerPreview?.text == "继续生成" }
            reopened.finish()
        } else {
            #expect(sessions.answerPreview == nil)
            if result == "completed" { #expect(sessions.conversation?.turns.first?.answer == "离开期间保存的完整正文") }
            else { #expect(sessions.conversation?.turns.first?.reason == "service_interrupted") }
        }
        resumed.finish()
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func processChangeBetweenSnapshotAndSubscriptionImmediatelyInvalidatesPreview() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 2, text: "已经失去进程归属的正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        let changed = String(decoding: event(text: "新进程正文", revision: 1), as: UTF8.self)
            .replacingOccurrences(of: "process", with: "new-process")
        stream.send(Data(changed.utf8))
        try await observed { sessions.answerFailure == .obsolete }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.status == "running")
        stream.finish()
        sessions.setForeground(false)
        await clock.advance()
    }

    private func snapshot(revision: Int = 0, text: String? = nil, active: Bool = true) -> String {
        """
        {"schema_version":1,"stream_id":"dataset","instance_id":"process","answer_revision":\(revision),
        "answer_preview":\(text.map { String(decoding: preview(text: $0, revision: revision), as: UTF8.self) } ?? "null"),
        "active_session_id":"existing","active_turn_id":\(active ? "\"turn\"" : "null"),"accepting":true,"stopping":false}
        """
    }

    private func page(answer: String? = nil) -> String {
        """
        {"schema_version":1,"stream_id":"dataset","cursor":12,"session":{"session_id":"existing","title":"旅行","updated_at":"now"},
        "turns":[{"turn_id":"turn","ordinal":1,"input":"杭州","status":"\(answer == nil ? "running" : "completed")","answer":\(answer.map { String(decoding: try! JSONEncoder().encode($0), as: UTF8.self) } ?? "null")}]}
        """
    }

    private func preview(text: String, revision: Int, attempt: String = "attempt", turn: String = "turn") -> Data {
        let preview: [String: Any] = ["instance_id":"process", "stream_id":"dataset", "session_id":"existing",
            "turn_id":turn, "attempt_id":attempt, "revision":revision, "text":text, "status":"generating"]
        return try! JSONSerialization.data(withJSONObject: preview)
    }

    private func event(text: String, revision: Int, attempt: String = "attempt", turn: String = "turn") -> Data {
        let preview = try! JSONSerialization.jsonObject(with: preview(text: text, revision: revision, attempt: attempt, turn: turn))
        let body = try! JSONSerialization.data(withJSONObject: ["schema_version":1, "kind":"answer.preview",
            "instance_id":"process", "revision":revision, "preview":preview])
        return Data("event: answer.preview\r\ndata: ".utf8) + body + Data("\r\n\r\n".utf8)
    }

    private func observed(_ condition: () -> Bool) async throws {
        let deadline = ContinuousClock.now + .seconds(5)
        while !condition(), ContinuousClock.now < deadline { await Task.yield() }
        try #require(condition(), "未观察到预期的公开正文状态")
    }
}
