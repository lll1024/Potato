# #97 的 58 条用户故事与最终验收映射

本表以 [父规格 #97](https://github.com/lll1024/Potato/issues/97) 为准，覆盖 #98–#104。每行给出实施任务、可定位测试及实际观察证据，不以子任务关闭或文档条目代替功能通过。本轮运行结果和证据限制统一见 [#104 联合验收](streaming-final.md)。

缩写只用于定位以下测试文件；方法名保持源码原文：

- B：[后端公开 HTTP／SSE](../../tests/test_answer_streaming.py)。
- I：[iOS SessionAccess](../../ios/PotatoTests/PollingTests.swift)；J：[iOS 真实 HTTPS 双客户端](../../ios/PotatoTests/SubmissionIntegrationTests.swift)。
- W：[Web 交互与协议](../../frontend/tests/ui/conversation.spec.ts)；U：[iOS 阅读界面](../../ios/PotatoUITests/ReadingNavigationTests.swift)。
- L：[真实模型 Web 观察端](../../frontend/tests/ui/answer-model-live.spec.ts) 与 U `testCurrentConfiguredModelShowsBatchesBeforeSavedHistory`，共享 [真实模型验收装配](../../tests/live_answer_eval.py)。
- “实际操作”中的可控模型、Chrome、模拟器和真实模型按各自记录解释，不代表真机／私网／TestFlight。

| 故事 | 行为 | 子任务 | 自动化测试／公开边界 | 实际操作与证据 |
| --- | --- | --- | --- | --- |
| 1 | Web 提前阅读最终正文 | #98、#104 | B `test_web_receives_real_batches_before_response_and_atomic_history`；L | [Web 屏障](web-answer-streaming.md)、[真实模型](streaming-final.md) |
| 2 | iOS 提前阅读最终正文 | #99、#104 | I `receivesBatchesBeforeSavedHistoryThroughOneForegroundSubscription`；L | [手机分批](ios-answer-streaming.md)、[真实模型](streaming-final.md) |
| 3 | 两端同会话同轮回答 | #99、#100、#104 | J `realStreamingAcrossClientsSurvivesBackgroundAndMatchesSavedHistory`；L 核对同一会话及轮次身份 | [双客户端](ios-answer-streaming.md)、[联合实连](streaming-final.md) |
| 4 | 查询期间显示真实状态 | #98 | B `test_web_receives_real_batches_before_response_and_atomic_history`；W “只显示真实阶段和耗时” | [阶段隔离](web-answer-streaming.md) |
| 5 | 仅最终正文进入回答 | #98 | B `test_query_commentary_is_not_recalled_as_an_assistant_answer`；无工具请求断言 | [阶段与历史回读](web-answer-streaming.md) |
| 6 | 摘要、恢复和工具参数不入正文 | #98、#104 | B 阶段、正文精确比较、`test_answer_accepts_reasoning_blocks_without_exposing_them_as_body` 与无正文重试回归；[上下文回归](../../tests/test_context_recovery.py) | [隔离机制](web-answer-streaming.md)、[真实模型推理块修复](streaming-final.md) |
| 7 | 按真实批次显示 | #98、#99、#103、#104 | B 无返回屏障；U `testAnswerReallyGrowsBeforeCompletionAndThenBecomesSavedHistory`；L | [分批与阅读](stream-reading.md)、[真实模型](streaming-final.md) |
| 8 | 生成中不误称已保存 | #98、#99 | B 历史仍 running／answer 为空；I `receivesBatchesBeforeSavedHistoryThroughOneForegroundSubscription` | [手机两批尚未保存](ios-answer-streaming.md) |
| 9 | 保存成功进入历史 | #98、#99 | B 完成后预览清除；U 保存后返回再进入 | [保存界面](ios-answer-streaming.md) |
| 10 | 实时结果与历史一致 | #98、#99、#104 | B 精确全文比较；J 双客户端；L 保存与剪贴板比较 | [屏障](web-answer-streaming.md)、[实连](streaming-final.md) |
| 11 | 刷新恢复当前正文 | #100 | B `test_snapshot_subscription_handoff_and_two_clients_resume_cumulative_text`；W “恢复正文采用累计快照” | [恢复](answer-recovery.md) |
| 12 | 普通断线不取消执行 | #100 | B 双订阅关闭仍继续；[通知回归](../../tests/test_reconnect.py) `test_closing_subscription_keeps_background_call_and_other_page_gets_release` | [恢复及双订阅](answer-recovery.md) |
| 13 | 重连先快照后订阅，无漏重 | #100、#104 | B 快照交接屏障；I `disconnectedStreamReconcilesSnapshotAndHistoryBeforeResubscribing` 含 retrying 参数 | [恢复](answer-recovery.md)、[重试交叉](streaming-final.md) |
| 14 | 另一设备恢复已有正文 | #100 | B 双客户端首事件相同；J 真实 HTTPS 同轮正文 | [跨设备恢复](answer-recovery.md) |
| 15 | iOS 前台实时接收 | #99 | I 首批／第二批不推进轮询时钟，且只建立一个 SSE；J | [手机订阅](ios-answer-streaming.md) |
| 16 | 后台或锁屏断开客户端接收 | #99 | I `backgroundAndPageExitCancelReceivingAndForegroundRestoresSnapshot`，传输实际取消 | [生命周期](ios-answer-streaming.md)；模拟器 Home 退后台，锁屏以公开生命周期测试覆盖，未做真机锁屏 |
| 17 | 后台断订阅不停止后端 | #99 | J 真实后端在手机后台生成，I 没有 stop POST | [后台生成](ios-answer-streaming.md) |
| 18 | 前台立即恢复状态与正文 | #100 | I `disconnectedStreamReconcilesSnapshotAndHistoryBeforeResubscribing`；J | [模拟器前后台](answer-recovery.md) |
| 19 | 关闭一个订阅不影响其他订阅 | #99、#100 | B 双订阅中关闭一个，另一个继续收到第三批；I 页面退出取消 | [双订阅](answer-recovery.md) |
| 20 | 重启明确显示原轮中断 | #100 | B `test_process_restart_discards_preview_preserves_dataset_and_never_replays_execution`；I restarted 参数；W 同库重启 | [真实子进程终止重启](answer-recovery.md) |
| 21 | 重启不重放请求或查询 | #100 | B 同上核对外部请求计数 | [真实进程与请求计数](answer-recovery.md) |
| 22 | 切会话隔离旧文字 | #100 | I `changedOwnershipInvalidatesOldConnection(conversation)`；W 旧连接回调失效 | [恢复隔离](answer-recovery.md) |
| 23 | 换服务或数据集隔离旧连接 | #100 | I 同上 configuration／dataset；[Web 数据集切换](../../frontend/tests/ui/session-pages.spec.ts) | [归属失效](answer-recovery.md) |
| 24 | 同轮不同尝试隔离 | #100、#101 | I `attemptReplacementWrongOwnershipAndStreamFailureRemainObservable`；B 中途失败重试 | [重试替换](answer-retries.md) |
| 25 | 持久终态不被迟到正文覆盖 | #100 | I `receivesBatchesBeforeSavedHistoryThroughOneForegroundSubscription` 和不完整终态参数；W 远端停止及迟到正文 | [恢复](answer-recovery.md)、[终态](answer-outcomes.md) |
| 26 | 失败清空并提示重试 | #101 | B `test_midstream_disconnect_retries_with_empty_preview_and_unchanged_input`；I `failedAnswerClearsBeforeRetryAndCompleteDeliveryRemainsInHistory` | [两端重试截图](answer-retries.md) |
| 27 | 新尝试从头显示 | #101、#104 | B 尝试身份变化；W 与 I 的 retrying 断线恢复矩阵 | [重试](answer-retries.md)、[交叉恢复](streaming-final.md) |
| 28 | 重试复用已完成查询 | #101 | B 中途失败用例断言请求相同且只有一个工具；#104 交叉用例再次核对 | [请求／查询计数](answer-retries.md) |
| 29 | 仅允许的错误重试 | #101 | B `test_stream_errors_keep_classification_without_accidental_fallback`、`test_retry_exhaustion_and_stop_during_backoff_release_the_turn` | [故障矩阵](answer-retries.md) |
| 30 | 耗尽或不可重试错误明确失败 | #101 | B 同上；W 失败终态／错误原因；I 持久历史投影 | [失败与耗尽](answer-retries.md) |
| 31 | 失败预览不进入模型上下文 | #101 | B `test_sdk_stream_ending_without_stop_reason_is_retried_instead_of_saved`，重试协议及历史搜索／已知身份回读 | [SDK 截断与回读反例](answer-retries.md) |
| 32 | 停止立即冻结正文 | #102 | W “点击停止立即冻结正文”；I `stopFreezesUntilSavedOrUnsavedResultIsConfirmed` | [两端停止界面](answer-outcomes.md) |
| 33 | 停止固定原轮次 | #102 | I `capturedOldTargetNeverStopsLaterTurnOrChangesWithNavigation`；[原目标 HTTP](../../tests/test_stop.py) | [原轮次与跨页停止](answer-outcomes.md) |
| 34 | 停止等待在途与保存收束 | #102、#104 | B `test_stop_waits_for_stream_and_saves_real_normal_or_budget_outcome`、新交叉用例 | [停止屏障](answer-outcomes.md)、[交叉故障](streaming-final.md) |
| 35 | 停止中禁止下一轮 | #102 | B 在途阶段下一轮 409；I stopState／canSubmit；W 冻结 | [停止收束](answer-outcomes.md) |
| 36 | 停止后采用真实保存结果 | #102 | B 普通完成／预算停止两种边界；I 保存分支；U | [真实保存界面](answer-outcomes.md) |
| 37 | 保存失败仍可查看复制 | #102、#104 | B 最终／请求事实保存故障；W 系统剪贴板全文；U `testStoppedStreamFreezesThenShowsSavedOrUnsavedCopy` | [未保存复制](answer-outcomes.md)、[交叉场景](streaming-final.md) |
| 38 | 正文标注未保存 | #102 | W 未保存提示；I 关联 unsavedFact；U 同上 | [Web／iOS 未保存截图](answer-outcomes.md) |
| 39 | 未保存正文不冒充历史 | #102 | B `test_stream_result_and_fact_storage_failures_keep_unsaved_body_without_history` | [SQLite 两类故障](answer-outcomes.md) |
| 40 | 保存故障禁止新输入 | #102 | B 下一轮 503 且没有后续请求；I canSubmit；W 禁用输入 | [故障屏障](answer-outcomes.md) |
| 41 | 流式 Markdown 排版 | #103 | W “流式中文阅读”桌面／窄屏；U `testStreamingMarkdownFollowsGrowthAndPreservesReadingPosition` | [两端阅读](stream-reading.md) |
| 42 | 半成型 Markdown 后续重排 | #103 | W 未闭合代码、表格、链接；U 同上 | [代码／表格／链接](stream-reading.md) |
| 43 | 中文长文与表格可读 | #103 | W 两种宽度；U `testChineseTableScrollsToLastColumn`；[固定截图](../../ios/PotatoTests/SnapshotTests.swift) | [中文及横向表格](stream-reading.md) |
| 44 | 底部自动跟随增长 | #103 | W bottomGap；U 同轮增长后的可见位置 | [最新正文](stream-reading.md) |
| 45 | 上翻后保位 | #103 | W scrollTop；U 上翻后后续批次不抢位置 | [上翻暂停](stream-reading.md) |
| 46 | 回到最新恢复跟随 | #103 | W 点击后继续增长；U 点击后继续生成及保存 | [回到最新](stream-reading.md) |
| 47 | 复制文案区分当前与完整 | #103、#102 | W 系统剪贴板原 Markdown；U 当前／未保存／完整标签及系统粘贴 | [阅读复制](stream-reading.md)、[未保存复制](answer-outcomes.md) |
| 48 | 输出截断标不完整 | #102 | B `test_truncated_stream_keeps_incomplete_normal_and_budget_outcomes`；I output_limit；W 不完整复制 | [截断边界](answer-outcomes.md) |
| 49 | 预算与停止各自真实终态 | #102 | B 普通／预算停止、截断；I `incompleteSavedOutcomeReplacesPreviewWithoutChangingItsReason` | [真实终态矩阵](answer-outcomes.md) |
| 50 | 明确不支持才完整响应降级 | #101 | B 明确文字／参数／结构化错误码三类；I 完整方式进入历史；W／U 降级原因 | [可控拒绝及两端降级](answer-retries.md)；本轮真实服务支持流式，无真实能力拒绝 |
| 51 | 普通故障不误判不支持 | #101 | B `test_stream_errors_keep_classification_without_accidental_fallback` | [连接、超时、429、5xx 与非法请求矩阵](answer-retries.md) |
| 52 | 分块敏感内容与安全链接 | #98、#103 | B 跨块、跨行、多文本块脱敏；[Web 呈现测试](../../frontend/tests/presentation.test.mjs)；W 安全 Markdown；U 外链 | [脱敏](web-answer-streaming.md)、[排版和外链](stream-reading.md) |
| 53 | 规划统一协调作答 | #98 | B 请求开始事实、无工具作答与完整历史；[结构检查](../../scripts/check_modules.py) | [公开能力边界](web-answer-streaming.md) |
| 54 | 作答、重试、降级共用预算 | #98、#101 | B `test_fallback_at_63_uses_final_reserve_but_final_stream_never_retries`；[总账](../../tests/test_call_budget.py) | [63／64 边界](answer-retries.md) |
| 55 | 预算收尾不增工具、摘要或重试 | #98、#101、#102 | B 第 64 次失败不重试／降级，截断与停止保留原校验；[预算与上下文](../../tests/test_call_budget.py) | [预算](answer-retries.md)、[停止和截断](answer-outcomes.md) |
| 56 | 预览与持久游标分开 | #98、#100 | B 相邻正文快照 cursor 不变；双订阅首事件无持久 cursor；I 初始 after 为历史 cursor | [游标交接](answer-recovery.md) |
| 57 | CLI、旧库、提交、偏好兼容 | #104 | [固定 Web／SSE／CLI 基线及旧库续聊](../../tests/test_migration_baseline.py)；[提交](../../tests/test_submissions.py)；[偏好](../../tests/test_preferences.py)；[iOS 草稿](../../ios/PotatoTests/DraftTests.swift)、[恢复](../../ios/PotatoTests/RecoveryTests.swift) | [本轮全量结果与兼容范围](streaming-final.md) |
| 58 | 公开边界与真实模型证据分开 | #104 | B、I、W、U 与 L；完整后端／Web／iOS 回归 | [本轮真实模型、模拟器和可控模型分层结论](streaming-final.md) |
