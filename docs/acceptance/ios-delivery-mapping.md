# #88 的 50 条用户故事与 #95 验证对应

范围固定为 [父规格 #88](https://github.com/lll1024/Potato/issues/88)，集成提交 `f5ca6eb`（#89–#94）。本次 #95 补交付说明、复验并修正 App 的设备范围配置，不改变共享会话实现。每行区分已有实现／本机证据与尚缺交付条件；有对应测试不表示真实 iPhone 已验收。当前运行结果及原始日志统一见 [#95 验收](ios-delivery.md)，前序版本证据从[验收索引](README.md)进入。

测试入口均为 `ios/PotatoTests/` 或 `ios/PotatoUITests/`。为便于定位，下表的缩写对应：

- C：[SessionAccessTests](../../ios/PotatoTests/SessionAccessTests.swift)；F：[ConnectionFailureTests](../../ios/PotatoTests/ConnectionFailureTests.swift)。
- S：[SubmissionTests](../../ios/PotatoTests/SubmissionTests.swift)；D：[DraftTests](../../ios/PotatoTests/DraftTests.swift)；R：[RecoveryTests](../../ios/PotatoTests/RecoveryTests.swift)。
- P：[PollingTests](../../ios/PotatoTests/PollingTests.swift)（含停止公开行为）；G：[PaginationTests](../../ios/PotatoTests/PaginationTests.swift)。
- I：[SubmissionIntegrationTests](../../ios/PotatoTests/SubmissionIntegrationTests.swift)；H：[ReadingIntegrationTests](../../ios/PotatoTests/ReadingIntegrationTests.swift)。两者连接真实 HTTPS / FastAPI / 临时 SQLite，仅控制外部资源。
- U：[NavigationTests](../../ios/PotatoUITests/NavigationTests.swift)；V：[ReadingNavigationTests](../../ios/PotatoUITests/ReadingNavigationTests.swift)；N：[SnapshotTests](../../ios/PotatoTests/SnapshotTests.swift)。U/V 是模拟器点击，N 是固定系统／字号视觉比较。

## 逐项映射

| 故事 | 实现与可复查证据 | 尚需的交付验证 |
| --- | --- | --- |
| 1. 本人 iPhone 原生使用 | SwiftUI App；U `testCreateSendContinueAndReadSavedAnswer` | TestFlight 本人安装、真机系统实跑 |
| 2. 私网连接现有电脑 | HTTPS 配置；[Serve 两端步骤](../../ios/DELIVERY.md) | 同一 tailnet 私人 HTTPS 与蜂窝实测 |
| 3. 配置保存地址 | C `configurationSurvivesReopeningTheLocalDatabase`、`invalidAddressDoesNotReplaceSavedConfiguration` | 真机安装后保存及更新保留 |
| 4. 区分连接状态 | C `trueEmptyListIsDifferentFromUnavailableService`；U `testConnectReadReturnEmptyAndUnavailable` | 真机网络切换 |
| 5. Mac 不可用明确提示 | F 的网络／迟到错误；U 离线草稿；交付说明区分服务、网络和设备离线 | 场景五：真实 Mac 休眠／离线 |
| 6. 查看电脑会话列表 | C `readsFullExistingConversationThroughRealHTTPS`；U 连接进入返回 | 场景一的真实记录 |
| 7. 空状态与新建入口 | U `testConnectReadReturnEmptyAndUnavailable`；N `testEmptySessions` | 真机首装操作 |
| 8. 更早会话与历史 | G 全部公开分页行为；H 完整 53 会话／55 轮；V 分页交互 | 真机长列表阅读 |
| 9. 电脑开始手机续聊 | I `phoneAndDesktopShareSingleAcceptanceFullAnswersAndPreferences` | 场景一的蜂窝实测 |
| 10. 手机开始电脑可见 | I 同上，两个客户端读取相同历史 | 场景一的电脑 Web 核对 |
| 11. 新建并提交 | S `newSubmissionPreservesOriginalInputAndNullableTargetBeforeHTTP`；U 新建发送 | 真机提交 |
| 12. 续聊修改 | I 的第二轮原会话续聊；U 新建续聊完整回答 | 场景一 |
| 13. 共用旅行者偏好 | I 核对真实偏好后台保存「不吃辣」并进入模型背景 | 实际服务使用已有偏好；不另建手机偏好库 |
| 14. 完整输入与回答 | C 完整 HTTPS 原文；H 与阅读样例逐字比较 | 真机阅读 |
| 15. 标题列表表格链接 | N 中文长回答与深色大字号；V `testChineseTableScrollsToLastColumn` | 真机实际尺寸／字号核对 |
| 16. 打开外链 | V `testPaginationExternalLinkAndFullCopy` 打开 Safari 样例页 | 真机系统浏览器打开 |
| 17. 复制完整回答 | V 同上，复制粘贴后逐字核对完整 Markdown | 真机复制到其他 App |
| 18. 各会话独立草稿 | D `draftsRemainSeparateAcrossSessionsAndReopenOffline`；U 草稿切换 | 真机日常切换 |
| 19. App 结束恢复草稿 | D 真实文件重新打开；U `testDraftsSurviveNavigationReleaseAndAppRestart` | 真机结束后重开、覆盖更新 |
| 20. 离线保存且不能发送 | D `offlineEditingDoesNotQueueInputAndUnknownSubmissionKeepsItsOriginal`；U 离线编辑 | 场景五 |
| 21. 重连不自动发送 | D `reconnectingInForegroundDoesNotSendAnOfflineDraft` | 场景五真实网络恢复 |
| 22. 接受与未知分开 | S 接受身份／未知；U `testRestartRecoversOriginalSubmissionAndOpensSavedConversation` | 场景三 |
| 23. 断网核对避免重复 | I `missingLookupAndOriginalInFlightRaceAcceptsOnlyOneRealExecution`，真实模型调用计数 | 场景三实际蜂窝断连 |
| 24. 重开核对原提交 | R `reopeningChecksOriginalAcceptanceAndKeepsLaterDraft`；U 重启恢复 | 场景三真机进程重开 |
| 25. 未确认保留原文 | S `unknownAcceptanceKeepsOriginalRecordAcrossReopen`；U 长原文核对 | 场景三 |
| 26. 接受后打开原轮次 | I `realLostAcceptanceRecoversOrReportsDeletedWithoutAnotherExecution`；U 重启后打开原会话 | 场景三 |
| 27. 明确重试同一提交 | R `missingAcceptanceNeverAutoSendsAndExplicitRetryKeepsOriginalRequest`；I 404 与在途竞态 | 场景三；若实际只产生已接受，另留未接受后显式重试记录 |
| 28. 清理不覆盖晚草稿 | D `acceptanceOnlyClearsTheSubmittedDraftRevision`；R 重开后晚草稿；U 迟到接受 | 场景三编辑较晚草稿 |
| 29. 服务／数据集隔离 | R `changedServiceAndDatasetIgnoreLateCheckAndRecoverOnlyAtOriginalIdentity`；D 草稿归属；F 旧响应失效 | 真机切回归属核对；不向新服务重发 |
| 30. 真实运行与停止状态 | P 原目标停止与新快照；N 运行／停止快照 | 场景四 |
| 31. 约两秒刷新 | P `foregroundPollingReadsPersistedCompletionWithoutTreatingAcceptanceAsCompletion`；可控调度核对间隔 | 真机实际网络观察，弱网不承诺固定时延 |
| 32. 断网不是后端失败 | P `storageFailureAndOfflinePollingPreserveLastSavedRunningFact` | 场景二、三实际断连 |
| 33. 保存回答及真实终态 | I 实际完成／停止保存；H 全文；[后端停止回归](../../tests/test_stop.py)与[恢复回归](../../tests/test_recovery.py) | 真机读取同一保存结果；自动化不是实时旅行信息核实 |
| 34. 后台／锁屏继续执行 | I `desktopTurnStopsOnlyAfterExternalReturnAndForegroundRestoresSavedResults`；U 切后台后真实 HTTP 释放 | 场景二分别切 App 与锁屏 |
| 35. 回前台立即核对 | P `backgroundCancelsSlowReadAndForegroundImmediatelyRefreshesOnce`；I 前后台恢复 | 场景二 |
| 36. 页面切换保留状态 | U `testCrossDeviceStopSurvivesNavigationAndBackgroundCompletion`；共享单例 | 真机导航与运行并行 |
| 37. 全局忙碌反馈 | S `definiteRejectionDoesNotCreateAQueuedInput`；P 停止收束；I 双客户端 | 场景一、四跨设备竞争 |
| 38. 无自动输入队列 | S 明确拒绝不排队；D 重连不发送 | 场景五 |
| 39. 停止电脑轮次 | I 真实在途模型停止；U 跨设备停止 | 场景四 |
| 40. 固定原停止目标 | P `capturedOldTargetNeverStopsLaterTurnOrChangesWithNavigation`、`staleIdleSnapshotCannotReleaseStopAndChangedServiceRejectsLateStop` | 场景四核对原目标和后来轮次 |
| 41. 等停止真实释放 | P `stopsDesktopTurnAndWaitsForRealReleaseBeforeSending`；I 实际返回再保存 | 场景四，不把停止 POST 当作终态 |
| 42. 后端保存故障区分 | P 保存故障；N `testStorageFailureStaysVisibleBesideUnknownSubmission`；[记录事务回归](../../tests/test_record_contracts.py) | [保存故障说明](../../ios/DELIVERY.md)；不主动破坏本人数据制造故障 |
| 43. 电脑改名删除刷新 | H 真实 HTTP 改名／删除；G 刷新已展开页及 404 竞争 | 真实服务刷新复核 |
| 44. 超时校验冲突反馈 | S 参数化拒绝／未知；R `explicitRetryRejectionsRemainDistinctAndKeepTheOriginal`；F 读取错误 | 真机网络与版本检查；保留原文 |
| 45. TestFlight 安装更新 | [签名分发与覆盖更新步骤](../../ios/DELIVERY.md) | **未完成**：会员、签名、上传、处理、本人安装及覆盖更新 |
| 46. 运行／私网／到期要求 | [持续运行、私网接通、90 天更新与故障定位](../../ios/DELIVERY.md) | **未完成**：实际私网、构建上传时间及到期日 |
| 47. VIPER 职责 | [AppAssembly](../../ios/Potato/AppAssembly.swift)、[列表 module](../../ios/Potato/SessionList/SessionListModule.swift)、[对话 module](../../ios/Potato/Conversation/ConversationModule.swift)、[公开契约](../modules/ios-sessions.md)；U 外部行为 | 本次静态复核；不增加每类旁路测试 |
| 48. 成熟依赖 | [project.yml](../../ios/project.yml) 精确版本、[Package.resolved](../../ios/Potato.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/Package.resolved)传递依赖；本次 Swift 6 构建 | iOS 17 实际运行、签名分发校验 |
| 49. 共享 interface 测试 | S/D/R/P/G 经 `SessionAccess` 操作，真实 GRDB 文件；I/H 真实服务 | 本次完整回归见验收记录；不以私有字段断言 |
| 50. 分层证据 | [本次分层结果](ios-delivery.md)、完整日志／机器汇总及本映射 | 尚缺真机、蜂窝、私网及 TestFlight 证据；#95 保持打开 |

## 实现和范围约束复核

| 约束 | 核对入口与结论 |
| --- | --- |
| 唯一共享能力／页面不拥有全局运行 | `AppAssembly` 解析 FactoryKit singleton；两个 Interactor 构造注入 `any SessionAccess`；恢复、分页、停止与前后台统一进入它，无第二套手机后端或轮询 |
| 协议与持久化事实 | `SessionHTTP` 复用既有 HTTP；接受不生成回答，历史决定终态；I/H 用真实 FastAPI / SQLite 核对，测试模型不证明旅行内容真实性 |
| 原提交、文件事务、归属与竞争 | S/D/R/P/G、真实 GRDB 文件及 I 的接受／404 在途竞态；不改原身份原文可空目标，无离线自动发送，无跨网络持有事务 |
| 手机不存历史或密钥 | `SharedSessions` 的 GRDB 仅配置／草稿／待确认材料；历史为内存投影；无手机模型／地图密钥入口；未修改电脑记录格式 |
| 前台轮询及停止收束 | P、I、U 共用 `setForeground`、`stop(target:)` 和真实快照；无 SSE、后台执行承诺、推送、进度百分比或流式回答 |
| 两个 VIPER 页面及导航 | 列表／对话 module、Router；简单连接设置；MarkdownUI 封装于 AnswerView；无搜索、改名、删除、详细轨迹及偏好管理扩围 |
| 依赖与系统下限 | Swift 6、严格并发、iOS 17 配置；Alamofire、FactoryKit、GRDB、Swift Navigation、MarkdownUI、Swift Testing／XCTest／SnapshotTesting 按锁定版本构建。实际 iOS 17 运行尚缺 |
| 私网与分发 | Serve 私网方案与本人 TestFlight 流程已有说明；未启用 Funnel，未部署公网后端或新认证体系。签名、图标资源、分发校验及真机证据仍是开放交付项 |
| Web／CLI／旧数据兼容 | 本次只改客户端工程配置、文档和验证证据，未改后端、数据库、Web 或 CLI 生产行为；本次回归结果另见验收，不将历史通过冒充本次重跑 |

结论：50 条均已对应实现、证据或明确缺口；这是一份覆盖映射，不是 50 条全部验收完成的声明。#95 仍等待真实安装与远程使用验收，父规格不自动关闭或修改。
