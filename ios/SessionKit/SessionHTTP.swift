import Alamofire
import Foundation

/// 传输类型只在能力内部使用；页面不依赖服务器 JSON 命名或 Alamofire。
struct SessionHTTP {
    let session: Session

    init(session: Session? = nil) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        self.session = session ?? Session(configuration: configuration)
    }

    func get<T: Decodable & Sendable>(_ type: T.Type, address: String, path: String,
                                      parameters: [String: String] = [:]) async throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let request = session.request(address + path, parameters: parameters)
        let response = await request.serializingDecodable(type, decoder: decoder).response
        try Task.checkCancellation()
        guard let status = response.response?.statusCode else { throw SessionFailure.unreachable }
        guard (200...299).contains(status) else { throw SessionFailure.http(status) }
        switch response.result {
        case .success(let value): return value
        case .failure: throw SessionFailure.invalidResponse
        }
    }
}

struct WireSession: Decodable, Sendable {
    let sessionId: String
    let title: String
    let updatedAt: String

    var entity: AssistantSession { AssistantSession(id: sessionId, title: title, updatedAt: updatedAt) }
}

struct WireList: Decodable, Sendable {
    let schemaVersion: Int
    let sessions: [WireSession]
    let nextCursor: String?
}

struct WireSnapshot: Decodable, Sendable {
    let schemaVersion: Int
    let streamId: String
    let activeSessionId: String?
    let activeTurnId: String?
    let accepting: Bool
    let stopping: Bool
    let storageError: String?
    let unsavedFact: [String: String]?

    var entity: ServiceState {
        ServiceState(streamID: streamId, activeSessionID: activeSessionId, activeTurnID: activeTurnId,
                     accepting: accepting, stopping: stopping, storageError: storageError, unsavedFact: unsavedFact)
    }
}

struct WirePage: Decodable, Sendable {
    struct Turn: Decodable, Sendable {
        let turnId: String
        let input: String
        let answer: String?
        let status: String
        let reason: String?
    }
    let schemaVersion: Int
    let streamId: String
    let session: WireSession
    let turns: [Turn]
    let nextBefore: Int?

    var entity: ConversationPage {
        ConversationPage(session: session.entity, turns: turns.map {
            ConversationTurn(id: $0.turnId, input: $0.input, answer: $0.answer, status: $0.status, reason: $0.reason)
        }, nextBefore: nextBefore, streamID: streamId)
    }
}
