# 助手会话：当前公开契约

Web／iOS 的当前真实模型联合证据与父规格 58 条故事覆盖见 [#104 联合验收](../acceptance/streaming-final.md)。可控外部模型、真实模型、模拟器及实际设备按该记录分别解释。

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

Web 回答复用 `AnswerBody` 的安全 Markdown 排版；每批累计正文重新解析，半成型代码、表格和链接可随后续正文重排。正文版本变化触发底部跟随；上翻暂停后保持阅读位置，点击“回到最新”恢复。复制保留当前展示的 Markdown 原文，并区分当前正文、未保存正文、不完整回答和完整回答。两端阅读验证见 [#103 验收](../acceptance/stream-reading.md)。

公开入口 [frontend/src/sessions/index.ts](../../frontend/src/sessions/index.ts) 导出 `SessionsApp`。外层 [main.tsx](../../frontend/src/main.tsx) 只装配会话界面与旅行者偏好的公开 `PreferencesView`；`SessionsApp` 接收该组件（`onBack` 回到对话），不接管偏好规则。

提交身份、不确定接受结果、同身份核对与重试、仅清理匹配的已提交草稿、当前会话和停止目标均在会话能力内部协调。通知恢复维持最后实际处理游标，状态核对不跳过未收到事件；数据目录变化使历史、草稿、提交回调和分页请求失效。侧栏删除会话清理相应草稿与缓存，切换会话保留独立草稿与已加载历史。

对话和执行轨迹使用各自的显示投影。[useSessionPage](../../frontend/src/sessions/useSessionPage.ts) 负责快照、分页与搜索定位请求、失效及迟到响应屏蔽；[sessionPages](../../frontend/src/sessions/sessionPages.ts) 统一按轮次和事实身份合并。刷新整轮替换相关请求、工具及事件，按快照游标保留较新事实；发现历史间隙时保留可补齐的分页前沿，迟到旧分页只补充对象，不关闭新的前沿，重复页不会重复显示；对话保留轮询，轨迹保留事件驱动刷新。滚动跟随、定位、桌面和移动显示方式保持。

实现见 [runtime.py](../../travel_app/sessions/runtime.py)、[http.py](../../travel_app/sessions/http.py) 与 [SessionsApp.tsx](../../frontend/src/sessions/SessionsApp.tsx)。偏好生命周期通过其公开契约协作；规划资源通过旅行规划公开装配入口提供，助手会话不直接依赖资料获取 implementation。

验证：[公开查看与管理](../../tests/test_session_contracts.py)、[提交身份](../../tests/test_submissions.py)、[原目标停止](../../tests/test_stop.py)、[保存故障](../../tests/test_storage.py)、[通知续接](../../tests/test_reconnect.py)、[真实根入口与旧数据](../../tests/test_migration_baseline.py)、[数据集与迟到分页](../../frontend/tests/ui/session-pages.spec.ts)及[浏览器交互](../../frontend/tests/ui/conversation.spec.ts)。

## 当前回答与实时正文（#98）

`GET /api/snapshot`（`service_snapshot()`）新增 `instance_id`、`answer_revision` 和 `answer_preview`。`instance_id` 是本次服务运行的随机身份，同库重启也会更换；既有 `stream_id` 仍标识持久数据集。`answer_preview` 为当前暂存正文或 `null`，只存在于服务内存，不逐块写 SQLite，也不推进持久事件 `cursor`。

正文对象包含 `instance_id`、`stream_id`、`session_id`、`turn_id`、`attempt_id`、`revision`、`text`、`status`。服务归属由客户端连接的服务地址和 `instance_id` 共同确定；`revision` 在同一进程内递增，`attempt_id` 区分同轮不同实际作答尝试。`text` 是本次尝试的累计脱敏正文；`generating` 表示正在生成，`generated` 表示模型响应已返回，`invalidated` 表示该尝试正文失效且为空。新尝试替换旧对象，不能拼接两次尝试。

既有 `GET /api/events` 增加无 `id:` 的瞬时 `answer.preview` SSE：`{schema_version: 1, kind: "answer.preview", transient: true, instance_id, revision, preview}`。`preview` 为上述对象或 `null`；订阅首次发出当前对象，后续发出最新累计对象，因此无需重放正文增量就能衔接快照。持久轨迹继续使用原 `trace` 身份与游标，`service.state` 保持服务状态，不携带全文。客户端不能把正文更新计入持久游标。

只有 `LocalRecords.finish` 成功才清除预览并以持久终态确认历史；保存失败保留最后内存正文及原有 `storage_error`／`unsaved_fact`。服务重启后只恢复持久事实，不恢复预览。Web 对当前会话的运行轮次展示“正在生成 · 尚未保存”，沿用 `AnswerBody` 的安全 Markdown 和现有阅读跟随；终态轮次使用历史回答，迟到预览不能覆盖已保存回答。跨设备恢复见下节，停止与未保存正文的当前行为见下节 #102。

## 正文恢复与交接（#100）

刷新、普通断线或另一设备进入时，先读取快照的进程、数据集、活动轮次及累计正文，再订阅。订阅首次返回最新累计对象；即使快照返回后正文继续增加，也可整体替换到最新内容，无需拼接或重放正文增量。`preview: null` 和递增的 `revision` 是明确的当前预览缺失结果；`invalidated` 是明确的尝试失效结果，不能由重复或更旧消息恢复原文。

`service.state.state` 附带 `answer_revision`，与该状态在同一同步段取得，用于识别早于已核对正文的旧状态；它不表示客户端已收到该次序的正文。状态与正文通过网络发送时可以交错，新轮正文也可能先于新轮状态抵达。Web 保留同进程、同数据集的最新累计对象，按会话、轮次及持久历史核对后展示；较旧状态不能清空更新的正文，相同次序的重复正文不能改写当前对象。

每次重连替换连接身份，已关闭连接的正文、状态、轨迹和错误回调均失效；卸载同样使在途快照失效。普通重连保留最后实际处理的持久游标，正文次序不推进此游标。数据集变化仍使分页、草稿及提交归属失效；同库重启只改变 `instance_id`，不据此清除持久会话。订阅先发现进程变化时立即清除原进程预览，并使在途旧快照失效；新快照重新核对归属，历史按 `service_interrupted` 显示原轮中断，不自动重放模型、查询或重试。一个订阅关闭不停止后台执行或其他订阅。

验证：[公开 HTTP 双订阅、交接及真实进程重启](../../tests/test_answer_streaming.py)、[Web 快照／状态／正文交错与旧连接隔离](../../frontend/tests/ui/conversation.spec.ts)，以及 [#100 验收](../acceptance/answer-recovery.md)。停止与未保存正文的当前行为见下节 #102。

主验收：[真实 HTTP 流式作答](../../tests/test_answer_streaming.py)，通过可控外部模型分块屏障验证多个正文批次先于模型返回和原子完成；查询期间不出现说明文字，最终历史一致，正文不推进轨迹游标，并验证跨块密钥、认证头、链接和 JSON 脱敏。[Web 交互](../../frontend/tests/ui/conversation.spec.ts)覆盖预览 Markdown、安全链接与保存后的历史展示。

### 重试与降级投影（#101）

当前正文的 `status` 增加 `retrying`，其 `text` 为空；`delivery` 可为 `stream` 或 `complete`（旧服务可省略）。两端清除失败尝试后显示“正在重试，新尝试将从头作答”，新尝试整体替换。`complete` 表示模型服务明确拒绝流式后改为完整响应，等待期间说明原因，完成后展示真实保存的全文。历史轮次增加可选 `answer_delivery`，由已保存请求开始事实投影，刷新仍能解释降级；不将提示文案放入正文或模型上下文。重试耗尽及不可重试错误采用历史真实失败终态并清除预览。


## 停止、保存故障与未完成回答（#102）

Web 在正常最终作答期间提供“停止生成”；点击时捕获原 `turn_id`，在等待 HTTP 响应前立即冻结当前正文并显示正在停止。同进程、同数据集、同轮次的后续预览仍可被核对，但停止期间不再追加到阅读区域；收到其他设备的 `stopping` 状态同样冻结展示。断线或停止响应未知不解除冻结，后续快照确认原轮次释放才解除。旧目标请求始终 POST 原轮次，不迁移到新轮次。

后端继续接收在途真实响应及保存事实，停止中保留全局名额，不启动新尝试。普通在途作答成功且原子完成保存后，显示历史中的真实完整回答；预算收尾保留原停止检查，不强制改成完成。已保存历史优先于任何预览。

原轮次收束释放名额后，若 `storage_error` 与关联轮次的 `unsaved_fact` 仍存在，当前页面以最新内存正文替换停止时的冻结展示，标注“未保存 · 请复制保留，刷新后可能丢失”，提供“复制未保存正文”。此正文不写入历史或模型上下文，也不改变原轮次最后持久状态。执行事实保存失败仍阻止后续调用，服务保持 `accepting=false`。没有可用正文时不生成虚假回答。普通预览使用“复制当前正文”；已保存正常回答使用“复制完整回答”；截断或预算产生的模型回答明确显示“回答不完整”和“复制不完整回答”。

验证：[公开 HTTP 停止、保存故障、截断与 64 次预算边界](../../tests/test_answer_streaming.py)、[原目标停止](../../tests/test_stop.py)、[Web 冻结及未保存复制](../../frontend/tests/ui/conversation.spec.ts)。

## 当前查询进展（#111）

公开快照另含 `progress_revision` 和 `progress_preview`；后者为 `{instance_id, stream_id, session_id, turn_id, revision, entries}` 或 `null`。`entries` 是规划提供的简短行动说明和真实工具状态，结构见[规划契约](planning.md#查询进展111)。它与 `answer_preview` 独立，不冒充旅行行程或已保存回答。

现有唯一 `/api/events` 增加瞬时 `progress.preview`，信封与正文同形：`{schema_version:1, kind:"progress.preview", transient:true, instance_id, revision, preview}`，没有 `id:`，不推进持久游标。订阅首次发出最新累计对象；同进程内独立递增次序、明确 `null`、归属核对与旧连接失效用于快照交接。此票的进展仅在当前轮内暂存，成功原子完成后清除；保存失败保留当前可见内容，重启不伪造未保存进展。持久回看由 #112 扩展。

Web 在回答之外展示“查询进展”，按事实显示准备中、执行中、已取得资料、查询失败或未执行；模型文字标记为行动说明。进展、正文、状态分别读取，复制正文不混入进展。原目标停止在发送 HTTP 前同时冻结进展与正文；迟到进展仍可核对，但不能继续追加到停止中的阅读区域。断线、重连与数据集隔离沿用现有会话接收链路。
