# 验收索引

先读下表的结论，再按各文档链接追溯原始证据。历史记录只说明其注明的版本、日期、配置与场景；测试通过、接口成功及真实模型语义核对各有适用范围，不能相互替代，也不能视为当前版本重新运行的结果。

| 行为范围 | 结论入口 | 证据与适用范围 |
| --- | --- | --- |
| 连续正文／查询进展与终态一致交接 | [#109／#117联合验收](continuous-final.md)、[60条故事映射](continuous-final-mapping.md) | 024后端408／Web116+2skip／原生87+10；真实query87有限结果与024实际plain、受控paced物理红绿分层；相关UI首10过1红+原样复验，限制不隐藏 |
| 查询进展／正文重试与降级、预算交叉 | [#115 验收](query-retries.md) | 公开HTTP／SSE、真实SQLite、SessionAccess与两端显示过渡；当前模型联合验收另见#117 |
| 查询进展与正文停止收束 | [#116 验收](query-progress-stop.md) | 双客户端、真实 SQLite 保存故障、两类队列冻结与原 Markdown 复制；原生最终证据见正文 |
| 查询进展折叠与持久回看 | [#112 验收](query-progress-history.md) | 可靠事实／回滚与同库重启、SessionAccess、模拟器重开及同轮Web第二客户端；固定兼容投影待关联修复复验 |
| 当前查询进展与独立最终正文 | [#111 验收](query-progress.md) | 公开 HTTP／SSE、真实 SQLite、SessionAccess 同订阅与模拟器／Web 页面；持久回看和平滑及当前实际模型联合验收由后续任务完成 |
| 两端短暂平滑与阅读保位 | [#113 验收](stream-smoothing.md) | 200ms绝对逻辑窗口、小块即时、恢复直达、停止清队列、字素／复制及真实折叠保位；可控界面与原生连续帧分别解释 |
| 安全小片段正文连续显示 | [#110 验收](continuous-body.md) | 普通未闭合阅读标点积压红绿、公开 HTTP／SSE、两端九批中文及当前模型时序；完整查询进展联合验收由 #117 交付 |
| 查询进展与正文的断线／前后台恢复 | [#114 恢复验收](progress-recovery.md) | 双通道次序、即时状态、真实子进程、单订阅与两端恢复页面；当前模型另见后续联合验收 |
| 当前真实模型与 Web／iOS 最终联合验收 | [#104 联合验收](streaming-final.md)、[58 条故事映射](streaming-final-mapping.md) | DeepSeek 当前配置实连、同轮保存前多批正文、恢复／重试／停止／保存失败交叉与兼容；模拟器与真机范围分别记录 |
| Web／iOS 流式 Markdown 与阅读跟随 | [#103 验收](stream-reading.md) | 半成型代码／表格／链接、长中文、同轮增长、上翻保位、回到最新及当前 Markdown 全文复制 |
| Web／iOS 停止、未保存正文与未完成结果 | [#102 验收](answer-outcomes.md) | 公开 HTTP／SSE、SessionAccess、停止完成竞争、SQLite 故障、截断、63／64 预算与两端复制界面 |
| Web／iOS 流式失败重试与完整回答降级 | [#101 验收](answer-retries.md) | 公开 HTTP／SSE、SDK 截断流、原有错误分类、63／64 预算、SessionAccess 与模拟器真实 HTTPS 故障界面 |
| Web／iOS 当前正文恢复与失效隔离 | [#100 验收](answer-recovery.md) | 双订阅、快照交接、旧状态／新正文交错、真实进程重启、公开 SessionAccess 及模拟器前后台；生产模型联合验收另见 #104 |
| iOS 最终回答实时阅读 | [#99 验收](ios-answer-streaming.md) | 共享前台订阅、分块 HTTP、真实 HTTPS／SQLite、前后台恢复与模拟器界面增长；生产模型实连另见后续任务 |
| Web 最终回答真实流式展示 | [#98 验收](web-answer-streaming.md) | 公开 HTTP／SSE、真实 SQLite、分块屏障、阶段隔离、63／64 预算及跨块脱敏；iOS 与生产模型联合验收另见后续任务 |
| iOS 模拟器本地免配置调试 | [本地调试验收](ios-local-debug.md) | 模拟器 Debug 默认回环 HTTP、失败重试、真实后端读取／新建／续聊；真机及 Release 保留 HTTPS，验证结果与适用范围见正文 |
| 原生 iPhone 本机集成与交付准备 | [#95 验收](ios-delivery.md) | 50 条故事映射、本机回归与无签名归档；TestFlight、iOS 17 实跑、私网和蜂窝真机未完成，任务保持打开 |
| 原生 iPhone 历史分页与中文回答阅读 | [#94 验收](ios-reading.md) | 游标、历史缺口、迟到事实、真实 HTTPS 完整读取及表格、链接和复制交互 |
| 原生 iPhone 跨设备停止与前后台恢复 | [#93 验收](ios-lifecycle.md) | 原目标隔离、停止收束屏障、可控调度竞争、真实 HTTPS 双客户端及模拟器交互 |
| 原生 iPhone 未确认提交恢复与同身份重试 | [#92 验收](ios-recovery.md) | 重启与响应丢失、404 在途竞争、真实后端一次执行、归属隔离与版本匹配清理 |
| 原生 iPhone 持久草稿与编辑竞争 | [#91 验收](ios-drafts.md) | 按服务、数据集和会话保存、离线与重启、版本匹配清理及页面切换；未确认提交核对由 #92 完成 |
| 原生 iPhone 新建、续聊与回答 | [#90 验收](ios-submission.md) | 发送前原子保存、接受与未知、前台轮询、真实 HTTPS 历史与共同偏好；恢复与停止另行验收 |
| 原生 iPhone 配置与只读会话 | [#89 验收](ios-readonly.md) | Swift 6 依赖组合、模拟器、真实后端 HTTP、完整对话及私网步骤；真机与 TestFlight 独立记录 |
| 全仓库六项能力与渐进披露 | [#86 最终验收](deep-modules-final.md) | 当前全量验证、基线与旧数据、独立阅读/调用/修改、93 项规格映射及限制 |
| 助手会话查看、提交与事件续接 | [#85 验收](session-interactions.md) | 公开会话操作、共同分页/失效、异步竞争修复及桌面/移动验证 |
| 资料获取、混合批次与共享预算 | [#83 验收](retrieval-planning.md) | 公开外部查询段、供应商隔离、历史屏障、128/8/64 总账、真实保存边界及固定 Web/CLI/旧库对照 |
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
