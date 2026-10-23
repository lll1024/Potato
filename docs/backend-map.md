# 后端职责地图

2026-10-23 起按 [ADR 0011](adr/0011-deep-modules-and-progressive-disclosure.md) 分阶段迁移六项能力。本文仍描述尚未迁移的真实实现；当前归属、公开契约与检查入口先见[迁移状态](modules/migration.md)，不能将下表中的旧内部入口视为新 module 的公开契约。

后端正按[六模块迁移清单](modules/migration.md)组织，公开使用先读[助手会话](modules/sessions.md)、[旅行规划](modules/planning.md)、[模型上下文](modules/context.md)和[本机记录](modules/local-records.md)契约；以下是按需展开的内部职责地图，无需额外安装项目包。根目录的 [agent.py](../agent.py) 和 [web.py](../web.py) 只调用包内 `main()`，保留日常启动命令。仓库生产代码、测试、子进程与语义验收脚本使用 `travel_app.*`，没有旧顶层模块的兼容层或模块别名。

## 从行为定位代码

| 要修改的行为 | 实现入口与主要调用关系 | 验证入口 |
| --- | --- | --- |
| CLI 启动、地图连接检查、退出诊断 | [agent.main / run_cli](../travel_app/agent.py) → 地图连接与旅行模型；地图故障仅暂停地图，CLI 可继续接收输入；`--check-amap` 只检查地图 | [CLI](../tests/test_cli.py)、[真实 MCP](../tests/test_mcp_integration.py) |
| Web 启动、资源装配、HTTP/SSE | [sessions.main / create_app](../travel_app/sessions/__init__.py) → LocalRecords、Runtime、TurnRuntime；资源与时间注入保持，路由负责协议适配，会话运行层负责持久化事件续接 | [正式入口](../tests/test_web_entrypoint.py)、[Web](../tests/test_web.py)、[事件续接](../tests/test_reconnect.py) |
| 批次查询与供应商名额 | [plan / _execute_plan](../travel_app/agent.py) 按提出顺序预留预算，以历史回读为串行边界，收齐前段真实结果；[AmapTools](../travel_app/amap_mcp.py) 保持地图串行，[ExaTools](../travel_app/exa_mcp.py) 共用十个网页名额并独立协调启动间隔；失败仅暂停对应供应商，保存异常传播前等待全部在途调用 | [分服务调度](../tests/test_tool_concurrency.py)、[网页故障](../tests/test_source_failures.py) |
| 提交身份、全局运行名额、停止、保存故障与退出 | [TurnRuntime](../travel_app/sessions/runtime.py) 的 `submit` / `stop` / `snapshot` / `storage_failed` / `shutdown`；集中持有活动身份、任务、接受状态、停止原因、未保存事实和变化通知，调用公开 plan，实际收束后释放名额；LocalRecords 保留事务职责 | [提交](../tests/test_submissions.py)、[停止](../tests/test_stop.py)、[保存故障](../tests/test_storage.py)、[真实进程恢复](../tests/test_recovery.py) |
| 对话轮次、旅行模型步骤、工具执行与观察事实 | [plan / _execute_plan](../travel_app/agent.py) → 重试、上下文维护、旅行日期、历史回读和查询工具 | [助手循环](../tests/test_agent.py)、[模型轨迹](../tests/test_trace.py)、[工具轨迹](../tests/test_tool_trace.py) |
| 普通模型网络重试 | [model_retry](../travel_app/model_retry.py) 判别故障与等待；_execute_plan 负责调用及停止边界 | [模型重试](../tests/test_model_retry.py)、[ADR 0004](adr/0004-bounded-model-request-retries.md) |
| 模型上下文维护、计量与预算 | _execute_plan → [ModelContext](../travel_app/context/runtime.py) 的 `prepare_request` / `prepare_recovery`；集中持有 ContextView、TokenMeter、整轮摘要额度与失败状态，统一整理、摘要和最终预算检查；基础数据与计量位于 [view](../travel_app/context/view.py) | [预算](../tests/test_context_budget.py)、[超限恢复](../tests/test_context_limit_recovery.py) |
| 摘要、完整交互切分、额度与采用校验 | [summary](../travel_app/context/summary.py) → 独立摘要请求；ModelContext 消费规划提供的共享额度并调用摘要，摘要职责复核和采用候选，LocalRecords 随轮次结果保存有效版本 | [摘要](../tests/test_context_summary.py)、[增量压缩](../tests/test_context_incremental.py)、[恢复](../tests/test_context_recovery.py) |
| 原始助手会话与工具载荷回读 | [history](../travel_app/context/history.py) → LocalRecords 原始记录；CLI 使用进程内记录 | [历史回读](../tests/test_session_history.py) |
| SQLite、助手会话、执行轨迹、提交身份和有效上下文版本 | [LocalRecords](../travel_app/local_records/storage.py) → 纯值投影与临时偏好记录适配 | [存储故障](../tests/test_storage.py)、[历史](../tests/test_history.py)、[真实进程恢复](../tests/test_recovery.py) |
| 跨会话旅行者偏好与后台提取 | [preferences](../travel_app/preferences.py) 提供模型提取及背景；[偏好记录适配](../travel_app/local_records/preference_records.py) 暂承接保存、闲置及版本复核；TurnRuntime 管理后台任务与存储故障，#84 继续收拢偏好规则 | [偏好](../tests/test_preferences.py)、[管理](../tests/test_preference_management.py)、[活动轮次](../tests/test_preference_activity.py)、[重启恢复](../tests/test_preference_recovery.py) |
| 当前时间、旅行日期与跨日续聊 | [travel_dates](../travel_app/travel_dates.py) → _execute_plan 本轮系统上下文；LocalRecords 恢复确定日期 | [日期](../tests/test_travel_dates.py)、[续聊](../tests/test_travel_date_continuation.py)、[纠正](../tests/test_travel_date_corrections.py) |
| 高德地点、餐饮、交通路线、天气 | [amap_mcp](../travel_app/amap_mcp.py) → [amap_http](../travel_app/amap_http.py)；[limits](../travel_app/limits.py) 保存额度、限速与超时 | [地点](../tests/test_search.py)、[餐饮](../tests/test_dining.py)、[交通路线](../tests/test_routes.py)、[故障](../tests/test_failures.py) |
| 网页资料获取与按需信息核实 | [travel_tools](../travel_app/travel_tools.py) 组合地图与 [exa_mcp](../travel_app/exa_mcp.py)，经 [source_http](../travel_app/source_http.py) 接入资料服务 | [公开资料展示](../tests/test_information_retrieval.py)、[Exa](../tests/test_exa_sources.py)、[专项来源](../tests/test_official_sources.py)、[日期适用性](../tests/test_official_date_applicability.py) |
| 已保存查询资料的摘要与参考索引 | [query_materials](../travel_app/local_records/query_materials.py) → LocalRecords；完整原文仍供回读 | [旧来源](../tests/test_legacy_sources.py)、[上下文整理](../tests/test_context_organization.py) |

