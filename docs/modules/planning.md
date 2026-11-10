# 旅行规划：当前公开契约

公开入口为 [travel_app.planning](../../travel_app/planning/__init__.py)。主要操作是异步 `plan(text, client, tools, model, *, seed=None, continuation=None, observer=None, stop_requested=None, stop_reason="user_stop", preference_background="", clock=None, history_records=None, max_rounds=80, max_tool_calls=128, context_policy=None)`。Web 与真实 CLI 都调用它；不创建数据库或后台偏好任务。

`client` 是已配置的 Anthropic 异步客户端；`tools` 是已发现能力的 `PlanningTools`（实际为资料获取公开的 `Retrieval` 类型）。生命周期由装配方管理。模型、地图、网页的生产连接配置及超时保持既有启动行为；调用方可以注入可控外部响应。查询能力说明、分段执行与失败语义见[资料获取契约](retrieval.md)。

`seed` 是本机记录在同一快照中读取的 `PlanningSeed`；首次对话可省略。CLI 将上次返回的 `continuation` 原样传入，不创建或拆解它；它仅在当前进程内有效，不是持久会话身份。两者互斥，同时传入抛出 `ValueError`。输入、完整协议、模型视图和已确定旅行日期由规划内部协调；调用方不追加消息或自行同步视图。偏好背景作为只读文本交入，历史原文通过同步 `history_records()` 回调按需读取。

返回不可变 [PlanningResult](../../travel_app/planning/runtime.py)，包含：

| 字段 | 含义 |
| --- | --- |
| `answer` | 已脱敏回答，可能是正常回答或未完成说明 |
| `status`、`reason` | 原协议的 `completed` / `terminated` / `failed` 及原因；完成时原因为 `None` |
| `answer_source` | `model` 或 `application` |
| `tool_error_count` | 实际失败的工具数；未启动调用不作为真实失败计数 |
| `material` | 不可变 `TurnMaterial`，包含完整协议和有效上下文保存材料 |
| `continuation` | CLI 使用的不透明进程内延续凭证 |

`fields()` 提供与记录终态字段相同的字典。结果直接来自执行循环的结构化返回，不由观察事件推导。`observer(kind, data)` 只承担事实记录与通知；即使没有 observer，返回值仍完整。`turn.finished` 观察不表示数据库已保存；只有本机记录的 `finish` 成功返回后，会话历史才算完成。

完成、用户停止、服务退出、预算耗尽、模型失败保留原状态和原因。`stop_requested` 在调用边界生效，等待已启动调用的真实返回，不取消或伪造其结果。事实记录回调必须在保存失败时抛出 `RecordAbort`，并通知会话停止接受输入；该异常贯穿规划，不转换成 `model_error`。恢复记录种子失败抛出 `RecordAbort(kind="turn.context")`；生成保存材料失败抛出 `RecordAbort(kind="turn.finished")`，均不返回可保存结果。其他未预期执行异常保留 `execution_error` 及原有未完成说明。

整轮工具 128 次、历史回读最多 8 次、实际模型请求 64 次及无工具收尾预留保持；地图串行，网页最多十个在途，重试、摘要和恢复仍共享请求预算。默认配置、旅行规则和模型可见输入未因入口变化而扩大。

`validate_configuration() -> None` 供启动装配检查模型上下文容量配置；错误为公开 `ContextConfigurationError`，可直接展示。CLI 的日期状态保存在不透明延续凭证中，观察回调只展示进度。

`configured_planning(*, retrieval_factory=None)` 检查配置并管理生产模型与查询连接，返回 `PlanningResources(client, tools, model)`；Web 将它装入会话运行资源，CLI 复用同一模型装配。可注入查询资源工厂重定向外部响应，默认连接与超时不变。`run_cli(..., retrieval_factory=None, model_client_factory=None)`、`main(*, load_environment=None, cli_runner=None)` 提供根入口的显式装配 seam；模型客户端工厂只替换连接资源，实际调用、重试、摘要和预算仍走生产 implementation；它们不替换规划、查询、上下文或保存 implementation。

生产装配从项目根 `.env` 加载环境（覆盖已有同名环境），默认静态资源与工作目录解析见[会话](sessions.md)。需要 `AMAP_MAPS_API_KEY`、`MODEL_ID` 及模型认证；`ANTHROPIC_BASE_URL` 可指定兼容服务，提供时使用 `ANTHROPIC_API_KEY`，否则也可用 `ANTHROPIC_AUTH_TOKEN`。模型请求超时 60 秒，SDK 自动重试关闭，由规划执行有界重试；配置错误由启动入口诊断。`configured_planning` 本身消费当前环境，不额外读取 `.env`。

