import Foundation
import SessionKit
import SQLite3
import Testing

@MainActor
struct RecoveryTests {
    @Test func reopeningChecksOriginalAcceptanceAndKeepsLaterDraft() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (phone, transport, address) = try await HTTPFixture.client(databaseURL: file)
        await enqueueIdentity(transport)
        _ = try await phone.refresh()
        let draft = try phone.saveDraft(input: "  杭州两日\n", scope: phone.draft(sessionID: nil).scope)
        let sending = Task { try await phone.submit(draft: draft) }
        let original = await transport.next()
        let pending = try #require(phone.pendingSubmission)
        original.fail()
        _ = try? await sending.value
        let (reopened, fixture, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        _ = try reopened.saveDraft(input: "后来编辑的内容", scope: draft.scope)
        await enqueueIdentity(fixture)
        await fixture.enqueue(body: receipt(pending))
        _ = try await reopened.refresh()
        #expect(reopened.pendingSubmission == nil)
        if case .accepted(let receipt) = reopened.submission {
            #expect(receipt.request == pending)
            #expect(receipt.sessionID == "created")
            #expect(receipt.turnID == "turn")
        } else { Issue.record("重启后应恢复原接受身份") }
        #expect(try reopened.draft(sessionID: nil).input == "后来编辑的内容")
        #expect(await fixture.receivedCount == 3)
    }

    @Test func missingAcceptanceNeverAutoSendsAndExplicitRetryKeepsOriginalRequest() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (phone, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        await enqueueIdentity(fixture)
        _ = try await phone.refresh()
        let sending = Task { try await phone.submit(input: "  原始输入\n", sessionID: nil) }
        let original = await fixture.next()
        let originalBody = try original.jsonBody()
        let pending = try #require(phone.pendingSubmission)
        original.fail()
        _ = try? await sending.value
        await enqueueIdentity(fixture)
        await fixture.enqueue(status: 404, body: "{\"detail\":{\"code\":\"SUBMISSION_NOT_FOUND\"}}")
        try await phone.reconcileSubmission()
        #expect(phone.submission == .notFound)
        #expect(phone.canRetrySubmission)
        #expect(phone.pendingSubmission == pending)
        #expect(await fixture.receivedCount == 6)
        await enqueueIdentity(fixture)
        await fixture.enqueue(status: 404, body: "{\"detail\":{\"code\":\"SUBMISSION_NOT_FOUND\"}}")
        await fixture.enqueue(status: 202, body: receipt(pending))
        let accepted = try await phone.retrySubmission()
        #expect(accepted.request == pending)
        #expect(accepted.request.sessionID == nil)
        #expect(accepted.request.input == "  原始输入\n")
        #expect(originalBody == ["submission_id": accepted.request.id, "input": accepted.request.input])
        #expect(await fixture.receivedCount == 10)
    }

    @Test(arguments: ["network", "malformed", "identity", "target", "proxy"])
    func failedChecksKeepOriginalAndNeverPermitRetry(mode: String) async throws {
        let (phone, fixture, pending, _) = try await unknownSubmission(target: "existing")
        let checking = Task { try await phone.reconcileSubmission() }
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        let query = await fixture.next()
        #expect(query.protocolRequest.request.url?.path == "/api/submissions/" + pending.id)
        #expect(phone.submission == .checking)
        #expect(!phone.canRetrySubmission)
        if mode == "network" { query.fail() }
        else if mode == "malformed" { query.reply(body: "{}") }
        else if mode == "proxy" { query.reply(status: 503, body: "unavailable") }
        else { query.reply(body: receipt(pending, id: mode == "identity" ? "other" : pending.id,
                                        session: mode == "target" ? "other" : "existing")) }
        await #expect(throws: SessionFailure.submissionUnknown) { try await checking.value }
        #expect(phone.pendingSubmission == pending)
        #expect(phone.submission == .unknown)
        await #expect(throws: SessionFailure.submissionUnknown) { try await phone.retrySubmission() }
        #expect(await fixture.receivedCount == 6)
    }

    @Test(arguments: ["BUSY", "SUBMISSION_CONFLICT", "SUBMISSION_DELETED", "INVALID_INPUT", "SERVICE_UNAVAILABLE"])
    func explicitRetryRejectionsRemainDistinctAndKeepTheOriginal(code: String) async throws {
        let (phone, fixture, pending, file) = try await unknownSubmission()
        await enqueueIdentity(fixture)
        await fixture.enqueue(status: 404, body: missing())
        try await phone.reconcileSubmission()
        await enqueueIdentity(fixture)
        await fixture.enqueue(status: 404, body: missing())
        let status = code == "SUBMISSION_DELETED" ? 410 : code == "INVALID_INPUT" ? 422 : code == "SERVICE_UNAVAILABLE" ? 503 : 409
        let failure: SessionFailure = code == "BUSY" ? .busy : code == "SUBMISSION_CONFLICT" ? .submissionConflict : code == "SUBMISSION_DELETED" ? .submissionDeleted : code == "INVALID_INPUT" ? .invalidInput : .serviceUnavailable
        await fixture.enqueue(status: status, body: "{\"detail\":{\"code\":\"\(code)\"}}")
        await #expect(throws: failure) { try await phone.retrySubmission() }
        #expect(phone.submission == .rejected(failure))
        #expect(!phone.canRetrySubmission)
        #expect(phone.pendingSubmission == pending)
        #expect(try SharedSessions(databaseURL: file).pendingSubmission == pending)
    }

