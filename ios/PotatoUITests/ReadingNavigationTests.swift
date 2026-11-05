import XCTest

@MainActor
final class ReadingNavigationTests: XCTestCase {
    func testPaginationExternalLinkAndFullCopy() async throws {
        let app = connectedApp()

        let more = app.buttons["load-more-sessions"]
        for _ in 0..<12 {
            if more.isHittable { break }
            app.swipeUp(velocity: .fast)
        }
        XCTAssertTrue(more.isHittable)
        more.tap()
        let oldest = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "阅读样例会话 01")).firstMatch
        for _ in 0..<6 {
            if oldest.isHittable { break }
            app.swipeUp(velocity: .fast)
        }
        XCTAssertTrue(oldest.isHittable)
        attach(app, "完整会话列表分页")
        for _ in 0..<12 {
            let reading = app.buttons.matching(identifier: "open-session")
                .matching(NSPredicate(format: "label CONTAINS %@", "杭州两日旅行阅读验收第1轮")).firstMatch
            if reading.isHittable { reading.tap(); break }
            app.swipeDown(velocity: .fast)
        }
        let copies = app.buttons.matching(identifier: "copy-answer")
        XCTAssertTrue(copies.firstMatch.waitForExistence(timeout: 10))
        let copy = copies.element(boundBy: copies.count - 1)
        for _ in 0..<4 { if copy.isHittable { break }; app.swipeUp(velocity: .fast) }
        XCTAssertTrue(copy.isHittable)
        attach(app, "中文完整回答与链接")
        copy.tap()
        XCTAssertEqual(copy.label, "已复制完整回答")
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap()
        input.press(forDuration: 1.2)
        let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: 5))
        paste.tap()
        let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18495/api/sessions")!)
        let list = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let rows = try XCTUnwrap(list["sessions"] as? [[String: Any]])
        let reading = try XCTUnwrap(rows.first { ($0["title"] as? String)?.hasPrefix("杭州两日旅行阅读验收第1轮") == true })
        let id = try XCTUnwrap(reading["session_id"] as? String)
        let (history, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18495/api/sessions/" + id + "?summary=false&include_messages=false")!)
        let page = try XCTUnwrap(JSONSerialization.jsonObject(with: history) as? [String: Any])
        let turns = try XCTUnwrap(page["turns"] as? [[String: Any]])
        let answer = try XCTUnwrap(turns.last?["answer"] as? String)
        XCTAssertEqual(input.value as? String, answer)
        attach(app, "复制全文并粘贴核对")
        // 留空草稿；不发送复制的回答。
        input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: answer.count))
        app.swipeDown()

        let links = app.links.matching(identifier: "查看预约说明")
        let link = links.element(boundBy: links.count - 1)
        for _ in 0..<5 { if link.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(link.isHittable)
        link.tap()
        let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
        XCTAssertTrue(safari.wait(for: .runningForeground, timeout: 10))
        XCTAssertTrue(safari.staticTexts["预约入口样例"].waitForExistence(timeout: 10))
        attach(safari, "系统浏览器打开预约入口")
        app.activate()
        app.buttons["earlier-history"].tap()
        // 通过原生入口回到已读范围开头，再实际点击继续加载。
        let earlier = app.buttons["load-earlier-turns"]
        XCTAssertTrue(earlier.waitForExistence(timeout: 5))
        XCTAssertTrue(earlier.isHittable)
        earlier.tap()
        let firstInput = app.staticTexts["你的输入：杭州两日旅行阅读验收第1轮：保留完整输入。"]
        let finishedLoading = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: earlier)
        await fulfillment(of: [finishedLoading], timeout: 10)
        app.buttons["earlier-history"].tap()
        XCTAssertTrue(firstInput.isHittable)
        XCTAssertFalse(earlier.exists)
        attach(app, "旧页完整输入")
        app.navigationBars.buttons["助手会话"].tap()
        XCTAssertTrue(app.navigationBars["助手会话"].exists)
    }

    func testChineseTableScrollsToLastColumn() {
        let app = connectedApp()
        let reading = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "杭州两日旅行阅读验收第1轮")).firstMatch
        XCTAssertTrue(reading.waitForExistence(timeout: 10))
        reading.tap()
        XCTAssertTrue(app.buttons["earlier-history"].waitForExistence(timeout: 10))
        app.buttons["earlier-history"].tap()
        let vertical = app.scrollViews.firstMatch
        let table = app.scrollViews["answer-table"].firstMatch
        XCTAssertTrue(table.waitForExistence(timeout: 10))
        // 先把整个表头移到阅读区，避免在底部输入栏附近合成横向手势。
        for _ in 0..<8 {
            if table.frame.minY < app.frame.height * 0.45 { break }
            vertical.coordinate(withNormalizedOffset: CGVector(dx: 0.45, dy: 0.75))
                .press(forDuration: 0.2, thenDragTo: vertical.coordinate(withNormalizedOffset: CGVector(dx: 0.45, dy: 0.55)))
        }
        XCTAssertTrue(table.isHittable)
        for _ in 0..<4 { table.swipeLeft(velocity: .fast) }
        let reminder = table.staticTexts["预约及交通提醒"]
        XCTAssertTrue(reminder.exists)
        let headingPoint = CGPoint(x: reminder.frame.midX, y: reminder.frame.midY)
        XCTAssertTrue(table.frame.contains(headingPoint))
        XCTAssertTrue(app.frame.contains(headingPoint))
        attach(app, "横向表格末列")
    }

    func testAnswerReallyGrowsBeforeCompletionAndThenBecomesSavedHistory() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("手机界面实时旅行")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        let heading = app.staticTexts["实时杭州旅行"]
        XCTAssertTrue(heading.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["正在生成 · 尚未保存"].exists)
        XCTAssertTrue(app.buttons["copy-answer"].label == "复制当前正文")
        XCTAssertFalse(app.staticTexts["上午游览西湖。"].exists)
        attach(app, "第一批正文尚未返回最终响应")
        XCUIDevice.shared.press(.home)
        try await streamControl("step")
        app.activate()
        XCTAssertTrue(app.staticTexts["上午游览西湖。"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["正在生成 · 尚未保存"].exists)
        attach(app, "恢复前台取得第二批累计正文且仍未保存")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["下午参观博物馆。"].waitForExistence(timeout: 5))
        XCUIDevice.shared.press(.home)
        try await streamControl("step")
        app.activate()
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"),
            object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        XCTAssertFalse(app.staticTexts["正在生成 · 尚未保存"].exists)
        attach(app, "完成保存后成为完整回答")
        app.navigationBars.buttons["助手会话"].tap()
        let session = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "手机界面实时旅行")).firstMatch
        XCTAssertTrue(session.waitForExistence(timeout: 5))
        session.tap()
        XCTAssertTrue(app.staticTexts["下午参观博物馆。"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["copy-answer"].label, "复制完整回答")
    }

    private func streamControl(_ action: String, method: String = "POST") async throws {
        var request = URLRequest(url: URL(string: "https://localhost:18497/__test__/" + action)!)
        request.httpMethod = method
        let (_, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }

    private func connectedApp(address: String = "https://localhost:18495") -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        app.buttons["connection-settings"].tap()
        let field = app.textFields["server-address"]
        XCTAssertTrue(field.waitForExistence(timeout: 5))
        field.tap()
        let old = field.value as? String ?? ""
        field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: old.count) + address + "\n")
        app.buttons["save-connection"].tap()
        XCTAssertTrue(app.staticTexts["已连接"].waitForExistence(timeout: 10))
        app.buttons["done-connection"].tap()

        return app
    }

    private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
