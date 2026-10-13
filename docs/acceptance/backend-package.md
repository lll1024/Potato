# 后端包迁移验收（#67）

2026-10-13 以主分支 `4fff3d2` 为基线实施 [Issue #67](https://github.com/lll1024/Potato/issues/67)，属于父规格 [#66](https://github.com/lll1024/Potato/issues/66) 的目录与导航阶段。16 个后端实现模块迁入单个浅层 `travel_app` 包；根目录只保留 `agent.py`、`web.py` 两个薄启动入口。

## 改动与行为范围

CLI、地图连接检查、Web 参数与退出契约保持。全部仓库生产代码、测试、真实子进程、浏览器辅助服务和语义脚本统一导入新包；依赖替换指向实际包模块，跨测试文件辅助导入保持。无旧顶层导入兼容层或模块别名，前端与测试目录未重排。

包内 `.env` 与默认 `frontend/dist` 按项目根定位，保留配置覆盖规则；默认与显式相对数据目录按启动工作目录解析，静态目录注入有效。未改变 SQLite schema、上下文状态格式、提示词、预算或重试规则。

[职责地图](../backend-map.md)、[验收索引](README.md)和[历史档案说明](../../.scratch/README.md)提供代码、行为结论、证据与当前任务入口。历史材料、原始证据及品牌资料原路径与内容保留；已确认的 [ADR 0006](../adr/0006-backend-package-and-execution-responsibilities.md) 纳入分支。

## 分阶段验证

| 阶段 | 实际结果 | 原始证据 |
| --- | --- | --- |
| 迁移前公开入口与相关行为基线 | CLI 3 项、正式 Web 入口 1 项、停止／存储／偏好／上下文恢复／提交等 55 项通过 | [CLI](backend-package/baseline-cli.log)、[Web](backend-package/baseline-web-entrypoint.log)、[相关行为](backend-package/baseline-behavior.log.gz) |
| 迁移前全部生产模块类型检查 | 0 错误、0 警告 | [基线类型检查](backend-package/baseline-types.log) |
| 先建立新包，暂留旧模块 | 新包类型检查 0 错误、0 警告 | [建立新包](backend-package/package-created-types.log) |
| 再迁移调用方与启动入口，暂留旧内部模块 | 相同公开入口与相关行为 59 项通过；类型检查 0 错误 | [调用方回归](backend-package/callers-migrated.log.gz)、[类型检查](backend-package/callers-migrated-types.log) |
| 移除旧内部模块后完整回归 | 后端 296 项全部通过，242.795 秒 | [最终后端](backend-package/backend-final.log.gz) |
| 最终全部生产模块类型检查 | 根入口与整个 travel_app 包 0 错误、0 警告 | [最终类型检查](backend-package/types-final.log) |
| 前端既有测试与生产构建 | 13 项测试通过；TypeScript／Vite 构建通过 | [测试](backend-package/frontend-tests.log)、[构建](backend-package/frontend-build.log) |
| 精准迁移核对 | 16 个生产模块内容仅包含预期导入前缀和项目根资源定位变化 | [机械核对](backend-package/mechanical-check.json) |
| 资源与启动契约 | 4 组核对通过，未发起外部服务请求 | [结果](backend-package/resource-checks.json)、[可复现脚本](backend-package/resource_checks.py) |
| 迁移前已有数据 | 原版公开入口生成已完成会话、有效摘要和后台偏好；新版原样读取会话与偏好，续聊实际恢复摘要并注入旧偏好 | [迁移前](backend-package/existing-data-before.log)、[迁移后](backend-package/existing-data-after.log)、[脚本](backend-package/existing_data_checks.py) |

数据兼容验证使用合成数据与可控外部模型，通过原版写入、新版读取同一个 SQLite 目录验证；未读取或导出个人本机资料。该结果不等同于逐个检查所有私人历史数据库。

## 浏览器范围与既有失败

迁移后的真实服务恢复（桌面／移动）和偏好恢复共 3 项通过。上下文场景原版桌面／移动夹具均在第一轮超过安全输入预算，未到达预期回答；在 `4fff3d2` 隔离快照上同样复现。这是现有夹具的基线失败，不报告为通过，也未作为本任务顺手修改原测试或生产预算。

首次浏览器调用还因沙箱限制导致 Chrome 启动终止，后续在允许运行 Chrome 的环境复验。原记录分别为[沙箱启动失败](backend-package/browser.log)、[迁移后原场景](backend-package/browser-final.log)和[主分支同场景](backend-package/browser-baseline-context.log)。

隔离副本只将重复背景文本的次数 `310/350/550` 改为 `260/280/450`，保留 40,000 窗口、全部原行为断言、完整原文检查和桌面／移动场景；桌面／移动 2 项通过（37.3 秒），结果见[补充上下文浏览器记录](backend-package/browser-context-adjusted.log)。此补充结果须与原夹具失败一起理解。

本次是机械迁移的机制与公开入口验证，未重跑付费模型或真实地图查询。真实旅行语义、服务来源与费用结论沿用[上下文压缩](context-compaction.md)和 [Exa 接入](exa-sources.md)的历史适用范围，不能表述为本次实连结果；生产模块精准核对确认提示词和模型可见规则未改变。

## 重现

从项目根运行：

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_cli test_web_entrypoint test_web test_recovery test_stop test_storage test_preferences test_context_recovery test_submissions -v
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
npm test --prefix frontend
npm run build --prefix frontend
npm run test:ui --prefix frontend -- context-compaction-live.spec.ts recovery-live.spec.ts preferences-recovery-live.spec.ts
.venv/bin/python docs/acceptance/backend-package/resource_checks.py
.venv/bin/python docs/acceptance/backend-package/browser_context_checks.py
```

旧数据重现使用全新的临时目录：在原版工作目录执行 `existing_data_checks.py seed /tmp/新的验证目录`，在新版工作目录执行同一脚本 `verify /tmp/新的验证目录`。脚本借用既有公开接口测试替身，不引入新的内部测试 seam。

导航相对链接全部有效，见[导航检查](backend-package/navigation.json)。[Standards／Spec 独立双轴审查](backend-package/review.md)均为 0 个发现；完整检查摘要见[验证清单](backend-package/validation.json)。上下文维护和 Web 运行状态的职责集中仍由父规格后续任务实施，本阶段不宣称完成。
