# #82：完整模型上下文、CLI 延续与恢复

2026-10-23，在 `codex/deep-modules` 上继续 #79，起点为 `b0e506f6c6602f282b7ab0111c4d1ea5e6773b53`；已重新获取远程，`origin/main` 仍为固定基线 `4e01678fdb036f034d72a858fadb226ea239a0c1`，功能分支远程与起点一致。复用 PR #87，用户原有未提交领域文档不纳入本次改动。

## 交付与验收对应

| #82 验收范围 | 实现及证据 |
| --- | --- |
| 普通及长助手会话统一入口 | `plan` 仅持有公开 `ModelContext`，完整协议与模型视图的追加、替换、冻结均在上下文内部；Web 和 CLI 不持有这两份状态 |
| 真实 CLI 续聊与日期 | CLI 只回传 `Continuation`；凭证冻结协议、视图和日期，不从事件提取日期，不引入持久会话或后台偏好 |
| 日期和当前旅行要求 | 日期仍由旅行规划解释，上下文消费确定状态；跨日、跨年、显式修改、摘要与重启回归保持 |
| 容量、摘要和超限恢复 | 公开准备操作封装完整请求计量、整理、摘要采用、水位和映射；同一步骤最多一次严格缩小后的恢复，摘要消费规划注入的实际额度 |
| 原文回读与混合批次 | 回读由上下文索引当前会话原文及已收齐的前段工具结果；余量检查包含当前回读及后段结果占位；分页、隔离和局部上限保持 |
| 共同保存及恢复 | 上下文产出冻结 `TurnMaterial`，`LocalRecords.finish` 原子保存；候选、轮内采用和保存生效分开，停止及未保存版本不在重启后晋升 |
| 事实与页面状态 | 原整理、摘要、采用、保存、中断和超限事件保持；前端呈现回归检查这些真实状态，不增加新 UI |
| 旧实现与依赖 | 四个旧生产上下文文件迁入能力内部并移除精确豁免；共享模型错误识别作为无业务状态的公开支撑登记；启动装配经规划检查容量 |

公开契约见[模型上下文](../modules/context.md)，入口与类型见 [travel_app.context](../../travel_app/context/__init__.py)，新增验证见[公开接口测试](../../tests/test_context_contracts.py)。`view.py` 与 `history.py` 原算法保持，`summary.py` 仅调整同能力与共享支撑导入；统一状态协调在 `runtime.py`。

## 验证与修复

先固定迁移前定向回归：预算、超限及回读 30 项通过，日志见 [targeted-before.log.gz](model-context/targeted-before.log.gz)。新增统一追加测试先因缺少 `ModelContext` 公开入口失败；错配结果测试先因未拒绝错误身份失败，补充校验后通过。

迁移后定向回归 59 项通过，覆盖容量、超限恢复、历史回读、摘要、进程恢复与日期续聊，日志见 [targeted-after.log.gz](model-context/targeted-after.log.gz)。新增公开接口验证冻结协议、身份配对、请求副本隔离、日期延续、前段回读、严格缩小及一次恢复，以及损坏状态中止。

Standards 审查发现损坏协议 `{}` 在恢复后追加输入时泄露 `AttributeError`。复现见 [corrupt-protocol-before-fix.log.gz](model-context/corrupt-protocol-before-fix.log.gz)。修复将协议类型检查和输入追加纳入恢复中止边界，补充协议及模型视图损坏的公开回归，均返回 `RecordAbort(kind="turn.context")`，不进入模型或工具调用。

首轮完整后端 **348 项通过，312.828 秒**，见 [full-before-review-fix.log.gz](model-context/full-before-review-fix.log.gz)。审查修复后的上下文公开契约、规划契约与进程恢复 **18 项通过，19.989 秒**，见 [final-recovery.log.gz](model-context/final-recovery.log.gz)。最终修复后的完整后端 **349 项通过，312.529 秒**，包含固定 Web/SSE/真实 CLI 模型请求对照、合成旧库续聊、停止、保存故障和真实进程恢复，见 [full-tests.log.gz](model-context/full-tests.log.gz)。

pyright 零错误、前端 13 项测试与 TypeScript / 生产构建通过；模块依赖检查通过，旧实现豁免从 117 项减为 112 项，检查器反例随完整后端运行。[类型日志](model-context/pyright.log.gz)、[模块检查](model-context/modules.log.gz)、[双轴审查](model-context/code-review.md)与公开契约本地链接可核对。Standards 的 1 项 P2 已经复现、修复和独立复核，最终两轴均无遗留。

## 浏览器原失败与补充验证

未经改动的旧浏览器夹具在 1440 / 390 两种宽度均失败：第一条输入触发 `context_budget`，未调用模型。通过同一公开 HTTP 探针对任务起点和本次实现分别运行，二者均估算 **28,153 tokens > 28,000 输入预算**，窗口均为 40,000，状态、原因和零模型调用完全一致。[原失败](model-context/browser-original-failure.log.gz)与[前后预算对照](model-context/browser-budget-comparison.json)保留；这是既有压力夹具失败，不标为通过。

另在临时副本补验页面状态：首次输入的无语义背景重复 310 → 300，停止输入的无语义背景重复 350 → 320，具体差异见 [browser-probe-input.patch](model-context/browser-probe-input.patch)。没有修改仓库夹具、旅行事实、原状态断言、40,000 窗口或生产代码。第一次只调整初始输入后，停止输入仍超预算，失败亦保留于 [browser-first-bounded-failure.log.gz](model-context/browser-first-bounded-failure.log.gz)。最终补验两例通过，36.5 秒，见 [browser-bounded-final.log.gz](model-context/browser-bounded-final.log.gz)。

补验覆盖真实 HTTP / SQLite 的轮内采用与保存区分、停止后迟到摘要未采用、SIGKILL 后摘要请求及用量未知和恢复上一有效版本；保留六张 `context-{saved,stopped,unknown}-{1440,390}.png` 及两份 `context-browser-{1440,390}.json.gz` 公开快照。已视觉检查桌面保存状态与移动中断状态，文本清楚且无横向溢出；这是补充场景通过，不能替代原夹具的失败结论。

## 验证范围

复用既有 HTTP/SSE、真实 CLI、子进程恢复、合成旧 SQLite 和固定模型请求对照；外部模型、地图及网页响应为可控替身，生产规划、上下文、网络 adapter 与真实 SQLite 保持。未修改固定夹具、归一化、窗口或已有断言，不宣称真实供应商语义质量、费用或性能收益。

旧跨测试辅助及未迁移查询装配仍受 `architecture.json` 的原精确例外限制；它们不构成普通生产调用管理上下文的入口。资料获取、偏好、会话查看与最终全仓库验收继续按 #83–#86 处理。
