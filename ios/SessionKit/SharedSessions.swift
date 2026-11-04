import Foundation
import GRDB
import Observation

@MainActor @Observable
public final class SharedSessions: SessionAccess {
    public private(set) var serverAddress: String
    public private(set) var connection: ConnectionState
    public private(set) var service: ServiceState?
    private let database: DatabaseQueue
    private let http: SessionHTTP
    private var configurationGeneration = 0
    private var refreshGeneration = 0
    private var historyGeneration = 0
    private var datasetGeneration = 0
    private var datasetID: String?

    public convenience init(databaseURL: URL) throws {
        try self.init(databaseURL: databaseURL, http: SessionHTTP())
    }

    init(databaseURL: URL, http: SessionHTTP) throws {
        self.http = http
        try FileManager.default.createDirectory(
            at: databaseURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        database = try DatabaseQueue(path: databaseURL.path)
        var migrator = DatabaseMigrator()
        migrator.registerMigration("connection") { db in
            try db.execute(sql: "CREATE TABLE connection (id INTEGER PRIMARY KEY CHECK (id = 1), address TEXT NOT NULL)")
        }
        try migrator.migrate(database)
        let savedAddress = try database.read { db in
            try String.fetchOne(db, sql: "SELECT address FROM connection WHERE id = 1") ?? ""
        }
        serverAddress = savedAddress
        connection = savedAddress.isEmpty ? .unconfigured : .connecting
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
        datasetID = nil
        configurationGeneration += 1
    }

    public func refresh() async throws -> SessionList {
        guard !serverAddress.isEmpty else { throw SessionFailure.unconfigured }
        let address = serverAddress
        let configuration = configurationGeneration
        refreshGeneration += 1
        let refresh = refreshGeneration
        var dataset = datasetGeneration
        let previousConnection = connection
        connection = .connecting
        do {
            let snapshot = try await http.get(WireSnapshot.self, address: address, path: "/api/snapshot")
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard snapshot.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
            if let datasetID, datasetID != snapshot.streamId { datasetGeneration += 1 }
            datasetID = snapshot.streamId
            dataset = datasetGeneration
            service = snapshot.entity
            let list = try await http.get(WireList.self, address: address, path: "/api/sessions",
                                          parameters: ["limit": "50"])
            guard configuration == configurationGeneration, refresh == refreshGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            guard list.schemaVersion == 1 else { throw SessionFailure.invalidResponse }
            connection = .connected
            return SessionList(sessions: list.sessions.map(\.entity), nextCursor: list.nextCursor)
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
                connection = .connecting
                throw SessionFailure.obsolete
            }
            datasetID = page.streamId
            connection = .connected
            return page.entity
        } catch {
            guard configuration == configurationGeneration, history == historyGeneration,
                  dataset == datasetGeneration else { throw SessionFailure.obsolete }
            if error is CancellationError { throw error }
            if error as? SessionFailure == .http(404) {
                connection = .connected
            } else {
                connection = .unavailable(error.localizedDescription)
            }
            throw error
        }
    }
}
