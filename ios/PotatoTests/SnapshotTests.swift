import SessionKit
import SnapshotTesting
import SwiftUI
import XCTest
@testable import Potato

@MainActor
final class SnapshotTests: XCTestCase {
    // 截图工具可能短暂保留视图；临时数据库随测试容器清理，不删除仍打开的文件。
    func testEmptySessions() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let sessions = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        try sessions.configure(address: "https://localhost:18490")
        let controller = UIHostingController(rootView: SessionListView(sessions: sessions)
            .environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, .large)
            .preferredColorScheme(.light))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
                       as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true)),
                       named: "empty-sessions-light")
    }

    func testUnconfiguredDarkLargeText() throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let sessions = try SharedSessions(databaseURL: directory.appending(path: "client.sqlite"))
        let controller = UIHostingController(rootView: SessionListView(sessions: sessions)
            .environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, .accessibility3)
            .preferredColorScheme(.dark))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
                       as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true,
                                                   traits: UITraitCollection(userInterfaceStyle: .dark))),
                       named: "unconfigured-dark-accessibility3")
    }
}
