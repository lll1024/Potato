# 验收索引

先读下表的结论，再按各文档链接追溯原始证据。历史记录只说明其注明的版本、日期、配置与场景；测试通过、接口成功及真实模型语义核对各有适用范围，不能相互替代，也不能视为当前版本重新运行的结果。

| 行为范围 | 结论入口 | 证据与适用范围 |
| --- | --- | --- |
| 助手会话查看、提交与事件续接 | [#85 验收](session-interactions.md) | 公开会话操作、共同分页/失效、异步竞争修复及桌面/移动验证 |
| 完整模型上下文、CLI 延续与恢复 | [#82 验收](model-context.md) | 统一协议与模型视图、公开容量/回读/恢复、冻结保存材料及固定行为回归 |
| 公开规划入口与原子轮次保存 | [#81 验收](public-planning.md) | 真实 Web/CLI、结构化结果、原子保存及一致读取；342 项全量、故障先复现后修复与双轴复核 |
| 六模块迁移前行为与结构基线 | [#80 基线](deep-modules-baseline.md) | 固定生产提交、HTTP/SSE/CLI/进程记录、合成旧数据库及公开入口反例；完整六模块实施留给 #81–#86 |
| 集中查询与预算/并发最终联合验收 | [最终联合验收](query-batching.md) | #76 的真实模型依赖、重复旅行任务对照、资料质量、真实 Exa 承载及本次完整回归；机制与整轮收益分开结论 |
| 共享调用预算与最终回答收束 | [共享调用预算](call-budget.md) | #74 的 128 次工具、64 次实际模型请求、预留收尾、停止与保存边界；网页并发及真实性能由 #75/#76 另行验收 |
| 默认资料获取与按需信息核实 | [资料获取验收](information-retrieval.md) | [前后对照及失败修正](information-retrieval/)；ADR 0007 的 CLI/Web 行为、已有入口保留与后端包迁移后的合并复验 |
| 后端重组最终整体验收 | [整体回归与职责集中](backend-organization.md) | [当前运行证据](backend-organization/)；#70 覆盖父规格 #66 的范围映射、直接主分支对照、旧数据续聊、最终审查与独立基线限制 |
| 本次后端包迁移、启动兼容与代码导航 | [后端包迁移](backend-package.md) | [本次运行证据](backend-package/)；迁移前后回归、资源与旧数据核对，覆盖 #67 |
| 上下文维护职责集中 | [上下文维护](context-maintenance.md) | [本次运行证据](context-maintenance/)；#68 的前后回归、模型输入与事件对照、完整回归与审查 |
| Web 对话轮次运行职责集中 | [运行职责](turn-runtime.md) | [本次运行证据](turn-runtime/)；#69 的提交、停止、存储、续接、偏好协作和进程恢复回归 |
| 上下文压缩、超限恢复、原文回读及旅行语义 | [上下文压缩](context-compaction.md) | [原始证据与失败记录](context-compaction/)；#56/#64 的机制、真实模型对照、用量、耗时与浏览器场景 |
| 普通模型请求重试与停止 | [模型重试](model-retries.md) | 文内自动与实际服务范围；可控故障验证不代表真实服务故障均发生过 |
| 本机页面、会话、提交、历史与恢复 | [页面首版](local-web.md) | 文内 23 项及真实服务限制；[当时双轴审查](code-review.md)，后续页面变化另见重设计 |
| 前端布局与交互 | [前端重设计](frontend-redesign.md) | 文内浏览器、截图与测试依据；范围为当时视觉和交互版本 |
| 交通路线的页面表达 | [交通路线表达](traffic-route-presentation.md) | 文内交通方式和路线显示范围，不代替交通信息实时核实 |
| 跨会话旅行者偏好 | [偏好验收](traveler-preferences.md) | 文内真实语义、公开接口及截图链接；[语义证据](traveler-preferences-semantic.json)、[失败记录](traveler-preferences-semantic-failures.json)、[审查](preferences-code-review.md) |
| 偏好闲置处理、失败与重启 | [偏好恢复](preferences-recovery.md) | 文内有限重试与恢复观察；[桌面](preferences-failure-desktop.png)、[移动](preferences-failure-mobile.png) 截图 |
| 故障、停止与保存事实 | [异常体验](exception-experience.md) | 文内自动、浏览器与真实服务依据；保留未知结果及诊断限制 |
| Exa 免费资料搜索与正文 | [Exa 接入](exa-sources.md) | [实连与验证目录](exa-sources/)；成功、修前语义失败与免费服务限制分开记录 |
| 国内旅行日期与官方资料 | [规格 #28](spec-28.md) | [原始证据](spec-28/)；包含当时 Tavily 失败、官方正文回填和日期修复，当前资料接入见 Exa |
| 旅行行程事实与回答差异 | [事实核对](itinerary-fact-check.md) | 文内逐项核对与限制；模型完成回答不能等同于信息已核实 |

[后端职责地图](../backend-map.md)关联代码与测试入口；[历史档案说明](../../.scratch/README.md)解释旧任务状态。当前任务与实施规格以 [GitHub Issues](https://github.com/lll1024/Potato/issues) 为准。
