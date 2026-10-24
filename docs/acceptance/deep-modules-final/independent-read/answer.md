# Issue #86：独立公开阅读验收

本答案仅依据本轮实际读取的根 `AGENTS.md`、`README.md`、`GLOSSARY.md`、`docs/modules/` 总览、六项契约与结构规则，以及契约链接的公开 `__init__.py`、`index.ts` 和 `local_records/values.py`。没有读取业务实现、测试正文、历史验收、其他代理输出，没有运行项目或联网。所有文件访问记录保存在 `/tmp/potato86-independent-read-access.jsonl`；每条含绝对路径、读取行区间与文件 SHA-256。第一次批量输出中部被工具截断，随后完整重读了资料获取与上下文契约，因此日志有重复访问。

## 阅读入口与判断

从根 `AGENTS.md:13–15` 可以明确进入 `docs/modules/README.md`，再按任务读取对应契约。该入口把“使用现有能力”与“修改内部行为”区分开：前者遵守公开入口和契约，后者沿契约展开实现与测试，跨能力修改先读双方契约，归属及依赖变化再读结构规则。总览也明确说明展开实现无需另外批准。此次任务限定只读公开材料，因此没有跟进 `docs/agents/domain.md`、ADR、验收和实现链接；这不是认定它们不需要，而是本次独立阅读的边界。

公开材料足以解释六项能力的主要职责、状态归属、依赖方向、正常与故障流程，并定位进一步修改的位置。以下不把测试链接当作已经验证的行为，也不将历史报告当作当前契约。

## 六项能力及状态归属

| 能力 | 自己拥有的责任和状态 | 公开入口与协作边界 |
| --- | --- | --- |
| 助手会话 | 提交身份及去重、不确定接受结果、全局单轮执行名额、原停止目标、接受状态、历史/轨迹/搜索/通知；前端草稿、分页、游标与迟到响应失效 | `travel_app.sessions` 的 `create_app`、`Runtime`、`TurnRuntime`、`RunRejected`；前端 `SessionsApp`。调用规划并共同保存结果，不维护协议与模型视图双份运行状态 |
| 旅行规划 | 当前旅行要求、确定旅行日期、旅行规则、模型步骤、混合工具批次、整轮工具和真实模型请求预算、结构化终态 | `travel_app.planning` 的 `plan`、`PlanningResult`、`PlanningTools`、`configured_planning` 等。接收种子或不透明延续凭证；不创建数据库或后台偏好任务 |
| 资料获取 | 地图与网页连接、能力发现、供应商并发及启动间隔、暂停和错误分类、外部查询分段、脱敏与单次查询结果 | `travel_app.retrieval` 的 `Retrieval`、`connected_retrieval`、`Query`、`QueryResult`、`Capabilities`。不拥有历史回读或整轮预算 |
| 模型上下文 | 完整原始协议和模型视图的一致推进、容量计量、摘要与采用、历史索引和回读、超限恢复、冻结保存材料与延续凭证 | `travel_app.context` 的 `ModelContext`、`Continuation`、`ContextPolicy` 等。上层 Web/CLI 调用 `plan`，不直接同步两个视图；日期由规划解释，上下文保存并延续已确定日期 |
| 旅行者偏好 | 管理界面与规划背景、稳定本人偏好提取、来源校验、闲置资格、处理顺序、歧义、版本及人工保护、有限重试与后台生命周期 | `travel_app.preferences.Preferences`；前端 `PreferencesView`、`Preference`、`PreferenceState`。调用方不筛选批次或自行推进处理状态 |
| 本机记录 | SQLite 格式、原子接受与完成、执行事实、一致读取及持久游标、条件快照写入、已提交状态的重启恢复 | `travel_app.local_records.LocalRecords`、不可变记录值、`RecordAbort`、`StartupError`。持久化资格事实，但不拥有偏好领域规则；目录创建与服务独占锁归会话启动装配 |

