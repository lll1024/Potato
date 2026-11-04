import MarkdownUI
import SessionKit
import SwiftUI

struct ConversationView: View {
    @State private var presenter: ConversationPresenter
    @FocusState private var inputFocused: Bool

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
                        Text("这里是最近 50 轮完整对话。更早的内容可在电脑端查看。")
                            .font(.footnote).foregroundStyle(.secondary)
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
                    }
                }
                Color.clear.frame(height: 1).id("latest")
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollDismissesKeyboard(.interactively)
        .onChange(of: presenter.latestTurnID) { proxy.scrollTo("latest", anchor: .bottom) }
        }
        .navigationTitle(presenter.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
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
                if presenter.recovery.message == nil, let message = presenter.serviceMessage {
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

/// 排版依赖收在回答视图内；原文来自后端保存的完整 answer。
struct AnswerView: View {
    let text: String
    var body: some View {
        Markdown(text).textSelection(.enabled)
    }
}
