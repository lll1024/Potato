import Foundation
import Observation
import SessionKit

@MainActor
struct ConversationInteractor {
    let sessions: any SessionAccess
    var page: ConversationPage? { sessions.conversation }
    var answerPreview: AnswerPreview? { sessions.answerPreview }
    var progressPreview: ProgressPreview? { sessions.progressPreview }
    var answerFailure: SessionFailure? { sessions.answerFailure }
    var canSubmit: Bool { sessions.canSubmit }
    var execution: ExecutionState { ExecutionState(sessions) }
    var stopTarget: StopTarget? { sessions.stopTarget }
    func stop(target: StopTarget) async {
        try? await sessions.stop(target: target)
        _ = try? await sessions.refresh()
    }
    var readFailure: SessionFailure? { sessions.conversationFailure }
    var recovery: SubmissionRecoveryPresentation { SubmissionRecoveryPresentation(sessions) }
    func checkSubmission() async { try? await sessions.reconcileSubmission() }
    func retrySubmission() async { _ = try? await sessions.retrySubmission() }
    var connection: ConnectionState { sessions.connection }
    var submission: SubmissionState { sessions.submission }
    func canSubmit(draft: SessionDraft) -> Bool { sessions.canSubmit(draft: draft) }
    func draft(sessionID: String?) throws -> SessionDraft { try sessions.draft(sessionID: sessionID) }
    func save(input: String, draft: SessionDraft, sessionID: String?) throws -> SessionDraft {
        if let sessionID, draft.scope.sessionID != sessionID {
            return try sessions.moveDraft(draft, input: input, to: sessionID)
        }
        return try sessions.saveDraft(input: input, scope: draft.scope)
    }
    func watch(sessionID: String?) { sessions.watchConversation(sessionID: sessionID) }
    func load(sessionID: String) async throws -> ConversationPage {
        try await sessions.history(sessionID: sessionID)
    }
    var isLoadingEarlier: Bool { sessions.isLoadingEarlierTurns }
    var earlierFailure: SessionFailure? { sessions.earlierTurnsFailure }
    func loadEarlier(sessionID: String) async { _ = try? await sessions.loadEarlierTurns(sessionID: sessionID) }
    func send(draft: SessionDraft) async throws -> SubmissionReceipt {
        try await sessions.submit(draft: draft)
    }
    func refresh() async { _ = try? await sessions.refresh() }
}

enum ConversationContent {
    case loading
    case loaded(ConversationPresentation)
    case failed(String)
}

struct ConversationPresentation {
    struct Turn: Identifiable {
        let id: String
        let input: String
        let answer: String?
        let statusMessage: String?
        let isPreview: Bool
        let isUnsaved: Bool
        let isIncomplete: Bool
        let progress: [ProgressEntry]
        let hasAnswerStarted: Bool
        let isCurrentProgress: Bool
        var answerIdentity: String? = nil
        var progressIdentity = ""
        var isGenerating = false
        var isStopped = false
        var contentRecoveryRevision = 0
    }
    let title: String
    let turns: [Turn]
    let hasEarlierTurns: Bool
}

