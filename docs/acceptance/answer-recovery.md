# 当前正文恢复与失效消息隔离（#100）

2026-11-06，本记录覆盖父规格 #97 的故事 3、11–14、18–25、56。基点为 `89a3909`，延续 #98／#99 的累计正文协议；后端运行真实会话、规划和 SQLite，只在外部模型／查询连接控制分块及暂停。iOS 使用公开 `SessionAccess`、真实 GRDB 文件及可控 HTTP 传输，并通过 localhost HTTPS 与真实后端联通。界面环境为 Chrome 与 iPhone 17 Pro／iOS 26.5 模拟器、Swift 6。

## 结果与边界

刷新、另一客户端进入和普通重连先恢复当前快照，再接收累计对象；快照与订阅交接期间生成的正文可直接补齐，不拼接增量。正文、服务状态和持久轨迹分别保留次序；已关闭连接的所有回调失效。重复次序不能回滚正文，缺失或失效尝试不能被旧消息复活。暂未核对到新轮的正文保留到历史确认归属，避免模型暂停后永远缺少当前正文。

iOS 恢复前台先核对进程、数据集、活动轮次、累计正文及历史，再建立唯一订阅。后台仍生成时恢复当前正文，后台已完成时直接采用保存历史。退出后重新进入相同会话可再次恢复同次序的有效累计对象；原连接及更旧响应继续失效。进程身份变化清除预览，同库的持久数据集身份保持。

真实子进程在正文已经可见、模型尚未完成时被终止；重启后预览为空，原轮为 `terminated / service_interrupted`，没有模型或查询重放。双订阅测试关闭其中一个连接后，另一连接继续收到后续正文，后台模型继续执行。没有新增逐块永久记录或客户端完整历史副本。

生产模型服务、真机、iOS 17、私网／蜂窝和 TestFlight 不属于本次证据；生产模型联合验收仍由 #104 完成，重试降级、停止／未保存和阅读打磨分别由 #101–#103 验收。

## 故事与证据

| 父规格故事 | 当前证据 |
| --- | --- |
| 3、11、13、14 | `AnswerStreamingTests.test_snapshot_subscription_handoff_and_two_clients_resume_cumulative_text`：第一批快照后、订阅前生成第二批，两订阅返回完全一致的累计正文；普通断线的新连接返回同一当前对象。Web 刷新及重连界面测试读取累计快照后继续增长 |
| 12、19 | 同一 HTTP 测试关闭一个订阅，另一订阅继续接收第三批，模型响应尚未返回；iOS 后台仅取消 GET，原后台任务继续生成 |
| 18、25 | `AnswerStreamingTests.disconnectedStreamReconcilesSnapshotAndHistoryBeforeResubscribing`：普通断线后运行／完成／重启三种恢复；`ReadingNavigationTests.testAnswerReallyGrowsBeforeCompletionAndThenBecomesSavedHistory`：实际按 Home 后继续生成，激活恢复；再次后台完成后激活读取保存结果 |
| 20、21 | `test_process_restart_discards_preview_preserves_dataset_and_never_replays_execution`：真实进程 kill／restart；同库不同进程、空预览、真实中断历史，外部模型请求计数不变且没有查询。Web 及 SessionAccess 独立验证新进程清除旧预览 |
| 22、23 | 原有 iOS 配置／会话／数据集／进程参数化失效测试、分页／提交恢复回归；Web 旧连接的正文、状态、删除轨迹和 error 均不能影响新连接；数据集切换沿用既有分页及提交隔离测试 |
| 24、25 | Web 累计快照、乱序／同次序重复、尝试替换、失效和 null 缺失测试；SessionAccess 延迟旧快照、重复空预览复活测试；已有持久终态优先和迟到历史测试继续通过 |
| 56 | HTTP 每次正文通知均没有持久 cursor，三批正文之间快照 cursor 不变；原轨迹续接和 Last-Event-ID 回归继续验证最后实际处理位置 |

## 缺陷复现与审查

首轮公开界面测试证实 Web 较旧及同次序事件会覆盖较新正文；公开 SessionAccess 测试证实空预览会被同次序消息复活。修复后对应测试通过。iOS 在页面切换时重置展示次序，使新连接能恢复同次序的有效正文，同时继续使用操作身份隔离旧连接。

独立 Standards／Spec 两轴审查各发现一项交接问题：旧状态可能清空已恢复正文；新轮正文先于活动状态时被静默丢弃。分别补充公开界面交错测试，均先红后绿。服务状态现附带取得状态时的 `answer_revision`；Web 忽略早于当前正文的状态，保留最新全局累计对象，按会话、轮次及持久历史决定展示。两轴复审均为 0 项未解决问题。

最终补充快照后、订阅前服务重启的竞争：两端在当前连接首次发现新进程身份时立即清除旧预览、使在途旧快照失效，再读取真实状态；不在客户端伪造中断终态。公开 Web 与 SessionAccess 测试均先复现红灯，再修复。一次 Swift Testing 方法过滤实际选中 0 项，未计入验证；改为运行完整恢复套件后实际复现失败。

首轮 Chrome 启动受沙盒应用权限限制，在正常应用权限下重新运行取得有效红灯；该环境失败没有当作功能复现。后续一次重启界面断言误用了文案“服务重启中断”，按既有“服务中断”公开文案修正后通过。审查修复前的后端全量运行主动停止，最终代码另行全量验证。

## 界面证据

- [Web 普通重连后继续增长](answer-recovery/web-reconnected.png)
- [Web 同库重启后显示服务中断](answer-recovery/web-restarted.png)
- [iOS 恢复前台取得累计正文，仍未保存](answer-recovery/ios-foreground.png)
- [iOS 后台完成后恢复真实保存回答](answer-recovery/ios-saved.png)

## 最终验证

- 后端最终全量 `.venv/bin/python -m unittest discover -s tests -v`：369 项通过，326.174 秒。
- 公开 HTTP 流式恢复与原通知续接定向回归：13 项通过，6.751 秒。
- 前端 `npm run build --prefix frontend` 成功，`npm test --prefix frontend` 13 项通过；最终 `npm run test:ui --prefix frontend` 59 项通过，1.5 分钟。
- iOS 完整 `xcodebuild test`：71 项 Swift Testing 能力／真实集成、10 项截图／状态和 14 项界面测试全部通过，675.647 秒；最终进程变化修复后再次运行全部 `PotatoTests` 及前后台恢复界面：72 项能力／集成、10 项截图／状态、1 项界面全部通过，46.125 秒。
- `.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app`：0 errors；模块结构、公开契约链接及 `git diff --check` 均通过。

## Standards

基点 `89a3909`，审查本次任务差异。首轮 1 项快照／状态交接契约问题已修复；复审及最终进程失效处理复核均为 0 项未解决问题，无需处理的代码异味。

## Spec

首轮 1 项新轮正文提前到达后丢失的问题已修复；代码、测试、故事映射、公开契约和最终进程失效处理均已复核，0 项未解决问题。范围覆盖 #100，后续异常体验、阅读打磨与生产模型仍分别由 #101–#104 验收。

最终汇总：Standards 0 项，Spec 0 项；两轴均无未解决的最严重问题。
