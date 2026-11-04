# iOS 助手会话能力

本客户端通过现有[助手会话 HTTP 契约](sessions.md)访问同一后端。后端六项能力、Web/CLI 与数据目录锁保持原样。当前覆盖 #89 的配置、最近一页阅读和 #90 的新建、续聊、发送前保存与共享前台轮询。完整草稿、提交恢复、停止及更早分页由 #91–#94 继续实现。

## 阅读入口

- `ios/SessionKit/SessionAccess.swift`：Interactor 使用的公开 interface、连接状态与领域数据。
- `ios/SessionKit/SharedSessions.swift`：App 生命周期内唯一共享实例；地址配置、原子提交材料、接受身份、连接核对、唯一轮询循环和服务失效规则。
- `ios/Potato/AppAssembly.swift`：FactoryKit 装配，解析一次共享能力后通过构造参数注入两个 Interactor。
- `ios/Potato/SessionList/` 与 `ios/Potato/Conversation/`：两个 VIPER module；View 只显示状态与转交操作，Interactor 调用公开能力，Presenter 提供 Observation 展示状态，Router 管理进入、返回与连接设置。
- `ios/Potato/Settings/`：简单连接配置入口。

## 状态归属与读取

全部操作在 MainActor 使用。App 通过 `SharedSessions(databaseURL:)` 打开手机端 GRDB 客户端文件；构造失败显示本机配置不可用，不伪造尚未配置。两个 Interactor 只接收 `any SessionAccess`。

| 公开操作 | 结果与失败 |
| --- | --- |
| `configure(address:)` | 短事务保存规范化地址；空串清除配置。校验或保存失败保留原配置；成功使旧请求失效 |
| `refresh()` | 返回 `SessionList`，更新共享连接与 `ServiceState`；列表失败抛错，不返回假空列表 |
| `history(sessionID:)` | 返回 `ConversationPage`，含完整轮次、下一页位置及数据集身份；404 独立表示会话不存在；更新正在观察的对话 |
| `submit(input:sessionID:)` | 先保存原始请求，再 POST；返回关联原请求、真实会话和轮次的 `SubmissionReceipt`，只表示已接受 |
| `watchConversation(sessionID:)` | 指定轮询观察的对话；切换使旧历史请求失效，空目标用于新建页 |
| `setForeground(_:)` | 幂等启停唯一轮询；前台立即刷新，随后每次核对后等待约两秒；取消读取不停止后端任务 |

`SessionFailure.obsolete` 表示响应所属配置、数据集或读取次序已过期，不能作为当前页面结果。配置变化返回列表；数据集身份变化清除旧页面，必要时重新读取列表。其他错误以可理解文案展示，保留错误与后端终态的区别。

共享能力拥有服务地址、连接状态和后端快照；连接状态与后端可接受输入、忙碌、停止及保存故障分开。配置存在不表示连接已成功，只有真实 HTTP 核对成功才显示已连接。页面局部加载和读取错误归 Presenter，页面释放不会停止后端轮次。

配置只接受无用户名、密码、查询、片段或子路径的 HTTPS 服务根地址，使用系统证书校验；不存模型或地图密钥。地址通过 GRDB 短事务保存。保存失败不切换内存配置。App 重启从同一文件恢复配置并重新核对连接，不恢复虚构的“已连接”。手机不复制后端完整历史。

列表读取 `GET /api/snapshot` 与 `GET /api/sessions?limit=50`；对话使用 `GET /api/sessions/{id}?limit=50&summary=false&include_messages=false`。`input`、`answer` 保持完整原文，只排除底层模型协议。保留不透明列表游标与历史 `next_before`，当前不提供继续加载操作。

更换地址使旧读取失效；重复刷新只采用较新的结果。服务身份来自持久 `stream_id`，普通重启不凭进程身份推断为新数据集。网络、HTTP、解码和会话不存在分别表达，错误不能生成空列表、空历史或后端失败终态。取消客户端读取不调用后端停止接口。

`sessionList`、`conversation`、`conversationFailure`、`submission`、`pendingSubmission` 和 `canSubmit` 是共享可观察投影。轮询依次核对快照、列表及当前对话，网络失败保留最后成功的持久事实并显示连接不可用；保存故障保留 `storageError` / `unsavedFact`，禁止新输入。地图供应商暂停不独立阻止发送。两个 Presenter 不维护各自的轮询或全局名额。

## 发送边界

`submit` 保留原始输入（只以去空白副本判断是否为空），在 GRDB 同一短事务中保存提交 UUID、规范化服务地址、持久数据集身份、原始输入及原始可空目标，再发送 `POST /api/turns`。网络等待不持有数据库事务。本机保存失败抛 `localSaveFailed`，不发送并由输入框保留原文。发送中重复点击被拒绝，不创建队列；未知记录存在时禁止新身份发送。

新会话的原始目标为空，接受结果中新生成的会话不回写原请求。只有 schema、提交身份和目标一致的 202 才转换为接受结果；接受后清理匹配记录，等接受后发起的真实快照核对后解除本机接受核对屏障，完整回答仍只来自历史。已知的忙碌、输入校验、服务不可用和会话不存在分别反馈，释放明确未接受的记录；其他 HTTP、网络、解码或身份不一致结果保留原待确认材料为 `unknown`。接受后本机清理失败也保留原记录。

配置或数据集变化时不采用迟到接受结果，也不将旧输入发送到新服务。重新打开本机文件可读取未决原文及归属并显示未知；本阶段不自动核对、重发或提供手动恢复流程（#92）。未发送草稿的按会话持久保存及编辑竞争也由 #91 完成。当前输入框在发送失败时保留原文，成功只清空仍等于本次提交的输入。

App 的 scene phase 在活跃时启用共享轮询、离开活跃态时取消读取；完整前后台恢复、原目标停止及竞态验收由 #93 完成。页面释放不停止已接受任务或丢弃待确认记录。

## 构建与验证

工程：`ios/Potato.xcodeproj`；生成源：`ios/project.yml`（XcodeGen 2.46.0）。Swift 6，最低 iOS 17，仅 iPhone；依赖解析结果随工程提交。`ios/PotatoTests/` 通过共享公开 interface、真实文件数据库及真实 HTTPS 后端验证；`ios/PotatoUITests/` 验证设置、进入和返回。运行步骤见 [iOS README](../../ios/README.md)；证据见 [#89](../acceptance/ios-readonly.md) 与 [#90](../acceptance/ios-submission.md)。