允许的业务依赖只有：会话 → 规划、偏好、记录；规划 → 资料、上下文；上下文 → 记录；偏好 → 记录。资料与记录不依赖其他业务能力。装配注入回调不形成反向实现依赖，共享支撑只保留无业务运行状态的模型事实、错误分类、常量等。跨能力通过 `__all__` 或前端具名导出访问，不能因为公开符号的定义位于内部文件就直接导入该文件。依据：总览第 9–22 行、结构规则第 15–21 行及各公开入口。

## 正常回答、停止、失败与重启

1. **启动与正常回答。** Web lifespan 创建并独占数据目录，打开 `LocalRecords`，先 `recover()`，配置模型和已发现的查询连接，再 `TurnRuntime.start()` 启动会话和偏好后台。提交先核对身份，后检查接受状态和全局名额；输入、轮次、去重身份及偏好待处理资格原子接受后才运行规划。会话向 `plan` 传入记录种子、只读偏好背景、事实回调、停止信号。模型步骤由上下文准备完整请求；规划预留整批工具额度，连续外部段交资料获取，历史回读等待前段真实返回并计入后段占位。整批结果收齐才继续模型。`plan` 返回回答、状态、原因、来源、错误数、冻结材料和延续凭证，会话一次 `finish` 保存回答、协议、有效上下文与偏好结束时间。只有 `finish` 成功才完成保存，`turn.finished` 观察事件不代表事务成功。
2. **停止。** 必须使用原 `turn_id`，不能使用页面当前选择推测。停止在调用边界生效，禁止继续启动；在途服务调用真实返回并完成保存尝试前仍占全局名额，不取消或伪造结果。关闭页面或 SSE 订阅不取消后台规划。服务退出先拒绝新输入，停止偏好与当前规划并等待收束，再关闭外部资源和记录、释放目录锁。CLI 退出连接前同样等待收束。
3. **单次查询失败。** 输入与输出身份、数量、顺序对应，失败成为 `QueryResult`；`is_error` 与 `not_executed` 区分真实失败和未启动。未知工具、参数错误仍为工具失败。严重故障只暂停对应供应商，暂停状态沿用到该资料实例连接生命周期结束；地图与网页互不连带暂停。在途结果都等待返回，模型在整批收齐后继续。地图发现失败阻止启动；网页发现失败仅令网页不可用。
4. **模型失败。** 规划提供结构化失败/终止结果，不靠观察事件推断。README 明确：连接、超时、临时限流、服务端错误或空响应，在当前步骤最多追加五次重试，并保留已完成工具结果；认证、配额及参数等永久错误直接结束。停止可在重试期间生效。真实请求上限、摘要和恢复共享同一本预算；64 次中的最后一次用于关闭工具的收尾，不再摘要、查询或重试。预算耗尽、停止、模型失败分别表达；未预期异常保留 `execution_error`。手动重新尝试建立新轮次，可能再次查询。
5. **记录故障。** 外部调用前先可靠保存开始事实。SQLite/序列化错误不能伪装空结果或模型失败；执行事实回调转换为 `RecordAbort`，禁止后续启动并等待在途返回和所有保存尝试。`TurnRuntime.storage_failed` 立即拒绝新输入并设置规划停止，故障通知不能提前关闭资源。快照展示 `accepting=false`、`storage_error` 和 `unsaved_fact`，轮次保留最后持久状态；未保存回答不能展示为已完成历史。`finish` 失败全部回滚，避免回答、上下文和偏好资格半成功。偏好后台记录故障走存储故障回调，不消耗模型失败重试；未提供回调时后台异常由 `shutdown()` 传播。
6. **服务重启。** `recover()` 只承认已提交事实，未完成轮次标识中断，不重放模型、工具或普通重试计时。恢复上一有效上下文、确定旅行日期及中断资料索引，未保存候选不生效，不重新解释旧相对日期。偏好 `start()` 在记录恢复后，恢复中断提取与结束资格，保留已发提取次数和退避；它不是旅行规划重放。通知游标与瞬时状态分开，记录目录身份变化或位置无效要求重新获取快照。

