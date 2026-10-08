# 旅行助手执行轨迹：SDK 数据能力核查

核查日期：2026-10-08。对应决策议题：[核实模型与高德 SDK 可提供的轨迹数据](https://github.com/lll1024/Potato/issues/2)，父地图：[旅行助手本地调试前端：对话与执行轨迹决策地图](https://github.com/lll1024/Potato/issues/1)。这是一份能力与缺口研究，不选择数据契约、前端框架、数据库或交互方案。

## 结论与适用范围

现有调用边界可以取得模型请求参数、模型全部已解析内容块、服务返回的消息身份／结束原因／usage，以及高德工具请求和已解析结果。但当前代码主要保存会话消息，模型顶层元数据与部分高德原始信息随后丢失。重启历史、实时状态、应用关联 ID、时间与限速等待都需要旅行助手新增采集。SDK 定义了字段，不表示任意 Anthropic 兼容服务实际返回这些字段；本研究没有向真实模型或高德发起请求，也没有读取 `.env`，因此不保证某个未指定模型服务的返回能力。[来源：当前模型调用与历史写入](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L143)、[当前高德包装](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L138)、[SDK 本地类型索引](#sdk-本地一手来源)。

本次读取仓库基线提交 `b47386738d2926fa13956fbfe719e7d0d8d5a81c`。`requirements.txt` 的固定版本与本机虚拟环境元数据一致：`anthropic==1.11.0`、`mcp==2.3.0`、`httpx2==2.13.1`；另核实已安装 `mcp-types==2.3.0`（MCP 的固定依赖）、`pydantic==2.13.5`，Python 为 `3.14.7`。后两者是本机已安装版本，不是本仓库新增锁定。以上由本地发行包 `METADATA`、`importlib.metadata.version` 与 SDK 源码核验；未以最新版网页替代锁定版本。[固定依赖](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/requirements.txt)。

## 模型边界的数据清单

| 数据 | 已核实的真实来源 | 当前保存情况与限制 |
| --- | --- | --- |
| 完整逻辑请求输入 | `messages.create(model, system, messages, tools, max_tokens=8000)` | 输入含独立 `SYSTEM`、累积消息、当时实际工具声明与模型／输出上限。只存 `messages` 无法还原完整请求；需要在调用前采集不可变快照。SDK 还会处理省略项并组装 HTTP 请求，“完整逻辑输入”与 HTTP 原始字节应分别命名。 |
| 模型返回全部内容块 | SDK `Message.content` | 当前逐块 `model_dump` 放入历史，保留已解析的非文本块；终端最终回答仅拼接 `text`。已解析对象不等同于原始响应字节。 |
| 服务消息 ID、返回模型、角色 | `Message.id`、`model`、`role` | 当前历史只存 `role/content`，ID 和返回模型未保留。返回模型应与请求模型分别记录；本地请求 ID 也不能冒充服务消息 ID。 |
| 模型请求 ID | SDK 顶层公开属性 `_request_id`，来自服务 `request-id` 响应头 | 可缺失；区别于 `Message.id`。默认 JSON 序列化不保存该属性，当前脱敏后重新校验会丢失，必须在该步骤之前单独取得。 |
| 结束原因 | `stop_reason`、`stop_sequence`，还有可选 `stop_details` | 当前不保存。已核实类型包括 `end_turn/max_tokens/stop_sequence/tool_use/pause_turn/refusal/model_context_window_exceeded`；当前控制流按有无 `tool_use` 决定结束，不能从“返回了文本”推断自然完成。 |
| 输入／输出 token | `usage.input_tokens/output_tokens` | 类型要求这两个字段，但兼容服务可能不满足，不能以必需类型伪造默认 0。当前不持久保存 usage；失败或缺失时没有可据实统计的 token。 |
| 缓存 token | 可选 `cache_creation_input_tokens/cache_read_input_tokens`，`cache_creation` 中 1 小时／5 分钟细分 | 只有服务实际返回才可呈现。当前没有设置 `cache_control`，不能推断开启缓存、缓存命中或缓存命中率。 |
| 思考块及 token 细分 | SDK 认识 `thinking`、`redacted_thinking`，可选 `usage.output_tokens_details.thinking_tokens` | 当前请求没有启用 `thinking` 配置。字段只证明 SDK 能解析；服务没有返回时不可补写思考内容。`redacted_thinking.data` 是不透明数据，`signature` 也不应解释为推理文字。已返回的思考文字不保证是全部内部推理。 |
| 服务错误 | `APIError` 的类型、`message/body/request`，`APIStatusError.status_code/request_id`，连接与超时子类 | 当前统一捕获 `AnthropicError` 生成通用说明，细节不存。收集异常的 `request` 时可能接触凭据，应先脱敏；无服务响应时没有响应 ID、usage 或服务耗时。 |
| 原始响应／响应头 | SDK `messages.with_raw_response` 可获得响应包装；普通 `create` 返回解析对象 | 当前未采用原始响应包装。是否新增原始响应保存由后续数据契约议题决定；不能把解析后 `model_dump_json` 标成 HTTP 原文。 |

上述 SDK 字段来自本地 `anthropic/types/message.py`、`usage.py`、`content_block.py`、思考块类型、`_models.py`、`_exceptions.py` 与 `resources/messages/messages.py`，见文末可点击文件和 SHA-256。当前调用与存储差异见 [agent_loop](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L143)。

Anthropic `Message` 源码说明输入总量包含普通输入、缓存创建和缓存读取；可见字符串长度不等于计费 token。该说明适用于 SDK 的上游 API 语义，不能未经兼容服务核实就推广为该服务的缓存或计费保证。当前也不能据输出 token／总耗时推出服务真实生成速度，因为总耗时可能含网络与处理延迟。[一手来源：`Message.usage` 注释与 `Usage` 类型，见 SDK 本地索引。]

## 高德与 MCP 边界的数据清单

| 数据 | SDK／现有代码能提供什么 | 当前缺口 |
| --- | --- | --- |
| 工具声明 | `list_tools` 返回名称、描述、输入 schema、可选输出 schema／annotations／meta 等；应用只把允许名单中的 `name/description/input_schema` 提供给模型 | “模型看到的声明”应记录应用最终声明，不以服务器完整列表代替；如果要调试发现过程，需另采集发现返回与分页，当前仅保存转换后的声明。 |
| 调用关联 | 模型 `tool_use.id/name/input`，回填 `tool_result.tool_use_id` | 现有历史可关联工具请求与工具结果；MCP JSON-RPC 请求 ID 是另一身份，目前应用层不保存。应用的轮次／请求／事件 ID 需另建。 |
| 原始已解析工具结果 | MCP `CallToolResult.content/structured_content/is_error/meta/result_type` | 应用只提取 `text` 与 `structured_content`，再转为 `{"text": ..., "data": ...}` 字符串。图像、音频、资源块、meta 等未进入模型结果或历史；不能把这个包装称为完整 MCP 结果。 |
| 协议错误 | `MCPError.code/message/data` | 当前转换为应用错误说明，原始错误细节丢失。要显示准确错误分类需在转换前采集，同时脱敏。 |
| 高德业务错误 | 文本 JSON／`structured_content` 中 `status/infocode/info`，以及 MCP `is_error` | 应用递归识别业务失败，并将部分鉴权／额度／服务故障映射为停止说明。即使 SDK `is_error=false` 也可能业务失败；应保留原始标记和应用分类的区别。 |
| HTTP／流故障 | `AmapHTTPClient` 保留部分 `http_status`，将 404/410、连接、超时／SSE 提前断开转成协议错误 | 这层会将故障响应关闭并生成新的 JSON-RPC 错误，原 HTTP 错误正文已丢失；如要留原文，采集位置必须早于转换。收到 HTTP 状态和内容故障不等于应用拥有服务内部追踪。 |
| 进度通知 | `session.call_tool` 支持可选 `progress_callback` | 当前没有传入回调；高德服务器会否发送进度未核实。首版开始／结束状态可由应用自行发出，不依赖服务进度通知。 |

来源：[工具发现与调用转换](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L116)、[HTTP 故障转换](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_http.py#L13)、本地 MCP `session.py`、`client.py`、`shared/exceptions.py` 与 `mcp_types/_types.py`（文末索引）。MCP `CallToolResult` 类型不定义工具 token 用量，不能为高德查询补造 token；如以后服务器用自定义元数据返回，需明确来源与语义。

现有代码特意调用 `client.session.call_tool`，一次预算只发一次工具请求，绕过高层 `Client.call_tool` 的 `HEADER_MISMATCH` 重列重发及 `input_required` 多轮处理；不要为采集轨迹直接换成高层调用，否则会改变预算和重试行为。会话层仍可能执行结果 schema 验证，必要时刷新工具清单，因此“一次工具预算”也不等于“一次所有类型的网络请求”。模型入口同时设置 `max_retries=0`。[应用调用位置](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L150)、[模型客户端参数](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L215)、本地 MCP `Client.call_tool`／`ClientSession.call_tool` 源码。

## 必须由应用采集的事实

- 对话轮次以真实用户输入开始；模型请求是轮次内的多次调用。协议中携带工具结果的 `role=user` 是结果回填，不是新用户输入。终止时的助手说明由应用生成，应与模型生成区分。历史列表本身不足以无歧义还原轮次、模型请求及应用终止事件。[当前循环](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L172)。
- 轮次／模型请求／工具调用开始和结束时间、总耗时、运行中状态、失败或取消状态、实时事件顺序与持久化，现有代码均未记录。墙钟用于时间定位，单调时钟用于耗时；没有实测时标为未采集，不从条目顺序推算秒数。这是从现有采集缺口得到的工程要求，字段与状态名称仍由数据契约议题决定。
- 地图调用有 `_next_call_at` 与单调时钟的应用节流，默认间隔 `0.4` 秒；需要分别记录等待开始／实际等待结束、工具网络处理开始／结束，才能区分限速等待和工具耗时。在 `tools.call` 外计时会包含节流等待。SDK 并未返回这段本地等待时长。[节流代码](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L145)、[预算与间隔常量](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/limits.py)。
- 不在允许名单、已有致命错误、连接已坏、工具预算耗尽、前一个调用使本批停止时，都可能产生失败结果但没有执行底层调用。现有预算在进入 `tools.call` 前递增，无法单凭计数判断发送了多少次请求。需要在各分支直接记录未执行事实与原因；未执行项没有服务执行耗时或服务返回。[应用预算分支](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L172)、[工具前置分支](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L138)。
- 非流式 `messages.create` 可以由应用在等待前后发送开始／结束事件，但调用中没有逐字内容。首 token 延迟、逐字时间线和真实生成速度无法由当前调用方式准确取得。[SDK `create` 的 stream 默认与应用调用](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L156)。
- 当前 `history` 只在 `run_cli` 内存中存在，重启回看无法从 SDK 补回。文件或数据库选择、恢复未完成事件的规则等留给会话与持久化决策。[内存历史](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L223)。

## 脱敏与“完整”的边界

当前 `AmapTools.redact` 替换已知高德 Key、模型 API Key／Auth Token 的原值及 URL 编码形式；删除带用户信息或 `key/api_key/api-key/access_token/token/auth/password` 查询参数的 HTTP(S) URL。工具声明、输入历史、模型已解析响应与正常工具结果都经过它；终端入口还关闭底层 SDK 网络日志输出。[脱敏实现](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/amap_mcp.py#L201)、[模型脱敏](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L148)、[终端日志设置](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/agent.py#L258)。

这不是面向任意轨迹载荷的完整脱敏策略：它不按 JSON 字段名通用屏蔽 Authorization、Cookie 或任意未登记秘密；不会识别所有编码、普通文本 `password=...` 或个人出行地址。当前不保存原始异常、HTTP 请求头、原始响应、被丢弃内容块与 SDK meta，因此现有测试也不证明这些新采集面已经安全。要保存新载荷，需为每个采集入口确认脱敏覆盖；“完整”应说明是脱敏后的完整逻辑载荷／已解析结果，凭据替换不被当成数据缺失错误。此处列出实际覆盖与缺口，具体规则仍需由相关 HITL 议题决定。

还有一个已离线验证的字段真实性陷阱：未返回的可选缓存字段在 SDK 对象的 `model_fields_set` 中不存在，但默认 `model_dump_json` 会补成 `null`；当前反序列化再校验后，字段会被视为出现过。因此如果未来需要区分“字段没返回”和“明确返回 null”，必须在默认序列化和重新校验之前记录原始字段存在性或保留适当快照；不能从当前重建对象反推。服务请求 ID `_request_id` 也在这个 JSON 往返中丢失。有效的数值 0、未返回和不可用不能混用。[一手类型／序列化实现：文末 `Usage`、`BaseModel`；离线验证记录如下。]

## 验证记录与未完成项

1. `importlib.metadata` 验证上述安装版本；读取发行包与 SDK 源码，没有读取 `.env` 或启动 CLI。
2. 使用人工构造的 `Message` 和 `CallToolResult` 进行纯内存能力检查：文本＋图像及 meta 可由 MCP 类型保留；`Message` 可以具有不同的消息 ID 与请求 ID；`_request_id` 不进入默认 JSON；usage 字段存在性在 JSON 往返前后发生上述变化。没有发送 HTTP 请求。
3. `.venv/bin/python -m unittest discover -s tests -p test_agent.py`：9 项通过。
4. `.venv/bin/python -m unittest discover -s tests -p test_failures.py`：8 项通过。全部使用本地桩／人工异常，覆盖工具预算、未执行结果、错误分类、调用 ID 保留、模型失败后的成功结果与已有脱敏范围。测试夹具 usage 值是合成的 `1/1`，不能作为真实服务 usage 证据。[模型夹具](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/tests/test_agent.py#L16)、[失败测试](https://github.com/lll1024/Potato/blob/b47386738d2926fa13956fbfe719e7d0d8d5a81c/tests/test_failures.py#L41)。

未验证：真实兼容模型身份及返回字段、思考／缓存开启条件、真实高德当前声明／内容块／进度通知、实测耗时／token／额度状态。研究无需为这些未验证项请求真实凭据；后续决定可按“可选字段据实显示，缺失明确标记”形成规格，若希望强保证某服务能力，则需要另设验证议题。

## SDK 本地一手来源

本地包安装根目录为 `/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/`。以下链接对应本次实际读取的锁定版本一手源码；SDK 没有复制进研究分支。表内 SHA-256 可用于以后核对环境是否相同。这些本地来源不能在 GitHub 网页直接打开，远程读者可按已核实版本安装并与哈希对照。本次没有访问或声称核实上游仓库的 tag URL。

| 一手源码 | SHA-256 |
| --- | --- |
| [Message](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/message.py) | `cdf6b3feba97d806d76922253de41539cb36b2c87d911052d1412fd16b4f3da4` |
| [Usage](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/usage.py) | `067d4e2d9fa8b623af5d05010351709078d803234260569b841a0f9ba460c271` |
| [ContentBlock](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/content_block.py) | `3c835c016c489c319ad4bf3fbbdc7aae6caece672d625e9528adaed262a0f03b` |
| [ThinkingBlock](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/thinking_block.py) | `f86c87c6b20e2b4f25cff074d7d859a0004a3c83b44eeecb5d0a9496bdb4ad2c` |
| [RedactedThinkingBlock](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/redacted_thinking_block.py) | `8d4ed28c2ce833ac98e687f3dfbe91f61eff4ebb587721d3deee3745e850a569` |
| [OutputTokensDetails](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/types/output_tokens_details.py) | `691e4e601042536f65d67db6655c52cc622efab2a82dde7e640033c68f9e0592` |
| [模型异常](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/_exceptions.py) | `10418f63dd4396903f6da4e90d4b776704a5438bb3b67b8023c4c20db075618d` |
| [BaseModel 与请求 ID](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/_models.py) | `39508d30ad89a4614d8b87fd6060ec330a026f2eccb23b997803bcb0807eeefe` |
| [模型请求组装](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/anthropic/resources/messages/messages.py) | `84cdd72a903199e010076ba41aebb62e54c3ab3d0bf8109d51ad37935ab8dbd0` |
| [MCP 会话调用](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/mcp/client/session.py) | `17895bd7fc0b98fd78f1ec48be89a3f27dca483f587cb32a4298ad9a6ba347eb` |
| [MCP 高层调用](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/mcp/client/client.py) | `36c91f9dcfff52dcf2a4736db6d570f874d7958806f68331ff2bae3b29639031` |
| [MCPError](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/mcp/shared/exceptions.py) | `3f78550758342eca46c8c17cf4a8659d4a5f3e1e074b032e940fc07e0f8b5fae` |
| [MCP 结果与工具声明](/Users/jarviliu/projects/travel/.venv/lib/python3.14/site-packages/mcp_types/_types.py) | `0d07007bf5052c5fe123d6f12addae8fdcd4eabfd2b3a2fd639c3a47675b7d98` |
