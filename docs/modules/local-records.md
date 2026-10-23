# 本机记录：当前公开契约

当前仅交付记录值与中止类型，公开入口是 [travel_app.local_records](../../travel_app/local_records/__init__.py)，实现类型在 [values.py](../../travel_app/local_records/values.py)。完整原子接受、完成、一致读取与恢复入口留给 #81 等后续任务；当前 SQLite 实现仍在 `store.py` 和 `preferences.py`，它们不是新模块的公开 interface。

`ContextCheckpoint.capture(state)` 接收上下文视图的值快照，生成不可变记录，包含 `version`、`original_watermark` 和 `state_json`。JSON 在调用时序列化，之后修改原视图不改变保存材料；不保存上下文运行对象引用。调用方只应传入当前上下文状态的真实快照，序列化失败按现有轮次保存失败处理。

现有 `Store.finish` 使用该只读材料，仍在原事务内将本轮已采用版本从 `used` 晋升为 `saved`，与回答、完整协议、偏好结束资格和完成事实共同提交。不存在已采用版本时不新增版本，失败仍回滚共同提交。这个参数整理不等于已提供完整的结构化规划结果。

`RecordAbort` 表示执行事实未可靠提交，是原有记录中止信号的独立类型。记录入口抛出后，执行方停止启动后续调用，等待在途调用及保存尝试收束；会话职责显示未保存状态并停止接受输入。它不能变成普通模型或工具失败，也不表示轮次已保存。当前由 `TurnRuntime` 观察入口发出并捕获；移入旅行规划及本机记录完整入口的工作属于 #81。

这两个符号无额外配置、网络或后台生命周期。值行为由 [record contracts](../../tests/test_record_contracts.py) 验证；共同保存、故障和进程恢复由 [storage](../../tests/test_storage.py)、[context recovery](../../tests/test_context_recovery.py) 及[行为基线](../../tests/test_migration_baseline.py) 验证。
