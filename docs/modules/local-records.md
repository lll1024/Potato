# 本机记录：当前公开契约

公开入口为 [travel_app.local_records](../../travel_app/local_records/__init__.py)。`LocalRecords(Path(data_dir))` 打开该目录的 `travel.sqlite3`，维护原 schema v1、外键、持久游标和数据格式；目录创建与单服务锁由会话启动负责。退出调用 `close()`。同一实例在服务事件循环中同步使用，不跨线程共享；网络调用和等待不得放进记录事务。

## 接受、事实与共同保存

`accept(text, session_id=None, *, submission_id=None, fingerprint=None, preference_now=None)` 原子保存输入、轮次、输入载荷、接受事实、可选提交去重身份，以及偏好待处理登记。正式会话必须交入 `preference_now`；省略仅用于构造启用偏好前的旧数据。已有目标不存在时抛出 `KeyError`。提交身份内容核对、全局名额和重试规则由助手会话拥有，不要求调用方在此层自行组合事务。

`observe_request(session_id, turn_id, kind, data)`、`observe_tool(...)`、`observe_context(...)` 原子保存对应执行事实及关联载荷。开始事实成功返回后才能启动对应外部调用；调用方交入已脱敏值。SQLite 或序列化错误直接传播，会话将执行事实错误转换为公开 `RecordAbort` 并显示未保存状态。真实调用返回未保存时，重启仍只承认最后已提交事实。

`finish(session_id, turn_id, answer, material, outcome, duration_ms, *, preference_now=None)` 共同提交回答、终态、完整协议、偏好结束资格、已采用上下文版本的晋升和完成事实。`material` 必须是规划返回的 `TurnMaterial`；`outcome` 为 `PlanningResult.fields()`。`answer` 和材料均须已脱敏。正式会话交入结束时的 `preference_now`。事务失败全部回滚，不出现回答已保存而上下文或偏好资格未提交的半成功；成功返回才表示保存完成。

## 记录值

[values.py](../../travel_app/local_records/values.py) 定义不携带运行对象的不可变值：

| 类型 | 字段与用途 |
| --- | --- |
| `ContextCheckpoint` | `version`、`original_watermark`、`state_json`；`capture(state)` 立即序列化真实上下文快照，随后修改原状态不会改变材料 |
| `TurnMaterial` | `messages_json` 和可选 `context_checkpoint`；由规划整体传回，会话不拆开双写 |
| `PlanningSeed` | `messages_json`、可选 `context_json` / `date_context_json`、`source_materials_json`；在同一快照读取的续聊输入 |
| `RecordAbort` | 执行事实未可靠保存或保存材料无法生成的中止信号；规划不将其转换为普通模型或工具失败；可选 `kind` 指明无法恢复上下文或生成保存材料的边界 |

仅本轮确有状态为 `used` 的上下文版本时，完成事务才将对应版本晋升为 `saved`；没有已采用版本不新增版本。未保存候选不在重启后生效。

## 一致读取与恢复

`planning_seed(session_id)` 一次取得已完成协议、上一有效上下文、已确定旅行日期及中断资料索引。`snapshot(session_id, limit=50, before=None, summary=False, include_messages=True)` 原子读取现有页面结构及其事件游标；会话不存在返回 `None`。`stream_position()` 取得同一数据目录的 `cursor`、`stream_id`。调用方不取得连接、不开启事务。

`sessions`、`search`、`history_records`、`source_context`、`date_context`、`events_after` 的多步读取同样使用内部一致快照；嵌套读取复用当前事务，不提前提交外层写入。`submission`、`turn`、`payload`、`cursor`、`event_exists` 提供现有单记录读取，`rename`、`delete` 保留原管理语义。SQLite 错误向上传播，不伪造空结果；会话统一停止接受输入。`recover()` 只从已提交事实恢复中断状态，不自动重放外部调用。

`SCHEMA_VERSION` 与 `StartupError` 保留正式启动和外部协议所需类型。纯值解释 `source_metadata`、`saved_date_context` 供历史与日期适配复用；它们不引用规划、查询、上下文或偏好的运行对象。实现见 [storage.py](../../travel_app/local_records/storage.py)、[metadata.py](../../travel_app/local_records/metadata.py)和[查询资料投影](../../travel_app/local_records/query_materials.py)。

`preferences.read() -> PreferenceState` 一致读取偏好、歧义、管理保护、未完成输入及其会话运行事实和全局输入水位。`PreferenceState.capture(data)` 冻结 JSON，`data()` 返回调用方独立拥有的值；不携带连接、SQL 或偏好运行对象。输入和会话原始事实只用于条件复核，调用方不改写接受顺序或运行状态。

`preferences.write(expected, replacement) -> bool` 在 `BEGIN IMMEDIATE` 短事务中重新读取并比较完整条件快照；不匹配返回假且不写入，匹配后原子保存偏好、歧义、保护记录及输入处理字段的差异。新输入、结束事实、停止、管理版本、聊天删除和输入水位都纳入复核；写入故障回滚全部变更并传播。调用方可重新读取并按最新事实计算，不能把旧计算结果直接换上新条件。`preferences.context(session_id, before_turn)` 只读取该输入前两轮原文，供能力解释指代。

这些值和条件是偏好模块的持久接口；[旅行者偏好](preferences.md)拥有资格、来源、顺序、去重、歧义、管理保护、重试和恢复规则，本机记录不导入其实现。接受登记与结束时间仍在 `accept` / `finish` 原子事务内部写入。实现见 [preference_records.py](../../travel_app/local_records/preference_records.py)，数据表及旧库格式保持。独立阅读验收属于 #86。

验证：[记录值与公开保存](../../tests/test_record_contracts.py)、[原子接受](../../tests/test_submissions.py)、[故障回滚与在途返回](../../tests/test_storage.py)、[上下文恢复](../../tests/test_context_recovery.py)及[固定真实入口基线](../../tests/test_migration_baseline.py)。
