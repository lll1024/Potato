import Observation
import SessionKit

@MainActor
struct SessionListInteractor {
    let sessions: any SessionAccess
    var connection: ConnectionState { sessions.connection }
    var service: ServiceState? { sessions.service }
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
    private(set) var content = SessionListContent.loading
    var connection: ConnectionState { interactor.connection }
    var serviceMessage: String? {
        guard let service = interactor.service, !service.accepting else { return nil }
        return service.storageError ?? "后端暂不接受新输入，已保存历史仍可读取。"
    }

    init(interactor: SessionListInteractor, router: SessionListRouter) {
        self.interactor = interactor
        self.router = router
    }

    func reload() async {
        content = .loading
        guard connection != .unconfigured else { return }
        do { content = .loaded(try await interactor.load()) }
        catch SessionFailure.obsolete { }
        catch is CancellationError { }
        catch { content = .failed(error.localizedDescription) }
    }
}

@MainActor @Observable
final class SessionListRouter {
    var selectedSession: AssistantSession?
    var showingConnection = false

    func open(_ session: AssistantSession) { selectedSession = session }
    func backToList() { selectedSession = nil }
    func configureConnection() { showingConnection = true }
}
