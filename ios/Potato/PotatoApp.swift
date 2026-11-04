import FactoryKit
import SessionKit
import SwiftUI

@main
struct PotatoApp: App {
    private let sessions = Container.shared.sessions()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            switch sessions {
            case .success(let sessions):
                SessionListView(sessions: sessions)
                    .onChange(of: scenePhase, initial: true) {
                        sessions.setForeground(scenePhase == .active)
                    }
            case .failure:
                ContentUnavailableView("无法打开本机配置", systemImage: "externaldrive.badge.exclamationmark",
                                       description: Text("请检查 iPhone 可用存储后重新打开 App。"))
            }
        }
    }
}
