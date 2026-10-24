# #86：全仓库六项能力与渐进披露最终集成验收

对应 [#86](https://github.com/lll1024/Potato/issues/86)，父规格为 [#79](https://github.com/lll1024/Potato/issues/79)。实施起点 `6908da28021d3e7eb7247494307380fca42ac6da`，固定生产对照基线 `4e01678fdb036f034d72a858fadb226ea239a0c1`；复用 `codex/deep-modules` 与 [PR #87](https://github.com/lll1024/Potato/pull/87)。不自动合并、关闭或改写父规格。用户原有 `GLOSSARY.md`、proposed ADR、项目指引及 `.ua/` 改动保留，不纳入本次提交。

## 当前职责及收缩结果

当前入口和共用契约从[仓库总览](../modules/README.md)进入。[归属清单](../../architecture.json)的 `legacy` 已清空；检查器拒绝重新登记迁移豁免。六个后端目录有显式公开导出和真实实现，现有前端按会话和偏好归组，其余能力未创建空前端 facade。

活动调用方、CLI、真实服务、浏览器辅助、语义验收、结构验证与基线采集均登记并通过公开入口。跨测试借用已移入具名导出的测试支撑，消息式 `planning_fixtures.agent_loop` 临时适配已删除，相关测试直接消费 `plan` 的结构化结果和冻结材料。内部计量测试明确归属上下文；静态呈现测试归属会话，通过 Vite 编译静态导入，去除不可检查的动态源码加载。装配及共享支撑没有业务私有导入特权，生产不得依赖测试支撑。

根 [AGENTS](../../AGENTS.md) 按任务触发总览、契约及内部展开；[内部地图](../backend-map.md)更新到真实目录，README 与验收索引统一入口。旧阶段报告和 `.scratch` 明确按其注明版本解释，不作为当前公开契约。保留固定旧生产树复现：从 `81ed92f` 导出当时装配脚本，通过采集器 `--entrypoint` 显式传入；当前装配已删除旧导入分支。

## 本次执行与证据

本次最终串行后端 **361 项全部通过，312.339 秒**；完整浏览器 **53 项全部通过**，前端呈现 **13 项全部通过**，类型、生产构建、全仓库结构与导航链接检查通过。独立公开调用及临时内部修改完成，93 项要求逐项有当前入口和证据。原始失败单独保留，下文的单次时序失败根因尚未被证明消除。

| 验证 | 最终结果 | 本次原始证据 |
| --- | --- | --- |
| 后端全量（删除适配后的最终串行源码） | 361/361，312.339 秒 | [完整日志](deep-modules-final/full-backend.log.gz) |
| 前端静态呈现 | 13/13 | [前端日志](deep-modules-final/frontend.log) |
| 完整浏览器、真实服务与桌面/移动 | 53/53 | [浏览器日志](deep-modules-final/full-browser.log.gz) |
| pyright，生产/工具/采集/测试支撑 | 0 错误 | [类型日志](deep-modules-final/typecheck.log) |
| TypeScript 与生产构建 | 通过 | [构建日志](deep-modules-final/build.log) |
| 全仓库公开导出/依赖/无环及正反例 | 通过，11/11 | [结构日志](deep-modules-final/structure.log) |
| 当前导航与契约本地链接 | 通过，17 份文档 | [链接日志](deep-modules-final/docs.log) |
| 固定行为、身份和事件因果 | 归一化一致 | [对照结果](deep-modules-final/baseline-comparison.json) |
| 独立阅读、严格公开调用及保存/续聊 | 修正契约与验收脚本后通过 | [独立报告](deep-modules-final/independent-use/report.md)、[公开导入审计](deep-modules-final/independent-use/public-import-audit.json) |
| 临时内部修改及调用方稳定性 | 修改前后各 40/40；调用方未改，副本删除 | [前后日志与精确 diff](deep-modules-final/independent-use/report.md) |
| Standards / Spec 独立审查 | 各 0 项遗留 | [双轴报告](deep-modules-final/code-review.md) |

原始日志与证据统一位于 [deep-modules-final/](deep-modules-final/)。独立任务的原始日志及补丁按原字节保留，其中编号空行的尾空格及 unified diff 的空白上下文不作格式清理；源码和本文等交付文档另作空白检查。

复现入口（已安装 Python 和前端开发依赖）：

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app scripts tests/compatibility tests/*fixtures.py tests/record_faults.py
.venv/bin/python scripts/check_modules.py
PYTHONPATH=tests .venv/bin/python -m unittest test_module_boundaries -v
.venv/bin/python scripts/check_docs.py
npm test --prefix frontend
npm run build --prefix frontend
npm run test:ui --prefix frontend
PYTHONPATH=tests .venv/bin/python -m compatibility.capture --output /tmp/potato86-current --source-commit working-tree
```

浏览器需允许 Chrome 启动。本次首次沙箱启动失败属于环境失败，保留记录并在允许进程启动后重跑；不把那次运行算作 UI 行为验证。

## 原上下文夹具失败及实际停止缺陷

保留原窗口 **40,000**、输入预算 **28,000**、压力阈值 **22,400**。原浏览器输入重复 310 次时首次请求估算 **28,153**，尚无可摘要旧轮次，实际为 `terminated/context_budget`、零模型调用。通过公开 HTTP 的单输入复现两次得到相同结果。只将非要求背景重复改为 300 次，首次估算 **27,873**，仍高于压力阈值、低于输入预算；新增浏览器断言同时固定这三项配置及区间。原有回答、完整原文、一次摘要、轮内采用与共同保存、停止、未知结果及恢复断言全部保留。

首次输入可执行后，浏览器暴露摘要在途停止的实际缺陷：停止后的摘要不得采用，但原始输入仍超预算，规划先处理 `context_budget`，覆盖了 `user_stop`。现有公开 HTTP 摘要停止测试增加同一断言下的长输入分支，先稳定失败，再把 `prepare_request` 等待后的停止核对移到预算结果处理前，测试转绿；未更改上下文计量、额度或摘要规则。停止仍等待真实返回，保存真实用量，不采用迟到摘要，也不再发旅行模型请求。

旧浏览器脚本在重启后点击已折叠轮次中的摘要控件，导致定位超时；增加明确的“全部展开”操作后，两种布局的完整上下文场景均通过。该调整只让既有断言实际可见可操作，没有删除或放宽断言。

## 时序断言的单次失败

删除测试适配后的首次全量为 361 项，360 通过、1 项失败：`test_ten_mixed_web_requests_overlap_serial_maps_and_keep_identity_and_live_order` 的服务端接收间隔没有全部达到原断言 0.39 秒。保留[完整失败日志](deep-modules-final/timing-failure-backend.log.gz)；该日志未包含具体间隔，不能据此确定哪类服务或具体延迟来源。此次未修改查询调度、默认 0.4 秒间隔或该测试断言，旧 `SourceService` / `call` / `finished` 与起点 AST 完全一致。原样定向复验通过，随后原样重复 **3/3** 通过（[日志](deep-modules-final/concurrency-repeat.log.gz)），最终串行全量 **361/361** 通过。该单次时序失败的根因没有稳定复现或被证明消除，不能据一次通过宣称时序绝无抖动。

## 固定行为与合成旧数据

[本次对照](deep-modules-final/baseline-comparison.json)保留最终生产源码指纹及[原始行为](deep-modules-final/current-behavior.json.gz)。使用原 [compare.py](../../tests/compatibility/compare.py) 归一化：模型可见请求、工具结果、终态、CLI 两轮输出、提交身份、停止、强制退出与恢复、有效摘要及偏好均与固定基线相同。身份引用、载荷、事件因果和持久 SSE 另行验证，未更改归一化规则。

本次 `test_migration_baseline` 从基线真实服务生成的[合成旧库](../../tests/fixtures/deep-modules/legacy.sql.gz)恢复，读取历史、请求、偏好及已保存摘要，确认无重放后继续对话；续聊模型输入保留酒店、已确定日期、偏好和有效摘要，旧轮次保持。完整后端还运行真实子进程在调用开始、返回、锁定写入、停止及恢复边界的故障场景。

## 独立 Codex 阅读、调用与内部修改

独立任务均使用无本次聊天历史的上下文，保留访问文件的实际路径、范围与 SHA-256、命令、回答及核对标准。初次阅读只允许总览、契约、公开入口和记录值，不读取实现、测试正文或历史验收。它能正确还原六项职责、依赖、正常/停止/查询失败/模型失败/记录故障/重启，以及四类公开定位，但指出资源管理协议、上下文关键字签名和错误枚举不够精确；这些按契约不足记录，补齐后交给第二个无历史上下文重验。

第二项任务另发现脚本化 MCP 装配缺少 `session.call_tool` 形状说明，保留失败，补齐契约后重新按公开面执行。主代理还发现该验收脚本从规划入口导入未列入 `__all__` 的 `ContextPolicy`，要求改为上下文公开入口并逐项静态核对全部仓库导入后重跑；原错误和复验均保留，不将原脚本判为公开调用通过。最终首轮和延续均为 `completed` / `None` / 零工具错误，累计 3 次模型及 1 次 MCP 查询，真实 SQLite `finish` 成功。临时内部调整前后各 40 项测试通过，调用方未改、临时源码树已删除。实际公开调用及临时内部修改结果另见[独立任务报告](deep-modules-final/independent-use/report.md)；不以读取文件少作为成功条件。

核对标准：六项职责和状态不混淆；允许依赖无反向实现通道；正常路径区分生成与保存；停止保持名额并等待真实返回；记录故障明确中止；重启不重放；正确定位四类问题；公开调用不获取数据库连接、不双写协议和视图、不从事件或文案推导终态；内部修改仅在临时树，调用方不变且相关契约与调用回归仍有效，实验源码无残留。

## 职责集中审查与规格映射

会话拥有接受、停止、对外状态及通知；规划拥有模型步骤、预算和批次；资料隐藏供应商调度；上下文独占协议/视图同步；偏好拥有资格与保护规则；记录拥有事务和一致快照。调用方不再同步两份运行视图、不取得供应商私有对象、不安排事务。结构机制、实际独立阅读效果及局部修改稳定性分别取证。

[逐项映射](deep-modules-final/spec-mapping.md)覆盖父规格 **56 条用户故事、23 项实施决定、14 项测试决定**，每项链接当前入口和本次证据。[双轴独立审查](deep-modules-final/code-review.md)规范轴与规格轴均无遗留。初次独立阅读的[回答](deep-modules-final/independent-read/answer.md)与[访问记录](deep-modules-final/independent-read/access.jsonl)同样保留，契约缺口按初审不足计，修正后另验。

## 适用范围

外部模型、地图、网页使用可控响应，运行真实规划、上下文、查询 adapter、SQLite、后台偏好、HTTP/SSE、CLI 与服务进程；没有读取个人 `.env` 或数据库。结果证明固定场景和本机机制兼容，不证明真实供应商资料质量、外部服务可靠性、真实账户并发或性能收益。ADR 0008、0010 的 proposed 功能不在本次范围。父规格的标题、正文与打开状态保持，见[核对证据](deep-modules-final/parent-unchanged.json)；后续处理由用户决定。
