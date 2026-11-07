import Alamofire
import Foundation

/// 传输类型只在能力内部使用；页面不依赖服务器 JSON 命名或 Alamofire。
struct SessionHTTP {
    let session: Session
    private let eventSession: Session

    init(session: Session? = nil) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        self.session = session ?? Session(configuration: configuration)
        configuration.timeoutIntervalForResource = 7 * 24 * 60 * 60
        eventSession = session ?? Session(configuration: configuration)
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

    func lookup(_ pending: PendingSubmission) async throws -> WireReceipt? {
        try await submissionResult(pending, lookup: true)
    }

    func stop(_ target: StopTarget) async throws {
        let request = session.request(target.serverAddress + "/api/turns/" + target.turnID + "/stop", method: .post)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let response = await request.serializingDecodable(WireStop.self, decoder: decoder).response
        try Task.checkCancellation()
        guard response.response?.statusCode == 200, case .success(let result) = response.result,
              result.schemaVersion == 1, result.turnId == target.turnID else {
            throw SessionFailure.stopUnknown
        }
    }

    func submit(_ pending: PendingSubmission) async throws -> WireReceipt {
        guard let result = try await submissionResult(pending, lookup: false) else {
            throw SessionFailure.submissionUnknown
        }
        return result
    }

    private func submissionResult(_ pending: PendingSubmission, lookup: Bool) async throws -> WireReceipt? {
        let parameters = WireSubmission(submission_id: pending.id, input: pending.input, session_id: pending.sessionID)
        let request = lookup
            ? session.request(pending.serverAddress + "/api/submissions/" + pending.id)
            : session.request(pending.serverAddress + "/api/turns", method: .post,
                parameters: parameters, encoder: JSONParameterEncoder.default)
        let response = await request.serializingData().response
        try Task.checkCancellation()
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard response.error == nil, let status = response.response?.statusCode,
              let data = response.data else { throw SessionFailure.submissionUnknown }
        if status == (lookup ? 200 : 202) {
            guard let receipt = try? decoder.decode(WireReceipt.self, from: data), receipt.schemaVersion == 1,
                  receipt.submissionId == pending.id, !receipt.sessionId.isEmpty, !receipt.turnId.isEmpty,
                  pending.sessionID == nil || pending.sessionID == receipt.sessionId else {
                throw SessionFailure.submissionUnknown
            }
            return receipt
        }
        guard let error = try? decoder.decode(WireRejection.self, from: data) else {
            throw SessionFailure.submissionUnknown
        }
        switch (status, error.detail.code) {
        case (404, "SUBMISSION_NOT_FOUND") where lookup: return nil
        case (410, "SUBMISSION_DELETED"): throw SessionFailure.submissionDeleted
        case (409, "BUSY"): throw SessionFailure.busy
        case (422, "INVALID_INPUT"): throw SessionFailure.invalidInput
        case (503, "SERVICE_UNAVAILABLE"), (503, "STORAGE_FAILURE"): throw SessionFailure.serviceUnavailable
        case (404, "SESSION_NOT_FOUND"): throw SessionFailure.http(404)
        case (409, "SUBMISSION_CONFLICT"): throw SessionFailure.submissionConflict
        default: throw SessionFailure.submissionUnknown
        }
    }
}

private struct WireStop: Decodable, Sendable {
    let schemaVersion: Int
    let turnId: String
    let status: String
    let stopping: Bool
}

private struct WireSubmission: Encodable, Sendable {
    let submission_id: String
    let input: String
    let session_id: String?
}

struct WireReceipt: Decodable, Sendable {
    let schemaVersion: Int
    let submissionId: String
    let sessionId: String
    let turnId: String
}

private struct WireRejection: Decodable {
    struct Detail: Decodable { let code: String }
    let detail: Detail
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

    let instanceId: String?
    let answerRevision: Int?
    let answerPreview: AnswerPreview?
    let progressRevision: Int?
    let progressPreview: ProgressPreview?

    var entity: ServiceState {
        ServiceState(streamID: streamId, activeSessionID: activeSessionId, activeTurnID: activeTurnId,
                     accepting: accepting, stopping: stopping, storageError: storageError, unsavedFact: unsavedFact)
    }
}

