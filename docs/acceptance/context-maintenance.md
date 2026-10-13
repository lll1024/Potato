# 上下文维护职责集中验收（#68）

2026-10-13 在 `codex/backend-organization` 上，以 #67 完成提交 `7563199` 为本任务基准。对应 [任务 #68](https://github.com/lll1024/Potato/issues/68)、[父规格 #66](https://github.com/lll1024/Potato/issues/66) 和 [ADR 0006](../adr/0006-backend-package-and-execution-responsibilities.md)。

## 实现与职责

`agent_loop` 的压力与服务明确超限分支调用同一个 `ContextMaintenance`。该职责持有本轮视图、计量器、最新计量、摘要额度和终止摘要失败状态；`prepare_request` 准备普通请求并返回预算阻止原因，`prepare_recovery` 准备严格缩小且预算允许的恢复输入。确定性整理、摘要切分、整轮四次额度、最终预算、用量校准及回读页面预算检查集中于这一模块。

助手循环继续负责旅行步骤、工具执行、普通网络重试以及原步骤的一次超限恢复控制；摘要有限尝试和候选复核继续由现有摘要流程执行。完整历史没有缩短，原视图仍随可靠保存生效。未调整提示词、模型参数、摘要结构、预算、超时、HTTP/SSE 或 CLI 输出。调用关系见[职责地图](../backend-map.md)。

## 本次验证

| 范围 | 实际结果 | 证据 |
| --- | --- | --- |
| 修改前上下文、摘要、恢复、重试与日期基线 | 79 项通过 | [基线](context-maintenance/baseline.log.gz) |
| 修改前生产模块类型检查 | 0 错误、0 警告 | [基线类型](context-maintenance/baseline-types.log) |
| 修改后超限恢复单文件 | 9 项通过；包括不重复地图查询、普通重试独立、共享四次摘要额度、停止与认证失败 | [超限恢复](context-maintenance/recovery.log.gz) |
| 修改后上下文、摘要、回读、日期、停止与保存回归 | 97 项通过 | [定向回归](context-maintenance/targeted.log.gz) |
| 完整后端回归 | 296 项通过，246.441 秒 | [完整回归](context-maintenance/backend-full.log.gz) |
| 全部生产后端类型检查 | 根入口与 travel_app 包 0 错误、0 警告 | [最终类型](context-maintenance/types-final.log) |
| 前端既有测试与生产构建 | 13 项通过；TypeScript/Vite 构建通过 | [前端测试](context-maintenance/frontend-tests.log)、[构建](context-maintenance/frontend-build.log) |
| 模型输入及上下文事件前后对照 | 20 项现有测试通过，其中 19 个 HTTP 场景捕获 106 次模型请求和 499 条上下文／请求开始事件；归一化后完全一致 | [对照结果](context-maintenance/equivalence.json)、[基准记录](context-maintenance/before.json.gz)、[修改后记录](context-maintenance/after.json.gz) |
| 正式服务浏览器恢复 | 桌面／移动 2 项通过 | [浏览器恢复](context-maintenance/browser-recovery.log) |
| 上下文浏览器隔离补充场景 | 桌面／移动 2 项通过，采用、保存、停止和中断后恢复均保持原断言 | [补充场景](context-maintenance/browser-adjusted.log) |

初次抽取的类型检查有 3 个 Optional 类型错误，均已修正；[初次记录](context-maintenance/types-first.log)保留，不能视为最终通过结果。此次为职责重构，沿用父规格已确认的公开 seam 与现有测试，没有为新类追加内部结构断言。

## 前后对照与语义范围

[对照脚本](context-maintenance/equivalence.py)复用现有公开 HTTP/SSE 测试，用固定时钟运行两次；基准运行仅加载 `7563199` 的助手循环，其余被调用模块在此次任务中未变。记录完整请求输入与 `context.*` 事件，比较事件顺序和字段。仅省略请求开始时间、耗时、随机请求身份和载荷身份；历史回读内的随机身份保留相互引用关系，以稳定标识归一化。一个资料正文去重场景的本次 `retrieved_at` 按生成时间归一化；历史资料日期、旅行日期、旅行条件、提示词、摘要、工具结果、计量和事件顺序均参与比较。CLI 在既有公开进程测试与完整回归中验证，该脚本不把 CLI 输出计作 HTTP 对照证据。

没有发现模型可见内容或事件顺序的行为变化，也未发现需要追加真实模型核实的旅行语义不确定性，因此本次未重跑付费模型或实际地图服务。[既有真实旅行语义证据](context-compaction.md)保留其历史适用范围，不宣称为本次实连验证。

## 浏览器既有失败与限制

首次浏览器调用因沙箱限制在 Chrome 启动时退出，见[启动失败](context-maintenance/browser.log)。允许运行后，原上下文夹具的桌面／移动场景仍在第一轮超过安全预算，见[原场景失败](context-maintenance/browser-final.log)；该现象与 [#67 已核实的基线失败](backend-package.md)一致，不报告为通过。

复用 #67 的[隔离脚本](backend-package/browser_context_checks.py)，只把重复背景次数 `310/350/550` 调为 `260/280/450`，保留 40,000 窗口和全部原断言，桌面／移动均通过。未修改原夹具或生产预算；补充通过须与原夹具失败一起理解。

## 重现与审查

从仓库根运行：

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_context_limit_recovery -v
PYTHONPATH=tests .venv/bin/python -m unittest test_context_budget test_context_summary test_context_incremental test_context_recovery test_context_organization test_model_retry test_travel_date_continuation test_travel_date_corrections test_session_history test_stop test_storage -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
npm test --prefix frontend
npm run build --prefix frontend
PYTHONPATH=tests .venv/bin/python docs/acceptance/context-maintenance/equivalence.py /tmp/context-before.json --before
PYTHONPATH=tests .venv/bin/python docs/acceptance/context-maintenance/equivalence.py /tmp/context-after.json
.venv/bin/python docs/acceptance/backend-package/browser_context_checks.py
npm run test:ui --prefix frontend -- recovery-live.spec.ts
```

对照 JSON 使用解析后的结构相等比较。Standards 与 Spec 分别独立审查，结论见[本次审查](context-maintenance/review.md)。#69 的 Web 对话轮次运行状态集中不属于本次交付。
