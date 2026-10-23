# 旅行者偏好：公开契约

后端公开入口为 [travel_app.preferences](../../travel_app/preferences/__init__.py)，前端为 [frontend/src/preferences/index.ts](../../frontend/src/preferences/index.ts)。能力同时拥有管理界面、规划背景、提取来源校验、闲置资格、输入顺序、歧义、人工管理保护、有限重试和后台生命周期。调用方不筛选批次，也不推进 pending / processing / failed / completed 状态。

## 构造与公开操作

`Preferences(records, client, model, *, redact, clock=time.time, wait=asyncio.sleep, timeout=60, storage_failed=None, available=lambda: True)` 使用已打开的 `LocalRecords`、Anthropic 兼容异步模型客户端、模型名称和脱敏函数。它不打开第二个数据库，不管理模型连接，也不跨网络请求持有事务。时间、等待和外部客户端可以在系统边界注入。

| 操作 | 输入、结果与条件 |
| --- | --- |
| `snapshot()` | 一致读取现有 `schema_version: 1`、有效 `preferences`、`ambiguities` 与未完成 `processing`；字段及 HTTP 数据结构保持兼容 |
| `background()` | 从同一快照生成本轮只读规划背景；包括有效偏好及仍匹配目标版本的待澄清表达。即使记录为空，也明确声明以当前有效记录为准，防止从旧助手复述恢复已删除偏好 |
| `edit(id, version, content)` | 去除首尾空白并脱敏；非空且最多 2000 字。成功返回 `saved`，目标不存在返回 `missing`，版本过期返回 `conflict`。修改与撤销旧输入授权、清理目标歧义共同保存 |
| `delete(id, version)` | 同样返回 `saved` / `missing` / `conflict`；删除与管理保护共同保存，原聊天保留 |
| `start()` | 在本机记录完成轮次恢复后调用；内部恢复中断提取和结束资格，再启动单个后台任务。运行中重复调用不新增任务 |
| `shutdown()` | 异步停止后台任务并等待退出；尚未提交的提取保留 processing 记录，下一次启动按恢复规则处理。先于关闭记录和模型资源调用 |
| `stop(turn_id)` | 取消对应输入尚未提交的偏好处理；完成的偏好保留。会话只在原运行轮次停止时调用，不按页面当前选择猜测目标 |

记录异常直接传播；Web 保留 503 存储故障映射。后台遇到 SQLite 故障时调用 `storage_failed`，会话停止接受输入，`available()` 随即变为假，后台不再发请求或写入；不把故障当作模型失败或耗尽重试。如果未提供该回调，后台异常由 `shutdown()` 传播。非法编辑内容抛出 `ValueError`；HTTP 保留严格正整数版本、422 校验、404 缺失与 409 冲突反馈。

## 接受、结束与后台规则

会话通过 `LocalRecords.accept(..., preference_now=...)` 原子接受输入并登记待处理记录；通过 `finish(..., preference_now=...)` 原子完成轮次与记录结束时间。会话仅提供时间和运行事实，这两项协作由本机记录共同保存。启动前先调用 `LocalRecords.recover()`，再调用偏好 `start()`；它们不重放旅行模型或工具请求。CLI 不构造本能力，也不新增后台偏好处理。

后台每 30 秒检查可处理输入：会话最新轮次结束后连续闲置至少 3600 秒，且不存在 running / stopping 轮次，待重试时间已到，才有资格。新输入重置闲置条件；只打开网页、阅读或编辑草稿不影响资格。单处理器按输入顺序选择最早合格会话，批量提取该会话尚未完成的新输入；前台规划与后台请求独立运行，不等待偏好保存。

只接受饮食、活动兴趣、交通与住宿的稳定本人偏好。旅行日期、单次预算、具体酒店、游玩节奏和同行人信息不保存为长期偏好。新增依据必须来自当前批次用户原文中的连续片段；少量旧聊天和截取的助手回答只能解释指代。模型语义限制保持，后端结构与来源校验不等于对自然语言含义的完美判断。

提取失败最多发出三次请求，分别在失败后等待 30、120 秒；超时与无效结构同样计次，达到上限保存固定失败状态。重启保留已发请求次数并退避；请求期间新增输入或人工管理导致资格失效时，退回 pending 并退还该次失效资格的次数。管理冲突后的进程恢复保留至少 30 秒等待。取消输入不恢复，旧聊天从未登记的新输入不回溯处理。

提交前由偏好模块以最新公开记录事实复核资格和版本，按用户输入顺序计算去重、合并、更新及歧义；本机记录在短写事务中再次比较条件快照并共同写入。发生新输入、停止、聊天删除或人工修改时，旧结果不能越过复核。人工管理保存旧内容、版本及当时全局输入水位；删除偏好不从旧聊天恢复，水位之后的新表达仍可明确建立偏好。删除聊天仅清除其待处理输入和来源关联，已保存偏好保留。

## 前端与验证

`PreferencesView({onBack})` 是独立管理页；外层只装配公开组件并提供返回导航。相关类型 `Preference`、`PreferenceState` 从同一公开入口导出。管理请求、加载、空态、版本冲突反馈、刷新、编辑和删除确认集中在能力内部。透明入口继续保持原位置、点击区域，以及默认、悬停和聚焦时不可见的既有约定，参见 [ADR 0002](../adr/0002-cross-session-traveler-preferences.md)。

实现按需展开：[生命周期与管理](../../travel_app/preferences/service.py)、[提取与背景](../../travel_app/preferences/extraction.py)、[变更规则](../../travel_app/preferences/rules.py)。持久化值与事务见[本机记录契约](local-records.md)。验证使用[公开接口与条件写入](../../tests/test_preference_contracts.py)、[闲置及停止](../../tests/test_preference_activity.py)、[管理保护](../../tests/test_preference_management.py)、[有限重试与故障](../../tests/test_preference_retry.py)、[真实进程恢复](../../tests/test_preference_recovery.py)及[浏览器管理](../../frontend/tests/ui/preferences-management.spec.ts)。
