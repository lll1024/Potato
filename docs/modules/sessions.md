# 助手会话：当前公开契约

公开入口为 [travel_app.sessions](../../travel_app/sessions/__init__.py)。`create_app(data_dir, *, resources=..., static_dir=None, request_shutdown=None, clock=None, preference_clock=..., preference_wait=..., preference_timeout=60)` 构造现有 FastAPI 服务；根 `web.py` 调用公开 `main()`，启动参数、数据目录、配置和 HTTP/SSE 协议保持。

服务 lifespan 创建并锁定数据目录，打开本机记录、恢复已提交事实、配置外部资源，再启动会话及既有偏好后台处理；退出时停止接受输入，等待当前执行和保存收束，关闭资源并释放锁。`resources` 是异步上下文管理器，产出 `Runtime(client, tools, model)`；模型客户端及已发现的查询 adapter 在服务使用期间保持有效。已被占用的数据目录、版本不兼容及配置错误保留固定启动诊断。

可直接使用 `TurnRuntime(records, runtime, ...)` 的 `start()`、异步 `submit(input, session_id, submission_id)`、`submission_result(submission_id)`、`stop(turn_id)`、`snapshot()` 和异步 `shutdown()`。通常使用 `create_app` 管理其生命周期。`changed` 是瞬时变化通知，不是持久游标。

提交先核对身份，再检查接受状态和全局名额。同身份同输入返回原接受身份；冲突为 409，已删除会话的身份为 410，全局忙碌为 409，无法保存或退出为 503。直接调用抛出 `RunRejected(status_code, detail)`，HTTP 层保留相同错误映射。接受输入与偏好待处理登记共同提交后才启动后台规划；关闭网页或订阅不取消已接受任务。

会话将记录快照及偏好背景交给 `planning.plan`，只使用其结构化终态和只读保存材料，随后一次原子保存。它不解析 `turn.finished` 观察事件，也不维护完整协议和模型视图两份状态。观察回调逐条保存请求、工具及上下文事实；保存失败抛出 `RecordAbort`，禁止后续调用。执行已返回与结果已保存分别表达：保存失败的轮次保持最后持久状态，`snapshot()` 的 `accepting=false`、`storage_error`、`unsaved_fact` 明确显示故障，不伪造完成或模型失败。

停止只接受原轮次身份。停止期间保留全局名额，已进入外部服务的调用及保存尝试真实收束后才释放。重启不重放请求或普通重试；未完成事实按原恢复规则标识中断。

HTTP 历史、执行轨迹、搜索、载荷及通知仍由现有路由提供，记录读取通过 `LocalRecords` 的一致读取操作完成，路由不取得连接或开启事务。完整会话查看与前端交互收拢属于 #85；偏好后台规则收拢属于 #84。当前实现分别位于 [turn_runtime.py](../../travel_app/turn_runtime.py) 和 [web.py](../../travel_app/web.py)，公开入口不代表这两项后续任务已完成。

验证：[提交身份与原子接受](../../tests/test_submissions.py)、[原目标停止](../../tests/test_stop.py)、[保存故障](../../tests/test_storage.py)、[通知续接](../../tests/test_reconnect.py)、[真实根入口与旧数据](../../tests/test_migration_baseline.py)。