整轮主要限额：工具 128 次、历史回读 8 次、真实模型请求 64 次；规划逻辑轮数另受 80 上限约束。地图串行；网页搜索及正文共用十个在途；两侧可重叠，各自默认 0.4 秒启动间隔。这些状态分别由规划、上下文、资料能力拥有，调用方不重新调度。

## 四类问题的公开操作与允许展开入口

| 问题 | 首先用公开契约及操作确认 | 需要修改内部时的定位，仅列出而未读取 |
| --- | --- | --- |
| 上下文超限 | 普通调用方仍调用 `plan`；上下文提供 `prepare_request` 全请求计量、`prepare_recovery` 同步骤最多一次且严格缩小的恢复、`read_history` 按完整请求余量回读；`observe_usage` 校准真实用量；配置错误为 `ContextConfigurationError`，容量阻止原因含 `context_budget`、`context_recovery_unavailable`、`context_window_exceeded` | `travel_app/context/runtime.py` 状态协调，`view.py` 视图和计量，`summary.py` 摘要及采用校验，`history.py` 索引和分页；规划真实请求总账在 `planning/budget.py` |
| 查询调度 | `Retrieval.capabilities` 查看声明与服务状态，`execute` 接收有序 `Query` 并返回有序结果；`plan` 管整轮预留、混合批次及历史屏障 | `retrieval/service.py` 分段收齐，`amap_mcp.py`/`exa_mcp.py` 供应商调度和分类，`connections.py`/`source_connection.py` 连接；`planning/batch.py` 批次顺序，`budget.py` 总账 |
| 偏好人工保护 | `Preferences.snapshot/background/edit/delete/start/shutdown`；编辑/删除带版本，返回 `saved/missing/conflict`。变更撤销旧输入授权并保存内容、版本和当时全局输入水位，迟到结果复核资格；删偏好不从旧聊天恢复，水位之后的新表达可明确建立偏好 | `preferences/service.py` 生命周期与管理，`extraction.py` 提取和背景，`rules.py` 资格及变更规则；`local_records/preference_records.py` 条件写入。跨能力修改先读两份契约 |
| 记录故障 | `LocalRecords.accept/observe_request/observe_tool/observe_context/finish/recover`；`TurnRuntime.storage_failed/snapshot/shutdown`，观察 `RecordAbort.kind` 与未保存状态；偏好通过公开条件快照 `preferences.read/write` 防止旧计算跨过管理变化 | `local_records/storage.py` 事务，`metadata.py` 值解释，`query_materials.py` 资料投影，`preference_records.py` 偏好条件事务；`sessions/runtime.py` 故障与停止收束、`http.py` 状态映射 |

偏好资格具体为结束后连续闲置一小时、没有运行/停止轮次、退避已到；后台每 30 秒检查，按输入顺序选择。失败最多三次请求，失败后分别等待 30、120 秒。人工修改、新输入、停止、删除等使资格失效时，旧结果不能提交，不能把旧计算结果换上新条件快照。上述规则足以定位人工保护，无需先读 SQL。

## 最小公开调用方案

选择本机记录的“一致读取已恢复会话”操作，避免为了读取记录自行构造会话、规划或模型。下面是依据契约写出的调用方案，没有运行。

```python
from pathlib import Path
from travel_app.local_records import LocalRecords

def read_recovered_session(data_dir: Path, session_id: str):
    # 前置条件：目录已创建；装配方已保证独占；在服务事件循环线程调用。
    records = LocalRecords(data_dir)
    try:
        records.recover()
        return records.snapshot(session_id)
    finally:
        records.close()
```

可以确定的构造、顺序、错误和释放规则如下：

