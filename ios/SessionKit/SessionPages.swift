import Foundation

/// 保存实际读过的连续区间，缺口的继续入口始终取自后端 next_before。
struct HistoryWindow: Equatable, Sendable {
    let first: Int
    let last: Int
    let before: Int?

    static func page(ordinals: [Int], before: Int?) -> [Self] {
        guard let first = ordinals.min(), let last = ordinals.max() else { return [] }
        return [Self(first: first, last: last, before: before)]
    }

    static func merge(_ windows: [Self]) -> [Self] {
        var result: [Self] = []
        for window in windows.sorted(by: { $0.first < $1.first }) {
            if let previous = result.last, window.first <= previous.last + 1 {
                result[result.count - 1] = Self(first: previous.first, last: max(previous.last, window.last),
                                              before: previous.before)
            } else { result.append(window) }
        }
        return result
    }
}

extension ConversationPage {
    func merging(_ page: Self, earlier: Bool = false) -> Self {
        guard streamID == page.streamID, session.id == page.session.id else { return page }
        let stale = page.cursor != nil && cursor != nil && page.cursor! < cursor!
        let keepCurrent = earlier || stale
        var rows: [String: ConversationTurn] = [:]
        for turn in turns { rows[turn.id] = turn }
        for turn in page.turns {
            if let existing = rows[turn.id] {
                if let old = existing.observedAt, let new = turn.observedAt {
                    if new < old || (earlier && new == old) { continue }
                } else if keepCurrent { continue }
            }
            rows[turn.id] = turn
        }
        // 身份去重；旧协议夹具未提供 ordinal 时沿用原顺序。
        var seen: Set<String> = []
        let order = (earlier ? page.turns + turns : turns + page.turns).filter { seen.insert($0.id).inserted }
        let merged = order.compactMap { rows[$0.id] }.sorted {
            guard let lhs = $0.ordinal, let rhs = $1.ordinal else { return false }
            return lhs < rhs
        }
        let coverage = HistoryWindow.merge(windows + page.windows)
        return Self(session: keepCurrent ? session : page.session, turns: merged,
                    nextBefore: coverage.last?.before ?? (coverage.isEmpty ? nextBefore : nil),
                    streamID: streamID, cursor: stale ? cursor : page.cursor, windows: coverage)
    }
}