`run_cli(api_key, *, check_amap=False, clock=None, retrieval_factory=None, model_client_factory=None)` 在上下文管理器内打开查询与模型资源；`retrieval_factory(api_key, *, include_sources=True, secrets=())` 产出已发现的 `Retrieval`。`model_client_factory` 接受 Anthropic 客户端同形构造参数并返回异步上下文管理器。`check_amap=True` 仅调用地图检查，跳过模型、网页及偏好后台；普通 CLI 输入 `q` 退出，同一进程的续聊自动使用延续凭证。

`configured_planning(...)` 返回异步上下文管理器，必须用 `async with`；其产出不可变 `PlanningResources(client: AsyncAnthropic, tools: Retrieval, model: str)`。`retrieval_factory` 同样返回异步上下文管理器。最小生产调用（环境已配置）为：

```python
from travel_app.planning import configured_planning, plan

async def answer(text):
    async with configured_planning() as ready:
        result = await plan(text, ready.client, ready.tools, ready.model)
        return result.answer, result.status, result.reason
```

`PlanningResult` 是不可变记录值；`answer: str`、`status: str`、`reason: str | None`、`answer_source: str`、`tool_error_count: int`、`material: TurnMaterial`、`continuation: Continuation`。调用方消费返回值，不自行构造结果或拆开延续凭证。`stop_reason` 可为字符串或无参同步回调，默认 `user_stop`；会话退出使用 `service_shutdown`，记录故障设置 `storage_failure` 并以 `RecordAbort` 中止。

| 结果类别 | `status` 与 `reason` |
| --- | --- |
| 正常最终回答 | `completed` / `None` |
| 输出截断 | `terminated` / `output_limit` |
| 停止或预算 | `terminated` / `user_stop`、`service_shutdown`、`budget`，或装配提供的停止原因 |
| 上下文启动或安全容量阻止 | `terminated` / `context_configuration`、`context_budget` |
| 普通模型失败或未预期执行失败 | `failed` / `model_error`、`execution_error` |
| 明确超限恢复失败 | `failed` / `context_window_exceeded`、`context_recovery_unavailable` |

模型事实的 `error.code` 与轮次 `reason` 各有用途：网络、超时、429、5xx、空响应分别为 `TRANSPORT`、`TIMEOUT`、`RATE_LIMIT`、`SERVER`、`EMPTY_RESPONSE`，普通步骤最多追加 5 次尝试，仍受整轮预算约束；401/403 为 `AUTH`、额度不足为 `QUOTA`、非法请求为 `INVALID_REQUEST`，不普通重试。明确超限为 `CONTEXT_WINDOW_EXCEEDED`，走一次上下文恢复。其他 HTTP 状态为 `HTTP_<状态码>`，非模型调用阶段或未知错误为 `UNKNOWN`。普通退避从 0.5 秒指数增长、±10% 抖动且最多 10 秒；服务给出有效正数 `Retry-After` 时优先使用，超过 10 秒不继续等待。最终收尾没有普通重试。上层通常只需消费结构化轮次结果，界面诊断可另展示真实请求事实。

文本块中出现 DSML 工具协议标记（全角或 ASCII 竖线）时，规划以 `INVALID_RESPONSE` 记录真实响应和用量；不执行文本中的工具，也不将失败响应追加至模型上下文或作为查询阶段结束信号。普通步骤沿用上述有限重试，持续异常以 `failed / model_error` 结束；预算收尾仍不重试。正文预览暂缓可能跨分片的标记前缀，并在识别标记后隐藏协议内容；失败尝试按原规则清除预览。此修复不重写已有会话历史，回归见 `tests/test_answer_streaming.py`（#119）。

`max_rounds`、`max_tool_calls` 可收紧原有运行限额，分别受 80 和 128 硬上限限制；`context_policy` 可注入公开 `ContextPolicy`，省略时仍读取原环境配置。既有受限运行测试通过此入口执行，不再需要生产 `agent_loop` 适配。资料获取实例的可用状态保留到连接生命周期结束，整轮工具和实际模型预算在每次 `plan` 重建。

