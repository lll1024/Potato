import XCTest

@MainActor
final class NavigationTests: XCTestCase {
    func testRestartRecoversOriginalSubmissionAndOpensSavedConversation() async throws {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18492")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "重启核对原提交")
        app.buttons["send-input"].tap()
        XCTAssertTrue(app.staticTexts["正在发送，等待电脑确认接受。"].waitForExistence(timeout: 5))
        app.terminate()
        app.launch()
        let recovered = app.buttons["open-recovered-submission"]
        XCTAssertTrue(recovered.waitForExistence(timeout: 10))
        attach(app, name: "重启核对并恢复原提交")
        var delay = URLRequest(url: URL(string: "https://localhost:18492/__test__/delay-history")!)
        delay.httpMethod = "POST"
        _ = try await URLSession.shared.data(for: delay)
        app.buttons["open-recovered-submission"].tap()
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(app.navigationBars["新建会话"].waitForExistence(timeout: 5))
        let noReplacement = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == true"),
            object: app.staticTexts["你的输入：重启核对原提交"])
        noReplacement.isInverted = true
        await fulfillment(of: [noReplacement], timeout: 6)
        app.navigationBars.buttons["助手会话"].tap()
        recovered.tap()
        XCTAssertTrue(app.staticTexts["你的输入：重启核对原提交"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["回答全文结尾"].waitForExistence(timeout: 10))
        XCTAssertEqual(input.value as? String, "")
        attach(app, name: "原会话完整回答")
        app.navigationBars.buttons["助手会话"].tap()
        let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18492/api/sessions")!)
        let list = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let rows = try XCTUnwrap(list["sessions"] as? [[String: Any]])
        let original = try XCTUnwrap(rows.first { $0["title"] as? String == "重启核对原提交" })
        let sessionID = try XCTUnwrap(original["session_id"] as? String)
        var deleting = URLRequest(url: URL(string: "https://localhost:18492/api/sessions/" + sessionID)!)
        deleting.httpMethod = "DELETE"
        _ = try await URLSession.shared.data(for: deleting)
        app.buttons["open-recovered-submission"].tap()
        XCTAssertTrue(app.staticTexts["会话不存在，可能已在电脑端删除。请返回列表刷新。"].waitForExistence(timeout: 5))
    }

