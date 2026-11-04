import Foundation
import Observation
import SessionKit

@MainActor
struct ConversationInteractor {
    let sessions: any SessionAccess
    var page: ConversationPage? { sessions.conversation }
    var canSubmit: Bool { sessions.canSubmit }
    var execution: ExecutionState { ExecutionState(sessions) }
    var readFailure: SessionFailure? { sessions.conversationFailure }
    var pendingInput: String? { sessions.pendingSubmission?.input }
    func watch(sessionID: String?) { sessions.watchConversation(sessionID: sessionID) }
    func load(sessionID: String) async throws -> ConversationPage {
        try await sessions.history(sessionID: sessionID)
    }
    func send(input: String, sessionID: String?) async throws -> SubmissionReceipt {
        try await sessions.submit(input: input, sessionID: sessionID)
    }
    func refresh() async { _ = try? await sessions.refresh() }
}

enum ConversationContent {
    case loading
    case loaded(ConversationPresentation)
    case failed(String)
}

struct ConversationPresentation {
    struct Turn: Identifiable {
        let id: String
        let input: String
        let answer: String?
        let statusMessage: String?
    }
    let title: String
    let turns: [Turn]
    let hasEarlierTurns: Bool
}

@MainActor @Observable
final class ConversationPresenter {
    let router: ConversationRouter
    private let interactor: ConversationInteractor
    private var sessionID: String?
    private var initialTitle: String
    private var readError: String?
    private var isVisible = false
    private(set) var sendMessage: String?
    var input = ""
    var serviceMessage: String? { executionMessage(interactor.execution) }
    var pendingInput: String? { interactor.pendingInput }
    var canSend: Bool { interactor.canSubmit && !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var title: String { currentPage?.session.title ?? initialTitle }
    var latestTurnID: String? { currentPage?.turns.last?.id }
    private var currentPage: ConversationPage? {
        if let page = interactor.page, page.session.id == sessionID { return page }
        return nil
    }
    var content: ConversationContent {
        if interactor.readFailure == .http(404) { return .failed(SessionFailure.http(404).localizedDescription) }
        guard let page = currentPage else {
            if let readError { return .failed(readError) }
            return sessionID == nil ? .loaded(ConversationPresentation(title: title, turns: [], hasEarlierTurns: false)) : .loading
        }
        return .loaded(ConversationPresentation(title: page.session.title, turns: page.turns.map {
            ConversationPresentation.Turn(id: $0.id, input: $0.input, answer: $0.answer, statusMessage: statusText($0))
        }, hasEarlierTurns: page.nextBefore != nil))
    }

    init(session: AssistantSession?, interactor: ConversationInteractor, router: ConversationRouter) {
        sessionID = session?.id
        initialTitle = session?.title ?? "新建会话"
        self.interactor = interactor
        self.router = router
    }

    func reload() async {
        guard isVisible else { return }
        interactor.watch(sessionID: sessionID)
        guard let sessionID else { return }
        do {
            _ = try await interactor.load(sessionID: sessionID)
            readError = nil
        }
        catch SessionFailure.obsolete { }
        catch is CancellationError { }
        catch { readError = error.localizedDescription }
    }

    func appear() async { isVisible = true; await reload() }
    func disappear() { isVisible = false }

    func send() async {
        guard canSend else { return }
        let originalInput = input
        sendMessage = nil
        do {
            let receipt = try await interactor.send(input: originalInput, sessionID: sessionID)
            sessionID = receipt.sessionID
            if input == originalInput { input = "" }
            sendMessage = "输入已接受。"
            guard isVisible else { return }
            await interactor.refresh()
            await reload()
        } catch {
            sendMessage = error.localizedDescription
        }
    }

    private func statusText(_ turn: ConversationTurn) -> String? {
        if turn.status == "running" { return "电脑端仍在处理，回答将在保存后显示。" }
        if turn.status == "completed" { return nil }
        switch turn.reason {
        case "user_stop": return "本轮已停止。"
        case "service_interrupted": return "服务曾中断，本轮未完成。"
        case "model_error": return "本轮模型请求失败。"
        case "budget", "output_limit": return "本轮达到处理上限，回答可能不完整。"
        default: return "本轮未完成（\(turn.status)）。"
        }
    }
}

@MainActor
struct ConversationRouter {
    let parent: SessionListRouter
    func backToList() { parent.backToList() }
    func configureConnection() { parent.configureConnection() }
}
