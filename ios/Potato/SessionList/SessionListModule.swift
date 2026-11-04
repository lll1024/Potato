import Foundation
import Observation
import SessionKit

@MainActor
struct SessionListInteractor {
    let sessions: any SessionAccess
    var connection: ConnectionState { sessions.connection }
    var list: SessionList? { sessions.sessionList }
    var execution: ExecutionState { ExecutionState(sessions) }
    func load() async throws -> SessionList { try await sessions.refresh() }
}

enum SessionListContent {
    case loading
    case loaded(SessionList)
    case failed(String)
}

@MainActor @Observable
final class SessionListPresenter {
    private let interactor: SessionListInteractor
    let router: SessionListRouter
    private var errorMessage: String?
    var content: SessionListContent {
        if case .unavailable(let message) = connection { return .failed(message) }
        if let list = interactor.list { return .loaded(list) }
        if let errorMessage { return .failed(errorMessage) }
        return .loading
    }
    var connection: ConnectionState { interactor.connection }
    var serviceMessage: String? { executionMessage(interactor.execution) }

    init(interactor: SessionListInteractor, router: SessionListRouter) {
        self.interactor = interactor
        self.router = router
    }

    func reload() async {
        errorMessage = nil
        guard connection != .unconfigured else { return }
        do { _ = try await interactor.load() }
        catch SessionFailure.obsolete { }
        catch is CancellationError { }
        catch { errorMessage = error.localizedDescription }
    }
}

struct ConversationDestination: Identifiable, Hashable {
    let id = UUID()
    let session: AssistantSession?
    static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

@MainActor @Observable
final class SessionListRouter {
    var selectedConversation: ConversationDestination?
    var showingConnection = false

    func open(_ session: AssistantSession) { selectedConversation = ConversationDestination(session: session) }
    func newConversation() { selectedConversation = ConversationDestination(session: nil) }
    func backToList() { selectedConversation = nil }
    func configureConnection() { showingConnection = true }
}
