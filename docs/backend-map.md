# 后端职责地图

后端实现位于单个浅层 [travel_app 包](../travel_app/)，无需额外安装项目包。根目录的 [agent.py](../agent.py) 和 [web.py](../web.py) 只调用包内 `main()`，保留日常启动命令。仓库生产代码、测试、子进程与语义验收脚本使用 `travel_app.*`，没有旧顶层模块的兼容层或模块别名。

## 从行为定位代码

| 要修改的行为 | 实现入口与主要调用关系 | 验证入口 |
| --- | --- | --- |
| CLI 启动、地图连接检查、退出诊断 | [agent.main / run_cli](../travel_app/agent.py) → 地图连接与旅行模型；`--check-amap` 只检查地图 | [CLI](../tests/test_cli.py)、[真实 MCP](../tests/test_mcp_integration.py) |
| Web 启动、资源装配、HTTP/SSE | [web.main / create_app](../travel_app/web.py) → Store、Runtime、TurnRuntime；资源与时间注入保持，路由负责协议和持久化事件续接 | [正式入口](../tests/test_web_entrypoint.py)、[Web](../tests/test_web.py)、[事件续接](../tests/test_reconnect.py) |
| 提交身份、全局运行名额、停止、保存故障与退出 | [TurnRuntime](../travel_app/turn_runtime.py) 的 `submit` / `stop` / `snapshot` / `storage_failed` / `shutdown`；集中持有活动身份、任务、接受状态、停止原因、未保存事实和变化通知，调用 agent_loop，实际收束后释放名额；Store 保留事务职责 | [提交](../tests/test_submissions.py)、[停止](../tests/test_stop.py)、[保存故障](../tests/test_storage.py)、[真实进程恢复](../tests/test_recovery.py) |
| 对话轮次、旅行模型步骤、工具执行与观察事实 | [agent_loop](../travel_app/agent.py) → 重试、上下文维护、旅行日期、历史回读和查询工具 | [助手循环](../tests/test_agent.py)、[模型轨迹](../tests/test_trace.py)、[工具轨迹](../tests/test_tool_trace.py) |
| 普通模型网络重试 | [model_retry](../travel_app/model_retry.py) 判别故障与等待；agent_loop 负责调用及停止边界 | [模型重试](../tests/test_model_retry.py)、[ADR 0004](adr/0004-bounded-model-request-retries.md) |
| 模型上下文维护、计量与预算 | agent_loop → [ContextMaintenance](../travel_app/context_maintenance.py) 的 `prepare_request` / `prepare_recovery`；集中持有 ContextView、TokenMeter、整轮摘要额度与失败状态，统一整理、摘要和最终预算检查；基础数据与计量位于 [model_context](../travel_app/model_context.py) | [预算](../tests/test_context_budget.py)、[超限恢复](../tests/test_context_limit_recovery.py) |
| 摘要、完整交互切分、额度与采用校验 | [context_summary](../travel_app/context_summary.py) → 独立摘要请求；ContextMaintenance 共享整轮额度并调用摘要，摘要职责复核和采用候选，Store 随轮次结果保存有效版本 | [摘要](../tests/test_context_summary.py)、[增量压缩](../tests/test_context_incremental.py)、[恢复](../tests/test_context_recovery.py) |
| 原始助手会话与工具载荷回读 | [session_history](../travel_app/session_history.py) → Store 原始记录；CLI 使用进程内记录 | [历史回读](../tests/test_session_history.py) |
| SQLite、助手会话、执行轨迹、提交身份和有效上下文版本 | [Store](../travel_app/store.py) → 查询资料索引、旅行日期、PreferenceStore | [存储故障](../tests/test_storage.py)、[历史](../tests/test_history.py)、[真实进程恢复](../tests/test_recovery.py) |
| 跨会话旅行者偏好与后台提取 | [preferences](../travel_app/preferences.py) 管理保存、闲置、版本及模型提取；TurnRuntime 管理后台任务、统一接受状态和存储故障，停止时取消当前输入的提取资格 | [偏好](../tests/test_preferences.py)、[管理](../tests/test_preference_management.py)、[活动轮次](../tests/test_preference_activity.py)、[重启恢复](../tests/test_preference_recovery.py) |
| 当前时间、旅行日期与跨日续聊 | [travel_dates](../travel_app/travel_dates.py) → agent_loop 本轮系统上下文；Store 恢复确定日期 | [日期](../tests/test_travel_dates.py)、[续聊](../tests/test_travel_date_continuation.py)、[纠正](../tests/test_travel_date_corrections.py) |
| 高德地点、餐饮、交通路线、天气 | [amap_mcp](../travel_app/amap_mcp.py) → [amap_http](../travel_app/amap_http.py)；[limits](../travel_app/limits.py) 保存额度、限速与超时 | [地点](../tests/test_search.py)、[餐饮](../tests/test_dining.py)、[交通路线](../tests/test_routes.py)、[故障](../tests/test_failures.py) |
| 官方资料搜索与正文读取 | [travel_tools](../travel_app/travel_tools.py) 组合地图与 [exa_mcp](../travel_app/exa_mcp.py)，经 [source_http](../travel_app/source_http.py) 接入资料服务 | [Exa](../tests/test_exa_sources.py)、[官方来源](../tests/test_official_sources.py)、[日期适用性](../tests/test_official_date_applicability.py) |
| 已保存查询资料的摘要与参考索引 | [query_materials](../travel_app/query_materials.py) → Store；完整原文仍供回读 | [旧来源](../tests/test_legacy_sources.py)、[上下文整理](../tests/test_context_organization.py) |

## 启动与资源

`.env` 与默认 `frontend/dist` 由包内入口按项目根定位，保留 `.env` 覆盖运行环境的原规则。默认 `.travel-data` 与相对 `--data-dir` 仍按启动工作目录解析；`create_app(..., static_dir=...)` 保留显式静态目录注入。SQLite schema 与数据格式保持，已有助手会话、偏好和有效上下文版本从原目录继续读取。

测试辅助代码继续位于 [tests](../tests/)，跨测试文件辅助导入仍按原方式工作（定向运行时使用 `PYTHONPATH=tests`）。临时 CLI 环境复制根入口与整个包，不复制 `.env`。真实进程辅助入口 [web_main_process.py](../tests/web_main_process.py) 替换实际执行的 `travel_app.web` 模块依赖。语义脚本 [context_semantic_eval.py](../tests/context_semantic_eval.py) 与 [preference_semantic_eval.py](../tests/preference_semantic_eval.py) 使用同一包实现。

## 实施阶段与依据

[Issue #67](https://github.com/lll1024/Potato/issues/67) 负责目录与导航；[Issue #68](https://github.com/lll1024/Potato/issues/68) 集中上下文维护职责，验证见[上下文维护验收](acceptance/context-maintenance.md)；[Issue #69](https://github.com/lll1024/Potato/issues/69) 集中 Web 运行职责，验证见[运行职责验收](acceptance/turn-runtime.md)。父规格 [#66](https://github.com/lll1024/Potato/issues/66) 的整体职责集中验收由 #70 后续处理。

领域词汇见[术语表](../GLOSSARY.md)，兼容与职责决策见 [ADR 0006](adr/0006-backend-package-and-execution-responsibilities.md)。验证结论从[验收索引](acceptance/README.md)进入，旧任务从[历史档案说明](../.scratch/README.md)进入。
