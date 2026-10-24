# 助手会话：当前公开契约

公开入口为 [travel_app.sessions](../../travel_app/sessions/__init__.py)。`create_app(data_dir, *, resources=..., static_dir=None, request_shutdown=None, clock=None, preference_clock=..., preference_wait=..., preference_timeout=60)` 构造现有 FastAPI 服务；根 `web.py` 调用公开 `main(*, app_factory=create_app, load_environment=load_dotenv)`，启动参数、数据目录、配置和 HTTP/SSE 协议保持。

服务 lifespan 创建并锁定数据目录，打开本机记录、恢复已提交事实、配置外部资源，再启动会话及既有偏好后台处理；退出时停止接受输入，等待当前执行和保存收束，关闭资源并释放锁。`resources` 是异步上下文管理器，产出 `Runtime(client, tools, model)`；模型客户端及已发现的查询 adapter 在服务使用期间保持有效。已被占用的数据目录、版本不兼容及配置错误保留固定启动诊断。

可直接使用 `TurnRuntime(records, runtime, ...)` 的 `start()`、异步 `submit(input, session_id, submission_id)`、`submission_result(submission_id)`、`stop(turn_id)`、`snapshot()` 和异步 `shutdown()`。通常使用 `create_app` 管理其生命周期。`changed` 是瞬时变化通知，不是持久游标。公开构造参数 `app_factory` 与 `load_environment` 供启动装配注入应用和环境加载，正式根入口保留默认行为。

直接装配时，构造 `Runtime(client, tools, model)`（已配置的异步模型客户端、已发现的 `PlanningTools`、模型名），再构造 `TurnRuntime(records, runtime, *, request_shutdown=None, clock=None, preference_clock=time.time, preference_wait=asyncio.sleep, preference_timeout=60)`。先在已创建并独占的数据目录打开 `LocalRecords`、执行 `recover()`，再 `start()`；资源有效期覆盖全部运行轮次。退出必须 `await shutdown()` 后才关闭记录及外部客户端。`start()` 仅用于首次启动，不在运行中重复调用。`clock()` 返回带时区的 `datetime`，用于本轮日期解释；`preference_clock()` 返回秒数，`preference_wait(delay)` 为异步等待。`request_shutdown()` 是记录事实失败后的服务退出通知，不能提前关闭在途资源。默认使用 `create_app` 可避免自行管理目录锁及上述顺序。

`storage_failed(message, *, kind=None, turn_id=None)` 是明确的记录故障通知入口：立即拒绝新输入并向规划设置停止信号，`kind` 和 `turn_id` 关联未保存事实；仍须等待执行和 `shutdown` 收束。提交输入须非空；`submission_id` 可为 `None`，提供时使用非空、最多 128 字符的身份。HTTP 对标题、分页和输入另作格式校验，直接调用方承担相同输入约束。

提交先核对身份，再检查接受状态和全局名额。同身份同输入返回原接受身份；冲突为 409，已删除会话的身份为 410，全局忙碌为 409，无法保存或退出为 503。直接调用抛出 `RunRejected(status_code, detail)`，HTTP 层保留相同错误映射。接受输入与偏好待处理登记共同提交后才启动后台规划；关闭网页或订阅不取消已接受任务。

会话将记录快照及偏好背景交给 `planning.plan`，只使用其结构化终态和只读保存材料，随后一次原子保存。它不解析 `turn.finished` 观察事件，也不维护完整协议和模型视图两份状态。观察回调逐条保存请求、工具及上下文事实；保存失败抛出 `RecordAbort`，禁止后续调用。执行已返回与结果已保存分别表达：保存失败的轮次保持最后持久状态，`snapshot()` 的 `accepting=false`、`storage_error`、`unsaved_fact` 明确显示故障，不伪造完成或模型失败。

停止只接受原轮次身份。停止期间保留全局名额，已进入外部服务的调用及保存尝试真实收束后才释放。重启不重放请求或普通重试；未完成事实按原恢复规则标识中断。

## 查看、管理与通知

同一 `TurnRuntime` 提供下列独立操作。HTTP 路由只校验请求格式、调用操作及转换响应，不持有分页、去重或通知循环。

