import Foundation
import SessionKit
import SQLite3
import Testing

@MainActor
struct SubmissionTests {
    @Test func idleSnapshotStartedBeforeAcceptanceCannotPermitAnotherSubmission() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let oldRefresh = Task { try await sessions.refresh() }
        let idleSnapshot = await fixture.next()
        let sending = Task { try await sessions.submit(input: "第一轮", sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn"}
            """)
        _ = try await sending.value
        idleSnapshot.reply(body: """
            {"schema_version":1,"stream_id":"dataset","accepting":true,"stopping":false}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await oldRefresh.value
        #expect(sessions.canSubmit == false)
        guard !sessions.canSubmit else { return }
        await #expect(throws: SessionFailure.busy) {
            try await sessions.submit(input: "不能用旧快照开放下一轮", sessionID: nil)
        }
    }

    @Test func newSubmissionPreservesOriginalInputAndNullableTargetBeforeHTTP() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let file = directory.appending(path: "client.sqlite")
        let (sessions, fixture, address) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let sending = Task { try await sessions.submit(input: "  杭州两日\n", sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        #expect(request.protocolRequest.request.httpMethod == "POST")
        #expect(request.protocolRequest.request.url?.path == "/api/turns")
        #expect(pending.input == "  杭州两日\n")
        #expect(pending.sessionID == nil)
        #expect(pending.serverAddress == address)
        #expect(pending.streamID == "dataset")
        let body = try request.jsonBody()
        #expect(body == ["submission_id": pending.id, "input": "  杭州两日\n"])
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.pendingSubmission == pending)
        await #expect(throws: SessionFailure.busy) {
            try await sessions.submit(input: pending.input, sessionID: nil)
        }
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn-one"}
            """)
        let receipt = try await sending.value
        #expect(receipt.request == pending)
        #expect(receipt.sessionID == "created")
        #expect(receipt.turnID == "turn-one")
        #expect(receipt.request.sessionID == nil)
        #expect(sessions.submission == .accepted(receipt))
        #expect(sessions.pendingSubmission == nil)
    }

    @Test(arguments: ["lost", "malformed", "wrong-identity", "proxy-error", "wrong-target"])
    func unknownAcceptanceKeepsOriginalRecordAcrossReopen(mode: String) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let sending = Task { try await sessions.submit(input: "原始输入\n ", sessionID: "existing") }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        if mode == "lost" { request.fail() }
        else if mode == "malformed" { request.reply(status: 202, body: "{}") }
        else if mode == "proxy-error" { request.reply(status: 503, body: "proxy unavailable") }
        else if mode == "wrong-target" {
            request.reply(status: 202, body: """
                {"schema_version":1,"submission_id":"\(pending.id)","session_id":"another-session","turn_id":"turn"}
                """)
        }
        else {
            request.reply(status: 202, body: """
                {"schema_version":1,"submission_id":"other","session_id":"existing","turn_id":"turn"}
                """)
        }
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        #expect(sessions.submission == .unknown)
        #expect(sessions.pendingSubmission == pending)
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        #expect(reopened.pendingSubmission == pending)
        #expect(reopened.submission == .unknown)
        let count = await fixture.receivedCount
        await #expect(throws: SessionFailure.submissionUnknown) {
            try await sessions.submit(input: "不要自动重发", sessionID: "existing")
        }
        #expect(await fixture.receivedCount == count)
    }

    @Test(arguments: [409, 422, 503])
    func definiteRejectionDoesNotCreateAQueuedInput(status: Int) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let sending = Task { try await sessions.submit(input: "补充当前旅行要求", sessionID: "existing") }
        let request = await fixture.next()
        #expect(try request.jsonBody()["session_id"] == "existing")
        let code = status == 409 ? "BUSY" : status == 422 ? "INVALID_INPUT" : "SERVICE_UNAVAILABLE"
        let failure: SessionFailure = status == 409 ? .busy : status == 422 ? .invalidInput : .serviceUnavailable
        request.reply(status: status, body: "{\"detail\":{\"code\":\"\(code)\"}}")
        await #expect(throws: failure) { try await sending.value }
        #expect(sessions.submission == .rejected(failure))
        #expect(sessions.pendingSubmission == nil)
        #expect(try SharedSessions(databaseURL: file).pendingSubmission == nil)
        #expect(await fixture.receivedCount == 3)
    }

    @Test func localSaveFailureNeverSendsTheInput() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        var database: OpaquePointer?
        #expect(sqlite3_open(file.path, &database) == SQLITE_OK)
        defer { sqlite3_close(database) }
        #expect(sqlite3_exec(database, "CREATE TRIGGER fail_pending BEFORE INSERT ON pending_submission BEGIN SELECT RAISE(ABORT,'disk failure'); END", nil, nil, nil) == SQLITE_OK)
        await #expect(throws: SessionFailure.localSaveFailed) {
            try await sessions.submit(input: "必须保留这份输入", sessionID: nil)
        }
        #expect(sessions.pendingSubmission == nil)
        #expect(sessions.submission == .rejected(.localSaveFailed))
        #expect(await fixture.receivedCount == 2)
    }

    @Test func lateAcceptanceCannotApplyToAnotherConfiguredServer() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        try await connect(sessions, fixture)
        let sending = Task { try await sessions.submit(input: "原服务的要求", sessionID: nil) }
        let request = await fixture.next()
        let pending = try #require(sessions.pendingSubmission)
        try sessions.configure(address: "https://another.example.test")
        request.reply(status: 202, body: """
            {"schema_version":1,"submission_id":"\(pending.id)","session_id":"created","turn_id":"turn"}
            """)
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        #expect(sessions.pendingSubmission == pending)
        #expect(sessions.submission == .isolated)
        #expect(sessions.service == nil)
    }

    private func connect(_ sessions: any SessionAccess, _ fixture: HTTPFixture) async throws {
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"dataset","active_session_id":null,"active_turn_id":null,
             "accepting":true,"stopping":false,"map_paused":true}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await refresh.value
    }
}