- `LocalRecords(Path(data_dir))` 打开目录中的 `travel.sqlite3`；不是连接一个正在被其他服务使用的数据目录。目录创建和服务锁属于会话启动，普通 Web 应使用 `create_app` 管理它们，而不是复制锁实现。
- 恢复先于后续业务使用。`recover()` 不调用模型或查询，不重放未完成请求；`snapshot(session_id)` 一次读取页面结构和事件游标，不由调用方开启事务。
- 会话不存在返回 `None`；SQLite 错误向上传播，不能转成空会话。公开入口保留 `StartupError` 和 `SCHEMA_VERSION`；契约没有给出全部启动错误的成员定义，因此方案不依赖未公布成员。
- 同一实例同步使用且不跨线程共享；网络和等待不放进记录事务。打开成功后，读取或恢复失败也通过 `finally` 调用 `close()`；构造失败时实例尚未交付，不声称能对其调用释放。
- 该方案的独占目录是装配前置条件。若要同时运行 Web，应改由 `create_app` 的 lifespan 持有记录，不能用这个函数旁路打开同一数据目录。

旅行规划的最短协作顺序同样可从公开资料确定：由 `configured_planning()` 管理生产模型和查询资源，在资源存活期 `await plan(text, client, tools, model)`，读取 `PlanningResult`，续聊时原样传回 `continuation`，等待调用收束后再退出资源。`seed` 与 `continuation` 互斥；记录故障抛 `RecordAbort` 而不是普通模型失败；CLI 不应自行拆解凭证，也不建立长期偏好后台。此处没有把未明确写出的工厂上下文管理协议或内部类型定义补成可执行代码。

## 不确定项与是否需要读取内部

1. **根引导与本次范围的冲突。** 根 AGENTS 另要求探索前读 `docs/agents/domain.md`，本次独立验收白名单不允许。已遵守本次更具体的范围限制；这不妨碍上述公开流程解释。无需读取业务内部；常规代码任务仍需跟进该引导。
2. **模型失败的精确 `reason` 全集、永久/暂时错误到各代码的完整映射及退避间隔。** README 提供追加五次和错误类别，规划契约只说保留原状态/原因，没有完整枚举。不能从公开材料给出每一种错误的精确 reason 或等待时间。若调用方需要按代码分支，属于契约精度缺口，应补公开枚举/映射；本次不读取内部来猜测。
3. **`configured_planning`/`connected_retrieval` 的上下文管理协议、返回工厂的精确类型。** 契约明确其资源管理职责和使用期，但未明确写成 `async with` 协议及完整返回类型注解。本次可确定生命周期顺序，不能仅据入口签名证明一段具体工厂用法可运行。若要求该最短规划示例直接可执行，需补公开示例或明确异步上下文管理器返回类型，不应强迫普通调用方读 `resources.py` 或 `connections.py`。
4. **直接调用 `ModelContext` 操作表中的精确关键字参数分界。** 契约说“非位置参数均按代码签名以关键字传入”，但表格没有 `*`，运行类型链接指向 `runtime.py`。普通调用 `plan` 不受影响；若要直接编排上下文，仅依赖当前契约不能保证每个关键字约束。需要补完整公开签名；否则为确认签名必须读取内部，违背此次限制。
5. **少数公开值的完整结构。** `PlanningResult`、`Query`、`QueryResult` 等的主要字段已在契约描述，但值定义与运行实现放在同一内部文件；本次未读取这些文件。`local_records/values.py` 则可以独立阅读。最小记录读取方案不依赖未公布结构。若需要精确类型检查或直接构造所有规划值，宜提供独立公开值定义或完整契约，不依据业务实现推测。
6. **实际实现与文档是否一致、事务和并发是否真实满足。** 本次仅是公开阅读验收，没有读或运行测试，不能证明实现符合文档。验证需另行运行契约链接的测试或展开对应实现；这属于实施验证，不是完成公开使用理解的前置条件。

除上述精确签名、枚举和类型层面的缺口外，六项能力归属、允许依赖、正常与异常流程、四类问题的公开定位，以及选定的记录读取方案，不需要读取内部实现才能解释。
