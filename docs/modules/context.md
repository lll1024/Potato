# 模型上下文：公开契约

公开入口为 [travel_app.context](../../travel_app/context/__init__.py)，运行类型为 [ModelContext 与 Continuation](../../travel_app/context/runtime.py)。旅行规划使用此入口；Web 和 CLI 使用上层 [planning.plan](planning.md)，不持有或双写完整协议、模型视图、水位和摘要版本。

## 开始与配置本轮

`ModelContext.begin(text, *, seed=None, continuation=None)` 恢复上一有效上下文并追加本轮输入，返回独立的轮内上下文。`seed` 是本机记录读取的不可变 `PlanningSeed`，首次可省略；`continuation` 是上轮返回的不透明进程内凭证，只能原样传回，与 `seed` 互斥。两种输入同时提供抛出 `ValueError`；损坏的持久上下文抛出 `RecordAbort(kind="turn.context")`，不得启动查询。`ModelContext(seed)` 用于恢复已经包含当前输入的协议，不再重复追加输入。

`latest_input`、`previous_date_context`、`source_materials` 提供旅行规划所需的只读副本。旅行规划解释当前旅行要求与日期，使用既定日期解释本轮新输入；上下文不重新解释历史相对日期。调用 `configure(policy=..., model=..., client=..., tools=..., date_context=..., next_request_ordinal=..., remaining_requests=..., observer=..., stop_requested=..., history_records=None)` 后执行模型步骤。配置在本轮仅调用一次；`date_context` 是已确定日期状态的副本，不从观察事件提取日期。

`client` 提供 Anthropic 异步 `messages.create`；`tools` 在此只需提供同步 `redact(str) -> str`。`observer(kind, data)` 是异步事实记录回调；可靠保存失败须抛出 `RecordAbort`，不转换成普通模型失败。`stop_requested` 为可选 `asyncio.Event`。`history_records()` 同步返回当前会话允许回读的原文记录；省略时只索引当前进程的原始协议，排除系统输入、请求快照和历史工具自身的回读结果。

`ContextPolicy.from_env()` 保持 `CONTEXT_COMPACTION_ENABLED`、`MODEL_CONTEXT_WINDOW` 的既有解释，关闭时返回 `None`；错误抛出 `ContextConfigurationError`。窗口不从模型名猜测。Web 装配通过上层 `planning.validate_configuration()` 进行启动检查，依赖方向仍为会话 → 规划 → 上下文。

## 协议、容量与历史回读

| 操作 | 输入与结果 |
| --- | --- |
| `append_response(content)` | 已脱敏响应块；复制后同时追加完整协议和模型视图 |
| `append_tool_results(results)` | 按模型提出顺序收齐的完整工具结果；身份须与上一响应一一配对，不符则抛出 `ValueError` 且不修改任一视图 |
| `append_answer(answer, replace_response=False)` | 应用终止说明；预算收尾时替换刚追加的最终响应，统一更新两份表示 |
| `prepare_request(parameters, step, attempt, recovering=False, final=False)` | 将独立模型消息副本填入完整请求，执行计量、整理及必要摘要；返回 `None` 或阻止原因 `context_budget` |
| `prepare_recovery(parameters, request_id, step, attempt)` | 明确超限后，同一步骤最多一次整理及摘要；必须严格缩小且容纳完整请求。返回 `None`、`context_recovery_unavailable` 或重复恢复的 `context_window_exceeded` |
| `observe_usage(request_id, usage)` | 根据实际返回用量校准后续检查，缺失用量不伪造为零 |
| `recovery_started(data)` | 记录恢复请求事实并在内部补入当前版本，调用方不读取版本 |
| `read_history(name, arguments, parameters, completed, pending, tool_use_id, observer=None)` | `completed` 是已收齐的本批前段结果，`pending` 是后段工具身份；回读包含前段原文，并连同当前及待完成结果检查完整请求余量。返回既有工具结果结构 |

操作表中的非位置参数均按代码签名以关键字传入。`HISTORY_DECLARATIONS`、`HISTORY_TOOLS` 提供现有模型工具声明与分类。回读每轮最多 8 次，每次搜索最多 5 条、每条摘录 200 字符，读取每页最多 4000 字符；关键词、偏移、隔离、来源日期与待核实状态保持原语义。规划继续负责整轮工具预算及混合批次顺序，回读不自动执行专项信息核实。

请求计量覆盖系统规则、日期、偏好、服务状态、工具声明、历史消息及待追加结果。`next_request_ordinal()` 从规划的真实模型额度扣账；`remaining_requests()` 返回可用于普通请求和摘要的余额，摘要不新建预算。摘要单次最多 2 次、整轮最多 4 次，同时受共享额度限制；`summary_blocked` 只读标志表示摘要认证或配额故障。最终无工具收尾不摘要、恢复或重试。

## 保存、停止与恢复

`material(redact=...) -> TurnMaterial` 一次冻结完整协议与有效上下文检查点；生成失败抛出 `RecordAbort(kind="turn.finished")`。`continuation() -> Continuation` 冻结当前协议、视图与已确定日期，后续上下文修改不会反向修改凭证。调用方不拆开材料双写，也不持有运行视图。记录值 `PlanningSeed`、`TurnMaterial`、`ContextCheckpoint`、`RecordAbort` 仍从此入口适配导出，字段所有权见[本机记录](local-records.md)。

原始协议独立完整保留，整理或摘要只替换模型视图。`context.candidate` 表示候选，`context.adopted` 只表示轮内采用；只有本机记录 `finish` 原子成功后，`context.saved` 才表示续聊生效。摘要失败保留旧状态；停止后不再调用模型，在途摘要记录真实返回但不采用迟到结果。保存失败或进程退出后，由本机记录恢复上一已提交有效版本及中断资料索引，不重放工具，不重新解释旧日期。页面沿用这些事实展示真实整理、采用、保存和中断状态。

## 验证与内部定位

[公开接口测试](../../tests/test_context_contracts.py)验证统一追加、身份配对、冻结、请求隔离、日期延续、前段回读与一次恢复；[预算](../../tests/test_context_budget.py)、[摘要](../../tests/test_context_summary.py)、[增量压缩](../../tests/test_context_incremental.py)、[超限](../../tests/test_context_limit_recovery.py)、[原文回读](../../tests/test_session_history.py)、[进程恢复及故障](../../tests/test_context_recovery.py)、[日期续聊](../../tests/test_travel_date_continuation.py)及[固定 Web/SSE/CLI 与旧库基线](../../tests/test_migration_baseline.py)覆盖真实调用链。阶段证据见 [#82 验收](../acceptance/model-context.md)。

修改本能力内部时再展开 [runtime.py](../../travel_app/context/runtime.py) 的状态协调、[view.py](../../travel_app/context/view.py) 的视图与计量、[summary.py](../../travel_app/context/summary.py) 的摘要和采用校验、[history.py](../../travel_app/context/history.py) 的原文索引与分页。普通规划与摘要仅复用 [model_execution.py](../../travel_app/model_execution.py) 采集实际模型返回事实，重试和摘要采用仍由各自策略决定。共享模型错误识别及重试等待在 [model_retry.py](../../travel_app/model_retry.py)，不引用任何能力运行状态。普通调用无需阅读这些内部文件。
