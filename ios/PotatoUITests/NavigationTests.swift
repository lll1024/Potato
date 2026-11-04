import XCTest

@MainActor
final class NavigationTests: XCTestCase {
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
