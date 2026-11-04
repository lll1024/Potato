# iOS 助手会话能力

本客户端通过现有[助手会话 HTTP 契约](sessions.md)访问同一后端。后端六项能力、Web/CLI 与数据目录锁保持原样。当前范围为 #89 的配置与只读最近一页；发送、草稿、提交恢复、轮询、停止与更早分页由 #90–#94 继续实现。

## 阅读入口

- `ios/SessionKit/SessionAccess.swift`：Interactor 使用的公开 interface、连接状态与领域数据。
- `ios/SessionKit/SharedSessions.swift`：App 生命周期内唯一共享实例；地址配置、连接核对、HTTP 映射和服务失效规则。
- `ios/Potato/AppAssembly.swift`：FactoryKit 装配，解析一次共享能力后通过构造参数注入两个 Interactor。
- `ios/Potato/SessionList/` 与 `ios/Potato/Conversation/`：两个 VIPER module；View 只显示状态与转交操作，Interactor 调用公开能力，Presenter 提供 Observation 展示状态，Router 管理进入、返回与连接设置。
- `ios/Potato/Settings/`：简单连接配置入口。

## 状态归属与读取

全部操作在 MainActor 使用。App 通过 `SharedSessions(databaseURL:)` 打开手机端 GRDB 配置文件；构造失败显示本机配置不可用，不伪造尚未配置。两个 Interactor 只接收 `any SessionAccess`。

| 公开操作 | 结果与失败 |
| --- | --- |
| `configure(address:)` | 短事务保存规范化地址；空串清除配置。校验或保存失败保留原配置；成功使旧请求失效 |
| `refresh()` | 返回 `SessionList`，更新共享连接与 `ServiceState`；列表失败抛错，不返回假空列表 |
| `history(sessionID:)` | 返回 `ConversationPage`，含完整轮次、下一页位置及数据集身份；404 独立表示会话不存在 |

`SessionFailure.obsolete` 表示响应所属配置、数据集或读取次序已过期，不能作为当前页面结果。配置变化返回列表；数据集身份变化清除旧页面，必要时重新读取列表。其他错误以可理解文案展示，保留错误与后端终态的区别。

共享能力拥有服务地址、连接状态和后端快照；连接状态与后端可接受输入、忙碌、停止及保存故障分开。配置存在不表示连接已成功，只有真实 HTTP 核对成功才显示已连接。页面局部加载和读取错误归 Presenter，页面释放不会停止后端轮次。

配置只接受无用户名、密码、查询、片段或子路径的 HTTPS 服务根地址，使用系统证书校验；不存模型或地图密钥。地址通过 GRDB 短事务保存。保存失败不切换内存配置。App 重启从同一文件恢复配置并重新核对连接，不恢复虚构的“已连接”。手机不复制后端完整历史。

列表读取 `GET /api/snapshot` 与 `GET /api/sessions?limit=50`；对话使用 `GET /api/sessions/{id}?limit=50&summary=false&include_messages=false`。`input`、`answer` 保持完整原文，只排除底层模型协议。保留不透明列表游标与历史 `next_before`，当前不提供继续加载操作。

更换地址使旧读取失效；重复刷新只采用较新的结果。服务身份来自持久 `stream_id`，普通重启不凭进程身份推断为新数据集。网络、HTTP、解码和会话不存在分别表达，错误不能生成空列表、空历史或后端失败终态。取消客户端读取不调用后端停止接口。

后续提交身份、核对、当前运行状态、前后台轮询和本地恢复材料继续归此 App 级能力；两个 Presenter 不分别管理全局执行与提交恢复。

## 构建与验证

工程：`ios/Potato.xcodeproj`；生成源：`ios/project.yml`（XcodeGen 2.46.0）。Swift 6，最低 iOS 17，仅 iPhone；依赖解析结果随工程提交。`ios/PotatoTests/` 通过共享公开 interface、真实文件数据库及真实 HTTPS 后端验证；`ios/PotatoUITests/` 验证设置、进入和返回。运行步骤及本次证据见 [iOS 验收](../acceptance/ios-readonly.md)。
