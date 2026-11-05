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
    case submissionDeleted
    case draftSaveFailed
    case draftLoadFailed
    case stopUnknown

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
        case .submissionDeleted: "本次提交曾被接受，但所属会话已删除，不会重新执行。原文仍保留在本机。"
        case .submissionConflict: "提交身份与服务记录冲突，请保留输入并核对原提交。"
        case .draftSaveFailed: "草稿尚未保存到本机，请保留输入并重试保存。"
        case .draftLoadFailed: "无法读取本机草稿，请重试读取后继续编辑。"
        case .stopUnknown: "暂时无法确认停止请求是否送达，请刷新核对原轮次。"
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
    var answerPreview: AnswerPreview? { get }
    var answerFailure: SessionFailure? { get }
    var canSubmit: Bool { get }
    func configure(address: String) throws
    func refresh() async throws -> SessionList
    func history(sessionID: String) async throws -> ConversationPage
    var isLoadingMoreSessions: Bool { get }
    var moreSessionsFailure: SessionFailure? { get }
    var isLoadingEarlierTurns: Bool { get }
    var earlierTurnsFailure: SessionFailure? { get }
    func loadMoreSessions() async throws -> SessionList
    func loadEarlierTurns(sessionID: String) async throws -> ConversationPage
    func submit(input: String, sessionID: String?) async throws -> SubmissionReceipt
    func reconcileSubmission() async throws
    func retrySubmission() async throws -> SubmissionReceipt
    var canRetrySubmission: Bool { get }
    func watchConversation(sessionID: String?)
    func setForeground(_ active: Bool)
    var stopTarget: StopTarget? { get }
    var stopState: StopState { get }
    func stop(target: StopTarget) async throws
    func draft(sessionID: String?) throws -> SessionDraft
    func saveDraft(input: String, scope: DraftScope) throws -> SessionDraft
    func moveDraft(_ draft: SessionDraft, input: String, to sessionID: String) throws -> SessionDraft
    func canSubmit(draft: SessionDraft) -> Bool
    func submit(draft: SessionDraft) async throws -> SubmissionReceipt
}

/// 用户操作时捕获的目标；导航和后来的活动轮次不会改写它。
public struct StopTarget: Equatable, Sendable {
    public let serverAddress: String
    public let streamID: String
    public let sessionID: String
    public let turnID: String
}

public enum StopState: Equatable, Sendable {
    case idle
    case requesting(StopTarget)
    case waiting(StopTarget)
    case uncertain(StopTarget)
}

public struct DraftScope: Equatable, Sendable, Codable {
    public let serverAddress: String
    public let streamID: String
    public let sessionID: String?
}

public struct SessionDraft: Equatable, Sendable {
    public let scope: DraftScope
    public let input: String
    public let revision: String?
}

public struct PendingSubmission: Equatable, Sendable, Codable {
    public let id: String
    public let serverAddress: String
    public let streamID: String
    public let input: String
    public let sessionID: String?
    public let draftRevision: String?
}

public struct SubmissionReceipt: Equatable, Sendable {
    public let request: PendingSubmission
    public let sessionID: String
    public let turnID: String
}

public enum SubmissionState: Equatable, Sendable {
    case idle
    case sending
    case checking
    case notFound
    case isolated
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
    public let ordinal: Int?
    public let input: String
    public let answer: String?
    public let status: String
    public let reason: String?
    let observedAt: Int?
}

public struct ConversationPage: Equatable, Sendable {
    public let session: AssistantSession
    public let turns: [ConversationTurn]
    public let nextBefore: Int?
    public let streamID: String
    public let cursor: Int?
    var windows: [HistoryWindow]
}

/// 当前服务内存中的累计正文，独立于已保存历史；新尝试整体替换旧预览。
public struct AnswerPreview: Decodable, Equatable, Sendable {
    public let instanceID: String
    public let streamID: String
    public let sessionID: String
    public let turnID: String
    public let attemptID: String
    public let revision: Int
    public let text: String
    public let status: String
}

// JSONDecoder 的 convertFromSnakeCase 产生 Id；公开领域数据沿用项目的 ID 命名。
extension AnswerPreview {
    enum CodingKeys: String, CodingKey {
        case instanceID = "instanceId", streamID = "streamId", sessionID = "sessionId"
        case turnID = "turnId", attemptID = "attemptId"
        case revision, text, status
    }
}
