import SessionKit

struct ExecutionState {
    let serverAddress: String
    let pendingSubmission: PendingSubmission?
    let submission: SubmissionState
    let service: ServiceState?
    let connection: ConnectionState
    let canSubmit: Bool

    @MainActor init(_ sessions: any SessionAccess) {
        serverAddress = sessions.serverAddress
        pendingSubmission = sessions.pendingSubmission
        submission = sessions.submission
        service = sessions.service
        connection = sessions.connection
        canSubmit = sessions.canSubmit
    }
}

func executionMessage(_ sessions: ExecutionState) -> String? {
    if let pending = sessions.pendingSubmission {
        if sessions.submission == .sending { return "正在发送，等待电脑确认接受。" }
        if pending.serverAddress != sessions.serverAddress || pending.streamID != sessions.service?.streamID {
            return "仍保留另一服务或数据集的待确认输入，暂不能发送。请保留原输入并核对原提交。"
        }
        switch sessions.submission {
        case .checking: return "正在核对原提交。"
        case .notFound: return "尚未查到接受记录，可明确重试同一提交。"
        case .rejected(let failure): return failure.localizedDescription
        default: return SessionFailure.submissionUnknown.localizedDescription
        }
    }
    if let service = sessions.service {
        if service.storageError != nil || service.unsavedFact != nil {
            return service.storageError ?? "后端存在未保存的执行事实，暂不能确认结果。"
        }
        if !service.accepting { return SessionFailure.serviceUnavailable.localizedDescription }
        if service.stopping { return "正在停止，等待电脑端收束。输入不会自动排队。" }
        if service.activeTurnID != nil { return "电脑端正在处理，回答将在保存后显示。输入不会自动排队。" }
    }
    if case .unavailable(let message) = sessions.connection {
        return "暂时无法核对电脑端状态。" + message
    }
    if !sessions.canSubmit, case .accepted = sessions.submission { return "输入已接受，正在核对轮次状态。" }
    return nil
}
