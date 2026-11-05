import SessionKit
import SwiftUI

/// 只表达共享提交投影；页面不保存另一份核对或重试状态。
struct SubmissionRecoveryPresentation {
    let pending: PendingSubmission?
    let state: SubmissionState
    let canRetry: Bool
    let canCheck: Bool
    let accepted: SubmissionReceipt?

    @MainActor init(_ sessions: any SessionAccess) {
        pending = sessions.pendingSubmission
        state = sessions.submission
        canRetry = sessions.canRetrySubmission
        canCheck = pending != nil && state != .sending && state != .checking
        if case .accepted(let receipt) = state,
           receipt.request.serverAddress == sessions.serverAddress,
           receipt.request.streamID == sessions.service?.streamID { accepted = receipt }
        else { accepted = nil }
    }

    var message: String? {
        guard pending != nil else { return nil }
        return switch state {
        case .checking: "正在核对原提交，不会自动重发。"
        case .notFound: "当前未查到接受记录。原发送仍可能在途；明确重试将沿用原提交。"
        case .isolated: "原提交属于另一服务或数据集，已保留原文。切回原服务和数据集后才能继续核对。"
        case .rejected(let failure): failure.localizedDescription
        case .unknown: SessionFailure.submissionUnknown.localizedDescription
        default: nil
        }
    }
}

struct SubmissionRecoveryView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    let presentation: SubmissionRecoveryPresentation
    let check: () async -> Void
    let retry: () async -> Void
    let open: () async -> Void

    @ViewBuilder private var actions: some View {
        Button("核对原提交") { Task { await check() } }
            .disabled(!presentation.canCheck)
            .accessibilityIdentifier("check-submission")
            .frame(minHeight: 44)
        Button("重试同一提交") { Task { await retry() } }
            .disabled(!presentation.canRetry)
            .accessibilityIdentifier("retry-submission")
            .frame(minHeight: 44)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let pending = presentation.pending {
                if let message = presentation.message {
                    Text(message).font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("submission-recovery-status")
                }
                DisclosureGroup("保留的待确认输入") {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("服务：" + pending.serverAddress)
                            Text("数据集：" + pending.streamID)
                            Text("目标：" + (pending.sessionID ?? "新建会话"))
                            Text(pending.input).textSelection(.enabled)
                        }
                        .font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 160)
                }.font(.footnote)
                if typeSize.isAccessibilitySize {
                    VStack(alignment: .leading, spacing: 4) { actions }
                } else {
                    HStack { actions }
                }
            } else if presentation.accepted != nil {
                Button("打开原会话") { Task { await open() } }
                    .frame(minHeight: 44).accessibilityIdentifier("open-recovered-submission")
            }
        }
    }
}
