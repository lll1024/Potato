import SessionKit
import SnapshotTesting
import SwiftUI
import XCTest
@testable import Potato

@MainActor
final class SnapshotTests: XCTestCase {
    func testChineseAnswer() throws { try assertReading(dark: false, large: false) }
    func testChineseTableDarkLargeText() throws { try assertReading(dark: true, large: true) }
    func testConversationLoading() async throws { try await assertConversationRead(missing: false) }
    func testConversationDeleted() async throws { try await assertConversationRead(missing: true) }

    private func assertConversationRead(missing: Bool, file: StaticString = #filePath,
                                         testName: String = #function, line: UInt = #line) async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        await fixture.enqueue(body: "{\"schema_version\":1,\"stream_id\":\"dataset\",\"accepting\":true,\"stopping\":false}")
        await fixture.enqueue(body: "{\"schema_version\":1,\"sessions\":[{\"session_id\":\"reading\",\"title\":\"杭州旅行\",\"updated_at\":\"now\"}]}")
        let list = try await sessions.refresh()
        let session = try XCTUnwrap(list.sessions.first)
        if missing {
            sessions.watchConversation(sessionID: session.id)
            await fixture.enqueue(status: 404, body: "{}")
            _ = try? await sessions.history(sessionID: session.id)
            await fixture.enqueue(status: 404, body: "{}")
        }
        let controller = UIHostingController(rootView: NavigationStack {
            ConversationView(session: session, sessions: sessions, router: ConversationRouter(parent: SessionListRouter()))
        }.environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, .large).preferredColorScheme(.light))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
            as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true, precision: missing ? 1 : 0.995)),
            named: missing ? "deleted-light" : "loading-light", file: file, testName: testName, line: line)
        if !missing { await fixture.next().reply(status: 404, body: "{}") }
    }

    private func assertReading(dark: Bool, large: Bool, file: StaticString = #filePath,
                               testName: String = #function, line: UInt = #line) throws {
        let url = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "reading-answer", withExtension: "md"))
        let full = try String(contentsOf: url, encoding: .utf8)
        let text = large ? "## 两日安排对照\n\n" + full.components(separatedBy: "### 两日安排对照\n\n")[1] : full
        let controller = UIHostingController(rootView: ScrollView { AnswerView(text: text).padding(20) }
            .environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, large ? .accessibility3 : .large)
            .preferredColorScheme(dark ? .dark : .light))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
            as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true,
                traits: UITraitCollection(userInterfaceStyle: dark ? .dark : .light))),
            named: large ? "table-dark-accessibility3" : "chinese-answer-light",
            file: file, testName: testName, line: line)
    }

    func testStorageFailureStaysVisibleBesideUnknownSubmission() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let load = Task { try await sessions.refresh() }
        await fixture.next().reply(body: "{\"schema_version\":1,\"stream_id\":\"dataset\",\"accepting\":true,\"stopping\":false}")
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await load.value
        let send = Task { try await sessions.submit(input: "保留未知提交", sessionID: nil) }
        await fixture.next().fail()
        _ = try? await send.value
        let refresh = Task { try await sessions.refresh() }
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"dataset","accepting":false,"stopping":false,
             "storage_error":"轮次结果未保存","unsaved_fact":{"kind":"turn.finished","turn_id":"turn"}}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        await fixture.next().fail()
        _ = try await refresh.value
        XCTAssertEqual(sessions.submission, .unknown)
        XCTAssertFalse(sessions.canSubmit)
        let list = SessionListPresenter(interactor: SessionListInteractor(sessions: sessions), router: SessionListRouter())
        let conversation = ConversationPresenter(session: nil, interactor: ConversationInteractor(sessions: sessions),
            router: ConversationRouter(parent: SessionListRouter()))
        XCTAssertNotNil(list.recovery.message)
        XCTAssertNotNil(conversation.recovery.message)
        XCTAssertEqual(list.serviceMessage, "轮次结果未保存")
        XCTAssertEqual(conversation.serviceMessage, "轮次结果未保存")
    }

    func testUnconfirmedConversationDarkLargeText() async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let connecting = Task { try await sessions.refresh() }
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"dataset","accepting":true,"stopping":false}
            """)
        await fixture.next().reply(body: "{\"schema_version\":1,\"sessions\":[]}")
        _ = try await connecting.value
        let sending = Task { try await sessions.submit(input: "杭州两日，保留原始旅行要求", sessionID: nil) }
        await fixture.next().fail()
        _ = try? await sending.value
        let controller = UIHostingController(rootView: NavigationStack {
            ConversationView(session: nil, sessions: sessions, router: ConversationRouter(parent: SessionListRouter()))
        }.environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, .accessibility3).preferredColorScheme(.dark))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
            as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true,
                                        traits: UITraitCollection(userInterfaceStyle: .dark))),
            named: "unconfirmed-dark-accessibility3")
    }

    func testDesktopRunning() async throws { try await assertExecutionSnapshot(stopping: false) }
    func testStoppingDarkLargeText() async throws { try await assertExecutionSnapshot(stopping: true) }

    private func assertExecutionSnapshot(stopping: Bool, file: StaticString = #filePath,
                                         testName: String = #function, line: UInt = #line) async throws {
        let directory = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        let (sessions, fixture, _) = try await HTTPFixture.client(databaseURL: directory.appending(path: "client.sqlite"))
        let load = Task { try await sessions.refresh() }
        await fixture.next().reply(body: """
            {"schema_version":1,"stream_id":"dataset","active_session_id":"desktop-session",
             "active_turn_id":"desktop-turn","accepting":true,"stopping":false}
            """)
        await fixture.next().reply(body: """
            {"schema_version":1,"sessions":[{"session_id":"desktop-session","title":"杭州两日旅行","updated_at":"now"}]}
            """)
        _ = try await load.value
        if stopping {
            let target = try XCTUnwrap(sessions.stopTarget)
            let stop = Task { try await sessions.stop(target: target) }
            await fixture.next().reply(body: """
                {"schema_version":1,"turn_id":"desktop-turn","status":"running","stopping":true}
                """)
            try await stop.value
        }
        let controller = UIHostingController(rootView: SessionListView(sessions: sessions)
            .environment(\.locale, Locale(identifier: "zh_CN"))
            .environment(\.dynamicTypeSize, stopping ? .accessibility3 : .large)
            .preferredColorScheme(stopping ? .dark : .light))
        let window = UIWindow(frame: CGRect(x: 0, y: 0, width: 390, height: 844))
        window.rootViewController = controller
        window.makeKeyAndVisible()
        defer { window.isHidden = true; window.rootViewController = nil }
        assertSnapshot(of: controller,
            as: .wait(for: 1, on: .image(on: .iPhone13, drawHierarchyInKeyWindow: true,
                traits: UITraitCollection(userInterfaceStyle: stopping ? .dark : .light))),
            named: stopping ? "stopping-dark-accessibility3" : "desktop-running-light",
            file: file, testName: testName, line: line)
    }

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
