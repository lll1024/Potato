import MarkdownUI
import SessionKit
import SwiftUI

struct ConversationView: View {
    @State private var presenter: ConversationPresenter

    init(session: AssistantSession, sessions: any SessionAccess, router: ConversationRouter) {
        _presenter = State(initialValue: ConversationPresenter(session: session,
            interactor: ConversationInteractor(sessions: sessions), router: router))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
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
            }
            .padding(20)
            .frame(maxWidth: .infinity, alignment: .leading)
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
        .task { await presenter.reload() }
    }

}

/// 排版依赖收在回答视图内；原文来自后端保存的完整 answer。
struct AnswerView: View {
    let text: String
    var body: some View {
        Markdown(text).textSelection(.enabled)
    }
}
