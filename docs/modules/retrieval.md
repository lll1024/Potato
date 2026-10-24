# 资料获取：公开契约

公开入口为 [travel_app.retrieval](../../travel_app/retrieval/__init__.py)。它负责地点、餐饮、交通路线、天气、网页搜索和正文资料，隐藏供应商对象、发现分页、并发名额、锁、启动间隔、暂停、超时、错误分类、内容处理和脱敏。历史搜索与原文回读属于[模型上下文](context.md)，整轮工具及模型请求预算属于[旅行规划](planning.md)。

## 装配与能力说明

生产启动通过异步上下文管理器（使用 `async with`）`connected_retrieval(api_key, *, secrets=(), map_url=None, source_url=None, include_sources=True)` 管理连接生命周期，得到 `Retrieval`；退出前须等待查询分段返回。默认仍使用高德 MCP 和免密钥 Exa，不携带网页服务认证头，不自动切换付费服务。地图发现失败阻止启动；网页连接或发现失败只使网页资料不可用，地图继续使用。`include_sources=False` 用于原 CLI `--check-amap` 路径，不初始化模型或网页连接。

已有 MCP 客户端的装配方可直接构造 `Retrieval(map_client, *, source_client=None, api_key="", secrets=(), http_client=None, tool_timeout=30, source_timeout=30, source_interval=0.4, map_interval=0.4, include_source_status=False)`，再 `await discover()`。地图发现失败向上传播；网页发现失败禁用网页能力并保留地图，状态说明为网页暂不可用。`map_interval` 与 `source_interval` 供受控资源装配使用，生产默认分别保持 0.4 秒。只在装配时注入客户端；普通规划调用方不持有或读取它们。实际需要的 HTTP adapter `MapHTTPClient`、`SourceHTTPClient` 供可控 HTTP 响应和连接状态注入使用，调度仍由资料获取拥有。注入的客户端生命周期由装配方负责。

直接注入的 `map_client` / `source_client` 类型是 `mcp.Client`，须已进入其异步上下文管理器。兼容的脚本化客户端须提供异步 `list_tools(*, cursor=None)`（返回 `tools` 和 `next_cursor`），以及 `session.call_tool(name, arguments)`（返回 `content`、`structured_content`、`is_error`）；只提供高层 `call_tool` 不满足此装配协议。实际执行使用会话层的单次调用，以保留一次预算只发一次请求的约束；资料模块内部拥有该选择，普通规划调用方只持有 `Retrieval`。本机 MCP 服务可用 `async with mcp.Client(server) as client` 再交入 `Retrieval(client)`，无需自行模拟 SDK 对象。

`capabilities -> Capabilities` 返回声明副本和当前能力说明：`declarations` 保留实际已发现工具的名称、描述及参数 schema；`map_status` 为地图暂停说明或空字符串；`source_status` 为网页状态说明，地图独立模式为 `None`；`map_pause_reason` 保留既有 Web 状态枚举或 `None`。消费方不根据供应商私有状态拼接能力说明。`redact(text) -> str` 统一脱敏模型输入、回答、事实和保存材料。

## 有序外部查询分段

`await execute(queries: list[Query], *, observer=None, stop_requested=None) -> list[QueryResult]` 接收一段外部查询，不接受历史回读的调度职责。`Query(query_id: str, name: str, arguments: dict[str, Any])` 保留调用身份、工具名和原始参数；执行开始时复制输入。分段内部地图按提出顺序串行，网页搜索与正文共用最多十个在途请求；两类服务可以重叠，各自保持既有 0.4 秒启动间隔。供应商严重故障仅暂停对应能力，后续分段沿用该资源实例的暂停状态。

每个输入对应一个同位置的 `QueryResult(query_id: str, content: str, is_error: bool, not_executed: bool = False)`。返回不可变记录值，长度、身份和顺序与输入对应；成功、实际失败和未启动明确区分。未知工具或参数错误保留普通工具失败语义；暂停、停止导致的未启动不伪报实际服务请求。网页返回保留摘录／正文区分、实际来源、查询时间及核实边界；获得资料不表示已核实或完成旅行规划。

`observer(kind, data)` 为异步事实回调，事件沿用 `tool.waiting/running/completed/failed/not_executed` 的已有字段，额外携带 `query_id` 供规划绑定整轮事实身份。事件数据与返回结果隔离。开始事实必须在进入服务前可靠保存；回调抛错时设置停止信号，禁止新启查询，等待已启动调用真实返回和全部保存尝试后传播原异常。生产记录故障回调抛出 `RecordAbort`，资料获取不依赖记录或上下文实现来识别它。普通供应商失败作为查询结果，不作为记录中止。

停止不会取消已经发送的查询，也不会释放仍在途的运行名额。调用方须等待 `execute` 返回或抛错，再完成轮次收束；不得把停止信号视为执行已经结束。

## 验证与内部定位

[公开契约测试](../../tests/test_retrieval_contracts.py)覆盖身份及原序、真实 MCP adapter、停止、开始事实失败、结果保存失败时等待其他真实返回，以及观察隔离；[公开 Web 并发回归](../../tests/test_tool_concurrency.py)覆盖地图串行、网页十个在途、启动间隔、故障隔离及全局名额；[混合批次与预算](../../tests/test_call_budget.py)和[固定 Web/SSE/CLI 与旧库基线](../../tests/test_migration_baseline.py)覆盖跨能力协作。

只在修改内部时展开：[service.py](../../travel_app/retrieval/service.py)负责分段收齐与能力视图，[amap_mcp.py](../../travel_app/retrieval/amap_mcp.py)和[exa_mcp.py](../../travel_app/retrieval/exa_mcp.py)保留供应商调度、错误分类及内容处理，[connections.py](../../travel_app/retrieval/connections.py)和[source_connection.py](../../travel_app/retrieval/source_connection.py)管理生产连接。阶段证据见 [#83 验收](../acceptance/retrieval-planning.md)。
