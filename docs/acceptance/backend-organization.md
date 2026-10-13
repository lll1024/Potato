# 后端重组整体回归与职责集中验收（#70）

2026-10-13 在 `codex/backend-organization` 验收 [任务 #70](https://github.com/lll1024/Potato/issues/70)，覆盖[父规格 #66](https://github.com/lll1024/Potato/issues/66) 的最终集成结果与 [ADR 0006](../adr/0006-backend-package-and-execution-responsibilities.md)。生产实现为 `1170aa2`，直接对照的重组前主分支为 `4fff3d26ef43a18a4a19263d50240a5ff4d130ae`；本任务补充可复现验收脚本、当前运行证据和导航，不修改生产行为。

## 当前运行结果

下表均为本任务重新运行的结果；#67–#69 的记录仅用于追溯分阶段基线。

| 检查 | 结果 | 本次证据 |
| --- | --- | --- |
| 完整后端回归 | 296 项通过，250.805 秒 | [完整回归](backend-organization/backend-full.log.gz) |
| 全部生产后端类型检查 | 根入口和整个 `travel_app` 包：0 错误、0 警告 | [类型检查](backend-organization/types.log) |
| 前端现有测试、类型与生产构建 | 13 项测试通过；TypeScript／Vite 构建通过 | [测试](backend-organization/frontend-tests.log)、[构建](backend-organization/frontend-build.log) |
| 两根启动入口与资源解析 | 4 组通过：项目根 `.env` 覆盖、默认／显式静态资源、工作目录相对数据目录、参数与退出诊断 | [资源结果](backend-organization/resources.json)、[复用脚本](backend-package/resource_checks.py) |
| 重组前后请求与上下文事件 | 两侧各 20 项测试通过；19 个 HTTP 场景、106 次完整请求、499 条事件归一化后相同 | [对照结果](backend-organization/equivalence.json)、[原版记录](backend-organization/before.json.gz)、[最终记录](backend-organization/after.json.gz)、[原版测试](backend-organization/equivalence-before.log)、[最终测试](backend-organization/equivalence-after.log) |
| 原版数据库在最终版本读取和续聊 | 原版保存的会话、执行轨迹、有效摘要与旅行者偏好保持；续聊注入旧偏好并恢复有效摘要 | [原版写入](backend-organization/existing-data-before.log)、[最终续聊](backend-organization/existing-data-after.log)、[复现脚本](backend-organization/baseline_checks.py) |
| 正式页面故障与偏好恢复 | 3 项通过：桌面／移动故障资料、轨迹、新轮次重试、刷新和偏好后台有限重试 | [浏览器恢复](backend-organization/browser-recovery.log) |
| 正式页面上下文隔离补充 | 桌面／移动 2 项通过：采用、保存、停止、中断后恢复及完整原文；保留生产预算与所有原断言 | [补充结果](backend-organization/browser-adjusted-context.log) |
| 原上下文浏览器夹具 | 桌面／移动 2 项失败；既有第一轮输入超预算问题，不计为通过 | [本次原夹具失败](backend-organization/browser-original-context.log)、[独立问题记录](backend-organization/baseline-issue.md) |
| 导航、内部导入与原行为基础 | 10 份文档的本地链接有效；81 份活动 Python 文件无旧内部导入；14 个基础模块仅改包导入；原提示词、薄入口和历史档案保持 | [核对清单](backend-organization/navigation.json)、[复现脚本](backend-organization/navigation_checks.py) |
| 最终独立审查 | Standards 0 项；Spec 0 项未解决发现；证据措辞建议已修正 | [双轴审查](backend-organization/review.md) |

首次浏览器运行的 Chrome 启动被沙箱限制阻止，保留[失败日志](backend-organization/browser-sandbox-failure.log)；允许运行后得到上述实际浏览器结果。启动失败不计作行为回归或通过。

初次导航检查在完整日志尚未压缩、自生成清单尚未产生时报告两个缺失链接，见[初次记录](backend-organization/navigation-first.log)。完成日志压缩，并在检查脚本中明确识别自身输出后，最终核对无缺失；没有通过忽略其他不存在的目标隐藏问题。

## 规则集中与调用关系

维护者从[职责地图](../backend-map.md)定位行为及其公开验证入口。上下文维护与运行状态不是只迁移文件：

- `ContextMaintenance` 集中持有本轮视图、计量、摘要额度与终止失败状态，`prepare_request` 与 `prepare_recovery` 共用整理、摘要、预算判断和严格缩小校验。调整压力或恢复规则从此职责进入；`agent_loop` 继续执行旅行模型步骤、工具、普通重试和一次恢复控制，不再分别编排摘要额度与预算决策。底层摘要保留候选结构、来源、版本、停止复核与有限尝试职责。
- `TurnRuntime` 集中持有接受锁、接受状态、活动身份、执行任务、停止原因、存储故障与未保存事实、变化通知和偏好后台任务。Web 路由、存储异常和退出通过 `submit`、`stop`、`snapshot`、`storage_failed`、`shutdown` 协作，实际执行及保存收束后释放名额。Web 保留资源装配和 HTTP/SSE，Store 保留 SQLite 事务，偏好模块保留闲置、来源与版本规则。

没有旧顶层内部模块、永久模块别名或转发兼容层；根入口只调用各自的包内 `main`。14 个其余原生产模块除包导入前缀外与主分支完全一致，包含摘要、预算、重试、数据 schema 与偏好规则；助手系统指令及终止文案也保持。此静态核对与公开行为验证分别说明未改动的基础及执行效果，不把结构检查当成运行证据。

## 父规格范围与证据映射

`US` 对应父规格的 User Stories，`D` 对应 Implementation Decisions。下表覆盖全部 26 条故事与 14 条实施决策，公开测试均在本次完整回归中运行。

| 范围 | 规格映射 | 最终实现及验证入口 |
| --- | --- | --- |
| 单浅层包、导航、验收索引和档案 | US1–6、US10；D1–2、D5 | 根入口与 `travel_app`；[职责地图](../backend-map.md)、[索引](README.md)、[档案](../../.scratch/README.md)；导航清单验证链接、导入和旧档案内容 |
| 命令、配置、页面、目录与既有数据 | US7–9；D1、D3–4、D13 | 资源脚本、原版数据库续聊；[CLI](../../tests/test_cli.py)、[正式 Web 进程](../../tests/test_web_entrypoint.py)、[真实 SDK／MCP 本机替身](../../tests/test_mcp_integration.py) |
| 压力与超限共用整理、计量和整轮四次摘要额度 | US11–12；D6–7 | `ContextMaintenance`；[超限恢复](../../tests/test_context_limit_recovery.py) 的共用额度／认证停止、不重复查询及严格缩小场景；[增量压缩](../../tests/test_context_incremental.py)、[预算](../../tests/test_context_budget.py) |
| 普通重试、摘要有限尝试与一次超限恢复独立 | US13、US16；D7–8 | `agent_loop`、`model_retry`、`context_summary`；超限恢复的五次普通重试与已完成地图调用保持场景；[模型重试](../../tests/test_model_retry.py)、[摘要](../../tests/test_context_summary.py) |
| 完整历史、原文回读、当前要求、日期、来源与未预订状态 | US14–15；D9 | [历史回读](../../tests/test_session_history.py)、[整理](../../tests/test_context_organization.py)、[日期续聊](../../tests/test_travel_date_continuation.py)、[纠正](../../tests/test_travel_date_corrections.py)、摘要和增量压缩；完整请求前后对照 |
| 停止在途摘要、模型、工具与等待 | US17、US23；D9、D11 | [停止](../../tests/test_stop.py)、超限恢复的在途摘要真实用量和禁止新请求、模型重试的停止等待；名额保留及保存故障覆盖在[存储](../../tests/test_storage.py) |
| 候选保存失败与真实进程恢复 | US18、US24；D9、D12 | [上下文恢复](../../tests/test_context_recovery.py) 覆盖杀进程、候选采用后退出及保存边界；存储、[进程恢复](../../tests/test_recovery.py) 覆盖只恢复已提交事实、不重放 |
| 全局名额、提交去重、响应丢失、冲突与旧停止隔离 | US19–22；D10–11 | `TurnRuntime`；[提交](../../tests/test_submissions.py)、停止和存储测试通过 HTTP/SSE 验证，无新内部类断言 |
| 页面断开、刷新、多订阅续接与后台生命周期 | US25；D10–13 | Web SSE 与 `TurnRuntime`；[续接](../../tests/test_reconnect.py)、[偏好活动](../../tests/test_preference_activity.py)、[偏好恢复](../../tests/test_preference_recovery.py)、正式页面浏览器场景 |
| 每阶段回归、最终检查与集中职责审查 | US26；D14 | [#67](backend-package.md)、[#68](context-maintenance.md)、[#69](turn-runtime.md) 保留前后基线；本页提供最终运行和独立审查 |

Testing Decisions 的最高公开 seam 继续使用 HTTP/SSE、真实 CLI／服务子进程、既有资源注入与纯逻辑测试；本任务没有新类结构测试或私有调用次数断言。测试辅助代码只按真实包路径替换依赖，未弱化原断言。旅行日期、长期偏好、价格条件、来源日期、待核实与未预订状态参加机制断言及完整请求对照。

Out of Scope 保持：没有新增旅行功能、界面变化、模型参数或提示词调整、数据迁移、新后端服务、多进程支持或旧导入兼容。前端、测试和历史档案布局保持；本任务不授权合并主分支或关闭父规格 #66。

## 适用范围与未通过项

原上下文夹具失败在重组前主分支也存在，独立记录其影响和后续处理。本次未调整原夹具或生产预算；隔离补充仅将重复背景次数 `310/350/550` 改为 `260/280/450`，同一 40,000 窗口、同一生产实现及全部原行为断言通过。这说明相关页面机制在可容纳输入下有效，不能宣称原夹具已经通过，也不能宣称所有既有浏览器测试通过。

请求对照直接在完整主分支隔离快照与最终版本运行原测试，复用 [#69 记录方式](turn-runtime/equivalence.py)。归一化随机原文／载荷身份，以及一个正文去重场景的本次动态检索时间；请求身份 `request_id`、`recovery_of`、`retry_id` 和开始时间、耗时从事件记录中省略。原始记录引用关系、旅行日期、历史来源日期、提示词、参数、摘要、工具结果与事件顺序参与比较；请求／恢复／重试身份关系由原公开测试断言覆盖，不计为快照对照证据。CLI 在公开回归验证，不计入 19 个 HTTP 场景。旧数据由原版公开入口生成合成数据库，未读取个人资料。

生产基础静态核对与完整请求对照未发现模型可见语义变化，本次未重跑付费模型、实际高德或 Exa 服务。现有[真实上下文旅行语义](context-compaction.md)、[旅行者偏好](traveler-preferences.md)和[官方来源](exa-sources.md)证据仍只说明其记录的历史版本与服务条件，不能视为本次实连结果，也不能保证所有未来旅行回答正确。

## 重现

从仓库根运行，所有数据与外部模型／地图替身位于测试临时目录：

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
npm test --prefix frontend
npm run build --prefix frontend
.venv/bin/python docs/acceptance/backend-package/resource_checks.py
.venv/bin/python docs/acceptance/backend-organization/baseline_checks.py
.venv/bin/python docs/acceptance/backend-organization/browser_checks.py
.venv/bin/python docs/acceptance/backend-organization/navigation_checks.py
```

基线脚本从固定提交创建临时快照，两侧记录及原版数据生成／最终续聊结果写入当前证据目录。浏览器脚本分别保留原夹具失败与隔离补充结果，原场景失败不因脚本最后成功而变为通过。类型检查覆盖全部生产模块，不使用只检查根入口的范围。
