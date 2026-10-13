# Web 对话轮次运行职责集中验收（#69）

2026-10-13 在 `codex/backend-organization` 上，以 #68 完成提交 `e8172c2` 为基准。对应 [任务 #69](https://github.com/lll1024/Potato/issues/69)、[父规格 #66](https://github.com/lll1024/Potato/issues/66) 和 [ADR 0006](../adr/0006-backend-package-and-execution-responsibilities.md)。

## 实现与职责

[TurnRuntime](../../travel_app/turn_runtime.py) 集中持有活动轮次身份、执行任务、接受锁与状态、停止信号与原因、存储故障和未保存事实，以及变化通知。`submit` 先核对提交身份，再检查接受状态与全局名额，可靠保存输入后启动执行；`stop` 只作用于指定的活动轮次；`snapshot` 提供统一运行状态。执行、后台偏好处理、路由存储异常和退出流程均通过同一职责协作。

`_run_turn` 在助手调用与保存尝试真正收束后释放名额；在途模型或地图调用没有因为停止、页面断开或存储故障而提前取消。`storage_failed` 停止接受输入和后续执行，保留最后可靠保存的事实及独立的瞬时故障提示，不伪造持久化终态。`shutdown` 停止接受，沿用现有停止原因优先级，取消偏好后台任务并等待活动轮次收束，再由应用释放外部资源和本机存储。

Web 保留资源装配、请求校验、错误响应和 SSE 编码／游标续接；不再持有或修改独立的运行状态。SQLite schema、事务及偏好闲置、版本、来源和恢复规则保持。应用资源、目录、时间及等待注入保持，`Runtime` 仍可从应用构造入口导入。未修改助手循环、提示词、预算、模型参数、超时、重试次数、前端或持久化数据格式。调用关系见[职责地图](../backend-map.md)。

## 本次验证

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 修改前提交、停止、存储、续接、真实进程、偏好、重试及轨迹基线 | 78 项通过；补充 Web 与偏好基线 41 项通过，共 119 项 | [主要基线](turn-runtime/baseline.log.gz)、[补充基线](turn-runtime/preferences-baseline.log.gz) |
| 修改前全部生产后端类型检查 | 0 错误、0 警告 | [基线类型](turn-runtime/baseline-types.log) |
| 初次抽取后的公开入口复测 | 28 项通过 | [复测](turn-runtime/first-fixed.log.gz) |
| 修改后相同范围回归 | 119 项通过，128.521 秒；包含真实 SQLite 故障、真实进程中断与恢复、响应丢失后身份查询及多订阅续接 | [定向回归](turn-runtime/targeted.log.gz) |
| 完整后端回归 | 296 项通过，237.941 秒 | [完整回归](turn-runtime/backend-full.log.gz) |
| 全部生产后端类型检查 | 根入口及 travel_app 包 0 错误、0 警告 | [最终类型](turn-runtime/types-final.log) |
| 前端既有测试及生产构建 | 13 项通过；TypeScript/Vite 构建通过 | [前端测试](turn-runtime/frontend-tests.log)、[构建](turn-runtime/frontend-build.log) |
| 请求内容与上下文事件前后对照 | 两侧各 20 项测试通过；19 个 HTTP 场景、106 次请求、499 条事件归一化后完全一致 | [对照结果](turn-runtime/equivalence.json)、[修改前](turn-runtime/before.json.gz)、[修改后](turn-runtime/after.json.gz) |
| 正式页面的故障、轨迹、新轮次重试、刷新与偏好后台恢复 | 3 项通过，包括桌面／移动恢复与偏好有限重试 | [浏览器恢复](turn-runtime/browser-recovery.log) |
| 上下文采用、保存、停止与中断恢复补充场景 | 桌面／移动 2 项通过；保留全部原断言与预算 | [补充浏览器](turn-runtime/browser-context.log) |

Standards 与 Spec 两个独立审查轴均为 0 项；报告见[双轴审查](turn-runtime/review.md)。

抽取脚本曾误改三个关键字参数，导致语法错误；已修正。保留[首次类型检查](turn-runtime/types-first.log)和[首次测试失败](turn-runtime/first-tests.log.gz)，不将其报告为通过；修正后的[抽取类型检查](turn-runtime/types-extracted.log)与上述复测通过。此次为职责重构，沿用父规格已确认的最高公开 seam 和现有测试，没有为新类增加结构或内部调用次数断言。

## 对照与适用范围

[对照脚本](turn-runtime/equivalence.py)复用 #68 的记录方式，以固定时钟分别加载 `e8172c2` 的 Web 实现和当前实现，其余生产依赖保持一致。记录完整模型请求内容与上下文／请求开始事件；归一化随机请求及原文身份、开始时间、耗时、持久载荷身份，以及一个资料正文去重场景的动态检索时间。旅行日期、历史资料日期、当前旅行要求、摘要、工具结果、提示词与事件顺序继续参与比较。脚本不把 CLI 场景算入 HTTP 对照数量，CLI 由完整回归的真实进程测试覆盖。

补充浏览器场景复用[隔离脚本](backend-package/browser_context_checks.py)，仅缩短重复背景文本，保持生产预算和全部原断言。原上下文夹具第一轮超预算的既有失败已在 [#67](backend-package.md) 和 [#68](context-maintenance.md) 核实；本次未修改或重跑原夹具，不以补充通过宣称原夹具问题已解决。

本次没有修改旅行模型行为，且前后输入和事件对照未发现变化，因此未重跑付费模型或实际地图服务。[历史旅行语义证据](context-compaction.md)保留其原有适用范围，不视为本次实连结果。父规格的整体职责验收仍由 #70 处理，本任务完成不代表父规格或 PR 已合并。

## 重现

从仓库根运行：

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_submissions test_stop test_storage test_reconnect test_recovery test_web_entrypoint test_preference_activity test_preference_recovery test_model_retry test_trace test_tool_trace test_web test_preferences test_preference_retry test_preference_changes test_preference_management test_preference_review_fixes -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
npm test --prefix frontend
npm run build --prefix frontend
PYTHONPATH=tests .venv/bin/python docs/acceptance/turn-runtime/equivalence.py /tmp/turn-runtime-before.json --before
PYTHONPATH=tests .venv/bin/python docs/acceptance/turn-runtime/equivalence.py /tmp/turn-runtime-after.json
.venv/bin/python docs/acceptance/backend-package/browser_context_checks.py
npm run test:ui --prefix frontend -- recovery-live.spec.ts preferences-recovery-live.spec.ts
```

对照使用解析后的 JSON 结构相等比较。正式页面浏览器测试此次使用临时 Playwright 配置，直接访问测试启动的正式后端服务，单 worker、Chrome，免去这些场景不使用的 Vite 开发服务器。全部数据和模型／地图替身均在测试的临时环境中。
