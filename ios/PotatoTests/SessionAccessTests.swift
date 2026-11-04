import Foundation
import SessionKit
import Testing

@MainActor
struct SessionAccessTests {
    @Test func trueEmptyListIsDifferentFromUnavailableService() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let sessions: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        try sessions.configure(address: "https://localhost:18490")
        #expect(try await sessions.refresh().sessions.isEmpty)
        #expect(sessions.connection == .connected)
        try sessions.configure(address: "https://localhost:18491")
        await #expect(throws: SessionFailure.unreachable) { try await sessions.refresh() }
        guard case .unavailable = sessions.connection else {
            Issue.record("服务连接失败不能伪造空会话")
            return
        }
        #expect(sessions.service == nil)
    }

    @Test func missingConversationDoesNotMarkReachableServiceOffline() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let sessions: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        try sessions.configure(address: "https://localhost:18489")
        _ = try await sessions.refresh()
        await #expect(throws: SessionFailure.http(404)) { try await sessions.history(sessionID: "missing") }
        #expect(sessions.connection == .connected)
    }

    @Test func readsFullExistingConversationThroughRealHTTPS() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let sessions: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        try sessions.configure(address: "https://localhost:18489")
        let list = try await sessions.refresh()
        #expect(sessions.connection == .connected)
        #expect(sessions.service?.accepting == true)
        let first = try #require(list.sessions.first)
        let page = try await sessions.history(sessionID: first.id)
        let turn = try #require(page.turns.last)
        #expect(turn.input.hasSuffix("输入全文结尾"))
        #expect(turn.input.count > 120)
        #expect(turn.answer?.hasSuffix("回答全文结尾") == true)
        #expect(turn.answer!.count > 120)
        #expect(turn.status == "completed")
        #expect(page.session.id == first.id)
        #expect(try await sessions.refresh().sessions.first?.id == first.id)
    }

    @Test(arguments: ["http://mac.example.ts.net", "https://user:secret@mac.example.ts.net",
                      "https://mac.example.ts.net/path", "https://mac.example.ts.net?key=secret",
                      "https://mac.example.ts.net#fragment", "https://", "not a URL"])
    func invalidAddressDoesNotReplaceSavedConfiguration(address: String) throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let sessions: any SessionAccess = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        try sessions.configure(address: "https://mac.example.ts.net")
        #expect(throws: (any Error).self) { try sessions.configure(address: address) }
        #expect(sessions.serverAddress == "https://mac.example.ts.net")
    }

    @Test func configurationSurvivesReopeningTheLocalDatabase() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let database = directory.appending(path: "client.sqlite")
        let first: any SessionAccess = try SharedSessions(databaseURL: database)
        #expect(first.connection == .unconfigured)
        try first.configure(address: "  https://mac.example.ts.net/  ")
        let reopened: any SessionAccess = try SharedSessions(databaseURL: database)
        #expect(reopened.serverAddress == "https://mac.example.ts.net")
        #expect(reopened.connection == .connecting)
    }
}