模型步骤在 [runtime.py](../../travel_app/planning/runtime.py)；[batch.py](../../travel_app/planning/batch.py)统一登记调用身份、按提出顺序预留工具额度、切分连续外部段、等待历史屏障并按原序收齐一次完整结果；模型步骤不持有分段拼接或供应商状态。历史回读将前段真实结果与后段占位一并交给上下文，按完整请求余量分页。外部段未启动沿用原预留计数，历史局部上限拒绝不消耗总账；实际失败照常计数。

[budget.py](../../travel_app/planning/budget.py)拥有工具总账与实际模型请求序号，摘要和恢复通过同一额度回调扣账，普通请求仅使用前 63 次，第 64 次预留给关闭工具的最终回答。达到预算时先等待在途返回；最终请求不再摘要、查询或重试。逻辑步骤仍独立计数。共同模型事实采集在无业务运行状态的 [model_execution.py](../../travel_app/model_execution.py)，普通重试与摘要的局部策略继续由各自能力拥有。

完整协议、模型视图、摘要、容量和回读通过[模型上下文](context.md)管理；日期解释在 [dates.py](../../travel_app/planning/dates.py)。旧 `travel_app/agent.py`、查询 adapter 和日期文件已迁入所属能力，生产和测试调用均更新到当前入口。阶段证据见 [#83 验收](../acceptance/retrieval-planning.md)。

验证：[规划契约测试](../../tests/test_agent.py)、[记录故障](../../tests/test_storage.py)、[调用预算](../../tests/test_call_budget.py)、[固定 Web/SSE/CLI 与旧库基线](../../tests/test_migration_baseline.py)。

## 最终回答预览（#98）

`plan(..., answer_observer=None)` 可选接收异步 `answer_observer(update)`。提供时启用显式的资料获取／最终作答两阶段：查询阶段仍调用工具，其文字只作为阶段结束信号，不追加为最终回答或作答上下文；下一逻辑步骤关闭工具并通过模型 SDK 的真实文本流作答。摘要、恢复和工具参数不发出正文更新。CLI 不提供该回调，继续完整输出既有结果。

每次作答尝试在请求开始事实可靠记录后发送 `{attempt_id, text: "", status: "generating"}`；`attempt_id` 为该实际模型请求身份。随后发送累计的安全 `text`，不会要求消费方拼接分片。模型的多个文本块按最终回答相同的换行边界组装后再脱敏，避免跨块密钥因边界丢失而泄露。`generated` 只表示该模型响应生成成功，尚不表示轮次结果已持久保存；失败尝试发送空正文与 `invalidated`。失败正文不追加至模型上下文，已有普通重试和上下文恢复继续走同一实际请求总账。

新增作答占用一个逻辑步骤和实际请求，并经 `ModelContext.prepare_request` 检查完整输入容量；普通请求限前 63 次，第 64 次仍是无工具预算收尾。预算收尾也可提供正文预览，但不新增重试、摘要或恢复，仍校验完整响应并执行原停止检查。最终结果、预算说明与保存材料仍以 `PlanningResult` 为准，预览不替代结果。重试与明确不支持流式的降级见下节；停止期间客户端冻结展示，规划仍交付真实响应，保存与未保存投影见[会话契约](sessions.md)。

启用预览时，`request.started` 同时标记 `answer_phase`（`query` 或 `answer`）；普通作答的 `purpose` 为 `travel_answer`，预算收尾继续使用 `travel_final`。该分类随开始事实可靠记录，供记录能力区分查询说明和真正助手回答；旧请求及不启用预览的 CLI 保持原字段语义。

## 作答重试与非流式降级（#101）

普通作答允许模型协议中的 `thinking`／`redacted_thinking` 与 `text` 一同返回，正文更新及最终聊天回答仍只提取文本块，不展示推理块，禁止工具调用。没有非空正文时按原有 `EMPTY_RESPONSE` 分类处理，不保存为空白完成。第 64 次预算收尾继续要求纯文本且完整结束，未因兼容当前模型而放宽。当前 DeepSeek 实连及公开 HTTP 复现见 [#104 联合验收](../acceptance/streaming-final.md)。

普通作答失败先发出空 `invalidated`；现有分类允许且有额度时，退避前发出空 `retrying`，下一实际请求使用新的 `attempt_id` 从空正文开始。沿用最多追加五次、指数退避、`Retry-After` 和停止检查。流读取中的底层连接／超时错误按 `TRANSPORT`／`TIMEOUT` 处理；SDK 返回但缺少结束原因也按连接中断处理，未收到任何事件的空流按 `EMPTY_RESPONSE` 处理，不能保存为完整回答。停止后不启动新尝试；在途真实失败仍保留原有模型失败终态，不把已返回故障一律改成停止。

只有正在使用流式的请求得到明确流式能力拒绝，才分类为 `STREAM_UNSUPPORTED`：400／404／405／422／501 中明确的“不支持 stream／streaming”、`stream must be false` 或针对 `stream` 的 `unsupported_parameter`。认证、额度、限流、上下文超限优先保留原分类；普通 5xx、网络、超时和无明确归因的非法请求不能触发降级。此轮后续作答改用原有完整响应调用，不建立供应商探测或永久缓存。

正文更新增加可选 `delivery: "stream" | "complete"`，完整响应等待期间仍发出空 `generating`，生成成功才交付安全完整正文。`request.started.answer_delivery` 同步记录真实调用方式。降级原因由客户端按 `complete` 展示，独立于回答和模型上下文；失败预览、错误和展示原因均不追加至重试协议。每次降级请求重新经过容量检查并扣同一实际请求预算，不重做工具查询。第 63 次失败后可使用第 64 次无工具收尾；第 64 次自身失败不再降级、恢复或重试，也不增加摘要。CLI 保持原有完整响应流程。

## 查询进展（#111）

`plan(..., progress_observer=None)` 可接收异步进展回调，和 `answer_observer` 分别投影查询行动与最终正文。Web／iOS 装配同时提供两者；CLI 不提供此回调，保持完整输出。进展不增加额外模型请求，查询阶段改用同一 SDK 真实文本流，工具仍只在完整响应和有效调用材料到齐后执行。

查询阶段提示要求：仅在准备查询时，以“进展：”开头给出一句不超过 120 字的面向用户行动说明；无需查询或资料齐全时只返回阶段信号。对外投影只接受显式前缀，去掉标记、保留第一行、最多 240 字；跨分片先做既有安全脱敏。无标记长回答、内部推理、摘要、工具参数和原始供应商载荷不进入进展通道。响应最终没有工具时清空本次说明，避免将无查询清单或阶段信号作为查询进展。模型未遵从标记时仍执行真实工具并提供工具状态，不把任意模型文字补成进展。

回调每次发送一个累计条目 `{id, attempt_id, kind, text, status}`。`kind=model` 的状态为 `generating/generated/invalidated/retrying`，只表示行动说明的生成与有效性；不同请求身份不拼接。`kind=tool` 的状态为 `pending/running/completed/failed/not_executed`，在对应执行事实可靠保存后投影，文字仅按工具能力描述行动，不暴露原始名称或参数。`completed` 只表示已取得查询资料，不能称为完成信息核实。批次提出顺序保留，工具执行次序和结果仍由原有批次协调与预算决定。

验证见 [公开查询进展测试](../../tests/test_query_progress.py)：外部模型分片和工具屏障之前观察说明增长、执行中及真实完成状态、无查询长正文隔离、跨块标记与凭据脱敏。

查询进展的保存自 #112 起复用原观察事实：成功查询响应的最终简短行动说明放入 `request.completed.progress`，工具条目放入对应生命周期事实的 `progress`。相同条目随后发给当前进展回调；工具事实须先可靠提交再发布对外状态。完整响应中的说明仍按原协议由上下文管理；新增显示投影不自行追加模型消息。模型失败尝试的临时文字不保存为进展，原始诊断载荷仍遵循执行轨迹和回读约束。

## 查询流式重试与完整响应（#115）

查询模型条目同样携带可选 `delivery: "stream" | "complete"`，来自本次真实请求方式；工具事实不添加此字段。明确拒绝流式后，下一次查询完整等待期间即发出空 `generating / complete`，无需等到最终回答才解释降级。成功且有真实工具的短行动说明仍随原完成事实保存；失败尝试的正文、无查询信号及空重试说明不成为可靠历史。

查询与作答共用既有错误分类、有限重试、上下文恢复及整轮预算。查询失败先发布空 `invalidated`，允许重试时再发布空 `retrying`；新尝试替换失败模型文字，先前可靠工具事实保持。网络／超时／空响应／429／5xx不因新增查询流而变为能力不支持。第63次查询明确拒绝流式时只能使用第64次无工具收尾；第64次自身失败不再重试或降级。验证见[查询重试与预算交叉验收](../acceptance/query-retries.md)。