    @Test func queryDeletedAcceptanceCannotBecomeARetry() async throws {
        let (phone, fixture, pending, _) = try await unknownSubmission()
        await enqueueIdentity(fixture)
        await fixture.enqueue(status: 410, body: "{\"detail\":{\"code\":\"SUBMISSION_DELETED\"}}")
        await #expect(throws: SessionFailure.submissionDeleted) { try await phone.reconcileSubmission() }
        #expect(phone.submission == .rejected(.submissionDeleted))
        #expect(phone.pendingSubmission == pending)
        await #expect(throws: SessionFailure.submissionUnknown) { try await phone.retrySubmission() }
        #expect(await fixture.receivedCount == 6)
    }

    @Test func changedServiceAndDatasetIgnoreLateCheckAndRecoverOnlyAtOriginalIdentity() async throws {
        let (phone, fixture, pending, _) = try await unknownSubmission()
        let originalAddress = phone.serverAddress
        let checking = Task { try await phone.reconcileSubmission() }
        await fixture.next().reply(body: snapshot())
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        let late = await fixture.next()
        try phone.configure(address: "https://other.example.test")
        #expect(phone.submission == .isolated)
        late.reply(body: receipt(pending))
        _ = try? await checking.value
        #expect(phone.submission == .isolated)
        #expect(phone.pendingSubmission == pending)
        try phone.configure(address: originalAddress)
        await enqueueIdentity(fixture, dataset: "replacement")
        _ = try await phone.refresh()
        #expect(phone.submission == .isolated)
        #expect(await fixture.receivedCount == 8)
        #expect(!phone.canRetrySubmission)
        await enqueueIdentity(fixture)
        await fixture.enqueue(body: receipt(pending))
        _ = try await phone.refresh()
        #expect(phone.pendingSubmission == nil)
        #expect(phone.conversation == nil)
        #expect(!phone.canSubmit)
    }

    @Test func interruptedLocalCleanupCanRecoverOriginalAcceptanceAfterReopen() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (phone, fixture, address) = try await HTTPFixture.client(databaseURL: file)
        await enqueueIdentity(fixture)
        _ = try await phone.refresh()
        let draft = try phone.saveDraft(input: "已接受但尚未清理", scope: phone.draft(sessionID: nil).scope)
        var database: OpaquePointer?
        #expect(sqlite3_open(file.path, &database) == SQLITE_OK)
        defer { sqlite3_close(database) }
        #expect(sqlite3_exec(database, "CREATE TRIGGER fail_cleanup BEFORE DELETE ON pending_submission BEGIN SELECT RAISE(ABORT,'disk failure'); END", nil, nil, nil) == SQLITE_OK)
        let sending = Task { try await phone.submit(draft: draft) }
        let post = await fixture.next()
        let pending = try #require(phone.pendingSubmission)
        post.reply(status: 202, body: receipt(pending))
        await #expect(throws: SessionFailure.submissionUnknown) { try await sending.value }
        #expect(sqlite3_exec(database, "DROP TRIGGER fail_cleanup", nil, nil, nil) == SQLITE_OK)
        let (reopened, transport, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        await enqueueIdentity(transport)
        await transport.enqueue(body: receipt(pending))
        _ = try await reopened.refresh()
        #expect(reopened.pendingSubmission == nil)
        #expect(try reopened.draft(sessionID: nil).input.isEmpty)
        #expect(await transport.receivedCount == 3)
    }

    @Test func acceptedDisplayIsInvalidatedWhenDatasetChanges() async throws {
        let (phone, fixture, pending, _) = try await unknownSubmission()
        await enqueueIdentity(fixture)
        await fixture.enqueue(body: receipt(pending))
        _ = try await phone.refresh()
        await enqueueIdentity(fixture, dataset: "replacement")
        _ = try await phone.refresh()
        #expect(phone.submission == .idle)
        #expect(phone.pendingSubmission == nil)
    }

    private func unknownSubmission(target: String? = nil) async throws -> (any SessionAccess, HTTPFixture, PendingSubmission, URL) {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let (phone, fixture, _) = try await HTTPFixture.client(databaseURL: file)
        await enqueueIdentity(fixture)
        _ = try await phone.refresh()
        let sending = Task { try await phone.submit(input: "  原始输入\n", sessionID: target) }
        let original = await fixture.next()
        let pending = try #require(phone.pendingSubmission)
        original.fail()
        _ = try? await sending.value
        return (phone, fixture, pending, file)
    }

    private func snapshot() -> String {
        "{\"schema_version\":1,\"stream_id\":\"dataset\",\"accepting\":true,\"stopping\":false}"
    }
    private func missing() -> String { "{\"detail\":{\"code\":\"SUBMISSION_NOT_FOUND\"}}" }

    private func enqueueIdentity(_ fixture: HTTPFixture, dataset: String = "dataset") async {
        await fixture.enqueue(body: "{\"schema_version\":1,\"stream_id\":\"\(dataset)\",\"accepting\":true,\"stopping\":false}")
        await fixture.enqueue(body: "{\"schema_version\":1,\"sessions\":[]}")
    }

    private func receipt(_ pending: PendingSubmission, id: String? = nil, session: String = "created") -> String {
        "{\"schema_version\":1,\"submission_id\":\"\(id ?? pending.id)\",\"session_id\":\"\(session)\",\"turn_id\":\"turn\"}"
    }
}
