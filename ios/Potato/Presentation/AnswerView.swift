import MarkdownUI
import SwiftUI
import UIKit

/// 排版依赖与系统复制操作收在回答视图内；复制当前展示的 Markdown 原文。
struct AnswerView: View {
    let text: String
    var isPreview = false
    var isUnsaved = false
    var isIncomplete = false
    var transitionID: String? = nil
    var isGenerating = false
    var isStopped = false
    var recoveryRevision = 0
    private var copyLabel: String { isUnsaved ? "未保存正文" : isPreview ? "当前正文" : isIncomplete ? "不完整回答" : "完整回答" }
    @State private var copied = false
    @ScaledMetric(relativeTo: .body) private var columnWidth = 180.0

    var body: some View {
      ReadingTransition(text: text, identity: transitionID ?? "saved", live: isGenerating,
                        stopped: isStopped, recovery: recoveryRevision) { displayed in
        VStack(alignment: .leading, spacing: 12) {
            Markdown(displayed)
                .markdownBlockStyle(\.paragraph) { configuration in
                    configuration.label
                        .fixedSize(horizontal: false, vertical: true)
                        .relativeLineSpacing(.em(0.22))
                        .markdownMargin(bottom: .em(1))
                }
                .markdownBlockStyle(\.table) { configuration in
                    VStack(alignment: .leading, spacing: 6) {
                        Text("表格可左右滑动").font(.caption).foregroundStyle(.secondary)
                        ScrollView(.horizontal) {
                            configuration.label
                                .fixedSize(horizontal: true, vertical: false)
                                .markdownTableBorderStyle(.init(color: Color.secondary.opacity(0.3)))
                        }
                        .accessibilityIdentifier("answer-table")
                    }
                    .markdownMargin(bottom: .em(1))
                }
                .markdownBlockStyle(\.tableCell) { configuration in
                    configuration.label
                        .markdownTextStyle { if configuration.row == 0 { FontWeight(.semibold) } }
                        .frame(width: columnWidth, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(12)
                }
                .markdownTheme(.basic)
                .textSelection(.enabled)
            Button((copied ? "已复制" : "复制") + copyLabel, systemImage: copied ? "checkmark" : "doc.on.doc") {
                UIPasteboard.general.string = displayed
                copied = true
            }
            .font(.subheadline).frame(minHeight: 44)
            .accessibilityIdentifier("copy-answer")
            .onChange(of: displayed) { copied = false }
            .onChange(of: copyLabel) { copied = false }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
      }
    }
}

/// 显示层只处理已收到的安全正文；200ms 截止不因后续块续期。
struct ReadingTransition<Content: View>: View {
    let text: String
    let identity: String
    let live: Bool
    var stopped = false
    var recovery = 0
    @ViewBuilder let content: (String) -> Content
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var visible: String
    @State private var previousIdentity: String
    @State private var previousRecovery: Int
    @State private var base: String
    @State private var started: ContinuousClock.Instant?

    init(text: String, identity: String, live: Bool, stopped: Bool = false, recovery: Int = 0,
         @ViewBuilder content: @escaping (String) -> Content) {
        self.text = text; self.identity = identity; self.live = live
        self.stopped = stopped; self.recovery = recovery; self.content = content
        _visible = State(initialValue: text); _base = State(initialValue: text)
        _previousIdentity = State(initialValue: identity); _previousRecovery = State(initialValue: recovery)
    }
    private struct Update: Equatable {
        let text: String; let identity: String; let live: Bool; let stopped: Bool
        let recovery: Int; let reduced: Bool
    }
    var body: some View {
        content(visible)
            .task(id: Update(text: text, identity: identity, live: live, stopped: stopped,
                             recovery: recovery, reduced: reduceMotion)) {
                let changedIdentity = previousIdentity != identity
                let restored = previousRecovery != recovery
                previousIdentity = identity; previousRecovery = recovery
                if stopped && !changedIdentity { started = nil; return }
                if changedIdentity || restored || !live || reduceMotion || !text.hasPrefix(visible) {
                    visible = text; base = text; started = nil; return
                }
                if started == nil && text.dropFirst(visible.count).count <= 64 {
                    visible = text; base = text; return
                }
                let clock = ContinuousClock()
                if started == nil { started = clock.now; base = visible }
                guard let beginning = started else { return }
                let characters = Array(text.dropFirst(base.count))
                while !Task.isCancelled {
                    let elapsed = beginning.duration(to: clock.now)
                    if elapsed >= .milliseconds(200) { visible = text; base = text; started = nil; return }
                    let seconds = Double(elapsed.components.seconds) + Double(elapsed.components.attoseconds) / 1e18
                    let count = min(characters.count, max(16, Int(ceil(Double(characters.count) * seconds / 0.2))))
                    visible = base + String(characters.prefix(count))
                    do { try await Task.sleep(for: .milliseconds(16)) } catch { return }
                }
            }
            .onDisappear { started = nil }
    }
}
