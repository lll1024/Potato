import Foundation
import Observation
import SessionKit

@MainActor
struct SessionListInteractor {
    let sessions: any SessionAccess
    var connection: ConnectionState { sessions.connection }
    var list: SessionList? { sessions.sessionList }
    var execution: ExecutionState { ExecutionState(sessions) }
    var stopTarget: StopTarget? { sessions.stopTarget }
    func stop(target: StopTarget) async {
        try? await sessions.stop(target: target)
        _ = try? await sessions.refresh()
    }
    var recovery: SubmissionRecoveryPresentation { SubmissionRecoveryPresentation(sessions) }
    func checkSubmission() async { try? await sessions.reconcileSubmission() }
    func retrySubmission() async { _ = try? await sessions.retrySubmission() }
    func history(sessionID: String) async throws -> ConversationPage { try await sessions.history(sessionID: sessionID) }
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
        if let errorMessage { return .failed(errorMessage) }
        if let list = interactor.list { return .loaded(list) }
        return .loading
    }
    var connection: ConnectionState { interactor.connection }
    var serviceMessage: String? { executionMessage(interactor.execution) }
    var stopTarget: StopTarget? { interactor.stopTarget }
    func stop(target: StopTarget) async { await interactor.stop(target: target) }
    var recovery: SubmissionRecoveryPresentation { interactor.recovery }
    func checkSubmission() async { await interactor.checkSubmission() }
    func retrySubmission() async { await interactor.retrySubmission() }
    func openSubmission() async {
        guard let accepted = recovery.accepted else { return }
        let navigation = router.navigationRevision
        do {
            let page = try await interactor.history(sessionID: accepted.sessionID)
            guard recovery.accepted == accepted, router.navigationRevision == navigation else { return }
            router.open(page.session)
        } catch {
            guard router.navigationRevision == navigation else { return }
            errorMessage = error.localizedDescription
        }
    }

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
    private(set) var navigationRevision = 0

    func open(_ session: AssistantSession) {
        navigationRevision += 1
        selectedConversation = ConversationDestination(session: session)
    }
    func newConversation() {
        navigationRevision += 1
        selectedConversation = ConversationDestination(session: nil)
    }
    func backToList() {
        navigationRevision += 1
        selectedConversation = nil
    }
    func configureConnection() {
        navigationRevision += 1
        showingConnection = true
    }
}
