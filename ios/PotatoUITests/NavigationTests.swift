import XCTest

@MainActor
final class NavigationTests: XCTestCase {
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
