# 仓库总览：六项能力与公开协作

Potato 通过 Web 和 CLI 帮助旅行者规划旅行。根 [web.py](../../web.py) 与 [agent.py](../../agent.py) 保留启动命令，后端在 `travel_app/` 下按完整能力组织；前端会话和偏好使用同名目录。实施规格以 [#79](https://github.com/lll1024/Potato/issues/79) 为准，当前验证范围见[最终集成验收](../acceptance/deep-modules-final.md)。

## 阅读与使用

先读本总览，再读任务涉及的公开契约和入口。只调用现有能力时，契约包含所需顺序和不变量；要修改内部规则时，沿契约的内部定位展开对应实现及测试。跨能力修改先读双方契约，再扩大范围；展开实现无需另行批准。类型以契约链接的代码为准。术语见 [GLOSSARY](../../GLOSSARY.md)，架构理由见 [ADR 0011](../adr/0011-deep-modules-and-progressive-disclosure.md)。

| 能力 | 状态及责任 | 后端公开入口 | 契约 |
| --- | --- | --- | --- |
| 助手会话 | 提交身份、全局单轮名额、停止目标、对外状态、历史/轨迹/搜索/通知；前端提交不确定性、草稿、分页与失效 | [sessions](../../travel_app/sessions/__init__.py) | [会话](sessions.md) |
| 旅行规划 | 当前旅行要求、旅行日期、规划规则、模型步骤、混合批次、整轮预算及结构化结果 | [planning](../../travel_app/planning/__init__.py) | [规划](planning.md) |
| 资料获取 | 能力发现、地图与网页连接、供应商并发/间隔/暂停、外部查询分段及单次结果 | [retrieval](../../travel_app/retrieval/__init__.py) | [资料](retrieval.md) |
| 模型上下文 | 完整协议与模型视图、容量/摘要/历史回读/恢复、保存材料与不透明延续凭证 | [context](../../travel_app/context/__init__.py) | [上下文](context.md) |
| 旅行者偏好 | 管理界面、规划背景、稳定偏好提取、资格/版本/人工保护和后台生命周期 | [preferences](../../travel_app/preferences/__init__.py) | [偏好](preferences.md) |
| 本机记录 | SQLite 格式、原子接受/完成、执行事实、一致快照/游标、条件持久化及重启恢复 | [local_records](../../travel_app/local_records/__init__.py) | [记录](local-records.md) |

只有会话与偏好具有现有前端功能；其公开入口分别是 [sessions/index.ts](../../frontend/src/sessions/index.ts) 与 [preferences/index.ts](../../frontend/src/preferences/index.ts)。其余能力没有空前端 facade。外层 [main.tsx](../../frontend/src/main.tsx) 只装配两者及导航。

## 依赖与主流程

允许依赖：会话 → 规划、偏好、记录；规划 → 资料、上下文；上下文 → 记录；偏好 → 记录；资料和记录不依赖其他业务能力。回调由装配注入，不形成反向实现依赖。[architecture.json](../../architecture.json) 是可检查的归属与依赖清单；共享模型事实采集、错误分类、额度常量、工具名称属于无领域运行状态的支撑。

Web lifespan 锁定数据目录 → 打开记录并 `recover()` → 配置模型和查询连接 → `TurnRuntime.start()` 启动会话及偏好后台。接受输入时，提交身份、输入及偏好待处理资格共同保存，再启动规划。会话传入 `planning_seed`、偏好背景、事实记录回调及停止信号；规划内部推进上下文、模型与查询。每个真实外部调用先可靠记录开始，再进入服务，返回后保存真实事实。

规划按提出顺序为整批工具预留额度，将连续外部查询段交给资料获取；遇到历史回读，先等待前段真实返回，再由上下文按完整请求余量回读。收齐完整结果后才推进模型。完整协议和模型视图只由上下文共同维护；普通调用方不双写。整轮 128 次工具、8 次回读、64 次实际模型请求及最后一次无工具收尾预留保持；重试、摘要和恢复共用额度。

`plan` 返回 `PlanningResult`（回答、状态、原因、来源、错误统计、冻结材料及延续凭证）。会话按其结构化字段一次 `finish` 保存回答、协议、已采用的有效上下文及偏好结束资格；成功返回后才是保存完成。观察事件用于执行轨迹，不能替代结果或事务成功。

CLI 复用 `plan`，将不透明 `continuation` 原样传回；它只保留进程内状态，不创建 Web 持久会话或偏好后台。退出连接时先等待执行收束。

## 停止、失败与重启

停止按原 `turn_id` 传递，停止中仍占用全局名额；已进入服务的调用真实返回且保存尝试收束后才释放。断开页面或订阅不取消规划。服务退出先拒绝输入，停止偏好与当前规划并等待，再关闭外部资源和记录、释放目录锁。

单次供应商失败成为查询结果，严重故障只暂停对应供应商；模型失败、预算耗尽和停止在结构化结果中分别表达。事实保存失败用 `RecordAbort` 中止，禁止后续启动，等待在途返回；会话展示 `storage_error` / `unsaved_fact` 并停止接受输入，不把未保存回答写成已完成历史。

重启只恢复已提交事实、上一有效上下文及已确定旅行日期；不重放模型、查询或普通重试计时。偏好按自身契约恢复处理中输入、有限重试与人工保护。通知持久游标与瞬时状态分开，目录身份变化要求重新读取快照。

## 定位与验证

| 问题 | 先读契约与公开操作 | 修改时展开 |
| --- | --- | --- |
| 上下文超限、摘要和历史回读 | [上下文](context.md)：`ModelContext.prepare_request/prepare_recovery/read_history`；调用方仍用 `plan` | 契约中的容量、摘要和回读定位 |
| 查询调度、供应商故障、混合批次 | [资料](retrieval.md)：`Retrieval.execute`；[规划](planning.md)：`plan` 的预算/批次 | 资料供应商调度或规划批次实现 |
| 偏好人工保护、迟到提取 | [偏好](preferences.md)：`Preferences.edit/delete/start/shutdown` | 资格、变更规则及条件写入 |
| 记录故障、半成功或恢复 | [记录](local-records.md)：`accept/observe_*/finish/recover`；[会话](sessions.md)：故障与停止契约 | 记录事务或会话故障收束 |

验证入口和测试支撑归属见[结构规则](migration.md)，按行为展开的实现地图见[后端职责地图](../backend-map.md)。[验收索引](../acceptance/README.md)中的旧阶段报告及 [.scratch](../../.scratch/README.md) 是历史证据；它们注明的旧内部路径和阶段状态不作为当前公开契约。