struct WirePage: Decodable, Sendable {
    struct Turn: Decodable, Sendable {
        let turnId: String
        let ordinal: Int?
        let input: String
        let answer: String?
        let status: String
        let reason: String?
        let answerDelivery: String?
    }
    let schemaVersion: Int
    let streamId: String
    let session: WireSession
    let turns: [Turn]
    let nextBefore: Int?
    let cursor: Int?

    var entity: ConversationPage {
        ConversationPage(session: session.entity, turns: turns.map {
            ConversationTurn(id: $0.turnId, ordinal: $0.ordinal, input: $0.input, answer: $0.answer,
                             status: $0.status, reason: $0.reason, answerDelivery: $0.answerDelivery, observedAt: cursor)
        }, nextBefore: nextBefore, streamID: streamId, cursor: cursor,
            windows: HistoryWindow.page(ordinals: turns.compactMap(\.ordinal), before: nextBefore))
    }
}

extension SessionHTTP {
    /// 传输只交付原始字节；关闭接收只取消此 GET，不调用后端停止。
    func events(address: String, streamID: String, after: Int) -> AsyncThrowingStream<Data, any Error> {
        AsyncThrowingStream { continuation in
            var request = URLRequest(url: URL(string: address + "/api/events")!)
            var url = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!
            url.queryItems = [URLQueryItem(name: "stream_id", value: streamID),
                              URLQueryItem(name: "after", value: String(after))]
            request.url = url.url
            request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            // 服务每 15 秒发送心跳；正文流不使用普通读取的 30 秒总时限。
            request.timeoutInterval = 60
            let receiving = eventSession.streamRequest(request).validate(statusCode: 200..<300)
            continuation.onTermination = { _ in receiving.cancel() }
            receiving.responseStream { response in
                switch response.event {
                case .stream(.success(let data)): continuation.yield(data)
                case .complete(let completion):
                    if let error = completion.error { continuation.finish(throwing: error) }
                    else { continuation.finish() }
                }
            }
        }
    }
}

/// 按字节留住未完成行，避免网络块切断中文；只解析完整的 SSE 事件。
struct SessionEventParser {
    private var pending = Data()
    private var kind = ""
    private var lines: [String] = []

    mutating func append(_ data: Data) throws -> [WireAnswerEvent] {
        pending.append(data)
        var events: [WireAnswerEvent] = []
        while let end = pending.firstIndex(of: 10) {
            var bytes = pending.prefix(upTo: end)
            if bytes.last == 13 { bytes = bytes.dropLast() }
            guard let line = String(data: bytes, encoding: .utf8) else { throw SessionFailure.invalidResponse }
            pending.removeSubrange(...end)
            if line.isEmpty {
                if ["answer.preview", "progress.preview"].contains(kind), !lines.isEmpty {
                    let decoder = JSONDecoder()
                    decoder.keyDecodingStrategy = .convertFromSnakeCase
                    events.append(try decoder.decode(WireAnswerEvent.self, from: Data(lines.joined(separator: "\n").utf8)))
                }
                kind = ""
                lines = []
            } else if line.hasPrefix("event:") {
                kind = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                let value = line.dropFirst(5)
                lines.append(String(value.first == " " ? value.dropFirst() : value))
            }
        }
        return events
    }
}

struct WireAnswerEvent: Decodable, Sendable {
    let schemaVersion: Int
    let instanceId: String
    let revision: Int
    let preview: AnswerPreview?
    let progress: ProgressPreview?
    let kind: String
    enum CodingKeys: String, CodingKey { case schemaVersion, instanceId, revision, preview, kind }
    init(from decoder: any Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try values.decode(Int.self, forKey: .schemaVersion)
        instanceId = try values.decode(String.self, forKey: .instanceId)
        revision = try values.decode(Int.self, forKey: .revision)
        kind = try values.decodeIfPresent(String.self, forKey: .kind) ?? "answer.preview"
        if kind == "progress.preview" {
            progress = try values.decodeIfPresent(ProgressPreview.self, forKey: .preview)
            preview = nil
        } else {
            preview = try values.decodeIfPresent(AnswerPreview.self, forKey: .preview)
            progress = nil
        }
    }
}