| 公开操作 | 行为及 HTTP 映射 |
| --- | --- |
| `history(limit=50, cursor=None)` | 会话列表与不透明分页游标；`GET /api/sessions` |
| `history_page(session_id, limit=50, before=None, summary=False, include_messages=True)` | 一致读取轮次、轨迹、用量及事件位置；`GET /api/sessions/{id}` |
| `search(session_id, query, scope="full")` | 脱敏搜索词，返回 `matches` 定位信息；`GET /api/sessions/{id}/search` |
| `payload(payload_id, offset=None, limit=8192)` | 完整载荷或分段载荷；`GET /api/payloads/{id}` |
| `rename(session_id, title)` / `delete(session_id)` | 脱敏标题、管理会话并保留原身份与错误语义；`PATCH` / `DELETE /api/sessions/{id}` |
| `service_snapshot()` | 当前运行状态和本机记录的一致事件位置；`GET /api/snapshot` |
| `notifications(after=None, stream_id=None, last_event_id=None)` | 先校验位置，返回异步 SSE 文本迭代器；`GET /api/events` |

读取只调用本机记录公开的一致读取操作，不取得数据库连接或安排事务。不存在的会话或载荷返回 404，非法分页或搜索范围返回 422；删除运行中的会话返回 409。读取故障停止接受输入并抛出 `RunRejected(503, detail)`；改名、删除保留各自未保存提示。`RunRejected.detail` 可以是原协议中的文本或含 `code` / `message` 的对象。

`last_event_id` 优先于初始 `after`。位置超出当前记录或 `stream_id` 不匹配时要求重新读取快照（409 `SNAPSHOT_REQUIRED`）。持久 `trace` 含事件身份和游标，瞬时 `service.state` 无持久游标；断线后从最后实际处理的位置补齐。读取通知故障只展示最后提交事实与不可用状态。关闭迭代器或 HTTP 订阅不取消后台轮次，多个页面独立订阅。

## 前端入口与状态归属

公开入口 [frontend/src/sessions/index.ts](../../frontend/src/sessions/index.ts) 导出 `SessionsApp`。外层 [main.tsx](../../frontend/src/main.tsx) 只装配会话界面与旅行者偏好的公开 `PreferencesView`；`SessionsApp` 接收该组件（`onBack` 回到对话），不接管偏好规则。

提交身份、不确定接受结果、同身份核对与重试、仅清理匹配的已提交草稿、当前会话和停止目标均在会话能力内部协调。通知恢复维持最后实际处理游标，状态核对不跳过未收到事件；数据目录变化使历史、草稿、提交回调和分页请求失效。侧栏删除会话清理相应草稿与缓存，切换会话保留独立草稿与已加载历史。

对话和执行轨迹使用各自的显示投影。[useSessionPage](../../frontend/src/sessions/useSessionPage.ts) 负责快照、分页与搜索定位请求、失效及迟到响应屏蔽；[sessionPages](../../frontend/src/sessions/sessionPages.ts) 统一按轮次和事实身份合并。刷新整轮替换相关请求、工具及事件，按快照游标保留较新事实；发现历史间隙时保留可补齐的分页前沿，迟到旧分页只补充对象，不关闭新的前沿，重复页不会重复显示；对话保留轮询，轨迹保留事件驱动刷新。滚动跟随、定位、桌面和移动显示方式保持。

实现见 [runtime.py](../../travel_app/sessions/runtime.py)、[http.py](../../travel_app/sessions/http.py) 与 [SessionsApp.tsx](../../frontend/src/sessions/SessionsApp.tsx)。偏好生命周期通过其公开契约协作；规划资源通过旅行规划公开装配入口提供，助手会话不直接依赖资料获取 implementation。

验证：[公开查看与管理](../../tests/test_session_contracts.py)、[提交身份](../../tests/test_submissions.py)、[原目标停止](../../tests/test_stop.py)、[保存故障](../../tests/test_storage.py)、[通知续接](../../tests/test_reconnect.py)、[真实根入口与旧数据](../../tests/test_migration_baseline.py)、[数据集与迟到分页](../../frontend/tests/ui/session-pages.spec.ts)及[浏览器交互](../../frontend/tests/ui/conversation.spec.ts)。
