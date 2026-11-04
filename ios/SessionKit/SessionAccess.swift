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
    case busy
    case invalidInput
    case serviceUnavailable
    case localSaveFailed
    case submissionUnknown
    case submissionConflict

    public var errorDescription: String? {
        switch self {
        case .invalidAddress: "请输入 HTTPS 服务根地址，不包含账号、密码、子路径或查询参数。"
        case .unconfigured: "请先配置服务地址。"
        case .invalidResponse: "无法读取服务返回的数据，请检查客户端与后端版本后重试。"
        case .unreachable: "无法连接服务。请确认 Mac、后端和 Tailscale 在线后重试。"
        case .http(404): "会话不存在，可能已在电脑端删除。请返回列表刷新。"
        case .http(let status): "服务暂时无法完成读取（HTTP \(status)），请稍后重试。"
        case .obsolete: "服务或读取目标已变化，请重新刷新。"
        case .busy: "已有一轮正在执行或正在发送，请等待结束。输入不会自动排队。"
        case .invalidInput: "请输入旅行需求。"
        case .serviceUnavailable: "后端暂不接受输入，请检查服务与保存状态后重试。"
        case .localSaveFailed: "本机未能保存本次提交，尚未发送。请保留输入并检查可用存储。"
        case .submissionUnknown: "本次发送结果未知，原始输入已保留。请勿重复发送，后续需核对原提交。"
        case .submissionConflict: "提交身份与服务记录冲突，请保留输入并核对原提交。"
        }
    }
}

@MainActor
public protocol SessionAccess: AnyObject {
    var serverAddress: String { get }
    var connection: ConnectionState { get }
    var service: ServiceState? { get }
    var submission: SubmissionState { get }
    var pendingSubmission: PendingSubmission? { get }
    var sessionList: SessionList? { get }
    var conversation: ConversationPage? { get }
    var conversationFailure: SessionFailure? { get }
    var canSubmit: Bool { get }
    func configure(address: String) throws
    func refresh() async throws -> SessionList
    func history(sessionID: String) async throws -> ConversationPage
    func submit(input: String, sessionID: String?) async throws -> SubmissionReceipt
    func watchConversation(sessionID: String?)
    func setForeground(_ active: Bool)
}

public struct PendingSubmission: Equatable, Sendable, Codable {
    public let id: String
    public let serverAddress: String
    public let streamID: String
    public let input: String
    public let sessionID: String?
}

public struct SubmissionReceipt: Equatable, Sendable {
    public let request: PendingSubmission
    public let sessionID: String
    public let turnID: String
}

public enum SubmissionState: Equatable, Sendable {
    case idle
    case sending
    case accepted(SubmissionReceipt)
    case unknown
    case rejected(SessionFailure)
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
