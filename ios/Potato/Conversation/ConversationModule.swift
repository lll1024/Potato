import Observation
import SessionKit

@MainActor
struct ConversationInteractor {
    let sessions: any SessionAccess
    func load(sessionID: String) async throws -> ConversationPage {
        try await sessions.history(sessionID: sessionID)
    }
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
    let session: AssistantSession
    let router: ConversationRouter
    private let interactor: ConversationInteractor
    private(set) var content = ConversationContent.loading
    var title: String {
        if case .loaded(let page) = content { return page.title }
        return session.title
    }

    init(session: AssistantSession, interactor: ConversationInteractor, router: ConversationRouter) {
        self.session = session
        self.interactor = interactor
        self.router = router
    }

    func reload() async {
        content = .loading
        do {
            let page = try await interactor.load(sessionID: session.id)
            content = .loaded(ConversationPresentation(title: page.session.title, turns: page.turns.map {
                ConversationPresentation.Turn(id: $0.id, input: $0.input, answer: $0.answer,
                                              statusMessage: statusText($0))
            }, hasEarlierTurns: page.nextBefore != nil))
        }
        catch SessionFailure.obsolete { }
        catch is CancellationError { }
        catch { content = .failed(error.localizedDescription) }
    }

    private func statusText(_ turn: ConversationTurn) -> String? {
        if turn.status == "completed" { return nil }
        if turn.status == "running" { return "电脑端仍在处理，可稍后下拉刷新。" }
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
