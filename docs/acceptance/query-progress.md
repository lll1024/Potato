# #111：当前查询进展的实时展示

2026-11-07，基于 #109 的查询进展子任务，以本机默认环境在独立临时服务验证。此记录只证明当前轮次查询进展与最终回答分别贯通，不代替 #112 的持久回看、#113 的平滑阅读或 #117 的当前实际配置模型联合验收。

## 行为与公开协议

查询阶段复用真实模型 SDK 文本流，不新建模型调用。提示要求模型仅在准备查询时用“进展：”引出一句不超过 120 字的行动说明。显示只接受该显式前缀，去掉标记、保留首行、最多 240 字；无标记长回答不作为过程。没有工具的阶段结束时清空说明，不将“资料齐全”或长清单虚构为查询步骤。真实工具的准备、执行、取得资料、失败及未执行状态分别来自可靠执行事实；取得资料不等于完成信息核实。

会话公开快照和同一 SSE 订阅增加独立 `progress_preview`／`progress_revision`／`progress.preview`，不推进持久轨迹游标。Web 与 iOS SessionAccess 独立读取查询进展和正文，保留尝试、归属、次序与停止冻结规则。成功原子保存后清除本票的当前投影；失败保留内存内容，不伪造进展历史。完整协议见 [规划](../modules/planning.md#查询进展111)、[会话](../modules/sessions.md#当前查询进展111)、[iOS](../modules/ios-sessions.md#当前查询进展111)。

## 本次证据

- [公开 HTTP／SSE 测试](../../tests/test_query_progress.py)：真实规划、上下文及 SQLite；只在外部模型／地图连接设置屏障。4 项通过，覆盖模型完成前两批说明、工具尚未启动、真实执行中／完成／失败、无查询长正文隔离、跨片段标记与凭据脱敏及限长。最终回答、真实工具调用和错误计数按历史读取核对。
- Web 会话浏览器回归 26 项通过。新增用例观察说明增长、真实状态区分、较旧次序无效及本机停止立即冻结；普通保存、重试、断线、草稿和未保存正文既有用例仍通过。
- iOS `AnswerStreamingTests` 12 项通过；新增公开 SessionAccess 用例用分块 HTTP 与真实 GRDB 检查进展和正文共用唯一订阅、错序／错误归属无效、切换会话清除。其余正文恢复、尝试替换、前后台、停止及保存结果回归仍通过。
- iOS `testQueryProgressGrowsBeforeToolReturnAndStaysSeparateFromAnswer` 1 项通过，耗时 24.615 秒。使用独立 HTTPS、真实 SQLite、外部分片屏障和本次 App 构建，在 iPhone 17 Pro／iOS 26.5 模拟器观察查询两批说明、真实工具成功、独立正文及最终保存；[第一批查询说明](query-progress/ios-query-first.png)、[查询进展与正文](query-progress/ios-answer-and-progress.png)。原始结果包 `/tmp/potato111-query-ui.xcresult` 包含五张截图。
- 同一独立 HTTPS 服务的真实 Web 页面另核对两批查询说明先于查询响应完整返回，工具执行一次且回答开始时历史仍 running／answer=null；最后转为保存全文。[可见增长与身份记录](query-progress/web-observations.json)、[桌面查询阶段](query-progress/web-query-desktop.png)、[窄屏查询进展与正文](query-progress/web-answer-mobile.png)。画面核对与输入来自真实会话接口，只有外部模型／查询使用可控响应。
- Web 类型／生产构建、模块依赖检查、公开契约链接检查、补丁格式检查通过；局部 UI 机械检测没有报告。

## 限制

上述界面证据使用可控外部连接，不能据此声称当前真实模型或真机已通过本规格联合验收。真机、TestFlight 与用户日常数据库未运行；本次不读取或修改日常数据库，也不注入假文字到生产模型响应。旧会话不补造进展；查询进展持久化、自动折叠、平滑队列与完整交叉场景由对应后续子任务验收。

## 首批集成回归修复（#109）

在 `cef06c4` 上的首次后端全量为 **390 项、342.401 秒、8 项失败**，保留[首跑原始记录](query-progress/regressions/frontier1-backend.log.gz)。本次没有把首跑改写为通过，也没有更改生产预算、查询提示、固定旧基线 gzip 或通用 `normalize`。后续同步 #112 的可靠进展历史，按已确认公开 HTTP／SSE 边界逐类复验。

- 外部 `ModelService.stream` 原先把 `None` 当作可遍历响应，错误产生 UNKNOWN；现在用空事件流表示，真实空响应分类、有限重试、同输入核对、名额释放和后续提交断言全部保留。[2 项红灯](query-progress/regressions/null-red.log.gz) → [2 项通过，17.305 秒](query-progress/regressions/null-green.log.gz)。
- 故意畸形的最终 `text=123` 原先被夹具提前作为非法文本流事件发出。夹具现在保持中性合法起始事件，最终畸形 Message 原样返回，继续验证已收到响应载荷、usage、`response_processing` 和原始畸形值。[1 项红灯](query-progress/regressions/trace-red.log.gz) → [1 项通过，0.565 秒](query-progress/regressions/trace-green.log.gz)。
- 新固定查询提示占用输入预算，使三个极窄容量场景跨阈值。仅将允许侧中文输入从 4300 改为 4200，并把两个场景的测试窗口从 40000 改为 40400（压力阈值相应为 22720）；完整价格正文、整理前后跨阈值、四次摘要、三个旅行步骤和硬预算拒发断言保留。[3 项红灯](query-progress/regressions/capacity-red.log.gz) → [3 项通过，1.631 秒](query-progress/regressions/capacity-green.log.gz)。
- 最初两项迁移基线因独立工作树没有前端产物而启动失败，构建后旧库续聊原样通过。全行为基线随后暴露明确新增协议的投影缺口；现在精确核对新查询提示恰好一次、查询与作答的每次真实流调用、对应容量检查及 SDK 仅补齐的空停止字段。公开采集与原固定基线的首个完整请求估计独立对照，在七个场景均增加 **258 tokens**，原始／校准计量均逐项断言后才投影。[协议红灯](query-progress/regressions/baseline-red.log.gz) → [基线模块 7 项通过，10.491 秒](query-progress/regressions/baseline-green.log.gz)。
- 合入 #112 后，新增每轮 `query_progress`、可靠工具事件及工具最新状态须先逐身份、行动、状态和先后关系核对，才剥离新增展示投影。固定场景没有显式模型说明，不能补造这类历史。[历史字段红灯](query-progress/regressions/history-red.log.gz) → [基线模块 7 项通过，9.327 秒](query-progress/regressions/history-green.log.gz)。

基线验证保留并扩充拒绝反例：额外流调用、未知查询提示、错误交付方式、查询／作答超容量或缺检查、摘要污染、非空停止元数据，以及错误历史状态、行动、尝试归属和伪造模型进展。全部固定原始协议、真实调用次数、因果顺序和旧库内容继续逐字段比较；不因新增进展而忽略未知字段。

同步 #112 后，最终针对模型重试、轨迹、容量、整理、摘要、固定真实入口／旧库、流式正文、查询进展与停止的九个模块共 **95 项通过，75.040 秒**（[日志](query-progress/regressions/final.log.gz)）。独立工作树的[前端生产构建](query-progress/regressions/build.log.gz)、模块归属、公开文档链接和 `git diff --check` 通过。本轮没有重新运行其余后端全量、Web／iOS 界面或真实模型；完整集成验收由后续任务报告。

## 最终 Web 上下文夹具复验（#109）

2026-11-08，最终常规 Web 回归中，`context-compaction-live.spec.ts` 的 1440／390 两视口均在第一答等待处失败。[独立两视口红灯](query-progress/regressions/context-ui-red.log.gz)复现原症状；[公开 HTTP 单变量观察](query-progress/regressions/context-ui-http.json)显示，原 300 次非需求背景使首轮完整请求估算为 **28131 tokens**，超过 **28000** 硬输入预算，`allowed=false`，真实轮次以 `context_budget` 终止，模型请求为零。生产预算拒发符合契约。

仅将该段“阅读背景资料，不是新要求。”从 300 次调整为 290 次；真实旅行日期、预算 3000 元人民币、指定湖滨酒店和未预订要求原样保留。窗口仍为 **40000**、输入预算仍为 **28000**、压力阈值仍为 **22400**；首轮公开估算为 **27851**，仍处于压力区间且允许发送。没有修改生产提示、预算、固定基线或任何已有断言。

修复后同一真实 HTTP／SQLite／Web 场景的两视口 **2 项通过，49.7 秒**（[绿灯](query-progress/regressions/context-ui-green.log.gz)）：第一答、摘要候选／轮内采用／保存供续聊、原输入逐字保存、恰好一次摘要、停止后不采用迟到摘要，以及进程中断后请求结果未知／恢复上一有效版本的断言全部保留并通过。上下文预算、摘要、整理与公开契约后端回归 **35 项通过，14.184 秒**（[日志](query-progress/regressions/context-ui-backend.log.gz)）；前端类型／生产构建、模块依赖、公开文档链接与补丁格式检查通过。此处使用可控外部模型，不能代替 #117 当前实际配置模型的双端联合验收。
