import XCTest
import UIKit

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
        let prompt = try XCTUnwrap(environment["prompt"] as? String)
        let preparation = try XCTUnwrap(environment["preparation_prompt"] as? String)
        let app = connectedApp(address: base)
        app.buttons["new-conversation"].tap()
        var input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap(); input.typeText(preparation); app.buttons["send-input"].tap()
        // 真实准备轮单独计账；两端进入已有会话后才发目标输入，不暂停任何模型流。
        var sessionID = ""
        var prepared = false
        let preparationDeadline = Date().addingTimeInterval(180)
        while Date() < preparationDeadline {
            let list = try await liveGet("/api/sessions")
            if let sessions = list["sessions"] as? [[String: Any]], let id = sessions.first?["session_id"] as? String {
                sessionID = id
                let history = try await liveGet("/api/sessions/" + id + "?summary=false&include_messages=false")
                if let turns = history["turns"] as? [[String: Any]], turns.first?["status"] as? String == "completed" { prepared = true; break }
            }
            try await Task.sleep(for: .milliseconds(300))
        }
        XCTAssertTrue(prepared)
        try await livePost("/__acceptance__/coordination/ios_session", ["session_id": sessionID])
        var webReady = false
        let readyDeadline = Date().addingTimeInterval(180)
        while Date() < readyDeadline {
            let readiness = try await liveGet("/__acceptance__/coordination")
            if readiness["web_ready"] != nil { webReady = true; break }
            try await Task.sleep(for: .milliseconds(300))
        }
        XCTAssertTrue(webReady)
        var observations: [[String: Any]] = []
        var target = ""
        var saved: [String: Any] = [:]
        defer {
            if let data = try? JSONSerialization.data(withJSONObject: ["environment": environment, "session_id": sessionID,
                "turn_id": target, "observations": observations, "outcome": saved], options: [.prettyPrinted, .sortedKeys]) {
                let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
                attachment.name = "真实模型原始观察全程（含失败）"; attachment.lifetime = .keepAlways; add(attachment)
            }
        }
        let began = Date()
        observations.append(["epoch_ms": began.timeIntervalSince1970 * 1000, "phase": "before_target_input", "rendered_document_chars": 0, "rendered_progress_chars": 0])
        input.tap(); input.typeText(prompt); app.buttons["send-input"].tap()
        var savedHistory: [String: Any] = [:]
        var queryImage = false
        var answerImage = false
        var stableSaved = 0
        var lastSavedDisplay: String?
        let deadline = Date().addingTimeInterval(600)
        while Date() < deadline {
            let operationBegan = Date()
            let snapshot = try await liveGet("/api/snapshot")
            let history = try await liveGet("/api/sessions/" + sessionID + "?summary=false&include_messages=false")
            let turns = history["turns"] as? [[String: Any]] ?? []
            if turns.count > 1 { target = turns[1]["turn_id"] as? String ?? ""; saved = turns[1]; savedHistory = history }
            // 公共XCTest快照一次取得不可变可访问性树，避免自动折叠时逐个元素引用失效。
            let tree = try app.snapshot()
            let region = liveFind(tree, identifier: "conversation-turn-" + target)
            let body = region.flatMap { liveFind($0, identifier: "answer-body") }
            let displayed = body.map(liveTextLabels)?.joined(separator: "\n") ?? ""
            let progress = region.map { liveMatchingLabels($0, identifier: "query-progress-text") } ?? []
            let statuses = region.map { liveMatchingLabels($0, identifier: "query-progress-status") } ?? []
            let copyLabel = region.flatMap { liveFind($0, identifier: "copy-answer") }?.label ?? ""
            let preview = snapshot["answer_preview"] as? [String: Any]
            let queryPreview = snapshot["progress_preview"] as? [String: Any]
            let entries = queryPreview?["entries"] as? [[String: Any]] ?? []
            observations.append(["epoch_ms": Date().timeIntervalSince1970 * 1000,
                "elapsed_seconds": Date().timeIntervalSince(began), "session_id": sessionID, "turn_id": target,
                "rendered_document_chars": displayed.count, "rendered_progress_chars": progress.joined(separator: "\n").count,
                "progress_labels": progress, "progress_statuses": statuses,
                "copy_label": copyLabel,
                "body_frame_y": body.map { Double($0.frame.minY) } ?? NSNull(),
                "body_frame_height": body.map { Double($0.frame.height) } ?? NSNull(),
                "app_viewport_height": Double(tree.frame.height),
                "runner_snapshot_answer_chars": (preview?["text"] as? String)?.count ?? 0,
                "runner_snapshot_answer_revision": preview?["revision"] ?? NSNull(),
                "runner_snapshot_progress_revision": queryPreview?["revision"] ?? NSNull(),
                "runner_snapshot_progress_chars": entries.reduce(0) { $0 + (($1["text"] as? String)?.count ?? 0) },
                "history_status": saved["status"] ?? "not_accepted", "observation_duration_ms": Date().timeIntervalSince(operationBegan) * 1000])
            if !queryImage && !progress.isEmpty { attach(app, "真实查询进展初现"); queryImage = true }
            if !answerImage && !displayed.isEmpty { attach(app, "真实正文初现且查询自动折叠"); answerImage = true }
            let status = saved["status"] as? String
            let terminal = ["completed", "terminated", "failed"].contains(status ?? "")
            if terminal && saved["answer_source"] as? String != "model" { break }
            let savedCopyLabel = status == "completed" ? "复制完整回答" : "复制不完整回答"
            if terminal, copyLabel == savedCopyLabel, !displayed.isEmpty {
                if lastSavedDisplay == displayed { stableSaved += 1 } else { stableSaved = 0 }
                lastSavedDisplay = displayed
            } else { stableSaved = 0; lastSavedDisplay = nil }
            if stableSaved >= 2 { break }
            try await Task.sleep(for: .milliseconds(150))
        }
        XCTAssertTrue(["completed", "terminated", "failed"].contains(saved["status"] as? String ?? ""))
        XCTAssertTrue(answerImage)
        guard saved["answer_source"] as? String == "model" else { XCTFail("本轮没有可验收的模型正文，保留真实终态与观察证据"); return }
        let finalAnswer = try XCTUnwrap(saved["answer"] as? String)
        let finalProgress = saved["query_progress"] as? [[String: Any]] ?? []
        if environment["scenario"] as? String == "query" { XCTAssertTrue(queryImage); XCTAssertFalse(finalProgress.isEmpty) }
        else { XCTAssertTrue(finalProgress.isEmpty); XCTAssertFalse(queryImage) }
        attach(app, "真实模型回答已保存")
        let outcome: [String: Any] = ["environment": environment, "session_id": sessionID, "turn_id": target,
            "observations": observations, "outcome": saved,
            "measurement": "UI可访问性排版字符；runner快照为旁路公开核对，不是App收到SSE时间"]
        try await livePost("/__acceptance__/ios-evidence", outcome)
        try await livePost("/__acceptance__/coordination/ios_saved", ["turn_id": target])
        // Web先复制，再交手机使用共享系统剪贴板。
        var webCopied = false
        let copyDeadline = Date().addingTimeInterval(120)
        while Date() < copyDeadline {
            if (try await liveGet("/__acceptance__/coordination"))["web_copied"] != nil { webCopied = true; break }
            try await Task.sleep(for: .milliseconds(300))
        }
        XCTAssertTrue(webCopied)
        let copies = app.buttons.matching(identifier: "copy-answer")
        let copy = copies.element(boundBy: copies.count - 1)
        for _ in 0..<8 { if copy.isHittable { break }; app.swipeUp(velocity: .fast) }
        XCTAssertTrue(copy.isHittable)
        XCTAssertEqual(copy.label, saved["status"] as? String == "completed" ? "复制完整回答" : "复制不完整回答")
        copy.tap()
        input.tap(); input.press(forDuration: 1.2)
        let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: 5)); paste.tap()
        let copyEqual = input.value as? String == finalAnswer
        XCTAssertTrue(copyEqual)
        attach(app, "真实最终Markdown复制全文一致")
        input.press(forDuration: 1.2)
        let selectAll = app.menuItems.matching(NSPredicate(format: "label IN %@", ["全选", "Select All"])).firstMatch
        if selectAll.waitForExistence(timeout: 2) {
            selectAll.tap(); input.typeText(XCUIKeyboardKey.delete.rawValue)
        } else {
            input.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: finalAnswer.count))
        }
        app.terminate(); app.launch()
        let reopen = app.buttons.matching(identifier: "open-session").firstMatch
        XCTAssertTrue(reopen.waitForExistence(timeout: 15)); reopen.tap()
        let history = try await liveGet("/api/sessions/" + sessionID + "?summary=false&include_messages=false")
        let reopenedTurns = try XCTUnwrap(history["turns"] as? [[String: Any]])
        XCTAssertEqual(reopenedTurns[1]["answer"] as? String, finalAnswer)
        let progressEqual = try JSONSerialization.data(withJSONObject: reopenedTurns[1]["query_progress"] ?? [], options: .sortedKeys)
            == JSONSerialization.data(withJSONObject: finalProgress, options: .sortedKeys)
        XCTAssertTrue(progressEqual)
        let reopenedRegion = app.descendants(matching: .any).matching(identifier: "conversation-turn-" + target).firstMatch
        var expandedObserved = finalProgress.isEmpty
        if !finalProgress.isEmpty {
            XCTAssertEqual(reopenedRegion.staticTexts.matching(identifier: "query-progress-text").count, 0)
            let disclosure = reopenedRegion.otherElements["query-progress"].buttons.firstMatch
            for _ in 0..<12 { if disclosure.isHittable { break }; app.swipeDown(velocity: .fast) }
            XCTAssertTrue(disclosure.isHittable); disclosure.tap()
            expandedObserved = reopenedRegion.staticTexts.matching(identifier: "query-progress-text").count > 0
            XCTAssertTrue(expandedObserved)
            attach(app, "重开App展开已保存查询进展")
        }
        input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.exists)
        let toolsBefore = savedHistory["tool_calls"] as? [[String: Any]] ?? []
        let toolsAfter = history["tool_calls"] as? [[String: Any]] ?? []
        XCTAssertEqual(toolsBefore.count, toolsAfter.count)
        var finalEvidence = outcome
        finalEvidence["copy_markdown_equal"] = copyEqual; finalEvidence["reopened_progress_equal"] = progressEqual
        finalEvidence["reopened_expanded_text_observed"] = expandedObserved
        finalEvidence["query_text_observed_during_target"] = queryImage
        finalEvidence["tools_before_reopen"] = toolsBefore.count; finalEvidence["tools_after_reopen"] = toolsAfter.count
        try await livePost("/__acceptance__/ios-evidence", finalEvidence)
        try await livePost("/__acceptance__/coordination/ios_finished", ["turn_id": target])
        let evidence = try JSONSerialization.data(withJSONObject: finalEvidence, options: [.prettyPrinted, .sortedKeys])
        let attachment = XCTAttachment(data: evidence, uniformTypeIdentifier: "public.json")
        attachment.name = "真实模型与iOS全程证据"; attachment.lifetime = .keepAlways; add(attachment)
    }

    func testConfiguredModelSavedHistorySamplingAcrossDisclosureChanges() async throws {
        guard let environment = try? await liveGet("/__acceptance__/environment"),
              environment["scenario"] as? String == "query" else {
            throw XCTSkip("仅对已保存的真实查询会话核验公开UI采样器，不调用模型")
        }
        let list = try await liveGet("/api/sessions")
        let rows = try XCTUnwrap(list["sessions"] as? [[String: Any]])
        let id = try XCTUnwrap(rows.first?["session_id"] as? String)
        let history = try await liveGet("/api/sessions/" + id + "?summary=false&include_messages=false")
        let turns = try XCTUnwrap(history["turns"] as? [[String: Any]])
        let saved = try XCTUnwrap(turns.last)
        XCTAssertTrue(["completed", "terminated"].contains(saved["status"] as? String ?? ""))
        let target = try XCTUnwrap(saved["turn_id"] as? String)
        let progress = try XCTUnwrap(saved["query_progress"] as? [[String: Any]])
        let expected = progress.compactMap { $0["text"] as? String }.filter { !$0.isEmpty }
        XCTAssertFalse(expected.isEmpty)
        let app = connectedApp(address: "https://localhost:18499")
        let open = app.buttons.matching(identifier: "open-session").firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 10)); open.tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "identifier == %@ AND label == %@", "copy-answer", saved["status"] as? String == "completed" ? "复制完整回答" : "复制不完整回答")).firstMatch.exists)
        let toggle = app.otherElements["query-progress"].buttons.firstMatch
        for _ in 0..<15 { if toggle.isHittable { break }; app.swipeDown(velocity: .fast) }
        XCTAssertTrue(toggle.isHittable)
        var observations: [[String: Any]] = []
        for _ in 0..<3 {
            toggle.tap()
            let began = Date()
            let expandedTree = try app.snapshot()
            let expanded = try XCTUnwrap(liveFind(expandedTree, identifier: "conversation-turn-" + target))
            let labels = liveMatchingLabels(expanded, identifier: "query-progress-text")
            XCTAssertEqual(labels, expected)
            let statuses = liveMatchingLabels(expanded, identifier: "query-progress-status")
            XCTAssertEqual(statuses.count, expected.count)
            // 已取得的快照在随后折叠时保持不可变，不引用会失效的XCUIElement。
            toggle.tap()
            XCTAssertEqual(liveMatchingLabels(expanded, identifier: "query-progress-text"), expected)
            let collapsedTree = try app.snapshot()
            let collapsed = try XCTUnwrap(liveFind(collapsedTree, identifier: "conversation-turn-" + target))
            XCTAssertTrue(liveMatchingLabels(collapsed, identifier: "query-progress-text").isEmpty)
            XCTAssertFalse(liveTextLabels(try XCTUnwrap(liveFind(collapsed, identifier: "answer-body"))).isEmpty)
            observations.append(["epoch_ms": Date().timeIntervalSince1970 * 1000,
                "expanded_entries": labels.count, "status_entries": statuses.count,
                "duration_ms": Date().timeIntervalSince(began) * 1000])
        }
        let copy = app.buttons.matching(identifier: "copy-answer").element(boundBy: app.buttons.matching(identifier: "copy-answer").count - 1)
        for _ in 0..<12 { if copy.isHittable { break }; app.swipeUp(velocity: .fast) }
        XCTAssertTrue(copy.isHittable)
        let expectedCopyLabel = saved["status"] as? String == "completed" ? "复制完整回答" : "复制不完整回答"
        XCTAssertEqual(copy.label, expectedCopyLabel)
        UIPasteboard.general.string = "potato117-independent-copy-sentinel"
        copy.tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.press(forDuration: 1.2)
        let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: 5)); paste.tap()
        let copyEqual = input.value as? String == saved["answer"] as? String
        XCTAssertTrue(copyEqual)
        attach(app, "只读已存不完整回答独立哨兵复制核对")
        input.press(forDuration: 1.2)
        let selectAll = app.menuItems.matching(NSPredicate(format: "label IN %@", ["全选", "Select All"])).firstMatch
        XCTAssertTrue(selectAll.waitForExistence(timeout: 5)); selectAll.tap(); input.typeText(XCUIKeyboardKey.delete.rawValue)
        let after = try await liveGet("/api/sessions/" + id + "?summary=false&include_messages=false")
        XCTAssertEqual((history["requests"] as? [[String: Any]])?.count, (after["requests"] as? [[String: Any]])?.count)
        XCTAssertEqual((history["tool_calls"] as? [[String: Any]])?.count, (after["tool_calls"] as? [[String: Any]])?.count)
        let evidence = try JSONSerialization.data(withJSONObject: ["session_id": id, "turn_id": target,
            "model_and_tool_counts_unchanged": true, "copy_sentinel_used": true, "copy_markdown_equal": copyEqual, "saved_copy_label": expectedCopyLabel, "observations": observations], options: [.prettyPrinted, .sortedKeys])
        let attachment = XCTAttachment(data: evidence, uniformTypeIdentifier: "public.json")
        attachment.name = "公开UI不可变快照与折叠展开核验"; attachment.lifetime = .keepAlways; add(attachment)
    }

    private func liveFind(_ root: any XCUIElementSnapshot, identifier: String) -> (any XCUIElementSnapshot)? {
        if root.identifier == identifier { return root }
        for child in root.children {
            if let found = liveFind(child, identifier: identifier) { return found }
        }
        return nil
    }

    private func liveTextLabels(_ root: any XCUIElementSnapshot) -> [String] {
        if root.elementType == .staticText { return [root.label] }
        return root.children.flatMap(liveTextLabels)
    }

    private func liveMatchingLabels(_ root: any XCUIElementSnapshot, identifier: String) -> [String] {
        if root.identifier == identifier { return liveTextLabels(root) }
        return root.children.flatMap { liveMatchingLabels($0, identifier: identifier) }
    }

    private func liveGet(_ path: String) async throws -> [String: Any] {
        let (data, response) = try await URLSession.shared.data(from: URL(string: "https://localhost:18499" + path)!)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    private func livePost(_ path: String, _ value: [String: Any]) async throws {
        var request = URLRequest(url: URL(string: "https://localhost:18499" + path)!)
        request.httpMethod = "POST"; request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: value)
        let (_, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
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

    func testLongMarkdownTerminalHandoffKeepsFollowingViewport() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=query-handoff-paced")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.typeText("HANDOFF117 INPUT"); app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        for _ in 0..<3 { try await streamControl("step") }
        let deadline = Date().addingTimeInterval(30)
        var raw = 0
        var sessionID = ""
        while raw < 3000 && Date() < deadline {
            let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/snapshot")!)
            let snapshot = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let preview = snapshot["answer_preview"] as? [String: Any]
            raw = (preview?["text"] as? String)?.count ?? 0
            sessionID = preview?["session_id"] as? String ?? sessionID
            try await Task.sleep(for: .milliseconds(30))
        }
        XCTAssertGreaterThanOrEqual(raw, 3000)
        let copy = app.buttons["copy-answer"]
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: copy)], timeout: 10)
        let before = copy.frame.midY
        XCTAssertEqual(copy.label, "复制当前正文")
        var samples: [[String: Any]] = []
        var copiedTerminal = false
        var firstSavedChars = 0
        var copyActionTime = 0.0
        UIPasteboard.general.string = "potato117-first-terminal-copy-sentinel"
        defer {
            if let data = try? JSONSerialization.data(withJSONObject: samples, options: [.prettyPrinted, .sortedKeys]) {
                let attachment = XCTAttachment(data: data, uniformTypeIdentifier: "public.json")
                attachment.name = "长Markdown终态真实视口位置采样"; attachment.lifetime = .keepAlways; add(attachment)
            }
        }
        for _ in 0..<50 {
            let tree = try app.snapshot()
            let button = liveFind(tree, identifier: "copy-answer")
            let body = liveFind(tree, identifier: "answer-body")
            let visible = button.map { $0.frame.intersects(tree.frame) && $0.frame.minY > 50 && $0.frame.maxY < tree.frame.maxY - 50 } ?? false
            samples.append(["epoch_ms": Date().timeIntervalSince1970 * 1000, "phase": "automatic-paced-completion",
                "copy_y": button.map { Double($0.frame.midY) } ?? NSNull(), "before_y": before, "copy_visible": visible,
                "copy_label": button?.label ?? "missing", "body_chars": body.map { liveTextLabels($0).joined().count } ?? 0])
            XCTAssertNotNil(button, "终态窗口不能撤空正文复制入口")
            XCTAssertTrue(visible, "底部跟随时真实复制入口不能回退至视口之外")
            // 中间AX布局可能先于实际合成帧，保留原始坐标；稳定终态再核底部位置。
            // 全窗口原录屏另外检测输入标记，不能用这些稀疏样本替代物理连续性。
            if !copiedTerminal, button?.label == "复制不完整回答" {
                firstSavedChars = body.map { liveTextLabels($0).joined().count } ?? 0
                copyActionTime = Date().timeIntervalSince1970 * 1000
                copy.tap()
                copiedTerminal = true
            }
            try await Task.sleep(for: .milliseconds(10))
        }
        XCTAssertTrue(copiedTerminal)
        XCTAssertTrue(["复制不完整回答", "已复制不完整回答"].contains(copy.label))
        XCTAssertLessThan(abs(copy.frame.midY - before), 80, "稳定终态仍须保持底部阅读位置")
        attach(app, "长Markdown已存不完整结果仍跟随底部")
        let (historyData, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/sessions/" + sessionID + "?summary=false&include_messages=false")!)
        let history = try XCTUnwrap(JSONSerialization.jsonObject(with: historyData) as? [String: Any])
        let turns = try XCTUnwrap(history["turns"] as? [[String: Any]])
        let expected = try XCTUnwrap(turns.last?["answer"] as? String)
        input.tap(); input.press(forDuration: 1.2)
        let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
        XCTAssertTrue(paste.waitForExistence(timeout: 5)); paste.tap()
        let exact = input.value as? String == expected
        XCTAssertTrue(exact, "首次终态标签可操作时必须复制可靠全文，不能复制旧显示前缀或哨兵")
        samples.append(["epoch_ms": Date().timeIntervalSince1970 * 1000, "phase": "terminal-copy-check",
            "copy_sentinel_used": true, "copy_markdown_equal": exact, "expected_chars": expected.count,
            "first_saved_snapshot_body_chars": firstSavedChars, "copy_action_epoch_ms": copyActionTime])
        attach(app, "首次终态复制与可靠全文一致")
        input.press(forDuration: 1.2)
        let selectAll = app.menuItems.matching(NSPredicate(format: "label IN %@", ["全选", "Select All"])).firstMatch
        XCTAssertTrue(selectAll.waitForExistence(timeout: 5)); selectAll.tap(); input.typeText(XCUIKeyboardKey.delete.rawValue)
    }

    func testSavedHistoryHandoffKeepsBodyAndPositionWhileHistoryIsDelayed() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=query-progress")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.typeText("终态历史交接保位验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        for _ in 0..<6 { try await streamControl("step") }
        let heading = app.staticTexts["实时杭州旅行"]
        XCTAssertTrue(heading.waitForExistence(timeout: 10))
        let before = heading.frame.minY
        XCTAssertEqual(app.buttons["copy-answer"].label, "复制当前正文")
        try await streamControl("delay-history")
        try await streamControl("step")
        try await streamControl("history-entered", method: "GET")
        for _ in 0..<3 {
            let tree = try app.snapshot()
            let body = liveFind(tree, identifier: "answer-body")
            XCTAssertNotNil(body, "终态已经通知、历史尚未返回时不得撤掉正在阅读的正文")
            XCTAssertTrue(body.map(liveTextLabels)?.joined().contains("上午游览西湖") ?? false)
            XCTAssertEqual(liveFind(tree, identifier: "copy-answer")?.label, "复制当前正文")
            XCTAssertFalse(app.buttons.matching(NSPredicate(format: "label == %@", "复制完整回答")).firstMatch.exists)
            if heading.exists { XCTAssertLessThan(abs(heading.frame.minY - before), 3) }
            try await Task.sleep(for: .milliseconds(250))
        }
        attach(app, "历史延迟期间正文连续保留且仍是未确认保存的当前正文")
        try await streamControl("release-history")
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        XCTAssertTrue(heading.exists)
        XCTAssertLessThan(abs(heading.frame.minY - before), 3)
        attach(app, "可靠历史采用后正文未撤空且阅读锚点保持")
    }

    func testQueryRetryAndCompleteFallbackReplaceFailedExplanationBeforeAnswer() async throws {
        for mode in ["query-retry", "query-fallback"] {
            let app = connectedApp(address: "https://localhost:18497")
            try await streamControl("arm?mode=" + mode)
            app.buttons["new-conversation"].tap()
            let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            input.tap()
            input.typeText(mode == "query-retry" ? "查询重试验收" : "查询降级验收")
            app.buttons["send-input"].tap()
            try await streamControl("entered", method: "GET")
            try await streamControl("step")
            let failed = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "失败旧说明")).firstMatch
            XCTAssertTrue(failed.waitForExistence(timeout: 10))
            try await streamControl("step")
            try await streamControl("step")
            let notice = mode == "query-retry" ? "查询进展正在重试，新尝试将重新生成。" : "模型服务不支持流式，改为生成完整回答。"
            let explained = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", notice)).firstMatch
            XCTAssertTrue(explained.waitForExistence(timeout: 5))
            XCTAssertFalse(failed.exists)
            XCTAssertFalse(app.buttons["copy-answer"].exists)
            attach(app, mode + "失败说明失效，查询等待真实解释")
            if mode == "query-retry" {
                try await streamControl("step")
                XCTAssertTrue(app.staticTexts["重新查找西湖"].waitForExistence(timeout: 10))
                XCTAssertFalse(failed.exists)
                try await streamControl("step")
                try await streamControl("step")
                XCTAssertTrue(app.staticTexts["已取得资料"].waitForExistence(timeout: 5))
                for _ in 0..<4 { try await streamControl("step") }
            } else {
                try await streamControl("step")
                XCTAssertTrue(app.staticTexts["已取得资料"].waitForExistence(timeout: 5))
                XCTAssertFalse(app.buttons["copy-answer"].exists)
                try await streamControl("step")
            }
            let copy = app.buttons["copy-answer"]
            let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: copy)
            await fulfillment(of: [saved], timeout: 10)
            let (stateData, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/sessions")!)
            let list = try XCTUnwrap(JSONSerialization.jsonObject(with: stateData) as? [String: Any])
            let rows = try XCTUnwrap(list["sessions"] as? [[String: Any]])
            let id = try XCTUnwrap(rows.first?["session_id"] as? String)
            let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/sessions/" + id)!)
            let history = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let requests = try XCTUnwrap(history["requests"] as? [[String: Any]])
            let tools = try XCTUnwrap(history["tool_calls"] as? [[String: Any]])
            let turns = try XCTUnwrap(history["turns"] as? [[String: Any]])
            XCTAssertEqual(requests.count, 4)
            XCTAssertEqual(tools.count, 1)
            XCTAssertEqual(turns.first?["status"] as? String, "completed")
            XCTAssertFalse(String(describing: turns.first?["query_progress"]).contains("失败旧说明"))
            if mode == "query-fallback" { XCTAssertEqual(turns.first?["answer_delivery"] as? String, "complete") }
            attach(app, mode + "真实查询仅执行一次，采用保存结果")
            app.terminate()
        }
    }

    func testQueryProgressFoldsAndRemainsReadableAfterRelaunch() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=query-progress")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("进展保存折叠验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖"].waitForExistence(timeout: 10))
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].waitForExistence(timeout: 5))
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["已取得资料"].waitForExistence(timeout: 5))
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 5))
        let toggle = app.otherElements["query-progress"].buttons.firstMatch
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
        attach(app, "正文出现后自动折叠查询进展")
        toggle.tap()
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].waitForExistence(timeout: 5))
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["上午游览西湖。"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
        for _ in 0..<2 { try await streamControl("step") }
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        app.terminate()
        app.launch()
        let session = app.buttons.matching(identifier: "open-session")
            .matching(NSPredicate(format: "label CONTAINS %@", "进展保存折叠验收")).firstMatch
        XCTAssertTrue(session.waitForExistence(timeout: 10))
        session.tap()
        XCTAssertTrue(app.buttons["copy-answer"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
        app.otherElements["query-progress"].buttons.firstMatch.tap()
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["已取得资料"].exists)
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format:"label CONTAINS %@", "已保存的查询进展")).firstMatch.exists)
        attach(app, "重开App后重新读取已保存的查询进展")
    }

    func testProgressFoldingPreservesPausedReadingAndLatestAction() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        defer { app.terminate() }
        try await streamControl("arm?mode=query-reading")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.typeText("折叠保位验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        for _ in 0..<4 { try await streamControl("step") }
        XCTAssertTrue(app.buttons["copy-answer"].waitForExistence(timeout: 10))
        app.buttons["earlier-history"].tap()
        let toggle = app.otherElements["query-progress"].buttons.firstMatch
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true"), object: toggle)], timeout: 5)
        toggle.tap()
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
        let offset = toggle.frame.minY
        toggle.tap()
        XCTAssertLessThan(abs(toggle.frame.minY - offset), 3)
        XCTAssertTrue(app.buttons["latest-answer"].exists)
        attach(app, "上翻后折叠进展保持阅读控制位置")
        for _ in 0..<5 { try await streamControl("step") }
        XCTAssertTrue(app.buttons["latest-answer"].exists)
        app.buttons["latest-answer"].tap()
        let copy = app.buttons["copy-answer"]
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "hittable == true AND label == %@", "复制完整回答"), object: copy)], timeout: 10)
        attach(app, "主动回到最新采用完整保存回答")
    }

    func testQueryProgressGrowsBeforeToolReturnAndStaysSeparateFromAnswer() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=query-progress")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("查询进展手机验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["copy-answer"].exists)
        attach(app, "查询阶段第一批行动说明，尚未执行工具")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["正在说明"].exists)
        attach(app, "查询阶段第二批行动说明，模型尚未完整返回")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["已取得资料"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["copy-answer"].exists)
        attach(app, "真实工具成功与行动说明区分，不声称完成核实")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["查询进展"].exists)
        XCTAssertEqual(app.buttons["copy-answer"].label, "复制当前正文")
        attach(app, "最终正文与查询进展分别展示")
        for _ in 0..<3 { try await streamControl("step") }
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        attach(app, "真实回答保存完成")
    }

    func testForegroundRestoresQueryProgressThenSavedAnswerWithoutReplay() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=query-progress")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap(); input.typeText("进展与正文前后台恢复")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖"].waitForExistence(timeout: 10))
        XCUIDevice.shared.press(.home)
        try await streamControl("step")
        app.activate()
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["copy-answer"].exists)
        attach(app, "前台恢复查询阶段完整累计进展")
        XCUIDevice.shared.press(.home)
        try await streamControl("step")
        try await streamControl("step")
        app.activate()
        XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["copy-answer"].label, "复制当前正文")
        app.otherElements["query-progress"].buttons.firstMatch.tap()
        XCTAssertTrue(app.staticTexts["已取得资料"].waitForExistence(timeout: 5))
        attach(app, "恢复正文并展开同轮可靠查询状态")
        XCUIDevice.shared.press(.home)
        for _ in 0..<3 { try await streamControl("step") }
        app.activate()
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        XCTAssertTrue(app.staticTexts["下午参观博物馆。"].waitForExistence(timeout: 5))
        attach(app, "后台完成后直接采用真实保存回答")
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

    func testContinuousChineseWithUnclosedQuoteIsReadableBeforeModelReturns() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        try await streamControl("arm?mode=continuous")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        XCTAssertTrue(input.waitForExistence(timeout: 5))
        input.tap()
        input.typeText("连续中文阅读验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        var observations: [[String: Any]] = []
        for index in 0..<9 {
            try await streamControl("step")
            let marker = index == 0 ? "先沿西湖步行" : "第\(index)站观察湖畔风景"
            let text = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", marker)).firstMatch
            XCTAssertTrue(text.waitForExistence(timeout: 5))
            XCTAssertEqual(app.buttons["copy-answer"].label, "复制当前正文")
            XCTAssertTrue(app.staticTexts["正在生成 · 尚未保存"].exists)
            let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/snapshot")!)
            let snapshot = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            let preview = try XCTUnwrap(snapshot["answer_preview"] as? [String: Any])
            XCTAssertEqual(preview["status"] as? String, "generating")
            observations.append(["at": ISO8601DateFormatter().string(from: Date()),
                "step": index, "displayed": text.label, "preview_chars": (preview["text"] as? String)?.count ?? 0,
                "revision": preview["revision"]!, "turn_id": snapshot["active_turn_id"]!])
        }
        let evidence = try JSONSerialization.data(withJSONObject: observations, options: [.prettyPrinted, .sortedKeys])
        let attachment = XCTAttachment(data: evidence, uniformTypeIdentifier: "public.json")
        attachment.name = "无换行中文九批可见增长"
        attachment.lifetime = .keepAlways
        add(attachment)
        attach(app, "九批连续正文先于模型完整返回及保存")
        try await streamControl("step")
        let saved = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: app.buttons["copy-answer"])
        await fulfillment(of: [saved], timeout: 10)
        attach(app, "连续正文成为保存历史")
    }

    func testLargeBurstThenSmallAppendKeepsRealCompletionAndCopy() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        defer { app.terminate() }
        try await streamControl("arm?mode=smooth")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.typeText("短暂平滑阅读验收")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["第一小片段。"].waitForExistence(timeout: 5))
        try await streamControl("step")
        let body = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "大块末尾。")).firstMatch
        XCTAssertTrue(body.waitForExistence(timeout: 5))
        XCTAssertEqual(app.buttons["copy-answer"].label, "复制当前正文")
        XCTAssertTrue(app.staticTexts["正在生成 · 尚未保存"].exists)
        attach(app, "大块在真实模型屏障返回前完成短暂展开")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "后续小片段。")).firstMatch.waitForExistence(timeout: 5))
        try await streamControl("step")
        let copy = app.buttons["copy-answer"]
        await fulfillment(of: [XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "复制完整回答"), object: copy)], timeout: 10)
        copy.tap()
        XCTAssertEqual(copy.label, "已复制完整回答")
        attach(app, "最终保存直接采用全文与完整复制状态")
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

    func testRemoteStopDuringQueryFreezesProgressBeforeRealResult() async throws {
        let app = connectedApp(address: "https://localhost:18497")
        defer { app.terminate() }
        try await streamControl("arm?mode=query-progress")
        app.buttons["new-conversation"].tap()
        let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
        input.tap(); input.typeText("跨设备停止查询进展")
        app.buttons["send-input"].tap()
        try await streamControl("entered", method: "GET")
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖"].waitForExistence(timeout: 5))
        let (data, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:18497/api/snapshot")!)
        let state = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let target = try XCTUnwrap(state["active_turn_id"] as? String)
        var request = URLRequest(url: URL(string: "https://localhost:18497/api/turns/" + target + "/stop")!)
        request.httpMethod = "POST"
        let (_, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        let stopping = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", "正在停止"), object: app.staticTexts["execution-message"])
        await fulfillment(of: [stopping], timeout: 5)
        try await streamControl("step")
        XCTAssertTrue(app.staticTexts["准备查找西湖"].isHittable)
        XCTAssertFalse(app.staticTexts["准备查找西湖的地点资料。"].exists)
        XCTAssertFalse(app.buttons["send-input"].isEnabled)
        attach(app, "远端停止后冻结查询阶段的当前进展")
        try await streamControl("step")
        let ended = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label == %@", "本轮已停止。"), object: app.staticTexts["answer-status"])
        await fulfillment(of: [ended], timeout: 10)
        if !app.staticTexts["准备查找西湖的地点资料。"].isHittable { app.otherElements["query-progress"].buttons.firstMatch.tap() }
        XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
        XCTAssertTrue(app.staticTexts["未执行"].exists)
        attach(app, "真实查询响应保存后说明保留且工具未执行")
    }

    func testStoppedStreamFreezesThenShowsSavedOrUnsavedCopy() async throws {
        for port in [18497, 18498] {
            let unsaved = port == 18498
            let app = connectedApp(address: "https://localhost:\(port)")
            try await streamControl(unsaved ? "arm?mode=query-progress" : "arm", port: port)
            app.buttons["new-conversation"].tap()
            let input = app.descendants(matching: .any).matching(identifier: "travel-input").firstMatch
            XCTAssertTrue(input.waitForExistence(timeout: 5))
            input.tap()
            input.typeText("停止及保存验收")
            app.buttons["send-input"].tap()
            try await streamControl("entered", method: "GET", port: port)
            if unsaved {
                for _ in 0..<3 { try await streamControl("step", port: port) }
            }
            try await streamControl("step", port: port)
            XCTAssertTrue(app.staticTexts["实时杭州旅行"].waitForExistence(timeout: 5))
            if unsaved {
                app.otherElements["query-progress"].buttons.firstMatch.tap()
                XCTAssertTrue(app.staticTexts["准备查找西湖的地点资料。"].isHittable)
                XCTAssertTrue(app.staticTexts["已取得资料"].exists)
            }
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
            if unsaved {
                let (stateData, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:\(port)/api/snapshot")!)
                let state = try XCTUnwrap(JSONSerialization.jsonObject(with: stateData) as? [String: Any])
                let preview = try XCTUnwrap(state["answer_preview"] as? [String: Any])
                let session = try XCTUnwrap(preview["session_id"] as? String)
                let (historyData, _) = try await URLSession.shared.data(from: URL(string: "https://localhost:\(port)/api/sessions/" + session)!)
                let history = try XCTUnwrap(JSONSerialization.jsonObject(with: historyData) as? [String: Any])
                let turns = try XCTUnwrap(history["turns"] as? [[String: Any]])
                XCTAssertTrue(turns[0]["answer"] is NSNull)
                let progress = try XCTUnwrap(turns[0]["query_progress"] as? [[String: Any]])
                XCTAssertEqual(progress.compactMap { $0["status"] as? String }, ["generated", "completed"])
                input.tap(); input.press(forDuration: 1.2)
                let paste = app.menuItems.matching(NSPredicate(format: "label IN %@", ["粘贴", "Paste"])).firstMatch
                XCTAssertTrue(paste.waitForExistence(timeout: 5)); paste.tap()
                let expected = "## 实时杭州旅行\n\n上午游览西湖。\n\n下午参观博物馆。\n"
                XCTAssertTrue((input.value as? String) == expected, "复制未保存正文应保留本轮Markdown全文且不混入进展。")
                attach(app, "可靠进展仍保存且未保存Markdown全文可复制")
            }
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