    func testLongRetainedInputKeepsRecoveryActionsAndComposerReachable() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18492")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        let original = "长原文核对" + String(repeating: "杭州两日旅行，保留完整要求。", count: 20) + "原文结尾"
        replaceInput(input, text: original)
        app.buttons["send-input"].tap()
        let disclosure = app.buttons["保留的待确认输入"]
        XCTAssertTrue(disclosure.waitForExistence(timeout: 5))
        disclosure.tap()
        let check = app.buttons["check-submission"]
        for _ in 0..<5 {
            if check.isHittable { break }
            app.scrollViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(check.isHittable)
        XCTAssertTrue(app.buttons["retry-submission"].isHittable)
        XCTAssertTrue(input.isHittable)
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label == %@", original)).firstMatch.exists)
        attach(app, name: "长原文展开与恢复操作")
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["open-recovered-submission"].waitForExistence(timeout: 10))
    }

    func testLateAcceptanceClearsOnlyOldDraftOnReopenedNewPage() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18492")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "延迟接受时保留草稿")
        app.buttons["send-input"].tap()
        app.navigationBars.buttons["助手会话"].tap()
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "延迟接受时保留草稿")
        let cleared = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == ''"), object: input)
        wait(for: [cleared], timeout: 12)
        XCTAssertFalse(app.staticTexts["草稿已保存到本机。"].exists)
    }

    func testOfflineDraftCanBeEditedAndRestoredWithoutSending() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18491")
        XCTAssertTrue(app.staticTexts["连接不可用"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "offline-draft")
        XCTAssertTrue(app.staticTexts["草稿已保存到本机。"].exists)
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["new-conversation"].waitForExistence(timeout: 5))
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "offline-draft")
        replaceInput(input, text: "offline-draft-edited")
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        input.typeText("\n")
        app.swipeDown()
        attach(app, name: "离线草稿与禁止发送")
        replaceInput(input, text: "")
    }

    func testLaterInputFollowsNewAcceptedConversationAndSurvivesReopen() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18492")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "延迟接受时保留草稿")
        app.buttons["send-input"].tap()
        input.tap()
        input.typeText("later-draft")
        let editedInput = input.value as? String
        XCTAssertTrue(editedInput?.contains("later-draft") == true)
        XCTAssertTrue(app.staticTexts["输入已接受。"].waitForExistence(timeout: 12))
        XCTAssertEqual(input.value as? String, editedInput)
        app.navigationBars.buttons["助手会话"].tap()
        let session = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "延迟接受时保留草稿")).firstMatch
        XCTAssertTrue(session.waitForExistence(timeout: 10))
        session.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, editedInput)
        replaceInput(input, text: "")
    }

    func testDraftsSurviveNavigationReleaseAndAppRestart() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18489")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["open-session"].firstMatch.tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "existing-draft")
        app.navigationBars.buttons["助手会话"].tap()
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "new-draft")
        app.navigationBars.buttons["助手会话"].tap()
        app.buttons["open-session"].firstMatch.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "existing-draft")
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["open-session"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["open-session"].firstMatch.tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "existing-draft")
        replaceInput(input, text: "")
        app.navigationBars.buttons["助手会话"].tap()
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertEqual(input.value as? String, "new-draft")
        attach(app, name: "恢复的新会话草稿")
        replaceInput(input, text: "")
    }

    func testCrossDeviceStopSurvivesNavigationAndBackgroundCompletion() async throws {
        let address = "https://localhost:18494"
        func request(_ path: String, method: String = "POST", body: [String: String]? = nil) async throws -> [String: Any] {
            var request = URLRequest(url: URL(string: address + path)!)
            request.httpMethod = method
            if let body {
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.httpBody = try JSONSerialization.data(withJSONObject: body)
            }
            let (data, response) = try await URLSession.shared.data(for: request)
            XCTAssertTrue([200, 202].contains((response as? HTTPURLResponse)?.statusCode ?? 0))
            return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        }
        _ = try await request("/__test__/arm")
        let first = try await request("/api/turns", body: ["input": "跨设备状态交互"])
        let sessionID = try XCTUnwrap(first["session_id"] as? String)
        _ = try await request("/__test__/entered", method: "GET")
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: address)
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        XCTAssertTrue(app.buttons["stop-turn"].waitForExistence(timeout: 5))
        let row = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "跨设备状态交互")).firstMatch
        row.tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        replaceInput(input, text: "手机草稿")
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        app.buttons["stop-turn"].tap()
        let stopping = app.staticTexts["正在停止，等待外部调用和保存收束。输入不会自动排队。"]
        XCTAssertTrue(stopping.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        attach(app, name: "对话等待真实停止收束")
        app.navigationBars.buttons["助手会话"].tap()
        XCTAssertTrue(stopping.waitForExistence(timeout: 5))
        attach(app, name: "列表保持全局停止状态")
        _ = try await request("/__test__/release")
        row.tap()
        XCTAssertTrue(app.staticTexts["本轮已停止。"].waitForExistence(timeout: 10))
        let sendReady = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: app.buttons["send-input"])
        await fulfillment(of: [sendReady], timeout: 5)
        _ = try await request("/__test__/arm")
        _ = try await request("/api/turns", body: ["input": "锁屏期间保存完整回答", "session_id": sessionID])
        _ = try await request("/__test__/entered", method: "GET")
        XCTAssertTrue(app.buttons["stop-turn"].waitForExistence(timeout: 10))
        XCUIDevice.shared.press(.home)
        _ = try await request("/__test__/release")
        for _ in 0..<100 {
            let snapshot = try await request("/api/snapshot", method: "GET")
            if snapshot["active_turn_id"] is NSNull { break }
            try await Task.sleep(for: .milliseconds(20))
        }
        app.activate()
        XCTAssertTrue(app.staticTexts["回答全文结尾"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["你的输入：锁屏期间保存完整回答"].exists)
        XCTAssertEqual(input.value as? String, "手机草稿")
        attach(app, name: "返回前台补齐完整回答")
        _ = try await request("/__test__/arm")
        _ = try await request("/api/turns", body: ["input": "长回答之后停止", "session_id": sessionID])
        _ = try await request("/__test__/entered", method: "GET")
        XCTAssertTrue(app.buttons["stop-turn"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["stop-turn"].isHittable)
        attach(app, name: "长历史底部仍可停止")
        app.buttons["stop-turn"].tap()
        XCTAssertTrue(stopping.waitForExistence(timeout: 5))
        _ = try await request("/__test__/release")
        let readyAgain = XCTNSPredicateExpectation(predicate: NSPredicate(format: "enabled == true"), object: app.buttons["send-input"])
        await fulfillment(of: [readyAgain], timeout: 10)
        replaceInput(input, text: "")
    }

    private func replaceInput(_ field: XCUIElement, text: String) {
        field.tap()
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.95, dy: 0.5)).tap()
        let old = field.value as? String ?? ""
        let count = old == "输入旅行需求" ? 0 : old.count
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: count) + text)
    }

    func testCreateSendContinueAndReadSavedAnswer() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        configure(app, address: "https://localhost:18492")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        app.buttons["new-conversation"].tap()
        XCTAssertTrue(app.staticTexts["开始一次旅行讨论"].waitForExistence(timeout: 5))
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        input.tap()
        input.typeText("手机新建杭州旅行")
        app.buttons["send-input"].tap()
        XCTAssertTrue(app.staticTexts["输入已接受。"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["回答全文结尾"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["已读取共同偏好：不吃辣。"].exists)
        revealInput(app, text: "手机新建杭州旅行")
        attach(app, name: "手机新建与已保存回答")
        input.tap()
        input.typeText("手机续聊调整第二天")
        app.buttons["send-input"].tap()
        XCTAssertTrue(app.staticTexts["你的输入：手机续聊调整第二天"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts.matching(identifier: "回答全文结尾").element(boundBy: 1).waitForExistence(timeout: 10))
        revealInput(app, text: "手机续聊调整第二天")
        attach(app, name: "续聊完整回答")
        app.navigationBars.buttons["助手会话"].tap()
        XCTAssertTrue(app.buttons["open-session"].firstMatch.waitForExistence(timeout: 10))
        app.buttons["new-conversation"].tap()
        input.tap()
        input.typeText("延迟接受时切换会话")
        app.buttons["send-input"].tap()
        app.navigationBars.buttons["助手会话"].tap()
        app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "手机新建杭州旅行")).firstMatch.tap()
        let originalInput = app.staticTexts["你的输入：手机新建杭州旅行"]
        XCTAssertTrue(originalInput.waitForExistence(timeout: 10))
        let noLongerSending = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"),
            object: app.staticTexts["正在发送，等待电脑确认接受。"])
        wait(for: [noLongerSending], timeout: 10)
        let replacedPage = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: originalInput)
        replacedPage.isInverted = true
        wait(for: [replacedPage], timeout: 3)
    }

    private func revealInput(_ app: XCUIApplication, text: String) {
        let input = app.staticTexts["你的输入：" + text]
        for _ in 0..<8 {
            if input.isHittable { return }
            app.scrollViews.firstMatch.swipeDown()
        }
        XCTAssertTrue(input.isHittable)
    }

    func testConnectReadReturnEmptyAndUnavailable() {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.buttons["connection-settings"].waitForExistence(timeout: 5))
        configure(app, address: "https://localhost:18489")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        let session = app.buttons["open-session"].firstMatch
        XCTAssertTrue(session.waitForExistence(timeout: 10))
        attach(app, name: "会话列表")
        session.tap()
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "输入全文结尾"))
            .firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["回答全文结尾"].exists)
        attach(app, name: "完整对话")
        app.navigationBars.buttons["助手会话"].tap()
        XCTAssertTrue(session.waitForExistence(timeout: 5))
        app.terminate()
        app.launch()
        XCTAssertTrue(session.waitForExistence(timeout: 10))
        configure(app, address: "https://localhost:18490")
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()
        XCTAssertTrue(app.staticTexts["还没有助手会话"].waitForExistence(timeout: 10))
        attach(app, name: "空列表")
        configure(app, address: "https://localhost:18491")
        XCTAssertTrue(app.staticTexts["连接不可用"].waitForExistence(timeout: 10))
        attach(app, name: "连接不可用")
        app.buttons["done-connection"].tap()
        XCTAssertFalse(session.exists)
        XCTAssertFalse(app.staticTexts["还没有助手会话"].exists)
        app.buttons["connection-settings"].tap()
        app.buttons["clear-connection"].tap()
        XCTAssertTrue(app.staticTexts["尚未配置"].exists)
        app.buttons["done-connection"].tap()
        XCTAssertTrue(app.staticTexts["连接你的旅行助手"].waitForExistence(timeout: 5))
    }

    private func configure(_ app: XCUIApplication, address: String) {
        app.buttons["connection-settings"].tap()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        let old = field.value as? String ?? ""
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count) + address + "\n")
        app.buttons["save-connection"].tap()
    }

    private func attach(_ app: XCUIApplication, name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
