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

    @Test(arguments: ["completed", "failed"])
    func foregroundPollingReadsPersistedOutcomeAndReleasesInput(status: String) async throws {
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
        await fixture.next().reply(body: page(status: status, answer: "本轮保存的结果"))
        #expect(await clock.nextInterval() == .seconds(2))
        #expect(sessions.service?.activeTurnID == nil)
        #expect(sessions.conversation?.turns.first?.status == status)
        #expect(sessions.conversation?.turns.first?.answer == "本轮保存的结果")
        #expect(sessions.canSubmit)
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
    @Test(arguments: ["saved", "unavailable", "deleted", "still-running"], [false, true])
    func endingSnapshotWaitsForReliableHistoryBeforeClearingReadableContent(outcome: String, lateTrace: Bool) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "结束快照之前已读到的正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next(); stream.beginStream()
        await clock.advance()
        let ending = await fixture.next()
        #expect(ending.protocolRequest.request.url?.path == "/api/snapshot")
        ending.reply(body: snapshot(revision: 3, active: false)
            .replacingOccurrences(of: "\"schema_version\":1,", with: "\"schema_version\":1,\"cursor\":13,"))
        let pending = await fixture.next()
        // 完成SSE尚未抵达，正在核对的一致读取不能先公布null、撤空已读正文。
        #expect(sessions.answerPreview?.text == "结束快照之前已读到的正文")
        #expect(sessions.conversation?.turns.first?.status == "running")
        #expect(sessions.conversation?.turns.first?.answer == nil)
        let historyFirst = pending.protocolRequest.request.url?.path == "/api/sessions/existing"
        let reading: ControlledRequest
        if historyFirst { reading = pending }
        else {
            #expect(pending.protocolRequest.request.url?.path == "/api/sessions")
            pending.reply(body: "{\"schema_version\":1,\"sessions\":[]}")
            reading = await fixture.next()
        }
        if lateTrace {
            stream.send(Data("""
                id: 13
                event: trace
                data: {"schema_version":1,"kind":"turn.finished","session_id":"existing","turn_id":"turn","cursor":13,"data":{"status":"completed","reason":null,"answer_source":"model"}}


                """.utf8))
        }
        switch outcome {
        case "unavailable": reading.reply(status: 500, body: "{}")
        case "deleted": reading.reply(status: 404, body: "{}")
        case "still-running": reading.reply(body: page())
        default: reading.reply(body: page(answer: "最终可靠历史的完整正文").replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":13"))
        }
        if historyFirst && outcome == "saved" { await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}") }
        _ = await clock.nextInterval()
        if outcome == "saved" {
            #expect(sessions.conversation?.turns.first?.answer == "最终可靠历史的完整正文")
            #expect(sessions.answerPreview == nil)
            #expect(sessions.service?.activeTurnID == nil)
            #expect(await fixture.receivedCount == 7, "沿现有一次历史读取完成交接，不追加重复轮询")
        } else if outcome == "deleted" {
            #expect(sessions.conversation == nil)
            #expect(sessions.answerPreview == nil)
            #expect(sessions.conversationFailure == .http(404))
            #expect(sessions.connection == .connected, "会话404不伪装成服务连接失败")
        } else {
            #expect(sessions.answerPreview?.text == "结束快照之前已读到的正文")
            #expect(sessions.conversation?.turns.first?.status == "running")
            #expect(sessions.service?.activeTurnID == "turn", "无法核对终态时保留上一份一致状态，不从null宣布完成")
            #expect(!sessions.canSubmit)
            if outcome == "unavailable" {
                #expect(sessions.conversationFailure == .http(500))
                if case .unavailable = sessions.connection { } else { Issue.record("历史核对失败应显示原连接错误") }
            }
        }
    }

    @Test(arguments: ["answer-failed", "answer-replaced", "query-failed", "summary-started", "answer-current", "snapshot-newer"], [false, true])
    func persistentRequestFactsPreventFailedAttemptHandoff(change: String, httpSnapshot: Bool) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        let currentB = change == "snapshot-newer"
        let initial = snapshot(revision: 1, text: currentB ? "尝试B已读正文" : "尝试A已读正文")
            .replacingOccurrences(of: "\"schema_version\":1,", with: "\"schema_version\":1,\"cursor\":\(currentB ? 15 : 12),")
            .replacingOccurrences(of: "\"attempt_id\":\"attempt\"", with: "\"attempt_id\":\"\(currentB ? "replacement" : "attempt")\"")
        await fixture.next().reply(body: initial)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        // 持久事实先成批补齐，瞬时B的预览已被后端收束为null。
        let request = change == "answer-failed" || currentB ? "attempt" : "replacement"
        let started = change != "answer-failed" && change != "query-failed"
        let phase = ["answer-replaced", "answer-current", "snapshot-newer"].contains(change) ? "answer" : change == "summary-started" ? "" : "query"
        let data = started ? "{\"answer_phase\":\"\(phase)\",\"purpose\":\"\(change == "summary-started" ? "context_summary" : "travel")\"}" : "{\"status\":\"failed\"}"
        stream.send(Data("""
            id: 13
            event: trace
            data: {"schema_version":1,"kind":"request.\(started ? "started" : "failed")","session_id":"existing","turn_id":"turn","request_id":"\(request)","cursor":13,"data":\(data)}


            id: 14
            event: trace
            data: {"schema_version":1,"kind":"request.completed","session_id":"existing","turn_id":"turn","request_id":"replacement","cursor":14,"data":{"status":"completed"}}


            """.utf8))
        // 公开状态作事件处理屏障，不能只在trace进入接收队列后就断言。
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":1,"progress_revision":0,"active_session_id":"existing","active_turn_id":"turn","accepting":false,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        if currentB {
            #expect(sessions.answerPreview?.text == "尝试B已读正文", "快照游标之前的请求不能撤销快照当前尝试")
            stream.send(Data("""
                id: 15
                event: trace
                data: {"schema_version":1,"kind":"request.started","session_id":"existing","turn_id":"turn","request_id":"replacement","cursor":15,"data":{"answer_phase":"answer"}}


                """.utf8))
        }
        let matchingB = change == "answer-current" || currentB
        let arrivingText = matchingB ? "尝试B迟到全文" : "尝试A迟到全文"
        // 两个传输来源都可能较迟送来更高raw revision的A，已知尝试归属不能复活。
        if httpSnapshot {
            let checking = Task { try await sessions.refresh() }
            await fixture.next().reply(body: snapshot(revision: 2, text: arrivingText)
                .replacingOccurrences(of: "\"attempt_id\":\"attempt\"", with: "\"attempt_id\":\"\(matchingB ? "replacement" : "attempt")\""))
            await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
            _ = try await checking.value
        } else { stream.send(event(text: arrivingText, revision: 2, attempt: matchingB ? "replacement" : "attempt")) }
        stream.send(Data("""
            id: 16
            event: trace
            data: {"schema_version":1,"kind":"turn.finished","session_id":"existing","turn_id":"turn","cursor":16,"data":{"status":"completed","reason":null,"answer_source":"model"}}


            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":3,"progress_revision":0,"active_session_id":null,"active_turn_id":null,"accepting":true,"stopping":false}}


            """.utf8))
        let delayedHistory = await fixture.next()
        #expect(delayedHistory.protocolRequest.request.url?.path == "/api/sessions/existing")
        stream.send(Data("""
            event: answer.preview
            data: {"schema_version":1,"kind":"answer.preview","instance_id":"process","revision":3,"preview":null}


            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":3,"progress_revision":0,"active_session_id":null,"active_turn_id":null,"accepting":false,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        let permitted = change == "query-failed" || change == "summary-started" || matchingB
        #expect(sessions.answerPreview?.text == (permitted ? arrivingText : nil))
        #expect(sessions.conversation?.turns.first?.status == "running")
        delayedHistory.reply(body: page(answer: "真正完成并保存的正文").replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":16"))
        try await observed { sessions.conversation?.turns.first?.answer == "真正完成并保存的正文" }
        #expect(sessions.answerPreview == nil)
        await clock.advance()
    }

    @Test(arguments: [false, true])
    func finishedStateKeepsReadableContentUntilReliableHistoryArrives(newerEarlierPage: Bool) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "等待核对保存结果的正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: newerEarlierPage ? page().replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":12,\"next_before\":2").replacingOccurrences(of: "\"ordinal\":1", with: "\"ordinal\":2") : page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(progressEvent(revision: 1, text: "准备查找地点资料。"))
        try await observed { sessions.progressPreview?.revision == 1 }
        if newerEarlierPage {
            let earlier = Task { try await sessions.loadEarlierTurns(sessionID: "existing") }
            await fixture.next().reply(body: page(answer: "更早轮次的回答")
                .replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":30")
                .replacingOccurrences(of: "\"turn_id\":\"turn\"", with: "\"turn_id\":\"old-turn\""))
            _ = try await earlier.value
            #expect(sessions.conversation?.cursor == 30)
            #expect(sessions.conversation?.turns.last?.status == "running")
        }
        stream.send(Data("""
            id: 13
            event: trace
            data: {"schema_version":1,"kind":"turn.finished","session_id":"existing","turn_id":"turn","cursor":13,"data":{"status":"completed","reason":null,"answer_source":"model"}}


            """.utf8))
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":2,"progress_revision":2,"active_session_id":null,"active_turn_id":null,"accepting":true,"stopping":false}}


            """.utf8))
        let delayedHistory = await fixture.next()
        #expect(delayedHistory.protocolRequest.request.url?.path == "/api/sessions/existing")
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文")
        #expect(sessions.progressPreview?.entries.first?.text == "准备查找地点资料。")
        stream.send(Data("event: answer.preview\ndata: {\"schema_version\":1,\"kind\":\"answer.preview\",\"instance_id\":\"process\",\"revision\":2,\"preview\":null}\n\n".utf8))
        stream.send(progressEvent(revision: 2, text: nil))
        // 同一订阅的后续公开状态作处理屏障，防止null尚在队列里而误报通过。
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":2,"progress_revision":2,"active_session_id":null,"active_turn_id":null,"accepting":false,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        // 只保留已读内容供交接；可靠历史仍为running，不宣称保存已完成。
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文")
        #expect(sessions.progressPreview?.entries.first?.text == "准备查找地点资料。")
        #expect(sessions.conversation?.turns.last?.status == "running")
        #expect(sessions.conversation?.turns.last?.answer == nil)
        let saved = page(answer: "最终完整的已保存正文").replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":13")
        delayedHistory.reply(body: saved)
        try await observed { sessions.conversation?.turns.last?.answer == "最终完整的已保存正文" }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.progressPreview == nil)
        stream.send(event(text: "旧预览不能在保存后复活", revision: 1))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.last?.answer == "最终完整的已保存正文")
        #expect(await fixture.receivedCount == (newerEarlierPage ? 6 : 5))
        await clock.advance()
    }

    @Test(arguments: [false, true])
    func lateSnapshotForSameAttemptKeepsCompletionReadingHandoff(historicalFailure: Bool) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "等待核对保存结果的正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(progressEvent(revision: 1, text: "准备查找地点资料。", historicalFailure: historicalFailure))
        try await observed { sessions.progressPreview?.revision == 1 }
        let checking = Task { try await sessions.refresh() }
        let delayedSnapshot = await fixture.next()
        #expect(delayedSnapshot.protocolRequest.request.url?.path == "/api/snapshot")
        stream.send(Data("""
            id: 13
            event: trace
            data: {"schema_version":1,"kind":"turn.finished","session_id":"existing","turn_id":"turn","cursor":13,"data":{"status":"completed","reason":null,"answer_source":"model"}}


            """.utf8))
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":4,"progress_revision":4,"active_session_id":null,"active_turn_id":null,"accepting":true,"stopping":false}}


            """.utf8))
        let delayedHistory = await fixture.next()
        #expect(delayedHistory.protocolRequest.request.url?.path == "/api/sessions/existing")
        var old = try JSONSerialization.jsonObject(with: Data(snapshot(revision: 3, text: "等待核对保存结果的正文，迟到的同尝试尾部").utf8)) as! [String: Any]
        let progress = try JSONSerialization.jsonObject(with: Data(progressSnapshot(revision: 3, text: "准备查找地点资料。迟到的同尝试进展", historicalFailure: historicalFailure).utf8)) as! [String: Any]
        old["progress_revision"] = 3
        old["progress_preview"] = progress["progress_preview"]
        delayedSnapshot.reply(body: String(decoding: try JSONSerialization.data(withJSONObject: old), as: UTF8.self))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await checking.value
        #expect(sessions.service?.activeTurnID == nil, "较旧快照不能撤回已收到的结束状态")
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文，迟到的同尝试尾部")
        #expect(sessions.progressPreview?.entries.last?.text == "准备查找地点资料。迟到的同尝试进展")
        // HTTP累计3先采用，订阅队列中较早的累计2后抵达，不能让交接回退。
        stream.send(event(text: "等待核对保存结果的正文，较早片段", revision: 2))
        stream.send(progressEvent(revision: 2, text: "准备查找地点资料。较早片段", historicalFailure: historicalFailure))
        stream.send(Data("event: answer.preview\ndata: {\"schema_version\":1,\"kind\":\"answer.preview\",\"instance_id\":\"process\",\"revision\":4,\"preview\":null}\n\n".utf8))
        stream.send(progressEvent(revision: 4, text: nil))
        // 同一订阅的后续公开状态作处理屏障，防止null尚在队列里而误报通过。
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":4,"progress_revision":4,"active_session_id":null,"active_turn_id":null,"accepting":false,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        // 只保留已读内容供交接；可靠历史仍为running，不宣称保存已完成。
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文，迟到的同尝试尾部")
        #expect(sessions.progressPreview?.entries.last?.text == "准备查找地点资料。迟到的同尝试进展")
        #expect(sessions.conversation?.turns.first?.status == "running")
        #expect(sessions.conversation?.turns.first?.answer == nil)
        let saved = page(answer: "等待核对保存结果的正文，最终完整尾部").replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":13")
        delayedHistory.reply(body: saved)
        try await observed { sessions.conversation?.turns.first?.answer == "等待核对保存结果的正文，最终完整尾部" }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.progressPreview == nil)
        stream.send(event(text: "旧预览不能在保存后复活", revision: 1))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.answer == "等待核对保存结果的正文，最终完整尾部")
        #expect(await fixture.receivedCount == 7)
        await clock.advance()
    }

    @Test func newAnswerAttemptCancelsCompletionReadingHandoff() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "等待核对保存结果的正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(progressEvent(revision: 1, text: "准备查找地点资料。"))
        try await observed { sessions.progressPreview?.revision == 1 }
        stream.send(Data("""
            id: 13
            event: trace
            data: {"schema_version":1,"kind":"turn.finished","session_id":"existing","turn_id":"turn","cursor":13,"data":{"status":"completed","reason":null,"answer_source":"model"}}


            """.utf8))
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":2,"progress_revision":2,"active_session_id":null,"active_turn_id":null,"accepting":true,"stopping":false}}


            """.utf8))
        let delayedHistory = await fixture.next()
        #expect(delayedHistory.protocolRequest.request.url?.path == "/api/sessions/existing")
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文")
        #expect(sessions.progressPreview?.entries.first?.text == "准备查找地点资料。")
        stream.send(Data("event: answer.preview\ndata: {\"schema_version\":1,\"kind\":\"answer.preview\",\"instance_id\":\"process\",\"revision\":2,\"preview\":null}\n\n".utf8))
        stream.send(progressEvent(revision: 2, text: nil))
        // 同一订阅的后续公开状态作处理屏障，防止null尚在队列里而误报通过。
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":2,"progress_revision":2,"active_session_id":null,"active_turn_id":null,"accepting":false,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        // 只保留已读内容供交接；可靠历史仍为running，不宣称保存已完成。
        #expect(sessions.answerPreview?.text == "等待核对保存结果的正文")
        #expect(sessions.progressPreview?.entries.first?.text == "准备查找地点资料。")
        #expect(sessions.conversation?.turns.first?.status == "running")
        #expect(sessions.conversation?.turns.first?.answer == nil)
        stream.send(event(text: "", revision: 3, attempt: "replacement", status: "retrying"))
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":3,"progress_revision":2,"active_session_id":null,"active_turn_id":null,"accepting":true,"stopping":false}}


            """.utf8))
        try await observed { sessions.service?.accepting == true }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.progressPreview == nil)
        let saved = page(answer: "最终完整的已保存正文").replacingOccurrences(of: "\"cursor\":12", with: "\"cursor\":13")
        delayedHistory.reply(body: saved)
        try await observed { sessions.conversation?.turns.first?.answer == "最终完整的已保存正文" }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.progressPreview == nil)
        stream.send(event(text: "旧预览不能在保存后复活", revision: 1))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.answer == "最终完整的已保存正文")
        #expect(await fixture.receivedCount == 5)
        await clock.advance()
    }

    @Test func savedProgressIsReadAgainAfterReopeningClientWithoutLocalHistoryCopy() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: file)
        let saved = page(answer: "已保存回答").replacingOccurrences(of: "\"status\":\"completed\"", with:
            "\"status\":\"completed\",\"query_progress\":[{\"id\":\"note\",\"attempt_id\":\"query\",\"kind\":\"model\",\"text\":\"准备查找地点资料。\",\"status\":\"generated\"},{\"id\":\"tool\",\"attempt_id\":\"query\",\"kind\":\"tool\",\"text\":\"查找地点资料\",\"status\":\"failed\"}]")
        sessions.watchConversation(sessionID: "existing")
        let reading = Task { try await sessions.history(sessionID: "existing") }
        await fixture.next().reply(body: saved)
        let first = try await reading.value
        #expect(first.turns.first?.queryProgress?.map(\.status) == ["generated", "failed"])
        #expect(sessions.progressPreview == nil)
        let (reopened, other, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        #expect(reopened.conversation == nil)
        reopened.watchConversation(sessionID: "existing")
        let restored = Task { try await reopened.history(sessionID: "existing") }
        await other.next().reply(body: saved)
        #expect(try await restored.value.turns.first?.queryProgress == first.turns.first?.queryProgress)
        let old = Task { try await reopened.history(sessionID: "existing") }
        await other.next().reply(body: page(answer: "旧记录回答"))
        #expect(try await old.value.turns.first?.queryProgress == nil)
    }

    @Test func queryRetryAndCompleteDeliveryReplaceOnlyTransientModelEntry() async throws {
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
        let stream = await fixture.next()
        stream.beginStream()
        func progress(_ revision: Int, _ text: String, _ status: String, attempt: String, delivery: String) -> Data {
            let entries: [[String: Any]] = [
                ["id":"saved-tool", "attempt_id":"first-query", "kind":"tool", "text":"查找地点资料", "status":"completed"],
                ["id":attempt, "attempt_id":attempt, "kind":"model", "text":text, "status":status, "delivery":delivery],
            ]
            let preview: [String: Any] = ["instance_id":"process", "stream_id":"dataset", "session_id":"existing",
                "turn_id":"turn", "revision":revision, "entries":entries]
            let body = try! JSONSerialization.data(withJSONObject: ["schema_version":1, "kind":"progress.preview",
                "instance_id":"process", "revision":revision, "preview":preview])
            return Data("event: progress.preview\ndata: ".utf8) + body + Data("\n\n".utf8)
        }
        stream.send(progress(1, "失败旧说明", "generating", attempt: "old", delivery: "stream"))
        try await observed { sessions.progressPreview?.revision == 1 }
        let saved = try #require(sessions.progressPreview?.entries.first)
        stream.send(progress(2, "", "retrying", attempt: "old", delivery: "stream"))
        try await observed { sessions.progressPreview?.entries.last?.status == "retrying" }
        #expect(sessions.progressPreview?.entries.last?.text == "")
        #expect(sessions.progressPreview?.entries.first == saved)
        stream.send(progress(3, "", "generating", attempt: "complete", delivery: "complete"))
        try await observed { sessions.progressPreview?.revision == 3 }
        #expect(sessions.progressPreview?.entries.last?.delivery == "complete")
        #expect(sessions.progressPreview?.entries.last?.attemptID == "complete")
        #expect(sessions.answerPreview == nil)
        let complete = "重新查找地点资料。" + String(repeating: "安全中文🙂", count: 100)
        stream.send(progress(4, complete, "generated", attempt: "complete", delivery: "complete"))
        try await observed { sessions.progressPreview?.entries.last?.text == complete }
        #expect(sessions.progressPreview?.entries.first == saved)
        stream.send(event(text: "真实完整正文", revision: 1, attempt: "answer", status: "generated", delivery: "complete"))
        try await observed { sessions.answerPreview?.text == "真实完整正文" }
        #expect(sessions.answerPreview?.delivery == "complete")
        #expect(await fixture.receivedCount == 4)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func queryProgressUsesExistingSubscriptionAndKeepsAnswerSeparate() async throws {
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
        let stream = await fixture.next()
        stream.beginStream()
        let snapshotRecovery = sessions.contentRecoveryRevision
        #expect(snapshotRecovery > 0)
        func progress(_ revision: Int, _ text: String, turn: String = "turn") -> Data {
            let preview: [String: Any] = ["instance_id":"process", "stream_id":"dataset", "session_id":"existing",
                "turn_id":turn, "revision":revision, "entries":[["id":"query", "attempt_id":"request", "kind":"model", "text":text, "status":"generating"]]]
            let body = try! JSONSerialization.data(withJSONObject: ["schema_version":1, "kind":"progress.preview",
                "instance_id":"process", "revision":revision, "preview":preview])
            return Data("event: progress.preview\ndata: ".utf8) + body + Data("\n\n".utf8)
        }
        stream.send(progress(1, "准备查找交通资料"))
        try await observed { sessions.progressPreview?.entries.first?.text == "准备查找交通资料" }
        #expect(sessions.contentRecoveryRevision > snapshotRecovery)
        let subscriptionRecovery = sessions.contentRecoveryRevision
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.answer == nil)
        stream.send(progress(2, "准备查找交通资料并比较出行方式。"))
        try await observed { sessions.progressPreview?.revision == 2 }
        #expect(sessions.contentRecoveryRevision == subscriptionRecovery)
        stream.send(progress(1, "重复旧内容"))
        stream.send(progress(3, "错误归属", turn: "other"))
        stream.send(event(text: "真实正文", revision: 1))
        try await observed { sessions.answerPreview?.text == "真实正文" }
        #expect(sessions.progressPreview?.entries.first?.text == "准备查找交通资料并比较出行方式。")
        #expect(await fixture.receivedCount == 4)
        sessions.watchConversation(sessionID: nil)
        #expect(sessions.progressPreview == nil)
        await clock.advance()
    }

    @Test func liveStateReconcilesAheadProgressWithoutWaitingForPolling() async throws {
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
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(progressEvent(revision: 8, text: "新轮已经准备查找资料", turn: "next-turn"))
        stream.send(stateEvent(progressRevision: 8, turn: "next-turn"))
        try await observed { sessions.service?.activeTurnID == "next-turn" && sessions.progressPreview?.revision == 8 }
        #expect(sessions.progressPreview?.entries.first?.text == "新轮已经准备查找资料")
        let history = await fixture.next()
        #expect(history.protocolRequest.request.url?.path == "/api/sessions/existing")
        history.reply(body: page().replacingOccurrences(of: "\"turn\"", with: "\"next-turn\"").replacingOccurrences(of: "\"ordinal\":1", with: "\"ordinal\":2"))
        try await observed { sessions.conversation?.turns.contains(where: { $0.id == "next-turn" }) == true }
        let checking = Task { try await sessions.refresh() }
        await fixture.next().reply(body: snapshot(active: false).replacingOccurrences(of: "\"answer_revision\":0", with: "\"answer_revision\":0,\"progress_revision\":7"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await checking.value
        #expect(sessions.service?.activeTurnID == "next-turn")
        stream.send(stateEvent(progressRevision: 7, turn: nil))
        stream.send(progressEvent(revision: 9, text: nil, turn: "next-turn"))
        try await observed { sessions.progressPreview == nil }
        stream.send(progressEvent(revision: 8, text: "旧内容不能复活", turn: "next-turn"))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.service?.activeTurnID == "next-turn")
        #expect(sessions.progressPreview == nil)
        #expect(await fixture.receivedCount == 7)
        await clock.advance()
    }

    @Test(arguments: ["running", "completed", "restarted"])
    func queryProgressRecoveryUsesSnapshotAndReliableHistory(result: String) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: progressSnapshot(revision: 1, text: "断线前说明"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let old = await fixture.next()
        old.beginStream(); old.fail()
        try await observed { sessions.answerFailure != nil }
        let recovery = sessions.contentRecoveryRevision
        await clock.advance()
        let current = String(repeating: "离开期间已取得的累计行动说明🙂", count: 50)
        let body = result == "running" ? progressSnapshot(revision: 4, text: current)
            : result == "completed" ? progressSnapshot(revision: 4, text: nil, active: false)
            : progressSnapshot(revision: 0, text: nil, active: false).replacingOccurrences(of: "process", with: "new-process")
        await fixture.next().reply(body: body)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        let saved = page(answer: "已保存回答").replacingOccurrences(of: "\"status\":\"completed\"", with:
            "\"status\":\"completed\",\"query_progress\":[{\"id\":\"tool\",\"attempt_id\":\"query\",\"kind\":\"tool\",\"text\":\"查找地点资料\",\"status\":\"completed\"}]")
        var interruptedPage = try JSONSerialization.jsonObject(with: Data(saved.utf8)) as! [String: Any]
        var interruptedTurns = interruptedPage["turns"] as! [[String: Any]]
        interruptedTurns[0]["status"] = "terminated"
        interruptedTurns[0]["reason"] = "service_interrupted"
        interruptedPage["turns"] = interruptedTurns
        let interrupted = String(decoding: try JSONSerialization.data(withJSONObject: interruptedPage), as: UTF8.self)
        await fixture.next().reply(body: result == "running" ? page() : result == "completed" ? saved : interrupted)
        _ = await clock.nextInterval()
        let resumed = await fixture.next()
        resumed.beginStream()
        #expect(sessions.contentRecoveryRevision > recovery)
        old.send(progressEvent(revision: 99, text: "旧连接不应复活的说明"))
        if result == "running" {
            #expect(sessions.progressPreview?.entries.first?.text == current)
            resumed.send(progressEvent(revision: 5, text: "恢复后继续说明"))
            try await observed { sessions.progressPreview?.revision == 5 }
            resumed.send(progressEvent(revision: 6, text: nil))
            try await observed { sessions.progressPreview == nil }
            resumed.send(progressEvent(revision: 5, text: "旧次序不应复活"))
        } else {
            #expect(sessions.progressPreview == nil)
            #expect(sessions.conversation?.turns.first?.queryProgress?.first?.status == "completed")
            if result == "restarted" { #expect(sessions.conversation?.turns.first?.reason == "service_interrupted") }
        }
        resumed.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.progressPreview == nil)
        #expect(await fixture.receivedCount == 8)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func queryOnlyRemoteStopFreezesBeforeAdoptingLateSnapshotProgress() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        sessions.watchConversation(sessionID: "existing")
        func progressSnapshot(_ revision: Int, stopping: Bool, active: Bool = true) throws -> String {
            var value = try JSONSerialization.jsonObject(with: Data(snapshot(active: active).utf8)) as! [String: Any]
            value["stopping"] = stopping
            let entries: [[String: Any]] = revision == 1
                ? [["id":"query", "attempt_id":"request", "kind":"model", "text":"准备查找地点资料。", "status":"generating"]]
                : [["id":"query", "attempt_id":"request", "kind":"model", "text":"准备查找地点资料并比较交通。", "status":"generated"],
                   ["id":"tool", "attempt_id":"request", "kind":"tool", "text":"查找地点资料", "status":active ? "running" : "completed"]]
            value["progress_revision"] = revision
            value["progress_preview"] = ["instance_id":"process", "stream_id":"dataset", "session_id":"existing",
                "turn_id":"turn", "revision":revision, "entries":entries]
            if !active {
                value["accepting"] = false; value["storage_error"] = "完成事实未保存"
                value["unsaved_fact"] = ["turn_id":"turn", "kind":"turn.finished"]
            }
            return String(decoding: try JSONSerialization.data(withJSONObject: value), as: UTF8.self)
        }
        let initial = Task { try await sessions.refresh() }
        await fixture.next().reply(body: try progressSnapshot(1, stopping: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await initial.value
        #expect(sessions.answerPreview == nil)
        let before = sessions.progressPreview
        let stopped = Task { try await sessions.refresh() }
        await fixture.next().reply(body: try progressSnapshot(2, stopping: true))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await stopped.value
        #expect(sessions.service?.stopping == true)
        #expect(sessions.progressPreview == before)
        #expect(!sessions.canSubmit)
        let released = Task { try await sessions.refresh() }
        await fixture.next().reply(body: try progressSnapshot(3, stopping: false, active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await released.value
        #expect(sessions.progressPreview?.entries.last?.status == "completed")
        #expect(sessions.service?.storageError != nil)
        #expect(!sessions.canSubmit)
    }

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

    @Test(arguments: ["running", "retrying", "completed", "restarted"])
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
        let continuing = result == "running" || result == "retrying"
        let body = result == "retrying" ? snapshot(revision: 4, text: "")
            .replacingOccurrences(of: "\"status\":\"generating\"", with: "\"status\":\"retrying\"")
            : result == "running" ? snapshot(revision: 4, text: "断线期间累计正文")
            : result == "completed" ? snapshot(revision: 4, active: false)
            : snapshot(active: false).replacingOccurrences(of: "process", with: "new-process")
        await fixture.next().reply(body: body)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        let history = continuing ? page() : result == "completed" ? page(answer: "离开期间保存的完整正文")
            : page().replacingOccurrences(of: "\"status\":\"running\"", with: "\"status\":\"terminated\",\"reason\":\"service_interrupted\"")
        await fixture.next().reply(body: history)
        _ = await clock.nextInterval()
        let resumed = await fixture.next()
        #expect(resumed.protocolRequest.request.url?.path == "/api/events")
        resumed.beginStream()
        if continuing {
            #expect(sessions.answerPreview?.text == (result == "retrying" ? "" : "断线期间累计正文"))
            if result == "retrying" { #expect(sessions.answerPreview?.status == "retrying") }
            resumed.send(event(text: "继续生成", revision: 5, attempt: "new-attempt"))
            try await observed { sessions.answerPreview?.text == "继续生成" }
            // 返回列表再进入相同会话，同次序的有效累计对象仍可恢复。
            sessions.watchConversation(sessionID: nil)
            sessions.watchConversation(sessionID: "existing")
            let read = Task { try await sessions.history(sessionID: "existing") }
            await fixture.next().reply(body: page())
            _ = try await read.value
            let reopened = await fixture.next()
            reopened.beginStream()
            reopened.send(event(text: "继续生成", revision: 5, attempt: "new-attempt"))
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

    @Test func failedAnswerClearsBeforeRetryAndCompleteDeliveryRemainsInHistory() async throws {
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
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(event(text: "失败旧正文", revision: 1))
        try await observed { sessions.answerPreview?.text == "失败旧正文" }
        stream.send(event(text: "", revision: 2, status: "retrying"))
        try await observed { sessions.answerPreview?.status == "retrying" }
        #expect(sessions.answerPreview?.text == "")
        stream.send(event(text: "新尝试正文", revision: 3, attempt: "second"))
        try await observed { sessions.answerPreview?.revision == 3 }
        #expect(sessions.answerPreview?.text == "新尝试正文")
        stream.send(event(text: "", revision: 4, attempt: "complete", delivery: "complete"))
        try await observed { sessions.answerPreview?.revision == 4 }
        #expect(sessions.answerPreview?.text == "")
        #expect(sessions.answerPreview?.delivery == "complete")
        await clock.advance()
        await fixture.next().reply(body: snapshot(revision: 5, active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(answer: "完整回答。").replacingOccurrences(of: "\"status\":\"completed\"", with: "\"status\":\"completed\",\"answer_delivery\":\"complete\""))
        _ = await clock.nextInterval()
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.answer == "完整回答。")
        #expect(sessions.conversation?.turns.first?.answerDelivery == "complete")
        stream.finish()
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test(arguments: ["output_limit", "budget", "user_stop"])
    func incompleteSavedOutcomeReplacesPreviewWithoutChangingItsReason(reason: String) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "暂存正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        await clock.advance()
        await fixture.next().reply(body: snapshot(revision: 2, active: false))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(answer: "本轮已保存的不完整结果")
            .replacingOccurrences(of: "\"status\":\"completed\"", with: "\"status\":\"terminated\",\"reason\":\"\(reason)\""))
        _ = await clock.nextInterval()
        stream.send(event(text: "迟到正文不能覆盖终态", revision: 3))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview == nil)
        #expect(sessions.conversation?.turns.first?.status == "terminated")
        #expect(sessions.conversation?.turns.first?.reason == reason)
        #expect(sessions.conversation?.turns.first?.answer == "本轮已保存的不完整结果")
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test func firstSnapshotAlreadyStoppingRestoresAndFreezesItsCurrentBody() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "停止中的已有正文")
            .replacingOccurrences(of: "\"stopping\":false", with: "\"stopping\":true"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        #expect(sessions.answerPreview?.text == "停止中的已有正文")
        let stream = await fixture.next()
        stream.beginStream()
        stream.send(event(text: "停止后继续增加的文字", revision: 2))
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview?.text == "停止中的已有正文")
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test(arguments: [false, true]) func stopFreezesUntilSavedOrUnsavedResultIsConfirmed(unsaved: Bool) async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        sessions.watchConversation(sessionID: "existing")
        sessions.setForeground(true)
        defer { sessions.setForeground(false) }
        await fixture.next().reply(body: snapshot(revision: 1, text: "停止前正文"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page())
        _ = await clock.nextInterval()
        let stream = await fixture.next()
        stream.beginStream()
        func progress(_ text: String, revision: Int) throws -> Data {
            let value: [String: Any] = ["schema_version":1, "kind":"progress.preview", "instance_id":"process", "revision":revision,
                "preview":["instance_id":"process", "stream_id":"dataset", "session_id":"existing", "turn_id":"turn", "revision":revision,
                    "entries":[["id":"query", "attempt_id":"query", "kind":"model", "text":text, "status":"generated"]]]]
            return Data("event: progress.preview\ndata: ".utf8) + (try JSONSerialization.data(withJSONObject: value)) + Data("\n\n".utf8)
        }
        stream.send(try progress("停止前的进展说明。", revision: 1))
        try await observed { sessions.progressPreview?.entries.first?.text == "停止前的进展说明。" }
        let target = try #require(sessions.stopTarget)
        let stop = Task { try await sessions.stop(target: target) }
        let response = await fixture.next()
        stream.send(event(text: "停止后的全文", revision: 2, status: "generated"))
        stream.send(try progress("停止后实际返回的进展说明。", revision: 2))
        // 合法后续状态作同一字节流的处理屏障；失败回调本身不能证明最后data已采用。
        stream.send(Data("""
            event: service.state
            data: {"schema_version":1,"kind":"service.state","state":{"schema_version":1,"instance_id":"process","answer_revision":2,"progress_revision":2,"active_session_id":"existing","active_turn_id":"turn","accepting":false,"stopping":true}}


            """.utf8))
        try await observed { sessions.service?.accepting == false }
        stream.fail()
        try await observed { sessions.answerFailure != nil }
        #expect(sessions.answerPreview?.text == "停止前正文")
        #expect(sessions.progressPreview?.entries.first?.text == "停止前的进展说明。")
        let fault = Task { try await sessions.refresh() }
        await fixture.next().reply(body: snapshot(revision: 2, text: "停止后的全文")
            .replacingOccurrences(of: "\"accepting\":true", with: "\"accepting\":false,\"storage_error\":\"改名未保存\",\"unsaved_fact\":{\"kind\":\"session.rename\"}"))
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await fault.value
        #expect(sessions.answerPreview?.text == "停止前正文")
        #expect(sessions.stopState == .requesting(target))
        #expect(!sessions.canSubmit)
        response.reply(body: "{\"schema_version\":1,\"turn_id\":\"turn\",\"status\":\"running\",\"stopping\":true}")
        try await stop.value
        await clock.advance()
        let finalState = unsaved ? snapshot(revision: 3, text: "停止后的全文", active: false)
            .replacingOccurrences(of: "\"accepting\":true", with: "\"accepting\":false,\"storage_error\":\"未保存\",\"unsaved_fact\":{\"turn_id\":\"turn\",\"kind\":\"turn.finished\"}")
            : snapshot(revision: 3, active: false)
        await fixture.next().reply(body: finalState)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().reply(body: page(answer: unsaved ? nil : "停止后的全文"))
        _ = await clock.nextInterval()
        #expect(sessions.answerPreview?.text == (unsaved ? "停止后的全文" : nil))
        #expect(sessions.progressPreview?.entries.first?.text == (unsaved ? "停止后实际返回的进展说明。" : nil))
        #expect(sessions.service?.storageError == (unsaved ? "未保存" : nil))
        #expect(sessions.conversation?.turns.first?.answer == (unsaved ? nil : "停止后的全文"))
        #expect(sessions.canSubmit == !unsaved)
        sessions.setForeground(false)
        await clock.advance()
    }

    private func progressEvent(revision: Int, text: String?, turn: String = "turn", attempt: String = "request", status: String = "generating", historicalFailure: Bool = false) -> Data {
        let prior: [[String: Any]] = historicalFailure ? [["id":"old-query", "attempt_id":"old-request", "kind":"model", "status":"invalidated", "text":""]] : []
        let preview: Any = text.map { ["instance_id":"process", "stream_id":"dataset", "session_id":"existing",
            "turn_id":turn, "revision":revision, "entries":prior + [["id":"query", "attempt_id":attempt, "kind":"model", "status":status, "text":$0]]] as [String: Any] } ?? NSNull()
        let body = try! JSONSerialization.data(withJSONObject: ["schema_version":1,"kind":"progress.preview",
            "instance_id":"process","revision":revision,"preview":preview])
        return Data("event: progress.preview\ndata: ".utf8) + body + Data("\n\n".utf8)
    }

    private func progressSnapshot(revision: Int, text: String?, active: Bool = true, historicalFailure: Bool = false) -> String {
        let prior: [[String: Any]] = historicalFailure ? [["id":"old-query", "attempt_id":"old-request", "kind":"model", "status":"invalidated", "text":""]] : []
        let preview: Any
        if let text {
            preview = ["instance_id":"process","stream_id":"dataset","session_id":"existing","turn_id":"turn","revision":revision,
                       "entries":prior + [["id":"query","attempt_id":"request","kind":"model","status":"generating","text":text]]] as [String: Any]
        } else { preview = NSNull() }
        let data = try! JSONSerialization.data(withJSONObject: ["schema_version":1,"stream_id":"dataset","instance_id":"process",
            "answer_revision":0,"answer_preview":NSNull(),"progress_revision":revision,"progress_preview":preview,
            "active_session_id":"existing","active_turn_id":active ? "turn" : NSNull(),"accepting":true,"stopping":false])
        return String(decoding: data, as: UTF8.self)
    }

    private func stateEvent(progressRevision: Int, turn: String?) -> Data {
        let state: [String: Any] = ["schema_version":1,"instance_id":"process","answer_revision":0,
            "progress_revision":progressRevision,"active_session_id":"existing","active_turn_id":turn as Any? ?? NSNull(),
            "accepting":true,"stopping":false]
        let body = try! JSONSerialization.data(withJSONObject: ["schema_version":1,"kind":"service.state","state":state])
        return Data("event: service.state\ndata: ".utf8) + body + Data("\n\n".utf8)
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

    private func event(text: String, revision: Int, attempt: String = "attempt", turn: String = "turn", status: String = "generating", delivery: String = "stream") -> Data {
        var preview = try! JSONSerialization.jsonObject(with: preview(text: text, revision: revision, attempt: attempt, turn: turn)) as! [String: Any]
        preview["status"] = status
        preview["delivery"] = delivery
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