@MainActor @Observable
final class ConversationPresenter {
    let router: ConversationRouter
    private let interactor: ConversationInteractor
    private var sessionID: String?
    private var initialTitle: String
    private var readError: String?
    private var isVisible = false
    private(set) var sendMessage: String?
    private var inputText = ""
    private var savedDraft: SessionDraft?
    private(set) var draftError: String?
    var input: String {
        get { inputText }
        set { inputText = newValue; saveDraft() }
    }
    var canEditDraft: Bool { savedDraft != nil }
    var draftMessage: String? { draftError ?? (inputText.isEmpty ? nil : "草稿已保存到本机。") }
    var submissionState: SubmissionState { interactor.submission }
    var serviceMessage: String? {
        if let savedDraft, !savedDraft.scope.streamID.isEmpty,
           let service = interactor.execution.service, savedDraft.scope.streamID != service.streamID {
            return "当前草稿属于此前的数据集，已保留在本机。请返回列表后重新打开会话。"
        }
        let message = executionMessage(interactor.execution)
        switch interactor.connection {
        case .unconfigured: return "请先配置服务。草稿可继续编辑，暂时不能发送。"
        case .connecting: return "正在连接服务。草稿可继续编辑，暂时不能发送。"
        case .unavailable: return (message ?? "连接不可用。") + " 当前无法在线发送，草稿可继续编辑。"
        case .connected: return message
        }
    }
    var recovery: SubmissionRecoveryPresentation { interactor.recovery }
    var stopTarget: StopTarget? { interactor.stopTarget }
    func stop(target: StopTarget) async { await interactor.stop(target: target) }
    func checkSubmission() async { await interactor.checkSubmission() }
    func retrySubmission() async { await interactor.retrySubmission() }
    func openSubmission() async {
        guard let accepted = recovery.accepted else { return }
        let navigation = router.navigationRevision
        do {
            let page = try await interactor.load(sessionID: accepted.sessionID)
            guard recovery.accepted == accepted, isVisible, router.navigationRevision == navigation else { return }
            router.open(page.session)
        } catch {
            guard isVisible, router.navigationRevision == navigation else { return }
            sendMessage = error.localizedDescription
        }
    }
    var canSend: Bool {
        guard let savedDraft, draftError == nil, !input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return false }
        return interactor.canSubmit(draft: savedDraft)
    }
    var title: String { currentPage?.session.title ?? initialTitle }
    var latestTurnID: String? { currentPage?.turns.last?.id }
    var firstTurnID: String? { currentPage?.turns.first?.id }
    var isLoadingEarlier: Bool { interactor.isLoadingEarlier }
    var earlierMessage: String? { interactor.earlierFailure?.localizedDescription }
    func loadEarlier() async {
        guard isVisible, let sessionID else { return }
        await interactor.loadEarlier(sessionID: sessionID)
    }
    private var currentPage: ConversationPage? {
        if let page = interactor.page, page.session.id == sessionID { return page }
        return nil
    }
    var content: ConversationContent {
        if interactor.readFailure == .http(404) { return .failed(SessionFailure.http(404).localizedDescription) }
        guard let page = currentPage else {
            if let readError { return .failed(readError) }
            return sessionID == nil ? .loaded(ConversationPresentation(title: title, turns: [], hasEarlierTurns: false)) : .loading
        }
        return .loaded(ConversationPresentation(title: page.session.title, turns: page.turns.map { turn in
            let preview = interactor.answerPreview.flatMap { $0.turnID == turn.id && turn.status == "running" ? $0 : nil }
            let execution = interactor.execution
            let isUnsaved = preview != nil && execution.service?.activeTurnID != turn.id
                && execution.service?.unsavedFact?["turn_id"] == turn.id && execution.service?.storageError != nil
            let requestedStop: Bool
            switch execution.stopState {
            case .requesting(let target), .waiting(let target), .uncertain(let target): requestedStop = target.turnID == turn.id
            case .idle: requestedStop = false
            }
            var message: String?
            if let preview {
                if isUnsaved {
                    message = "未保存 · 请复制保留，刷新后可能丢失"
                } else if (execution.service?.stopping == true && execution.service?.activeTurnID == turn.id) || requestedStop {
                    message = "正在停止 · 正文已暂停追加"
                } else if interactor.answerFailure != nil { message = "实时正文连接中断，正在重新连接。当前正文尚未保存。" }
                else if preview.status == "invalidated" { message = "本次正文已失效，等待后端处理。" }
                else if preview.status == "retrying" { message = "正在重试，新尝试将从头作答。" }
                else { message = preview.status == "generated" ? "正在保存回答。" : "正在生成 · 尚未保存" }
            } else { message = statusText(turn) }
            if preview?.delivery == "complete" || turn.answerDelivery == "complete" {
                message = [message, "模型服务不支持流式，改为生成完整回答。"].compactMap { $0 }.joined(separator: "\n")
            }
            return ConversationPresentation.Turn(id: turn.id, input: turn.input,
                answer: preview?.text ?? turn.answer, statusMessage: message, isPreview: preview != nil,
                isUnsaved: isUnsaved, isIncomplete: turn.status != "running" && turn.status != "completed",
                progress: interactor.progressPreview.flatMap { $0.turnID == turn.id ? $0.entries : nil } ?? turn.queryProgress ?? [],
                hasAnswerStarted: !(preview?.text ?? turn.answer ?? "").isEmpty,
                isCurrentProgress: interactor.progressPreview?.turnID == turn.id,
                answerIdentity: preview.map { "\($0.instanceID):\($0.streamID):\($0.sessionID):\($0.turnID):\($0.attemptID)" },
                progressIdentity: interactor.progressPreview.map { "\($0.instanceID):\($0.streamID):\($0.sessionID):\($0.turnID)" } ?? "saved:\(turn.id)",
                isGenerating: preview?.status == "generating" && preview?.delivery != "complete" && !isUnsaved,
                isStopped: (execution.service?.stopping == true && execution.service?.activeTurnID == turn.id) || requestedStop,
                contentRecoveryRevision: interactor.sessions.contentRecoveryRevision)
        }, hasEarlierTurns: page.nextBefore != nil))
    }

    init(session: AssistantSession?, interactor: ConversationInteractor, router: ConversationRouter) {
        sessionID = session?.id
        initialTitle = session?.title ?? "新建会话"
        self.interactor = interactor
        self.router = router
        restoreDraft()
    }

    private func restoreDraft() {
        do {
            let draft = try interactor.draft(sessionID: sessionID)
            savedDraft = draft
            inputText = draft.input
            draftError = nil
        } catch { draftError = error.localizedDescription }
    }

    private func saveDraft() {
        guard let savedDraft else { return }
        do {
            self.savedDraft = try interactor.save(input: inputText, draft: savedDraft, sessionID: sessionID)
            draftError = nil
        } catch { draftError = error.localizedDescription }
    }

    func retryDraft() {
        if savedDraft == nil { restoreDraft() }
        else { saveDraft() }
    }

    func submissionChanged() {
        guard isVisible, draftError == nil, case .accepted = interactor.submission else { return }
        restoreDraft()
    }

    func reload() async {
        guard isVisible else { return }
        interactor.watch(sessionID: sessionID)
        guard let sessionID else { return }
        do {
            _ = try await interactor.load(sessionID: sessionID)
            readError = nil
        }
        catch SessionFailure.obsolete { }
        catch is CancellationError { }
        catch { readError = error.localizedDescription }
    }

    func appear() async {
        isVisible = true
        if draftError == nil { restoreDraft() }
        await reload()
    }
    func disappear() {
        isVisible = false
        if currentPage != nil { interactor.watch(sessionID: nil) }
    }

    func send() async {
        guard canSend, let savedDraft else { return }
        let originalInput = input
        let originalRevision = savedDraft.revision
        sendMessage = nil
        do {
            let receipt = try await interactor.send(draft: savedDraft)
            guard isVisible else { return }
            sessionID = receipt.sessionID
            if input == originalInput, self.savedDraft?.revision == originalRevision, draftError == nil {
                restoreDraft()
            } else if self.savedDraft?.scope.sessionID != sessionID {
                saveDraft()
            }
            sendMessage = "输入已接受。"
            await interactor.refresh()
            await reload()
        } catch {
            sendMessage = error.localizedDescription
        }
    }

    private func statusText(_ turn: ConversationTurn) -> String? {
        if turn.status == "running" { return "电脑端仍在处理，最终作答时将实时显示正文。" }
        if turn.status == "completed" { return nil }
        switch turn.reason {
        case "user_stop": return "本轮已停止。"
        case "service_interrupted": return "服务曾中断，本轮未完成。"
        case "model_error": return "本轮模型请求失败。"
        case "budget", "output_limit": return "本轮达到处理上限，回答可能不完整。"
        default: return "本轮未完成（\(turn.status)）。"
        }
    }
}

@MainActor
struct ConversationRouter {
    let parent: SessionListRouter
    var navigationRevision: Int { parent.navigationRevision }
    func backToList() { parent.backToList() }
    func open(_ session: AssistantSession) { parent.open(session) }
    func configureConnection() { parent.configureConnection() }
}
