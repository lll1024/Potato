import MarkdownUI
import SwiftUI
import UIKit

/// 排版依赖与系统复制操作收在回答视图内；始终使用后端完整原文。
struct AnswerView: View {
    let text: String
    var isPreview = false
    var isUnsaved = false
    var isIncomplete = false
    private var copyLabel: String { isUnsaved ? "未保存正文" : isPreview ? "当前正文" : isIncomplete ? "不完整回答" : "完整回答" }
    @State private var copied = false
    @ScaledMetric(relativeTo: .body) private var columnWidth = 180.0

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Markdown(text)
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
                UIPasteboard.general.string = text
                copied = true
            }
            .font(.subheadline).frame(minHeight: 44)
            .accessibilityIdentifier("copy-answer")
            .onChange(of: text) { copied = false }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
