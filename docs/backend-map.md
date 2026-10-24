# 后端内部职责地图

公开使用先读[仓库总览与六份契约](modules/README.md)。本页只在修改内部规则时展开，当前实现位于六个能力目录；历史阶段报告中的旧路径按当时版本解释。

| 修改范围 | 内部实现 | 验证入口 |
| --- | --- | --- |
| 提交身份、全局名额、停止、保存故障、退出 | [sessions/runtime.py](../travel_app/sessions/runtime.py) | [提交](../tests/test_submissions.py)、[停止](../tests/test_stop.py)、[存储故障](../tests/test_storage.py) |
| HTTP/SSE、启动、锁与资源释放 | [sessions/http.py](../travel_app/sessions/http.py)、通知逻辑（同文件内） | [根入口](../tests/test_web_entrypoint.py)、[续接](../tests/test_reconnect.py)、[进程](../tests/test_recovery.py) |
| 模型步骤、结构化结果、CLI | [planning/runtime.py](../travel_app/planning/runtime.py)、[资源](../travel_app/planning/resources.py) | [规划](../tests/test_agent.py)、[CLI](../tests/test_cli.py)、[模型重试](../tests/test_model_retry.py) |
| 混合批次、历史屏障与整轮预算 | [planning/batch.py](../travel_app/planning/batch.py)、[budget.py](../travel_app/planning/budget.py) | [预算](../tests/test_call_budget.py)、[查询批次](../tests/test_query_batching.py) |
| 旅行日期及纠正 | [planning/dates.py](../travel_app/planning/dates.py) | [日期](../tests/test_travel_dates.py)、[跨日续聊](../tests/test_travel_date_continuation.py)、[纠正](../tests/test_travel_date_corrections.py) |
| 查询分段、能力说明与供应商调度 | [retrieval/service.py](../travel_app/retrieval/service.py)、[地图](../travel_app/retrieval/amap_mcp.py)、[网页](../travel_app/retrieval/exa_mcp.py) | [公开查询](../tests/test_retrieval_contracts.py)、[并发](../tests/test_tool_concurrency.py)、[资料故障](../tests/test_source_failures.py) |
| 外部查询连接与 HTTP adapter | [retrieval/connections.py](../travel_app/retrieval/connections.py)、[source_connection.py](../travel_app/retrieval/source_connection.py)、[地图 HTTP](../travel_app/retrieval/amap_http.py)、[网页 HTTP](../travel_app/retrieval/source_http.py) | [MCP](../tests/test_mcp_integration.py)、[Exa](../tests/test_exa_sources.py) |
| 完整协议、模型视图与计量 | [context/runtime.py](../travel_app/context/runtime.py)、[view.py](../travel_app/context/view.py) | [公开上下文](../tests/test_context_contracts.py)、[容量](../tests/test_context_budget.py)、[增量](../tests/test_context_incremental.py) |
| 摘要、历史回读、超限恢复 | [context/summary.py](../travel_app/context/summary.py)、[history.py](../travel_app/context/history.py) | [摘要](../tests/test_context_summary.py)、[回读](../tests/test_session_history.py)、[超限](../tests/test_context_limit_recovery.py)、[恢复](../tests/test_context_recovery.py) |
| 偏好管理、提取、资格、保护与后台 | [preferences/service.py](../travel_app/preferences/service.py)、[extraction.py](../travel_app/preferences/extraction.py)、[rules.py](../travel_app/preferences/rules.py) | [公开偏好](../tests/test_preference_contracts.py)、[管理](../tests/test_preference_management.py)、[恢复](../tests/test_preference_recovery.py) |
| 事务、快照、持久游标、原子完成与恢复 | [local_records/storage.py](../travel_app/local_records/storage.py)、[记录值](../travel_app/local_records/values.py)、[偏好条件持久化](../travel_app/local_records/preference_records.py) | [公开记录](../tests/test_record_contracts.py)、[故障](../tests/test_storage.py)、[固定基线及旧库](../tests/test_migration_baseline.py) |
| 查询资料投影与已有来源 | [local_records/query_materials.py](../travel_app/local_records/query_materials.py)、[metadata.py](../travel_app/local_records/metadata.py) | [旧资料](../tests/test_legacy_sources.py)、[资料整理](../tests/test_context_organization.py) |

正式入口仍按项目根读取 `.env` 和默认前端构建，按启动工作目录解析相对数据目录；已有 schema 和协议保持。语义脚本和浏览器服务均按[检查清单](../architecture.json)登记，外部响应通过公开装配注入。共享模型事实采集与错误识别位于 [model_execution.py](../travel_app/model_execution.py) 和 [model_retry.py](../travel_app/model_retry.py)，不持有任何能力状态。

当前验证从[验收索引](acceptance/README.md)进入，任务以 [GitHub Issues](https://github.com/lll1024/Potato/issues) 为准；旧任务见[历史档案说明](../.scratch/README.md)。
