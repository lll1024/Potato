import SessionKit
import SwiftUI
import SwiftUINavigation

struct SessionListView: View {
    private let sessions: any SessionAccess
    @State private var presenter: SessionListPresenter
    @State private var router: SessionListRouter

    init(sessions: any SessionAccess) {
        self.sessions = sessions
        let router = SessionListRouter()
        _router = State(initialValue: router)
        _presenter = State(initialValue: SessionListPresenter(
            interactor: SessionListInteractor(sessions: sessions), router: router))
    }

    var body: some View {
        @Bindable var router = router
        NavigationStack {
            Group {
                if presenter.connection == .unconfigured {
                    ContentUnavailableView {
                        Label("连接你的旅行助手", systemImage: "bubble.left.and.bubble.right")
                    } description: {
                        Text("配置 Mac 上的服务地址，继续阅读已有的旅行讨论。")
                    } actions: {
                        Button("配置连接", action: router.configureConnection).buttonStyle(.borderedProminent)
                    }
                } else {
                    List {
                        Section {
                            ConnectionIndicator(state: presenter.connection)
                            if let message = presenter.serviceMessage {
                                Text(message).foregroundStyle(.secondary)
                            }
                            if let target = presenter.stopTarget {
                                Button("停止当前轮次", role: .destructive) {
                                    Task { await presenter.stop(target: target) }
                                }
                                .frame(minHeight: 44)
                                .accessibilityIdentifier("stop-turn")
                            }
                        }
                        if presenter.recovery.pending != nil || presenter.recovery.accepted != nil {
                            Section {
                                SubmissionRecoveryView(presentation: presenter.recovery, check: presenter.checkSubmission,
                                    retry: presenter.retrySubmission, open: presenter.openSubmission)
                            }
                        }
                        content
                    }
                    .refreshable { await presenter.reload() }
                }
            }
            .navigationTitle("助手会话")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("新建会话", systemImage: "square.and.pencil", action: router.newConversation)
                        .disabled(presenter.connection == .unconfigured)
                        .accessibilityIdentifier("new-conversation")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("连接配置", systemImage: "gearshape", action: router.configureConnection)
                        .accessibilityIdentifier("connection-settings")
                }
            }
            .navigationDestination(item: $router.selectedConversation) { destination in
                ConversationView(session: destination.session, sessions: sessions, router: ConversationRouter(parent: router))
                    .id(destination.id)
            }
            .sheet(isPresented: $router.showingConnection, onDismiss: {
                Task { await presenter.reload() }
            }) {
                ConnectionSettingsView(sessions: sessions)
            }
            .task { await presenter.reload() }
            .onChange(of: router.selectedConversation?.id) {
                presenter.conversationChanged(sessionID: router.selectedConversation?.session?.id)
            }
            .onChange(of: sessions.serverAddress) {
                router.backToList()
            }
            .onChange(of: sessions.service?.streamID) { previous, current in
                guard previous != nil, previous != current else { return }
                router.backToList()
                if current == nil { Task { await presenter.reload() } }
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch presenter.content {
        case .loading:
            Section { ProgressView("正在读取会话") }
        case .failed(let message):
            Section {
                Text(message).foregroundStyle(.secondary)
                Button("重新连接") { Task { await presenter.reload() } }
            }
        case .loaded(let list):
            if list.sessions.isEmpty {
                Section {
                    ContentUnavailableView("还没有助手会话", systemImage: "bubble.left",
                                           description: Text("轻点新建会话，开始一次旅行讨论。"))
                }
            } else {
                Section {
                    ForEach(list.sessions) { session in
                        Button { presenter.router.open(session) } label: {
                            HStack {
                                Text(session.title).foregroundStyle(.primary).lineLimit(2)
                                Spacer(minLength: 12)
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                            }
                            .frame(minHeight: 44)
                            .contentShape(Rectangle())
                        }
                        .accessibilityIdentifier("open-session")
                    }
                } header: { Text("最近的讨论") }
                if list.nextCursor != nil {
                    Section {
                        if let message = presenter.moreMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                        if presenter.isLoadingMore {
                            ProgressView("正在加载更早会话")
                        } else {
                            Button(presenter.moreMessage == nil ? "加载更早会话" : "重试加载更早会话") {
                                Task { await presenter.loadMore() }
                            }
                            .frame(minHeight: 44)
                            .accessibilityIdentifier("load-more-sessions")
                        }
                    }
                }
            }
        }
    }
}
