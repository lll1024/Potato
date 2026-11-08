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
    public private(set) var contentRecoveryRevision = 0
    public private(set) var answerFailure: SessionFailure?
    private var pendingProgress: ProgressPreview?
    private var pendingAnswer: AnswerPreview?
    private var currentProgress: ProgressPreview?
    private var frozenProgress: ProgressPreview?
    private var progressRevision = -1
    private var currentAnswer: AnswerPreview?
    private var frozenAnswer: AnswerPreview?
    private var frozenTurnID: String?
    private var answerInstance: String?
    private var answerRevision = -1
    private var answerSubscription: Task<Void, Never>?
    private var answerOperation = UUID()
    private var isForeground = false

    public var answerPreview: AnswerPreview? {
        let frozen = frozenTurnID != nil && frozenTurnID == service?.activeTurnID
        guard let preview = frozen ? frozenAnswer : currentAnswer, preview.sessionID == watchedSessionID,
              preview.streamID == datasetID, preview.instanceID == answerInstance else { return nil }
        if let turn = conversation?.turns.first(where: { $0.id == preview.turnID }), turn.status != "running" { return nil }
        return preview
    }

    public var progressPreview: ProgressPreview? {
        let frozen = frozenTurnID != nil && frozenTurnID == service?.activeTurnID
        guard let preview = frozen ? frozenProgress : currentProgress, preview.sessionID == watchedSessionID,
              preview.streamID == datasetID, preview.instanceID == answerInstance else { return nil }
        if let turn = conversation?.turns.first(where: { $0.id == preview.turnID }), turn.status != "running" { return nil }
        return preview
    }

    public private(set) var stopState = StopState.idle
    public private(set) var isLoadingMoreSessions = false
    public private(set) var moreSessionsFailure: SessionFailure?
    public private(set) var isLoadingEarlierTurns = false
    public private(set) var earlierTurnsFailure: SessionFailure?
    private var earlierOperation: UUID?
    private var conversationGeneration = 0
    private var listOperation: UUID?
    private var loadedListPages = 1
    private var listReadInProgress = false
    private var listReadWaiters: [CheckedContinuation<Void, Never>] = []

    private func beginListRead() async throws {
        try Task.checkCancellation()
        if listReadInProgress {
            await withCheckedContinuation { listReadWaiters.append($0) }
        } else { listReadInProgress = true }
        if Task.isCancelled { endListRead(); throw CancellationError() }
    }

    private func endListRead() {
        if listReadWaiters.isEmpty { listReadInProgress = false }
        else { listReadWaiters.removeFirst().resume() }
    }

    public func loadMoreSessions() async throws -> SessionList {
        guard listOperation == nil else { throw SessionFailure.busy }
        let operation = UUID()
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        let address = serverAddress
        listOperation = operation
        isLoadingMoreSessions = true
        moreSessionsFailure = nil
        defer {
            if listOperation == operation { listOperation = nil; isLoadingMoreSessions = false }
        }
        do {
            try await beginListRead()
            defer { endListRead() }
            guard configuration == configurationGeneration, dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard let current = sessionList else { throw SessionFailure.obsolete }
            guard let cursor = current.nextCursor else { return current }
            let page = try await http.get(WireList.self, address: address, path: "/api/sessions",
                                         parameters: ["limit": "50", "cursor": cursor])
            guard configuration == configurationGeneration, dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard page.schemaVersion == 1, page.nextCursor != cursor else { throw SessionFailure.invalidResponse }
            let result = SessionList(sessions: uniqueSessions(current.sessions + page.sessions.map(\.entity)),
                                     nextCursor: page.nextCursor)
            sessionList = result
            loadedListPages += 1
            return result
        } catch {
            guard configuration == configurationGeneration, dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if !(error is CancellationError), error as? SessionFailure != .obsolete {
                moreSessionsFailure = error as? SessionFailure ?? .invalidResponse
            }
            throw error
        }
    }

    private func uniqueSessions(_ rows: [AssistantSession]) -> [AssistantSession] {
        var seen: Set<String> = []
        return rows.filter { seen.insert($0.id).inserted }
    }

    private func invalidateSessionPages() {
        loadedListPages = 1
        listOperation = nil
        isLoadingMoreSessions = false
        moreSessionsFailure = nil
    }

    public func loadEarlierTurns(sessionID: String) async throws -> ConversationPage {
        guard let current = conversation, current.session.id == sessionID,
              watchedSessionID == sessionID else { throw SessionFailure.obsolete }
        guard let before = current.nextBefore else { return current }
        guard earlierOperation == nil else { throw SessionFailure.busy }
        let operation = UUID()
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        let target = conversationGeneration
        let address = serverAddress
        earlierOperation = operation
        isLoadingEarlierTurns = true
        earlierTurnsFailure = nil
        defer {
            if earlierOperation == operation { earlierOperation = nil; isLoadingEarlierTurns = false }
        }
        do {
            let page = try await http.get(WirePage.self, address: address, path: "/api/sessions/" + sessionID,
                parameters: ["limit": "50", "before": String(before), "summary": "false", "include_messages": "false"])
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  target == conversationGeneration else { throw SessionFailure.obsolete }
            guard page.schemaVersion == 1, page.streamId == current.streamID,
                  page.session.sessionId == sessionID else { throw SessionFailure.invalidResponse }
            guard let latest = conversation else { throw SessionFailure.obsolete }
            let result = latest.merging(page.entity, earlier: true)
            conversation = result
            return result
        } catch {
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  target == conversationGeneration else { throw SessionFailure.obsolete }
            if error as? SessionFailure == .http(404) {
                historyGeneration += 1
                invalidateEarlierTurns()
                conversation = nil
                conversationFailure = .http(404)
                currentAnswer = nil
                pendingAnswer = nil
                currentProgress = nil
                pendingProgress = nil
                progressRevision = -1
                cancelAnswerSubscription()
            } else if !(error is CancellationError), error as? SessionFailure != .obsolete {
                earlierTurnsFailure = error as? SessionFailure ?? .invalidResponse
            }
            throw error
        }
    }

    private func invalidateEarlierTurns() {
        conversationGeneration += 1
        earlierOperation = nil
        isLoadingEarlierTurns = false
        earlierTurnsFailure = nil
    }
    private var stopOperation: UUID?
    private let database: DatabaseQueue
    private let http: SessionHTTP
    private var configurationGeneration = 0
    private var refreshGeneration = 0
    private var historyGeneration = 0
    private var connectionGeneration = 0
    private var datasetGeneration = 0
    private var datasetID: String?
    private let wait: @Sendable (Duration) async throws -> Void
    private var polling: Task<Void, Never>?
    private var watchedSessionID: String?
    private var acceptanceNeedsRefresh = false
    private var acceptedAfterRefresh = 0
    private var submissionOperation: UUID?

    public var canSubmit: Bool {
        connection == .connected && stopState == .idle && pendingSubmission == nil && submission != .sending && !acceptanceNeedsRefresh &&
        service?.accepting == true && service?.activeTurnID == nil && service?.stopping == false &&
        service?.storageError == nil && service?.unsavedFact == nil
    }

    public var stopTarget: StopTarget? {
        guard connection == .connected, let service, !service.stopping,
              let sessionID = service.activeSessionID, let turnID = service.activeTurnID,
              stopOperation == nil else { return nil }
        if case .waiting = stopState { return nil }
        return StopTarget(serverAddress: serverAddress, streamID: service.streamID, sessionID: sessionID, turnID: turnID)
    }

    public func stop(target: StopTarget) async throws {
        guard target.serverAddress == serverAddress, target.streamID == service?.streamID else {
            throw SessionFailure.obsolete
        }
        guard stopOperation == nil else { throw SessionFailure.busy }
        let operation = UUID()
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        stopOperation = operation
        if target.turnID == service?.activeTurnID { freezeAnswer(turnID: target.turnID) }
        stopState = .requesting(target)
        // 请求之前取得的快照不能解除停止核对屏障。
        refreshGeneration += 1
        defer { if stopOperation == operation { stopOperation = nil } }
        do {
            try await http.stop(target)
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  stopOperation == operation else { throw SessionFailure.obsolete }
            refreshGeneration += 1
            stopState = .waiting(target)
        } catch {
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  stopOperation == operation else { throw SessionFailure.obsolete }
            refreshGeneration += 1
            stopState = .uncertain(target)
            throw SessionFailure.stopUnknown
        }
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
        let restoredAddress = (try? Self.normalizedAddress(savedAddress)) ?? ""
        serverAddress = restoredAddress
        datasetID = try database.read {
            try String.fetchOne($0, sql: "SELECT stream_id FROM server_dataset WHERE address = ?", arguments: [restoredAddress])
        }
        connection = restoredAddress.isEmpty ? .unconfigured : .connecting
        if let data = try database.read({ try Data.fetchOne($0, sql: "SELECT payload FROM pending_submission WHERE id = 1") }) {
            pendingSubmission = try JSONDecoder().decode(PendingSubmission.self, from: data)
            submission = .unknown
        }
    }

    private static func normalizedAddress(_ address: String) throws -> String {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "" }
        guard var url = URLComponents(string: trimmed),
              let scheme = url.scheme?.lowercased(), let host = url.host, !host.isEmpty,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.isEmpty || url.path == "/",
              url.port == nil || (1...65535).contains(url.port!), url.url != nil else {
            throw SessionFailure.invalidAddress
        }
        var allowed = scheme == "https"
        #if DEBUG && targetEnvironment(simulator)
        allowed = allowed || (scheme == "http" && host == "127.0.0.1")
        #endif
        guard allowed else { throw SessionFailure.invalidAddress }
        url.scheme = scheme
        url.host = host.lowercased()
        url.path = ""
        return url.string!
    }

    private func connectionFailureMessage(_ error: any Error) -> String {
        if error as? SessionFailure == .unreachable, serverAddress.hasPrefix("http://127.0.0.1") {
            return "无法连接本地服务，请确认后端已启动。"
        }
        return error.localizedDescription
    }

    public func configure(address: String) throws {
        let normalized = try Self.normalizedAddress(address)
        let savedDataset = try database.write { db in
            try db.execute(sql: "INSERT OR REPLACE INTO connection (id, address) VALUES (1, ?)",
                           arguments: [normalized])
            return try String.fetchOne(db, sql: "SELECT stream_id FROM server_dataset WHERE address = ?", arguments: [normalized])
        }
        resetAnswer()
        serverAddress = normalized
        connection = normalized.isEmpty ? .unconfigured : .connecting
        service = nil
        stopOperation = nil
        stopState = .idle
        sessionList = nil
        invalidateSessionPages()
        conversation = nil
        conversationFailure = nil
        watchedSessionID = nil
        invalidateEarlierTurns()
        acceptanceNeedsRefresh = false
        datasetID = savedDataset
        configurationGeneration += 1
        submissionOperation = nil
        submission = pendingSubmission == nil ? .idle : .unknown
        isolatePendingIfNeeded()
    }

    public func submit(input: String, sessionID: String?) async throws -> SubmissionReceipt {
        if submission == .sending || acceptanceNeedsRefresh || stopState != .idle { throw SessionFailure.busy }
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
        return try await sendPending(pending, retry: false)
    }

    private func sendPending(_ pending: PendingSubmission, retry: Bool) async throws -> SubmissionReceipt {
        let operation = UUID()
        submissionOperation = operation
        submission = .sending
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        defer { if submissionOperation == operation { submissionOperation = nil } }
        do {
            let wire = try await http.submit(pending)
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  submissionOperation == operation, pendingSubmission == pending else {
                throw SessionFailure.submissionUnknown
            }
            return try accept(wire, pending: pending)
        } catch {
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  submissionOperation == operation, pendingSubmission == pending else {
                throw SessionFailure.submissionUnknown
            }
            let failure = error as? SessionFailure ?? .submissionUnknown
            if [.busy, .invalidInput, .serviceUnavailable, .http(404), .submissionDeleted, .submissionConflict].contains(failure) {
                if !retry && failure != .submissionDeleted && failure != .submissionConflict {
                    do { try clearPending() }
                    catch { submission = .unknown; throw SessionFailure.submissionUnknown }
                }
                submission = .rejected(failure)
                throw failure
            }
            submission = .unknown
            throw SessionFailure.submissionUnknown
        }
    }

    private func accept(_ wire: WireReceipt, pending: PendingSubmission) throws -> SubmissionReceipt {
        let receipt = SubmissionReceipt(request: pending, sessionID: wire.sessionId, turnID: wire.turnId)
        try clearPending(accepted: pending)
        submission = .accepted(receipt)
        acceptanceNeedsRefresh = true
        acceptedAfterRefresh = refreshGeneration
        return receipt
    }

    private func isolatePendingIfNeeded() {
        guard let pending = pendingSubmission else { return }
        if pending.serverAddress != serverAddress || (datasetID != nil && pending.streamID != datasetID) {
            submissionOperation = nil
            submission = .isolated
        }
    }

    public var canRetrySubmission: Bool {
        submission == .notFound && submissionOperation == nil && connection == .connected &&
        pendingSubmission?.serverAddress == serverAddress && pendingSubmission?.streamID == service?.streamID
    }

    public func reconcileSubmission() async throws {
        guard submissionOperation == nil else { throw SessionFailure.busy }
        _ = try await refresh()
        switch submission {
        case .accepted, .notFound, .idle: return
        case .isolated: throw SessionFailure.obsolete
        case .rejected(let failure): throw failure
        default: throw SessionFailure.submissionUnknown
        }
    }

    public func retrySubmission() async throws -> SubmissionReceipt {
        guard canRetrySubmission, let pending = pendingSubmission else { throw SessionFailure.submissionUnknown }
        // 用户确认后仍重新核对身份及接受记录；404 后原 POST 也可能尚在途。
        try await reconcileSubmission()
        if case .accepted(let receipt) = submission, receipt.request == pending { return receipt }
        guard canRetrySubmission, pendingSubmission == pending else { throw SessionFailure.submissionUnknown }
        return try await sendPending(pending, retry: true)
    }

    private func checkPending() async throws {
        guard let pending = pendingSubmission, submissionOperation == nil else { return }
        isolatePendingIfNeeded()
        guard pending.serverAddress == serverAddress, pending.streamID == service?.streamID else { return }
        let configuration = configurationGeneration
        let dataset = datasetGeneration
        let operation = UUID()
        submissionOperation = operation
        submission = .checking
        defer { if submissionOperation == operation { submissionOperation = nil } }
        do {
            let wire = try await http.lookup(pending)
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  submissionOperation == operation, pendingSubmission == pending else { throw SessionFailure.obsolete }
            if let wire { _ = try accept(wire, pending: pending) }
            else { submission = .notFound }
        } catch {
            guard configuration == configurationGeneration, dataset == datasetGeneration,
                  submissionOperation == operation, pendingSubmission == pending else { throw SessionFailure.obsolete }
            let failure = error as? SessionFailure ?? .submissionUnknown
            submission = [.submissionDeleted, .submissionConflict, .busy, .invalidInput, .serviceUnavailable].contains(failure)
                ? .rejected(failure) : .unknown
            throw failure
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
        cancelAnswerSubscription()
        currentAnswer = nil
        pendingAnswer = nil
        currentProgress = nil
        pendingProgress = nil
        progressRevision = -1
        answerRevision = -1
        watchedSessionID = sessionID
        invalidateEarlierTurns()
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
        isForeground = active
        guard active else {
            polling?.cancel(); polling = nil
            cancelAnswerSubscription()
            return
        }
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
                    self.updateAnswerSubscription()
                } catch is CancellationError { return }
                catch { /* 读取入口保留错误及最后保存事实，下轮重新核对。 */ }
                guard !Task.isCancelled else { return }
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
        connectionGeneration += 1
        let connectionRead = connectionGeneration
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
                resetAnswer()
                datasetGeneration += 1
                submissionOperation = nil
                submission = pendingSubmission == nil ? .idle : .unknown
                acceptanceNeedsRefresh = false
                stopOperation = nil
                stopState = .idle
                conversation = nil
                conversationFailure = nil
                sessionList = nil
                invalidateSessionPages()
                watchedSessionID = nil
                invalidateEarlierTurns()
            }
            datasetID = snapshot.streamId
            dataset = datasetGeneration
            let currentState = answerInstance != snapshot.instanceId
                || ((snapshot.answerRevision ?? answerRevision) >= answerRevision
                    && (snapshot.progressRevision ?? progressRevision) >= progressRevision)
            if currentState {
                if snapshot.instanceId != answerInstance || snapshot.activeTurnId != frozenTurnID {
                    frozenAnswer = nil
                    frozenProgress = nil
                    frozenTurnID = nil
                }
                if snapshot.stopping, let turnID = snapshot.activeTurnId,
                   (currentAnswer?.instanceID == snapshot.instanceId && currentAnswer?.turnID == turnID)
                    || (currentProgress?.instanceID == snapshot.instanceId && currentProgress?.turnID == turnID) {
                    freezeAnswer(turnID: turnID)
                }
                service = snapshot.entity
            }
            if answerInstance != snapshot.instanceId {
                cancelAnswerSubscription()
                currentAnswer = nil
                pendingAnswer = nil
                currentProgress = nil
                pendingProgress = nil
                progressRevision = -1
                answerRevision = -1
                answerInstance = snapshot.instanceId
            }
            if let instance = snapshot.instanceId, let revision = snapshot.answerRevision {
                try adoptAnswer(instance: instance, revision: revision, preview: snapshot.answerPreview)
            }
            if let instance = snapshot.instanceId, let revision = snapshot.progressRevision {
                try adoptProgress(instance: instance, revision: revision, preview: snapshot.progressPreview)
            }
            if let instance = snapshot.instanceId { try adoptPendingContent(instance: instance) }
            contentRecoveryRevision += 1
            if currentState, snapshot.stopping, let turnID = snapshot.activeTurnId { freezeAnswer(turnID: turnID) }
            if currentState, stopOperation == nil {
                switch stopState {
                case .requesting(let target), .waiting(let target), .uncertain(let target):
                    if snapshot.activeTurnId != target.turnID { stopState = .idle }
                    else if snapshot.stopping { stopState = .waiting(target) }
                case .idle: break
                }
            }
            isolatePendingIfNeeded()
            if refresh > acceptedAfterRefresh { acceptanceNeedsRefresh = false }
            // 刷新已展开的页数，电脑端对旧会话的改名和删除也会反映出来。
            let result: SessionList
            do {
                try await beginListRead()
                defer { endListRead() }
                guard configuration == configurationGeneration, refresh == refreshGeneration,
                      dataset == datasetGeneration else { throw SessionFailure.obsolete }
                let pageCount = loadedListPages
                var rows: [AssistantSession] = []
                var nextCursor: String?
                var cursors: Set<String> = []
                var pagesRead = 0
                repeat {
                    var parameters = ["limit": "50"]
                    if let nextCursor { parameters["cursor"] = nextCursor }
                    let list = try await http.get(WireList.self, address: address, path: "/api/sessions", parameters: parameters)
                    guard configuration == configurationGeneration, refresh == refreshGeneration,
                          dataset == datasetGeneration else { throw SessionFailure.obsolete }
                    guard list.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
                    if let cursor = list.nextCursor, !cursors.insert(cursor).inserted { throw SessionFailure.invalidResponse }
                    rows += list.sessions.map(\.entity)
                    nextCursor = list.nextCursor
                    pagesRead += 1
                } while pagesRead < pageCount && nextCursor != nil
                guard configuration == configurationGeneration, refresh == refreshGeneration,
                      dataset == datasetGeneration else { throw SessionFailure.obsolete }
                if connectionRead == connectionGeneration { connection = .connected }
                result = SessionList(sessions: uniqueSessions(rows), nextCursor: nextCursor)
                sessionList = result
                loadedListPages = pagesRead
                moreSessionsFailure = nil
            }
            try? await checkPending()
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            return result
        } catch {
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if error as? SessionFailure == .obsolete { throw error }
            if error is CancellationError {
                if connectionRead == connectionGeneration { connection = previousConnection }
                throw error
            }
            if connectionRead == connectionGeneration { connection = .unavailable(connectionFailureMessage(error)) }
            throw error
        }
    }

    public func history(sessionID: String) async throws -> ConversationPage {
        guard !serverAddress.isEmpty else { throw SessionFailure.unconfigured }
        let address = serverAddress
        let configuration = configurationGeneration
        connectionGeneration += 1
        let connectionRead = connectionGeneration
        let dataset = datasetGeneration
        let refresh = refreshGeneration
        historyGeneration += 1
        let history = historyGeneration
        do {
            let page = try await http.get(WirePage.self, address: address, path: "/api/sessions/" + sessionID,
                                          parameters: ["limit": "50", "summary": "false", "include_messages": "false"])
            guard configuration == configurationGeneration, history == historyGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard page.schemaVersion == 1, page.session.sessionId == sessionID else { throw SessionFailure.invalidResponse }
            try rememberDataset(page.streamId)
            if let datasetID, datasetID != page.streamId {
                self.datasetID = page.streamId
                resetAnswer()
                datasetGeneration += 1
                submissionOperation = nil
                submission = pendingSubmission == nil ? .idle : .unknown
                acceptanceNeedsRefresh = false
                service = nil
                stopOperation = nil
                stopState = .idle
                conversation = nil
                conversationFailure = nil
                sessionList = nil
                invalidateSessionPages()
                watchedSessionID = nil
                invalidateEarlierTurns()
                connection = .connecting
                isolatePendingIfNeeded()
                throw SessionFailure.obsolete
            }
            datasetID = page.streamId
            if connectionRead == connectionGeneration { connection = .connected }
            if watchedSessionID == sessionID {
                conversation = conversation?.merging(page.entity) ?? page.entity
                conversationFailure = nil
                updateAnswerSubscription()
            }
            return watchedSessionID == sessionID ? conversation! : page.entity
        } catch {
            guard configuration == configurationGeneration, history == historyGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if error is CancellationError { throw error }
            if watchedSessionID == sessionID { conversationFailure = error as? SessionFailure ?? .invalidResponse }
            if error as? SessionFailure == .http(404) {
                if connectionRead == connectionGeneration { connection = .connected }
                if watchedSessionID == sessionID {
                    conversation = nil
                    currentAnswer = nil
                    pendingAnswer = nil
                    currentProgress = nil
                    pendingProgress = nil
                    progressRevision = -1
                    cancelAnswerSubscription()
                    invalidateEarlierTurns()
                }
            } else {
                if connectionRead == connectionGeneration { connection = .unavailable(connectionFailureMessage(error)) }
            }
            throw error
        }
    }

    private func freezeAnswer(turnID: String) {
        guard frozenTurnID != turnID else { return }
        frozenTurnID = turnID
        frozenAnswer = currentAnswer?.turnID == turnID ? currentAnswer : nil
        frozenProgress = currentProgress?.turnID == turnID ? currentProgress : nil
    }

    private func resetAnswer() {
        frozenAnswer = nil
        frozenProgress = nil
        frozenTurnID = nil
        cancelAnswerSubscription()
        currentAnswer = nil
        pendingAnswer = nil
        currentProgress = nil
        pendingProgress = nil
        progressRevision = -1
        answerInstance = nil
        answerRevision = -1
    }

    private func cancelAnswerSubscription() {
        answerOperation = UUID()
        answerSubscription?.cancel()
        answerSubscription = nil
        answerFailure = nil
    }

    private func adoptAnswer(instance: String, revision: Int, preview: AnswerPreview?) throws {
        guard instance == answerInstance, revision > answerRevision else { return }
        if let preview {
            guard preview.instanceID == instance, preview.streamID == datasetID, preview.revision == revision,
                  ["generating", "generated", "invalidated", "retrying"].contains(preview.status) else {
                throw SessionFailure.invalidResponse
            }
            // 全局状态尚未核对到新轮次时，暂存候选，不能混入原轮次。
            let active = preview.turnID == service?.activeTurnID && preview.sessionID == service?.activeSessionID
            let unsaved = service?.storageError != nil && service?.unsavedFact?["turn_id"] == preview.turnID
            guard active || unsaved else {
                if preview.sessionID == watchedSessionID, revision > (pendingAnswer?.revision ?? -1) { pendingAnswer = preview }
                return
            }
        }
        if (pendingAnswer?.revision ?? -1) <= revision { pendingAnswer = nil }
        answerRevision = revision
        currentAnswer = preview
    }

    private func adoptProgress(instance: String, revision: Int, preview: ProgressPreview?) throws {
        guard instance == answerInstance, revision > progressRevision else { return }
        if let preview {
            guard preview.instanceID == instance, preview.streamID == datasetID, preview.revision == revision else {
                throw SessionFailure.invalidResponse
            }
            let active = preview.turnID == service?.activeTurnID && preview.sessionID == service?.activeSessionID
            let unsaved = service?.storageError != nil && service?.unsavedFact?["turn_id"] == preview.turnID
            guard active || unsaved else {
                if preview.sessionID == watchedSessionID, revision > (pendingProgress?.revision ?? -1) { pendingProgress = preview }
                return
            }
        }
        if (pendingProgress?.revision ?? -1) <= revision { pendingProgress = nil }
        progressRevision = revision
        currentProgress = preview
    }

    private func adoptPendingContent(instance: String) throws {
        if let pendingAnswer { try adoptAnswer(instance: instance, revision: pendingAnswer.revision, preview: pendingAnswer) }
        if let pendingProgress { try adoptProgress(instance: instance, revision: pendingProgress.revision, preview: pendingProgress) }
    }

    private func adoptLiveState(_ state: WireLiveState, streamID: String) throws {
        guard state.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
        guard (state.answerRevision ?? answerRevision) >= answerRevision,
              (state.progressRevision ?? progressRevision) >= progressRevision else { return }
        let previous = service
        if state.stopping, let turnID = state.activeTurnId { freezeAnswer(turnID: turnID) }
        service = state.entity(streamID: streamID)
        if frozenTurnID != state.activeTurnId {
            frozenTurnID = nil
            frozenAnswer = nil
            frozenProgress = nil
        }
        // 内容可能先于状态抵达；只在新状态确认其轮次后采用，不等待下一次普通轮询。
        try adoptPendingContent(instance: state.instanceId)
        if stopOperation == nil {
            switch stopState {
            case .requesting(let target), .waiting(let target), .uncertain(let target):
                if state.activeTurnId != target.turnID { stopState = .idle }
                else if state.stopping { stopState = .waiting(target) }
            case .idle: break
            }
        }
        if previous?.activeTurnID != state.activeTurnId, let sessionID = watchedSessionID,
           previous?.activeSessionID == sessionID || state.activeSessionId == sessionID {
            Task { [weak self] in
                guard let self, self.watchedSessionID == sessionID else { return }
                _ = try? await self.history(sessionID: sessionID)
            }
        }
    }

    private func updateAnswerSubscription() {
        guard isForeground, answerSubscription == nil, let sessionID = watchedSessionID,
              conversation?.session.id == sessionID, conversationFailure != .http(404),
              let instance = answerInstance, let streamID = datasetID else { return }
        let operation = UUID()
        answerOperation = operation
        let address = serverAddress
        let bytes = http.events(address: address, streamID: streamID, after: conversation?.cursor ?? 0)
        answerSubscription = Task { [weak self] in
            do {
                var parser = SessionEventParser()
                var firstAnswer = true, firstProgress = true
                for try await data in bytes {
                    try Task.checkCancellation()
                    guard let self, self.answerOperation == operation else { return }
                    for event in try parser.append(data) {
                        guard event.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
                        // 同库重启也必须重新读取快照；旧进程消息不能作为新状态。
                        guard event.instanceId == instance else {
                            self.currentAnswer = nil
                            self.pendingAnswer = nil
                            self.currentProgress = nil
                            self.pendingProgress = nil
                            self.progressRevision = -1
                            self.answerInstance = nil
                            self.answerRevision = -1
                            self.refreshGeneration += 1
                            throw SessionFailure.obsolete
                        }
                        if let state = event.state {
                            try self.adoptLiveState(state, streamID: streamID)
                        } else if event.kind == "progress.preview" {
                            if firstProgress {firstProgress = false; self.contentRecoveryRevision += 1}
                            try self.adoptProgress(instance: event.instanceId, revision: event.revision, preview: event.progress)
                        } else {
                            if firstAnswer {firstAnswer = false; self.contentRecoveryRevision += 1}
                            try self.adoptAnswer(instance: event.instanceId, revision: event.revision, preview: event.preview)
                        }
                        self.answerFailure = nil
                    }
                }
                throw SessionFailure.unreachable
            } catch {
                guard let self, self.answerOperation == operation, !Task.isCancelled else { return }
                self.answerSubscription = nil
                self.answerFailure = error as? SessionFailure ?? .unreachable
                // 保留最后正文，下一轮前台核对先恢复快照，再创建订阅。
            }
        }
    }

}
