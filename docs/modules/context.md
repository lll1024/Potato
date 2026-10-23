# 模型上下文：当前迁移适配

[travel_app.context](../../travel_app/context/__init__.py) 当前只公开记录值适配：`PlanningSeed`、`TurnMaterial`、`ContextCheckpoint`、`RecordAbort`，以及解释已保存请求日期的 `saved_date_context(system)`。值的所有权和字段定义见[本机记录契约](local-records.md)。适配不持有记录连接，不运行模型，不变更旅行日期。

旅行规划经此入口接收和产出持久化值，避免反向依赖本机记录的运行实现。它不是完整的上下文服务。完整协议追加、模型视图同步、容量、摘要和恢复暂由规划内部与旧 `model_context.py`、`context_maintenance.py`、`context_summary.py`、`session_history.py` 协作，按 #82 继续封装；公开调用方只使用 `planning.plan`，无需自行构造 `ContextView`。

[记录值测试](../../tests/test_record_contracts.py)、[旅行日期延续](../../tests/test_travel_date_continuation.py)及[旧库续聊](../../tests/test_migration_baseline.py)覆盖本阶段适配。完整迁移状态见[迁移清单](migration.md)。
