import Foundation
@testable import SessionKit
import Testing

@MainActor
@Suite(.serialized)
struct SubmissionIntegrationTests {
    #if DEBUG && targetEnvironment(simulator)
    @Test func localHTTPReadsExistingConversationContinuesAndCreatesWithSharedHistory() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let phone: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "phone.sqlite"))
        let desktop: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "desktop.sqlite"))
        try phone.configure(address: "http://127.0.0.1:18496")
        try desktop.configure(address: "http://127.0.0.1:18496")
        _ = try await desktop.refresh()
        let original = try await desktop.submit(input: "电脑开始本地旅行讨论", sessionID: nil)
        try await waitForCompletion(desktop)
        #expect(try await phone.refresh().sessions.contains { $0.id == original.sessionID })
        #expect(try await phone.history(sessionID: original.sessionID).turns.first?.input == "电脑开始本地旅行讨论")

        let continuation = try await phone.submit(input: "模拟器续聊第二天", sessionID: original.sessionID)
        #expect(continuation.sessionID == original.sessionID)
        try await waitForCompletion(phone)
        let fromPhone = try await phone.history(sessionID: original.sessionID)
        #expect(try await desktop.history(sessionID: original.sessionID) == fromPhone)
        #expect(fromPhone.turns.count == 2)
        #expect(fromPhone.turns.last?.input == "模拟器续聊第二天")
        #expect(fromPhone.turns.allSatisfy { $0.status == "completed" && $0.answer?.hasSuffix("回答全文结尾") == true })

        let created = try await phone.submit(input: "模拟器新建旅行讨论", sessionID: nil)
        #expect(created.sessionID != original.sessionID)
        try await waitForCompletion(phone)
        #expect(try await desktop.refresh().sessions.contains { $0.id == created.sessionID })
    }
    #endif

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

    @Test func missingLookupAndOriginalInFlightRaceAcceptsOnlyOneRealExecution() async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let address = "https://localhost:18493"
        let before = try await modelCount()
        let (phone, fixture, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        let connecting = Task { try await phone.refresh() }
        try await forwardIdentity(fixture)
        _ = try await connecting.value
        let sending = Task { try await phone.submit(input: "  原 POST 在途与重试竞争\n", sessionID: nil) }
        let original = await fixture.next()
        let pending = try #require(phone.pendingSubmission)
        original.fail()
        _ = try? await sending.value
        let (reopened, transport, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        let checking = Task { try await reopened.refresh() }
        try await forwardIdentity(transport)
        let missing = try await forward(await transport.next())
        #expect(missing.1.statusCode == 404)
        _ = try await checking.value
        #expect(reopened.canRetrySubmission)
        let retrying = Task { try await reopened.retrySubmission() }
        try await forwardIdentity(transport)
        #expect(try await forward(await transport.next()).1.statusCode == 404)
        let retry = await transport.next()
        #expect(try retry.jsonBody() == original.jsonBody())
        // 原发送与显式重试在真实 HTTP / SQLite 边界竞争；没有假后端幂等逻辑。
        async let first = realResponse(original)
        async let second = forward(retry)
        let (firstResult, secondResult) = try await (first, second)
        #expect(firstResult.1.statusCode == 202)
        #expect(secondResult.1.statusCode == 202)
        let accepted = try await retrying.value
        #expect(accepted.request == pending)
        let firstIdentity = try #require(JSONSerialization.jsonObject(with: firstResult.0) as? [String: Any])
        #expect(firstIdentity["turn_id"] as? String == accepted.turnID)
        #expect(firstIdentity["session_id"] as? String == accepted.sessionID)
        let observer: any SessionAccess = try SharedSessions(databaseURL: file.deletingLastPathComponent().appending(path: "observer.sqlite"))
        try observer.configure(address: address)
        try await waitForCompletion(observer)
        let page = try await observer.history(sessionID: accepted.sessionID)
        #expect(page.turns.count == 1)
        #expect(page.turns.first?.input == pending.input)
        #expect(page.turns.first?.status == "completed")
        #expect(try await modelCount() - before == 1)
    }

    @Test(arguments: [false, true])
    func realLostAcceptanceRecoversOrReportsDeletedWithoutAnotherExecution(deleted: Bool) async throws {
        let file = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + "/client.sqlite")
        let address = "https://localhost:18493"
        let before = try await modelCount()
        let (phone, fixture, _) = try await HTTPFixture.client(databaseURL: file, address: address)
        let connecting = Task { try await phone.refresh() }
        try await forwardIdentity(fixture)
        _ = try await connecting.value
        let sending = Task { try await phone.submit(input: "接受响应丢失后重启", sessionID: nil) }
        let original = await fixture.next()
        let pending = try #require(phone.pendingSubmission)
        let (data, response) = try await realResponse(original)
        #expect(response.statusCode == 202)
        original.fail()
        _ = try? await sending.value
        let identity = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let sessionID = try #require(identity["session_id"] as? String)
        let observer: any SessionAccess = try SharedSessions(databaseURL: file.deletingLastPathComponent().appending(path: "observer.sqlite"))
        try observer.configure(address: address)
        try await waitForCompletion(observer)
        if deleted {
            var request = URLRequest(url: URL(string: address + "/api/sessions/" + sessionID)!)
            request.httpMethod = "DELETE"
            let (_, response) = try await URLSession.shared.data(for: request)
            #expect((response as? HTTPURLResponse)?.statusCode == 200)
        }
        let reopened: any SessionAccess = try SharedSessions(databaseURL: file)
        _ = try await reopened.refresh()
        if deleted {
            #expect(reopened.submission == .rejected(.submissionDeleted))
            #expect(reopened.pendingSubmission == pending)
            #expect(!reopened.canRetrySubmission)
        } else {
            guard case .accepted(let accepted) = reopened.submission else { Issue.record("未恢复真实接受身份"); return }
            #expect(accepted.request == pending)
            #expect(accepted.sessionID == sessionID)
            #expect(accepted.turnID == identity["turn_id"] as? String)
            #expect(reopened.pendingSubmission == nil)
        }
        #expect(try await modelCount() - before == 1)
    }

    @Test func desktopTurnStopsOnlyAfterExternalReturnAndForegroundRestoresSavedResults() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let clock = PollClock()
        let phone: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "phone.sqlite"),
            http: SessionHTTP(), wait: { try await clock.wait($0) })
        let desktop: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "desktop.sqlite"))
        let address = "https://localhost:18494"
        try phone.configure(address: address)
        try desktop.configure(address: address)
        _ = try await desktop.refresh()
        try await control("arm")
        let original = try await desktop.submit(input: "电脑发起的在途调用", sessionID: nil)
        try await control("entered", method: "GET")
        phone.watchConversation(sessionID: original.sessionID)
        phone.setForeground(true)
        _ = await clock.nextInterval()
        let target = try #require(phone.stopTarget)
        #expect(target.turnID == original.turnID)
        #expect(phone.service?.accepting == true)
        #expect(!phone.canSubmit)
        try await phone.stop(target: target)
        _ = try await phone.refresh()
        #expect(phone.service?.activeTurnID == original.turnID)
        #expect(phone.service?.stopping == true)
        #expect(phone.stopState == .waiting(target))
        await #expect(throws: SessionFailure.busy) { try await phone.submit(input: "不可排队", sessionID: nil) }
        var busyRequest = URLRequest(url: URL(string: address + "/api/turns")!)
        busyRequest.httpMethod = "POST"
        busyRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        busyRequest.httpBody = Data("{\"input\":\"电脑也不能抢占停止中的名额\"}".utf8)
        let (_, busyResponse) = try await URLSession.shared.data(for: busyRequest)
        #expect((busyResponse as? HTTPURLResponse)?.statusCode == 409)
        phone.setForeground(false)
        await clock.advance()
        try await control("release")
        try await waitForCompletion(desktop)
        #expect(phone.conversation?.turns.first?.status == "running")
        phone.setForeground(true)
        _ = await clock.nextInterval()
        #expect(phone.canSubmit)
        #expect(phone.conversation?.turns.first?.reason == "user_stop")
        #expect(phone.conversation?.turns.first?.status == "terminated")
        phone.setForeground(false)
        await clock.advance()

        try await control("arm")
        let next = try await desktop.submit(input: "电脑继续，手机锁屏后完成", sessionID: original.sessionID)
        try await control("entered", method: "GET")
        // 捕获的原目标已经结束；真实后端不得停止后来开始的轮次。
        try await phone.stop(target: target)
        _ = try await phone.refresh()
        #expect(phone.service?.activeTurnID == next.turnID)
        #expect(phone.service?.stopping == false)
        let draft = try phone.saveDraft(input: "离线文字不自动排队", scope: phone.draft(sessionID: next.sessionID).scope)
        try await control("release")
        try await waitForCompletion(desktop)
        phone.setForeground(true)
        _ = await clock.nextInterval()
        #expect(phone.conversation?.turns.count == 2)
        #expect(phone.conversation?.turns.last?.status == "completed")
        #expect(phone.conversation?.turns.last?.answer?.hasSuffix("回答全文结尾") == true)
        #expect(try phone.draft(sessionID: next.sessionID) == draft)
        #expect(try await desktop.history(sessionID: next.sessionID) == phone.conversation)
        phone.setForeground(false)
        await clock.advance()
    }

    private func control(_ action: String, method: String = "POST") async throws {
        var request = URLRequest(url: URL(string: "https://localhost:18494/__test__/" + action)!)
        request.httpMethod = method
        let (_, response) = try await URLSession.shared.data(for: request)
        #expect((response as? HTTPURLResponse)?.statusCode == 200)
    }

    private func forwardIdentity(_ fixture: HTTPFixture) async throws {
        _ = try await forward(await fixture.next())
        _ = try await forward(await fixture.next())
    }

    private func realResponse(_ controlled: ControlledRequest) async throws -> (Data, HTTPURLResponse) {
        var request = controlled.protocolRequest.request
        if request.httpMethod == "POST" {
            request.httpBodyStream = nil
            request.httpBody = try JSONSerialization.data(withJSONObject: controlled.jsonBody())
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        return (data, try #require(response as? HTTPURLResponse))
    }

    @discardableResult
    private func forward(_ controlled: ControlledRequest) async throws -> (Data, HTTPURLResponse) {
        let result = try await realResponse(controlled)
        controlled.reply(status: result.1.statusCode, body: String(decoding: result.0, as: UTF8.self))
        return result
    }

    private func modelCount() async throws -> Int {
        let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18493/__test__/model-count")!)
        return try JSONDecoder().decode([String: Int].self, from: data)["count"] ?? -1
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