## 启动与资源

`.env` 与默认 `frontend/dist` 由包内入口按项目根定位，保留 `.env` 覆盖运行环境的原规则。默认 `.travel-data` 与相对 `--data-dir` 仍按启动工作目录解析；`create_app(..., static_dir=...)` 保留显式静态目录注入。SQLite schema 与数据格式保持，已有助手会话、偏好和有效上下文版本从原目录继续读取。

测试辅助代码继续位于 [tests](../tests/)，跨测试文件辅助导入仍按原方式工作（定向运行时使用 `PYTHONPATH=tests`）。临时 CLI 环境复制根入口与整个包，不复制 `.env`。真实进程辅助入口 [web_main_process.py](../tests/web_main_process.py) 替换实际执行的 `travel_app.web` 模块依赖。语义脚本 [context_semantic_eval.py](../tests/context_semantic_eval.py) 与 [preference_semantic_eval.py](../tests/preference_semantic_eval.py) 使用同一包实现。

## 实施阶段与依据

[Issue #67](https://github.com/lll1024/Potato/issues/67) 负责目录与导航；[Issue #68](https://github.com/lll1024/Potato/issues/68) 集中上下文维护职责，验证见[上下文维护验收](acceptance/context-maintenance.md)；[Issue #69](https://github.com/lll1024/Potato/issues/69) 集中 Web 运行职责，验证见[运行职责验收](acceptance/turn-runtime.md)。父规格 [#66](https://github.com/lll1024/Potato/issues/66) 的最终范围映射、整体回归、职责集中审查及基线限制见 [#70 整体验收](acceptance/backend-organization.md)。阶段记录只说明各自运行范围，不代替最终验收或合并授权。

领域词汇见[术语表](../GLOSSARY.md)，兼容与职责决策见 [ADR 0006](adr/0006-backend-package-and-execution-responsibilities.md)。验证结论从[验收索引](acceptance/README.md)进入，旧任务从[历史档案说明](../.scratch/README.md)进入。
