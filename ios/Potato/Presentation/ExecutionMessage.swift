import SessionKit

struct ExecutionState {
    let submission: SubmissionState
    let service: ServiceState?
    let connection: ConnectionState
    let canSubmit: Bool
    let stopState: StopState

    @MainActor init(_ sessions: any SessionAccess) {
        submission = sessions.submission
        service = sessions.service
        connection = sessions.connection
        canSubmit = sessions.canSubmit
        stopState = sessions.stopState
    }
}

func executionMessage(_ sessions: ExecutionState) -> String? {
    if case .unavailable(let message) = sessions.connection {
        return "暂时无法核对电脑端状态，电脑上的轮次可能仍在执行。" + message
    }
    if let service = sessions.service, service.storageError != nil || service.unsavedFact != nil {
        return service.storageError ?? "后端存在未保存的执行事实，暂不能确认结果。"
    }
    switch sessions.stopState {
    case .requesting: return "正在发送停止请求，等待电脑端确认。"
    case .waiting: return "正在停止，等待外部调用和保存收束。输入不会自动排队。"
    case .uncertain: return SessionFailure.stopUnknown.localizedDescription
    case .idle: break
    }
    if sessions.submission == .sending { return "正在发送，等待电脑确认接受。" }
    if let service = sessions.service {
        if service.stopping { return "正在停止，等待电脑端收束。输入不会自动排队。" }
        if !service.accepting { return SessionFailure.serviceUnavailable.localizedDescription }
        if service.activeTurnID != nil { return "电脑端正在处理，回答将在保存后显示。输入不会自动排队。" }
    }
    if !sessions.canSubmit, case .accepted = sessions.submission { return "输入已接受，正在核对轮次状态。" }
    return nil
}
