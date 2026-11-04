import Foundation
import GRDB
import Observation

@MainActor @Observable
public final class SharedSessions: SessionAccess {
    public private(set) var serverAddress: String
    public private(set) var connection: ConnectionState
    public private(set) var service: ServiceState?
    public private(set) var submission = SubmissionState.idle
    public private(set) var pendingSubmission: PendingSubmission?
    public private(set) var sessionList: SessionList?
    public private(set) var conversation: ConversationPage?
    public private(set) var conversationFailure: SessionFailure?
    private let database: DatabaseQueue
    private let http: SessionHTTP
    private var configurationGeneration = 0
    private var refreshGeneration = 0
    private var historyGeneration = 0
    private var datasetGeneration = 0
    private var datasetID: String?
    private let wait: @Sendable (Duration) async throws -> Void
    private var polling: Task<Void, Never>?
    private var watchedSessionID: String?
    private var acceptanceNeedsRefresh = false
    private var acceptedAfterRefresh = 0

    public var canSubmit: Bool {
        connection == .connected && pendingSubmission == nil && submission != .sending && !acceptanceNeedsRefresh &&
        service?.accepting == true && service?.activeTurnID == nil && service?.stopping == false &&
        service?.storageError == nil && service?.unsavedFact == nil
    }

    public convenience init(databaseURL: URL) throws {
        try self.init(databaseURL: databaseURL, http: SessionHTTP())
    }

    init(databaseURL: URL, http: SessionHTTP,
         wait: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) throws {
        self.http = http
        self.wait = wait
        try FileManager.default.createDirectory(
            at: databaseURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        database = try DatabaseQueue(path: databaseURL.path)
        var migrator = DatabaseMigrator()
        migrator.registerMigration("connection") { db in
            try db.execute(sql: "CREATE TABLE connection (id INTEGER PRIMARY KEY CHECK (id = 1), address TEXT NOT NULL)")
        }
        migrator.registerMigration("pending-submission") { db in
            try db.execute(sql: "CREATE TABLE pending_submission (id INTEGER PRIMARY KEY CHECK (id = 1), payload BLOB NOT NULL)")
        }
        try migrator.migrate(database)
        let savedAddress = try database.read { db in
            try String.fetchOne(db, sql: "SELECT address FROM connection WHERE id = 1") ?? ""
        }
        serverAddress = savedAddress
        connection = savedAddress.isEmpty ? .unconfigured : .connecting
        if let data = try database.read({ try Data.fetchOne($0, sql: "SELECT payload FROM pending_submission WHERE id = 1") }) {
            pendingSubmission = try JSONDecoder().decode(PendingSubmission.self, from: data)
            submission = .unknown
        }
    }

    public func configure(address: String) throws {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        var normalized = ""
        if !trimmed.isEmpty {
            guard var url = URLComponents(string: trimmed),
                  url.scheme?.lowercased() == "https", let host = url.host, !host.isEmpty,
                  url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
                  url.path.isEmpty || url.path == "/",
                  url.port == nil || (1...65535).contains(url.port!), url.url != nil else {
                throw SessionFailure.invalidAddress
            }
            url.scheme = "https"
            url.host = host.lowercased()
            url.path = ""
            normalized = url.string!
        }
        try database.write { db in
            try db.execute(sql: "INSERT OR REPLACE INTO connection (id, address) VALUES (1, ?)",
                           arguments: [normalized])
        }
        serverAddress = normalized
        connection = normalized.isEmpty ? .unconfigured : .connecting
        service = nil
        sessionList = nil
        conversation = nil
        conversationFailure = nil
        watchedSessionID = nil
        acceptanceNeedsRefresh = false
        datasetID = nil
        configurationGeneration += 1
    }

    public func submit(input: String, sessionID: String?) async throws -> SubmissionReceipt {
        if submission == .sending || acceptanceNeedsRefresh { throw SessionFailure.busy }
        guard pendingSubmission == nil else { throw SessionFailure.submissionUnknown }
        guard !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw SessionFailure.invalidInput
        }
        guard connection == .connected, let service else { throw SessionFailure.unreachable }
        guard service.accepting, service.storageError == nil, service.unsavedFact == nil else {
            throw SessionFailure.serviceUnavailable
        }
        guard service.activeTurnID == nil, !service.stopping else { throw SessionFailure.busy }
        let pending = PendingSubmission(id: UUID().uuidString, serverAddress: serverAddress,
                                        streamID: service.streamID, input: input, sessionID: sessionID)
        do {
            try savePending(pending)
        } catch {
            submission = .rejected(.localSaveFailed)
            throw SessionFailure.localSaveFailed
        }
        pendingSubmission = pending
        submission = .sending
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        do {
            let wire = try await http.submit(pending)
            guard configuration == configurationGeneration, dataset == datasetGeneration else {
                throw SessionFailure.submissionUnknown
            }
            let receipt = SubmissionReceipt(request: pending, sessionID: wire.sessionId, turnID: wire.turnId)
            try clearPending()
            submission = .accepted(receipt)
            acceptanceNeedsRefresh = true
            acceptedAfterRefresh = refreshGeneration
            return receipt
        } catch {
            let failure = error as? SessionFailure ?? .submissionUnknown
            if configuration == configurationGeneration, dataset == datasetGeneration,
               [.busy, .invalidInput, .serviceUnavailable, .http(404)].contains(failure) {
                do { try clearPending() }
                catch { submission = .unknown; throw SessionFailure.submissionUnknown }
                submission = .rejected(failure)
                throw failure
            }
            submission = .unknown
            throw SessionFailure.submissionUnknown
        }
    }

