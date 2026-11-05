import SessionKit
import SwiftUI

struct ConversationView: View {
    @State private var presenter: ConversationPresenter
    @FocusState private var inputFocused: Bool
    @State private var followsLatest = true

    init(session: AssistantSession?, sessions: any SessionAccess, router: ConversationRouter) {
        _presenter = State(initialValue: ConversationPresenter(session: session,
            interactor: ConversationInteractor(sessions: sessions), router: router))
    }

    var body: some View {
        @Bindable var presenter = presenter
        ScrollViewReader { proxy in
          ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                SubmissionRecoveryView(presentation: presenter.recovery, check: presenter.checkSubmission,
                    retry: presenter.retrySubmission, open: presenter.openSubmission)
                switch presenter.content {
                case .loading: ProgressView("正在读取对话").frame(maxWidth: .infinity)
                case .failed(let message):
                    ContentUnavailableView {
                        Label("无法读取对话", systemImage: "wifi.exclamationmark")
                    } description: { Text(message) } actions: {
                        Button("重试") { Task { await presenter.reload() } }
                        Button("返回列表", action: presenter.router.backToList)
                    }
                case .loaded(let page):
                    if page.turns.isEmpty {
                        ContentUnavailableView("开始一次旅行讨论", systemImage: "bubble.left.and.bubble.right",
                            description: Text("写下目的地、日期或需要调整的旅行安排。"))
                    }
                    if page.hasEarlierTurns {
                        VStack(alignment: .leading, spacing: 8) {
                            if let message = presenter.earlierMessage { Text(message).font(.footnote).foregroundStyle(.secondary) }
                            if presenter.isLoadingEarlier {
                                ProgressView("正在加载更早对话")
                            } else {
                                Button(presenter.earlierMessage == nil ? "加载更早对话" : "重试加载更早对话") {
                                    let anchor = presenter.firstTurnID
                                    inputFocused = false
                                    followsLatest = false
                                    Task {
                                        await presenter.loadEarlier()
                                        if let anchor { proxy.scrollTo(anchor, anchor: .top) }
                                    }
                                }
                                .frame(minHeight: 44)
                                .accessibilityIdentifier("load-earlier-turns")
                            }
                        }
                    }
                    ForEach(page.turns) { turn in
                        VStack(alignment: .leading, spacing: 20) {
                            Text(turn.input)
                                .textSelection(.enabled)
                                .padding(16)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
                                .accessibilityLabel("你的输入：" + turn.input)
                            if let answer = turn.answer, !answer.isEmpty {
                                AnswerView(text: answer)
                            }
                            if let message = turn.statusMessage {
                                Text(message)
                                    .font(.subheadline).foregroundStyle(.secondary)
                            }
                        }
                        .id(turn.id)
                    }
                }
                Color.clear.frame(height: 1).id("latest")
            }
            .id("history-start")
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollDismissesKeyboard(.interactively)
        .simultaneousGesture(DragGesture(minimumDistance: 10).onChanged { _ in followsLatest = false })
        .onChange(of: presenter.latestTurnID) { previous, _ in
            if previous == nil || followsLatest { proxy.scrollTo("latest", anchor: .bottom) }
        }
        .overlay(alignment: .bottomTrailing) {
            if !followsLatest, presenter.latestTurnID != nil {
                Button("回到最新", systemImage: "arrow.down") {
                    inputFocused = false
                    followsLatest = true
                    proxy.scrollTo("latest", anchor: .bottom)
                }
                .buttonStyle(.bordered).background(.background, in: Capsule())
                .frame(minHeight: 44).padding(12)
                .accessibilityIdentifier("latest-answer")
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if presenter.firstTurnID != nil {
                    Button("查看更早对话", systemImage: "clock.arrow.circlepath") {
                        inputFocused = false
                        followsLatest = false
                        proxy.scrollTo("history-start", anchor: .top)
                    }
                    .accessibilityIdentifier("earlier-history")
                }
            }
        }
        }
        .navigationTitle(presenter.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                if let target = presenter.stopTarget {
                    Button(role: .destructive) {
                        Task { await presenter.stop(target: target) }
                    } label: {
                        Image(systemName: "stop.circle")
                    }
                    .accessibilityLabel("停止当前轮次")
                    .accessibilityIdentifier("stop-turn")
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button("连接配置", systemImage: "gearshape", action: presenter.router.configureConnection)
                    .accessibilityIdentifier("connection-settings")
            }
        }
        .refreshable { await presenter.reload() }
        .task { await presenter.appear() }
        .onDisappear { presenter.disappear() }
        .onChange(of: presenter.submissionState) { presenter.submissionChanged() }
        .safeAreaInset(edge: .bottom) {
            VStack(alignment: .leading, spacing: 10) {
                if let message = presenter.serviceMessage {
                    Text(message).font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("execution-message")
                }
                if let message = presenter.sendMessage {
                    Text(message).font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("send-feedback")
                }
                if let message = presenter.draftMessage {
                    Text(message).font(.footnote)
                        .foregroundStyle(presenter.draftError == nil ? Color.secondary : Color.primary)
                        .accessibilityIdentifier("draft-status")
                }
                if presenter.draftError != nil {
                    Button(presenter.canEditDraft ? "重试保存草稿" : "重试读取草稿", action: presenter.retryDraft)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("retry-draft")
                }
                HStack(alignment: .bottom, spacing: 12) {
                    TextField("输入旅行需求", text: $presenter.input, axis: .vertical)
                        .lineLimit(1...5)
                        .padding(12)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
                        .focused($inputFocused)
                        .disabled(!presenter.canEditDraft)
                        .accessibilityIdentifier("travel-input")
                    Button {
                        followsLatest = true
                        Task { await presenter.send() }
                        inputFocused = false
                    } label: {
                        Image(systemName: "arrow.up").frame(width: 44, height: 44)
                    }
                    .buttonStyle(.borderedProminent).buttonBorderShape(.circle)
                    .disabled(!presenter.canSend)
                    .accessibilityLabel("发送")
                    .accessibilityIdentifier("send-input")
                }
            }
            .padding(.horizontal, 20).padding(.vertical, 12)
            .background(.bar)
        }
    }

}
