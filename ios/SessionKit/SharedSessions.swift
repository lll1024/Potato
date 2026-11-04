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
        migrator.registerMigration("session-drafts") { db in
            try db.execute(sql: """
                CREATE TABLE draft (
                    address TEXT NOT NULL, stream_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    input TEXT NOT NULL, revision TEXT NOT NULL,
                    PRIMARY KEY (address, stream_id, session_id)
                );
                CREATE TABLE server_dataset (address TEXT PRIMARY KEY NOT NULL, stream_id TEXT NOT NULL);
                """)
        }
        migrator.registerMigration("initial-draft-dataset") { db in
            try db.execute(sql: "ALTER TABLE server_dataset ADD COLUMN first_stream_id TEXT")
            try db.execute(sql: "UPDATE server_dataset SET first_stream_id = stream_id")
        }
        try migrator.migrate(database)
        let savedAddress = try database.read { db in
            try String.fetchOne(db, sql: "SELECT address FROM connection WHERE id = 1") ?? ""
        }
        serverAddress = savedAddress
        datasetID = try database.read {
            try String.fetchOne($0, sql: "SELECT stream_id FROM server_dataset WHERE address = ?", arguments: [savedAddress])
        }
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
        let savedDataset = try database.write { db in
            try db.execute(sql: "INSERT OR REPLACE INTO connection (id, address) VALUES (1, ?)",
                           arguments: [normalized])
            return try String.fetchOne(db, sql: "SELECT stream_id FROM server_dataset WHERE address = ?", arguments: [normalized])
        }
        serverAddress = normalized
        connection = normalized.isEmpty ? .unconfigured : .connecting
        service = nil
        sessionList = nil
        conversation = nil
        conversationFailure = nil
        watchedSessionID = nil
        acceptanceNeedsRefresh = false
        datasetID = savedDataset
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
        let pending: PendingSubmission
        do {
            pending = try savePending(input: input, sessionID: sessionID, streamID: service.streamID)
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
            try clearPending(accepted: pending)
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

    public func canSubmit(draft: SessionDraft) -> Bool {
        guard canSubmit, let scope = try? resolvedScope(draft.scope) else { return false }
        return scope.serverAddress == serverAddress && scope.streamID == service?.streamID
    }

    public func submit(draft: SessionDraft) async throws -> SubmissionReceipt {
        let scope = try resolvedScope(draft.scope)
        guard scope.serverAddress == serverAddress, scope.streamID == service?.streamID else {
            throw SessionFailure.obsolete
        }
        return try await submit(input: draft.input, sessionID: scope.sessionID)
    }

    public func watchConversation(sessionID: String?) {
        guard watchedSessionID != sessionID else { return }
        watchedSessionID = sessionID
        historyGeneration += 1
        conversation = nil
        conversationFailure = nil
    }

    public func draft(sessionID: String?) throws -> SessionDraft {
        let scope = DraftScope(serverAddress: serverAddress, streamID: datasetID ?? "", sessionID: sessionID)
        do { return try database.read { try readDraft(scope: scope, db: $0) } }
        catch { throw SessionFailure.draftLoadFailed }
    }

    public func saveDraft(input: String, scope: DraftScope) throws -> SessionDraft {
        do {
            return try database.write { db in
                let draft = SessionDraft(scope: try resolvedScope(scope, db: db), input: input, revision: UUID().uuidString)
                try writeDraft(draft, db: db)
                return draft
            }
        } catch { throw SessionFailure.draftSaveFailed }
    }

    private func readDraft(scope: DraftScope, db: Database) throws -> SessionDraft {
        let row = try Row.fetchOne(db, sql: "SELECT input, revision FROM draft WHERE address = ? AND stream_id = ? AND session_id = ?",
                                  arguments: [scope.serverAddress, scope.streamID, scope.sessionID ?? ""])
        return SessionDraft(scope: scope, input: row?["input"] ?? "", revision: row?["revision"])
    }

    public func moveDraft(_ draft: SessionDraft, input: String, to sessionID: String) throws -> SessionDraft {
        do {
            return try database.write { db in
                let source = try resolvedScope(draft.scope, db: db)
                let scope = DraftScope(serverAddress: source.serverAddress, streamID: source.streamID, sessionID: sessionID)
                let moved = SessionDraft(scope: scope, input: input, revision: UUID().uuidString)
                let current = try readDraft(scope: source, db: db)
                let destination = try readDraft(scope: scope, db: db)
                guard draft.scope.sessionID == nil,
                      current.revision == nil || current.revision == draft.revision,
                      destination.input.isEmpty else { throw SessionFailure.obsolete }
                try writeDraft(moved, db: db)
                try db.execute(sql: "DELETE FROM draft WHERE address = ? AND stream_id = ? AND session_id = ? AND revision = ?",
                               arguments: [source.serverAddress, source.streamID, "", draft.revision])
                return moved
            }
        } catch { throw SessionFailure.draftSaveFailed }
    }

    private func resolvedScope(_ scope: DraftScope) throws -> DraftScope {
        // 归属核对与提交落盘之间不挂起，避免配置变化插入这个边界。
        try database.read { try resolvedScope(scope, db: $0) }
    }

    private func resolvedScope(_ scope: DraftScope, db: Database) throws -> DraftScope {
        guard scope.streamID.isEmpty,
              let streamID = try String.fetchOne(db, sql: "SELECT first_stream_id FROM server_dataset WHERE address = ?",
                                                 arguments: [scope.serverAddress]) else { return scope }
        return DraftScope(serverAddress: scope.serverAddress, streamID: streamID, sessionID: scope.sessionID)
    }

    private func writeDraft(_ draft: SessionDraft, db: Database) throws {
        try db.execute(sql: "INSERT OR REPLACE INTO draft (address, stream_id, session_id, input, revision) VALUES (?, ?, ?, ?, ?)",
                       arguments: [draft.scope.serverAddress, draft.scope.streamID, draft.scope.sessionID ?? "", draft.input, draft.revision])
    }

    private func rememberDataset(_ streamID: String) throws {
        guard streamID != datasetID else { return }
        try database.write { db in
            if datasetID == nil {
                // 首次离线输入尚无数据集归属；仅在首次核对成功时绑定，后续数据集变化不迁移。
                try db.execute(sql: "UPDATE draft SET stream_id = ? WHERE address = ? AND stream_id = ''",
                               arguments: [streamID, serverAddress])
            }
            try db.execute(sql: """
                INSERT INTO server_dataset (address, stream_id, first_stream_id) VALUES (?, ?, ?)
                ON CONFLICT(address) DO UPDATE SET stream_id = excluded.stream_id
                """, arguments: [serverAddress, streamID, streamID])
        }
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

    private func clearPending(accepted: PendingSubmission? = nil) throws {
        try database.write { db in
            if let accepted, let revision = accepted.draftRevision {
                try db.execute(sql: "DELETE FROM draft WHERE address = ? AND stream_id = ? AND session_id = ? AND revision = ? AND input = ?",
                               arguments: [accepted.serverAddress, accepted.streamID, accepted.sessionID ?? "", revision, accepted.input])
            }
            try db.execute(sql: "DELETE FROM pending_submission WHERE id = 1")
        }
        pendingSubmission = nil
    }

    private func savePending(input: String, sessionID: String?, streamID: String) throws -> PendingSubmission {
        try database.write { db in
            let scope = DraftScope(serverAddress: serverAddress, streamID: streamID, sessionID: sessionID)
            let draft = try readDraft(scope: scope, db: db)
            let pending = PendingSubmission(id: UUID().uuidString, serverAddress: serverAddress,
                streamID: streamID, input: input, sessionID: sessionID,
                draftRevision: draft.input == input ? draft.revision : nil)
            let data = try JSONEncoder().encode(pending)
            try db.execute(sql: "INSERT INTO pending_submission (id, payload) VALUES (1, ?)", arguments: [data])
            return pending
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
            try rememberDataset(snapshot.streamId)
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
            try rememberDataset(page.streamId)
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