    public func watchConversation(sessionID: String?) {
        guard watchedSessionID != sessionID else { return }
        watchedSessionID = sessionID
        historyGeneration += 1
        conversation = nil
        conversationFailure = nil
    }

    public func setForeground(_ active: Bool) {
        guard active else { polling?.cancel(); polling = nil; return }
        guard polling == nil else { return }
        let wait = wait
        polling = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    _ = try await self.refresh()
                    if let sessionID = self.watchedSessionID {
                        _ = try await self.history(sessionID: sessionID)
                    }
                } catch is CancellationError { return }
                catch { /* 读取入口保留错误及最后保存事实，下轮重新核对。 */ }
                do { try await wait(.seconds(2)) }
                catch { return }
            }
        }
    }

    private func clearPending() throws {
        try database.write { try $0.execute(sql: "DELETE FROM pending_submission WHERE id = 1") }
        pendingSubmission = nil
    }

    private func savePending(_ pending: PendingSubmission) throws {
        let data = try JSONEncoder().encode(pending)
        try database.write { db in
            try db.execute(sql: "INSERT INTO pending_submission (id, payload) VALUES (1, ?)", arguments: [data])
        }
    }

    public func refresh() async throws -> SessionList {
        guard !serverAddress.isEmpty else { throw SessionFailure.unconfigured }
        let address = serverAddress
        let configuration = configurationGeneration
        refreshGeneration += 1
        let refresh = refreshGeneration
        var dataset = datasetGeneration
        let previousConnection = connection
        if connection != .connected { connection = .connecting }
        do {
            let snapshot = try await http.get(WireSnapshot.self, address: address, path: "/api/snapshot")
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard snapshot.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
            if let datasetID, datasetID != snapshot.streamId {
                datasetGeneration += 1
                conversation = nil
                conversationFailure = nil
                sessionList = nil
                watchedSessionID = nil
            }
            datasetID = snapshot.streamId
            dataset = datasetGeneration
            service = snapshot.entity
            if refresh > acceptedAfterRefresh { acceptanceNeedsRefresh = false }
            let list = try await http.get(WireList.self, address: address, path: "/api/sessions",
                                          parameters: ["limit": "50"])
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard list.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
            connection = .connected
            let result = SessionList(sessions: list.sessions.map(\.entity), nextCursor: list.nextCursor)
            sessionList = result
            return result
        } catch {
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if error is CancellationError {
                connection = previousConnection
                throw error
            }
            connection = .unavailable(error.localizedDescription)
            throw error
        }
    }

    public func history(sessionID: String) async throws -> ConversationPage {
        guard !serverAddress.isEmpty else { throw SessionFailure.unconfigured }
        let address = serverAddress
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        historyGeneration += 1
        let history = historyGeneration
        do {
            let page = try await http.get(WirePage.self, address: address, path: "/api/sessions/" + sessionID,
                                          parameters: ["limit": "50", "summary": "false", "include_messages": "false"])
            guard configuration == configurationGeneration, history == historyGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard page.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
            if let datasetID, datasetID != page.streamId {
                self.datasetID = page.streamId
                datasetGeneration += 1
                service = nil
                conversation = nil
                conversationFailure = nil
                sessionList = nil
                watchedSessionID = nil
                connection = .connecting
                throw SessionFailure.obsolete
            }
            datasetID = page.streamId
            connection = .connected
            if watchedSessionID == sessionID {
                conversation = page.entity
                conversationFailure = nil
            }
            return page.entity
        } catch {
            guard configuration == configurationGeneration, history == historyGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if error is CancellationError { throw error }
            if watchedSessionID == sessionID { conversationFailure = error as? SessionFailure ?? .invalidResponse }
            if error as? SessionFailure == .http(404) {
                connection = .connected
                if watchedSessionID == sessionID { conversation = nil }
            } else {
                connection = .unavailable(error.localizedDescription)
            }
            throw error
        }
    }
}
