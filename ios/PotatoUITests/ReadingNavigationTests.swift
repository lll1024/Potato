import XCTest

@MainActor
final class ReadingNavigationTests: XCTestCase {
    func testCurrentConfiguredModelShowsBatchesBeforeSavedHistory() async throws {
        // 显式启动 live_answer_eval.py 才启用；日常回归不调用真实模型。
        let base = "https://localhost:18499"
        guard let (metadata, response) = try? await URLSession.shared.data(from: URL(string: base + "/__acceptance__/environment")!),
              (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw XCTSkip("未启动当前配置模型联合验收服务")
        }
        let environment = try XCTUnwrap(JSONSerialization.jsonObject(with: metadata) as? [String: Any])
        XCTAssertEqual(environment["kind"] as? String, "configured-model-answer-acceptance")
        let app = connectedApp(address: base)
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("联合验收：只依据我给定的信息，用中文详细写一份居家旅行行前准备清单，不查询外部资料，不需要实时价格、天气或开放信息。请以标题‘联合验收旅行’开头，分成十个主题，每个主题至少写五条具体建议，最后附一张十行的检查表。全文约两千字，直接给出完整正文。")
        app.buttons["send-input"].tap()
        let copy = app.buttons["copy-answer"]
        XCTAssertTrue(copy.waitForExistence(timeout: 120))
        var observations: [[String: Any]] = []
        var previous = ""
        let deadline = Date().addingTimeInterval(90)
        while observations.count < 2 && Date() < deadline {
            let displayed = app.scrollViews.firstMatch.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: "\n")
            if displayed.count > previous.count + 50 && copy.label == "复制当前正文" {
                let (data, _) = try await URLSession.shared.data(from: URL(string: base + "/api/snapshot")!)
                let snapshot = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
                let preview = try XCTUnwrap(snapshot["answer_preview"] as? [String: Any])
                let id = try XCTUnwrap(snapshot["active_session_id"] as? String)
                let (history, _) = try await URLSession.shared.data(from: URL(string: base + "/api/sessions/" + id)!)
                let page = try XCTUnwrap(JSONSerialization.jsonObject(with: history) as? [String: Any])
                let turns = try XCTUnwrap(page["turns"] as? [[String: Any]])
                let requests = try XCTUnwrap(page["requests"] as? [[String: Any]])
                XCTAssertEqual(preview["status"] as? String, "generating")
                XCTAssertEqual(preview["delivery"] as? String, "stream")
                XCTAssertEqual(turns.first?["status"] as? String, "running")
                XCTAssertTrue(turns.first?["answer"] is NSNull)
                XCTAssertEqual(requests.filter { $0["status"] as? String == "running" }.count, 1)
                observations.append(["at": ISO8601DateFormatter().string(from: Date()),
                    "session_id": id, "turn_id": snapshot["active_turn_id"]!,
                    "revision": preview["revision"]!, "displayed": displayed,
                    "preview_chars": (preview["text"] as? String)?.count ?? 0,
                    "history_status": "running", "history_answer": NSNull()])
                attach(app, "真实模型第\(observations.count)批，响应与保存尚未完成")
                previous = displayed
            }
            // 只轮询观察界面，不控制或延迟模型、服务与客户端输出。
            if observations.count < 2 { try await Task.sleep(for: .milliseconds(300)) }
        }
        XCTAssertEqual(observations.count, 2)
        let evidence = try JSONSerialization.data(withJSONObject: ["environment": environment, "observations": observations], options: [.prettyPrinted, .sortedKeys])
        let attachment = XCTAttachment(data: evidence, uniformTypeIdentifier: "public.json")
        attachment.name = "真实模型与 iOS 多批正文证据"
        attachment.lifetime = .keepAlways
        add(attachment)
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: copy)
        await fulfillment(of: [saved], timeout: 120)
        XCTAssertFalse(app.staticTexts["正在生成 · 尚未保存"].exists)
        attach(app, "真实模型回答已保存")
    }

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

    func testFailedAnswerRetriesFromBeginningAndFallbackExplainsCompleteAnswer() async throws {
        for mode in ["retry", "fallback"] {
            let app = connectedApp(address: "https://localhost:18497")
            try await streamControl("arm?mode=" + mode)
            app.buttons["new-conversation"].tap()
            let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            input.tap()
            input.typeText("手机故障验收 " + mode)
            app.buttons["send-input"].tap()
            try await streamControl("entered", method: "GET")
            try await streamControl("step")
            XCTAssertTrue(app.staticTexts["失败旧正文"].waitForExistence(timeout: 5))
            try await streamControl("step")
            let status = app.staticTexts["answer-status"]
            let notice = mode == "retry" ? "正在重试" : "模型服务不支持流式"
            let feedback = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", notice), object: status)
            await fulfillment(of: [feedback], timeout: 5)
            XCTAssertFalse(app.staticTexts["失败旧正文"].exists)
            attach(app, mode == "retry" ? "清空失败正文并提示正在重试" : "说明不支持流式并等待完整回答")
            try await streamControl("step")
            if mode == "retry" {
                XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 10))
                XCTAssertFalse(app.staticTexts["失败旧正文"].exists)
                for _ in 0..<3 { try await streamControl("step") }
            }
            let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
            await fulfillment(of: [saved], timeout: 10)
            if mode == "fallback" {
                XCTAssertTrue(app.staticTexts["完整的杭州安排。"].exists)
                XCTAssertTrue(status.label.contains("模型服务不支持流式"))
                attach(app, "降级后保存完整回答并保留原因")
            }
            app.terminate()
        }
    }

    func testStoppedStreamFreezesThenShowsSavedOrUnsavedCopy() async throws {
        for port in [18497, 18498] {
            let unsaved = port == 18498
            let app = connectedApp(address: "https://localhost:\(port)")
            try await streamControl("arm", port: port)
            app.buttons["new-conversation"].tap()
            let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            input.tap()
            input.typeText("停止及保存验收")
            app.buttons["send-input"].tap()
            try await streamControl("entered", method: "GET", port: port)
            try await streamControl("step", port: port)
            XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 5))
            app.buttons["stop-turn"].tap()
            XCTAssertTrue(app.staticTexts["正在停止 · 正文已暂停追加"].waitForExistence(timeout: 5))
            try await streamControl("step", port: port)
            try await streamControl("step", port: port)
            XCTAssertFalse(app.staticTexts["上午游览西湖。"].exists)
            XCTAssertFalse(app.staticTexts["下午参观博物馆。"].exists)
            XCTAssertFalse(app.buttons["send-input"].isEnabled)
            attach(app, "停止后冻结当前正文")
            try await streamControl("step", port: port)
            let copy = app.buttons["copy-answer"]
            let ready = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", unsaved ? "复制未保存正文" : "复制完整回答"), object: copy)
            await fulfillment(of: [ready], timeout: 10)
            XCTAssertTrue(app.staticTexts["下午参观博物馆。"].exists)
            if unsaved {
                XCTAssertTrue(app.staticTexts["未保存 · 请复制保留，刷新后可能丢失"].exists)
                XCTAssertFalse(app.buttons["send-input"].isEnabled)
            }
            copy.tap()
            XCTAssertEqual(copy.label, unsaved ? "已复制未保存正文" : "已复制完整回答")
            attach(app, unsaved ? "保存失败保留正文与复制入口" : "停止后真实完成并保存")
            app.terminate()
        }
    }

    func testStreamingMarkdownFollowsGrowthAndPreservesReadingPosition() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=reading")
        defer { app.terminate() }
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap()
        input.typeText("流式中文排版验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        let copy = app.buttons["copy-answer"]
        try await streamControl("step")
        XCTAssertTrue(copy.waitForExistence(timeout: 10))
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: copy)], timeout: 5)
        XCTAssertEqual(copy.label, "复制当前正文")
        attach(app, "未闭合代码与长中文正文跟随到底部")
        try await streamControl("step")
        try await streamControl("step")
        let table = app.scrollViews["answer-table"]
        XCTAssertTrue(table.waitForExistence(timeout: 5))
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: copy)], timeout: 5)
        table.swipeLeft()
        XCTAssertFalse(app.buttons["latest-answer"].exists)
        try await streamControl("step")
        let marker = app.staticTexts["第四批阅读标记。"]
        XCTAssertTrue(marker.waitForExistence(timeout: 5))
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: copy)], timeout: 5)
        app.scrollViews.firstMatch.swipeDown()
        XCTAssertTrue(app.buttons["latest-answer"].waitForExistence(timeout: 5))
        let position = marker.frame.minY
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["最新一批旅行建议。"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertEqual(marker.frame.minY, position, accuracy: 3)
        XCTAssertFalse(copy.isHittable)
        attach(app, "上翻后新正文不抢位置")
        app.buttons["latest-answer"].tap()
        XCTAssertTrue(copy.isHittable)
        copy.tap()
        XCTAssertEqual(copy.label, "已复制当前正文")
        let (snapshotData, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/snapshot")!)
        let snapshot = try XCTUnwrap(JSONSerialization.jsonObject(with: snapshotData) as? [String: Any])
        let preview = try XCTUnwrap(snapshot["answer_preview"] as? [String: Any])
        let copiedText = try XCTUnwrap(preview["text"] as? String)
        try await streamControl("step")
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: copy)], timeout: 10)
        XCTAssertTrue(copy.isHittable)
        attach(app, "主动回到最新并跟随保存结果")
        input.tap()
        input.press(forDuration: 1.2)
        let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: 5))
        paste.tap()
        XCTAssertEqual(input.value as? String, copiedText)
    }

    private func streamControl(_ action: String, method: String = "POST", port: Int = 18497) async throws {
        var request = URLRequest(url: URL(string: "https://localhost:\(port)/__test__/" + action)!)
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
