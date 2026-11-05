import Foundation
import SessionKit
import SQLite3
import Testing

@MainActor
struct DraftTests {
    @Test func initialOfflineScopeNeverRetargetsToAReplacementDataset() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let offline = try sessions.saveDraft(input: "首次离线原文", scope: sessions.draft(sessionID: nil).scope)
        try await connect(sessions, fixture)
        try await connect(sessions, fixture, dataset: "replacement")
        _ = try sessions.saveDraft(input: "原页面继续编辑", scope: offline.scope)
        #expect(try sessions.draft(sessionID: nil).input.isEmpty)
        try await connect(sessions, fixture)
        #expect(try sessions.draft(sessionID: nil).input == "原页面继续编辑")
    }

    @Test func offlineRestoredDraftCannotBeSubmittedToAReplacementDataset() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        _ = try sessions.saveDraft(input: "原数据集的旅行要求", scope: sessions.draft(sessionID: nil).scope)
        let (reopened, _, _) = try await HTTPFixture.client(databaseURL: file)
        try reopened.configure(address: address)
        let offline = try reopened.draft(sessionID: nil)
        #expect(offline.input == "原数据集的旅行要求")
        try await connect(reopened, fixture, dataset: "replacement")
        #expect(!reopened.canSubmit(draft: offline))
        guard !reopened.canSubmit(draft: offline) else { return }
        await #expect(throws: SessionFailure.obsolete) { try await reopened.submit(draft: offline) }
        #expect(await fixture.receivedCount == 4)
        try await connect(reopened, fixture)
        #expect(try reopened.draft(sessionID: nil).input == "原数据集的旅行要求")
    }

    @Test func reconnectingInForegroundDoesNotSendAnOfflineDraft() async throws {
        let clock = PollClock()
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file, wait: { try await clock.wait($0) })
        try await connect(sessions, fixture)
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().fail()
        _ = try? await refresh.value
        _ = try sessions.saveDraft(input: "离线内容仍须自己发送", scope: sessions.draft(sessionID: nil).scope)
        sessions.setForeground(true)
        let snapshot = await fixture.next()
        #expect(snapshot.protocolRequest.request.httpMethod == "GET")
        snapshot.reply(body: "{\"schema_version\":1,\"stream_id\":\"dataset\",\"accepting\":true,\"stopping\":false}")
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = await clock.nextInterval()
        #expect(sessions.canSubmit)
        #expect(sessions.pendingSubmission == nil)
        #expect(try sessions.draft(sessionID: nil).input == "离线内容仍须自己发送")
        #expect(await fixture.receivedCount == 5)
        sessions.setForeground(false)
        await clock.advance()
    }

    @Test(arguments: ["source", "destination", "write-failure"])
    func movingDraftNeverOverwritesOtherSavedContentOrPartiallyCommits(mode: String) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let saved = try sessions.saveDraft(input: "原草稿", scope: sessions.draft(sessionID: nil).scope)
        if mode == "source" { _ = try sessions.saveDraft(input: "后来另一页面的草稿", scope: saved.scope) }
        if mode == "destination" {
            _ = try sessions.saveDraft(input: "目标已有内容", scope: sessions.draft(sessionID: "created").scope)
        }
        var database: OpaquePointer?
        #expect(sqlite3_open(file.path, &database) == SQLITE_OK)
        defer { sqlite3_close(database) }
        if mode == "write-failure" {
            #expect(sqlite3_exec(database, "CREATE TRIGGER fail_move BEFORE DELETE ON draft BEGIN SELECT RAISE(ABORT,'disk failure'); END", nil, nil, nil) == SQLITE_OK)
        }
        #expect(throws: SessionFailure.draftSaveFailed) {
            try sessions.moveDraft(saved, input: saved.input, to: "created")
        }
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(try reopened.draft(sessionID: nil).input == (mode == "source" ? "后来另一页面的草稿" : "原草稿"))
        #expect(try reopened.draft(sessionID: "created").input == (mode == "destination" ? "目标已有内容" : ""))
    }

    @Test func draftWrittenBeforeFirstConnectionRemainsAvailableAfterVerification() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        let offline = try sessions.saveDraft(input: "首次离线编辑", scope: sessions.draft(sessionID: nil).scope)
        #expect(try SharedSessions(databaseURL: file).draft(sessionID: nil).input == "首次离线编辑")
        try await connect(sessions, fixture)
        #expect(try sessions.draft(sessionID: nil).input == "首次离线编辑")
        let later = try sessions.saveDraft(input: "连接后继续编辑", scope: offline.scope)
        #expect(later.scope.streamID == "dataset")
        #expect(try SharedSessions(databaseURL: file).draft(sessionID: nil).input == "连接后继续编辑")
    }

    @Test func laterNewConversationDraftCanFollowItsAcceptedConversation() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let original = try sessions.saveDraft(input: "开始旅行", scope: sessions.draft(sessionID: nil).scope)
        let sending = Task { try await sessions.submit(input: original.input, sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        let later = try sessions.saveDraft(input: "继续编辑下一条", scope: original.scope)
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn"}
            """)
        let receipt = try await sending.value
        _ = try sessions.moveDraft(later, input: later.input, to: receipt.sessionID)
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(try reopened.draft(sessionID: "created").input == "继续编辑下一条")
        #expect(try reopened.draft(sessionID: nil).input.isEmpty)
    }

    @Test func offlineEditingDoesNotQueueInputAndUnknownSubmissionKeepsItsOriginal() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let original = try sessions.saveDraft(input: "已经提交的原文", scope: sessions.draft(sessionID: "first").scope)
        let sending = Task { try await sessions.submit(input: original.input, sessionID: "first") }
        let request = await fixture.next()
        _ = try sessions.saveDraft(input: "仍在编辑的草稿", scope: original.scope)
        request.fail()
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().fail()
        _ = try? await refresh.value
        #expect(!sessions.canSubmit)
        _ = try sessions.saveDraft(input: "离线继续编辑", scope: original.scope)
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.pendingSubmission?.input == "已经提交的原文")
        #expect(try reopened.draft(sessionID: "first").input == "离线继续编辑")
        try await connect(sessions, fixture)
        #expect(await fixture.receivedCount == 7)
        #expect(sessions.pendingSubmission?.input == "已经提交的原文")
        #expect(try sessions.draft(sessionID: "first").input == "离线继续编辑")
    }

    @Test func serviceAndDatasetChangesIsolateDraftsAndLateAcceptance() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let original = try sessions.saveDraft(input: "原数据集草稿", scope: sessions.draft(sessionID: "same").scope)
        let sending = Task { try await sessions.submit(input: original.input, sessionID: "same") }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        try await connect(sessions, fixture, dataset: "replacement")
        #expect(try sessions.draft(sessionID: "same").input.isEmpty)
        _ = try sessions.saveDraft(input: "新数据集草稿", scope: sessions.draft(sessionID: "same").scope)
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"same","turn_id":"turn"}
            """)
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        #expect(try sessions.draft(sessionID: "same").input == "新数据集草稿")
        try sessions.configure(address: "https://other.example.test")
        #expect(try sessions.draft(sessionID: "same").input.isEmpty)
        _ = try sessions.saveDraft(input: "另一服务草稿", scope: sessions.draft(sessionID: "same").scope)
        try sessions.configure(address: address)
        #expect(try sessions.draft(sessionID: "same").input == "新数据集草稿")
        try await connect(sessions, fixture)
        #expect(try sessions.draft(sessionID: "same").input == "原数据集草稿")
    }

    @Test func failedDraftSaveIsVisibleAndKeepsTheLastSavedText() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let saved = try sessions.saveDraft(input: "已保存内容", scope: sessions.draft(sessionID: nil).scope)
        var database: OpaquePointer?
        #expect(sqlite3_open(file.path, &database) == SQLITE_OK)
        defer { sqlite3_close(database) }
        #expect(sqlite3_exec(database, "CREATE TRIGGER fail_draft BEFORE INSERT ON draft BEGIN SELECT RAISE(ABORT,'disk failure'); END", nil, nil, nil) == SQLITE_OK)
        #expect(throws: SessionFailure.draftSaveFailed) {
            try sessions.saveDraft(input: "未保存内容", scope: saved.scope)
        }
        #expect(try sessions.draft(sessionID: nil) == saved)
        #expect(try SharedSessions(databaseURL: file).draft(sessionID: nil).input == "已保存内容")
        #expect(await fixture.receivedCount == 2)
    }

    @Test func failedAcceptanceCleanupRollsBackDraftAndPendingTogether() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let saved = try sessions.saveDraft(input: "清理失败须保留", scope: sessions.draft(sessionID: nil).scope)
        let sending = Task { try await sessions.submit(input: saved.input, sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        var database: OpaquePointer?
        #expect(sqlite3_open(file.path, &database) == SQLITE_OK)
        defer { sqlite3_close(database) }
        #expect(sqlite3_exec(database, "CREATE TRIGGER fail_clear BEFORE DELETE ON pending_submission BEGIN SELECT RAISE(ABORT,'disk failure'); END", nil, nil, nil) == SQLITE_OK)
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn"}
            """)
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.pendingSubmission == pending)
        #expect(try reopened.draft(sessionID: nil) == saved)
    }

    @Test(arguments: ["unchanged", "edited", "edited-back", "other-session"])
    func acceptanceOnlyClearsTheSubmittedDraftRevision(edit: String) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let original = try sessions.saveDraft(input: "原始要求", scope: sessions.draft(sessionID: nil).scope)
        let sending = Task { try await sessions.submit(input: original.input, sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        if edit == "edited" || edit == "edited-back" {
            _ = try sessions.saveDraft(input: "稍后输入", scope: original.scope)
        }
        if edit == "edited-back" { _ = try sessions.saveDraft(input: "原始要求", scope: original.scope) }
        if edit == "other-session" {
            _ = try sessions.saveDraft(input: "其他会话草稿", scope: sessions.draft(sessionID: "other").scope)
        }
        let beforeAcceptance: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(beforeAcceptance.pendingSubmission?.input == "原始要求")
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn"}
            """)
        _ = try await sending.value
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.pendingSubmission == nil)
        let expected = edit == "edited" ? "稍后输入" : edit == "edited-back" ? "原始要求" : ""
        #expect(try reopened.draft(sessionID: nil).input == expected)
        if edit == "other-session" {
            #expect(try reopened.draft(sessionID: "other").input == "其他会话草稿")
        }
    }

    @Test func draftsRemainSeparateAcrossSessionsAndReopenOffline() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let new = try sessions.draft(sessionID: nil)
        let first = try sessions.draft(sessionID: "first")
        let second = try sessions.draft(sessionID: "second")
        _ = try sessions.saveDraft(input: "  新旅行\n", scope: new.scope)
        _ = try sessions.saveDraft(input: "第一段旅行", scope: first.scope)
        _ = try sessions.saveDraft(input: "第二段旅行", scope: second.scope)
        #expect(try sessions.draft(sessionID: "first").input == "第一段旅行")
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.connection == .connecting)
        #expect(try reopened.draft(sessionID: nil).input == "  新旅行\n")
        #expect(try reopened.draft(sessionID: "first").input == "第一段旅行")
        #expect(try reopened.draft(sessionID: "second").input == "第二段旅行")
        #expect(try reopened.draft(sessionID: "first").scope.streamID == "dataset")
    }

    private func connect(_ sessions: any SessionAccess, _ fixture: HTTPFixture, dataset: String = "dataset") async throws {
        let shouldCheck = sessions.pendingSubmission?.serverAddress == sessions.serverAddress &&
            sessions.pendingSubmission?.streamID == dataset && sessions.submission != .sending
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"\(dataset)","accepting":true,"stopping":false}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        if shouldCheck {
            await fixture.next().reply(status: 404, body: "{\"detail\":{\"code\":\"SUBMISSION_NOT_FOUND\"}}")
        }
        _ = try await refresh.value
    }
}
