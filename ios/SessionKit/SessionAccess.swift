/// iOS 会话能力的公开入口；契约见 docs/modules/ios-sessions.md。
import Foundation

public enum ConnectionState: Equatable, Sendable {
    case unconfigured
    case connecting
    case connected
    case unavailable(String)
}

public enum SessionFailure: Error, LocalizedError, Equatable, Sendable {
    case invalidAddress
    case unconfigured
    case invalidResponse
    case unreachable
    case http(Int)
    case obsolete

    public var errorDescription: String? {
        switch self {
        case .invalidAddress: "请输入 HTTPS 服务根地址，不包含账号、密码、子路径或查询参数。"
        case .unconfigured: "请先配置服务地址。"
        case .invalidResponse: "无法读取服务返回的数据，请检查客户端与后端版本后重试。"
        case .unreachable: "无法连接服务。请确认 Mac、后端和 Tailscale 在线后重试。"
        case .http(404): "会话不存在，可能已在电脑端删除。请返回列表刷新。"
        case .http(let status): "服务暂时无法完成读取（HTTP \(status)），请稍后重试。"
        case .obsolete: "服务或读取目标已变化，请重新刷新。"
        }
    }
}

@MainActor
public protocol SessionAccess: AnyObject {
    var serverAddress: String { get }
    var connection: ConnectionState { get }
    var service: ServiceState? { get }
    func configure(address: String) throws
    func refresh() async throws -> SessionList
    func history(sessionID: String) async throws -> ConversationPage
}

public struct ServiceState: Equatable, Sendable {
    public let streamID: String
    public let activeSessionID: String?
    public let activeTurnID: String?
    public let accepting: Bool
    public let stopping: Bool
    public let storageError: String?
    public let unsavedFact: [String: String]?
}

public struct AssistantSession: Equatable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let updatedAt: String
}

public struct SessionList: Equatable, Sendable {
    public let sessions: [AssistantSession]
    public let nextCursor: String?
}

public struct ConversationTurn: Equatable, Identifiable, Sendable {
    public let id: String
    public let input: String
    public let answer: String?
    public let status: String
    public let reason: String?
}

public struct ConversationPage: Equatable, Sendable {
    public let session: AssistantSession
    public let turns: [ConversationTurn]
    public let nextBefore: Int?
    public let streamID: String
}
