# #81：公开规划入口与原子轮次保存

2026-10-23，在 `codex/deep-modules` 上继续 #79；本次起点为 `81ed92f3cf2d312c1a622efcab970cf6c80b004a`，远程主分支核对为原固定生产基线 `4e01678fdb036f034d72a858fadb226ea239a0c1`。复用 PR #87，保留用户已有未提交领域文档。

## 已交付与验收对应

| #81 验收范围 | 本次证据 |
| --- | --- |
| 真实公开 Web 接受、查询、回答、保存和页面读取 | 根入口转向公开 sessions；固定行为对照运行生产规划、查询 adapter 与临时 SQLite，Web/HTTP/SSE 行为保持 |
| 结构化规划结果及一致材料 | Web 与 CLI 调用同一 plan；直接返回回答、状态、原因、来源、工具错误数与冻结材料，不从观察终态推导，不由会话双写协议和模型视图 |
| 原子接受与原子完成 | LocalRecords 共同保存提交身份、偏好待处理登记，以及回答、完整协议、上下文晋升、偏好结束资格；故障回滚回归通过 |
| 执行与保存分离、故障中止 | 接受、事实开始、真实返回与最终保存故障均保留原回归；开始未保存不调用外部服务，在途等待真实返回，未保存不伪报为模型失败 |
| 去重、冲突、忙碌及原目标停止 | 提交、停止及记录故障测试通过，实际调用和保存尝试收束后才释放全局名额 |
| 真实 CLI 范围 | 固定真实 CLI 进程两次输入及跨日日期回归通过；进程内延续凭证不新增 Web 历史或偏好后台任务 |
| 一致读取 | SQLite 连接改为记录内部状态；页面不取得连接或开启事务，多步读取内部共享快照 |
| 契约、类型及依赖 | sessions / planning / context 适配 / local_records 公开契约链接真实入口、类型和测试；移除旧 store 和 query_materials 及对应豁免，未新增迁移豁免 |

## 测试与审查

新增公开规划和记录契约先因缺少入口失败，再完成实现。定向验证覆盖存储、提交、停止、CLI、旧数据和旅行日期；42 项集成回归包含原固定 Web/SSE/CLI/恢复/旧库对照，全部通过，未修改固定夹具或归一化规则。

首次完整后端 340 项通过。独立 Standards 审查随后发现损坏上下文恢复可能绕过失败收束；公开规划及真实 HTTP/SQLite 两条复现先失败，再改为带边界的 RecordAbort 并验证停止接受、无后续调用和无虚假完成。最终完整后端 **342 项通过，307.682 秒**，原始日志见 [full-tests.log.gz](public-planning/full-tests.log.gz)，修复前 HTTP 失败记录见 [recovery-before-fix.log.gz](public-planning/recovery-before-fix.log.gz)。

```sh
PYTHONPATH=tests .venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
.venv/bin/python scripts/check_modules.py
npm test --prefix frontend
npm run build --prefix frontend
```

pyright 零错误；前端 13 项通过，TypeScript 和生产构建通过；模块检查通过，旧实现豁免由 121 项减至 117 项，检查器反例随全量通过。公开契约本地链接与 diff 空白检查通过。两个独立代理分别复核规范与规格，最终各 0 项遗留，详见[双轴审查](public-planning/code-review.md)。

## 适用范围

无 schema 迁移，已有 SQLite 数据及模型可见行为由固定基线对照保持。外部响应为可控本机服务，不据此推断真实供应商质量或性能。公开入口已运行真实链路，但内部上下文、资料获取、偏好规则及完整会话查看仍按 #82–#85 迁移；#86 的全仓库封装与独立渐进披露验收尚未交付。当前契约从[迁移状态](../modules/migration.md)进入。
