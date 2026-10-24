# Issue #86 独立阅读、公开使用与内部修改验收

结论：初审发现 MCP 装配契约缺口；随后主代理发现验收脚本错用了未显式导出的 ContextPolicy。这两项问题分别修正后，严格公开导入的实际调用复验通过。临时内部调整前后同一组 40 项测试全部通过，调用方无需修改。结论依据实际调用、结构化结果、真实 SQLite 保存和前后回归，不依据阅读文件数量。

## 独立阅读边界与六项能力

先阅读根 AGENTS、领域阅读规则、仓库总览和术语表；随后只看六份公开契约、六个 Python 公开入口、两个 TypeScript 公开入口以及本机记录公开值类型。公开使用完成后才展开上下文内部。没有阅读已有验收材料或历史任务答案；复制阶段对文件仅作字节复制和摘要，未借此阅读答案。完整路径、行范围和文件 SHA-256 见 [access.jsonl](access.jsonl)，复制读数明确标为“不展开内容”。

| 能力 | 拥有的状态和公开调用 |
| --- | --- |
| 助手会话 | 提交身份、全局单轮名额、原轮次停止目标、保存状态、历史与通知；通过 TurnRuntime 或 create_app；前端 SessionsApp 拥有草稿、提交不确定性、分页失效 |
| 旅行规划 | 当前旅行要求、日期、规划规则、整轮预算、混合工具批次和结构化 PlanningResult；Web/CLI 都调用 plan，CLI 原样传回 continuation |
| 资料获取 | 能力发现、地图/网页连接、供应商限流与暂停、单段查询及真实返回；Retrieval.discover/capabilities/execute，查询成功不等于信息核实 |
| 模型上下文 | 完整协议和模型视图、容量、摘要、超限恢复、原文回读、冻结材料及延续凭证；ModelContext 的追加/请求/恢复操作共同维护两种表示 |
| 旅行者偏好 | 管理界面、稳定本人偏好提取、闲置资格、版本与人工保护、有限重试和后台生命周期；Preferences.snapshot/background/edit/delete/start/shutdown |
| 本机记录 | 真实 SQLite、原子接受和完成、执行事实、一致读取/游标、条件持久化及重启恢复；LocalRecords.accept/observe_*/finish/recover |

依赖为：会话 → 规划、偏好、记录；规划 → 资料、上下文；上下文 → 记录；偏好 → 记录；资料和记录不依赖其他业务能力。装配注入的回调不构成反向实现依赖。共享错误分类/额度常量等支撑不携带业务运行状态。

正常流程：Web 锁定目录，打开记录并 recover，配置外部资源后启动会话及偏好；accept 共同保存输入与偏好待处理资格，然后调用 plan。模型/查询开始前可靠保存开始事实，真实返回后保存实际事实；规划按提出顺序预留工具额度，连续外部段交给资料，历史回读形成屏障，再推进模型。plan 返回结构化结果和冻结材料；会话一次 finish 共同保存回答、协议、有效上下文和偏好结束资格。只有 finish 成功返回才算完成保存。普通调用方不双写消息与视图。

停止流程：按原 turn_id 传入停止，停止中继续占全局名额；已进入服务的调用真实返回并完成保存尝试后释放。关闭页面/SSE 不取消规划。服务退出先拒绝输入，再停止和等待偏好/当前规划，最后关闭资源及记录、释放目录锁。

失败流程：普通供应商失败变成对应查询结果，严重故障只暂停该供应商；模型错误、停止、预算耗尽通过 PlanningResult.status/reason 区分。事实保存失败抛 RecordAbort，禁止新外部请求，等在途返回；会话 accepting=false，并展示 storage_error/unsaved_fact，不伪造完成历史。

重启流程：只恢复已提交事实、上一有效上下文和确定日期，不重放模型、查询或普通重试计时；偏好恢复未完成提取、有限重试和人工管理保护。持久通知游标与瞬时状态分离，目录身份变化要求重新读快照。

公开定位足够解释所问问题：上下文超限先用 ModelContext.prepare_request/prepare_recovery/read_history，上层继续调用 plan；查询调度用 Retrieval.execute 和 plan 批次预算；人工保护通过 Preferences.edit/delete 和 start/shutdown 管理、由内部资格/版本复核执行；记录故障通过 LocalRecords.accept/observe_*/finish/recover、RecordAbort 与 TurnRuntime.storage_failed 收束。上述解释不需要先读内部。

## 初次装配缺口与修正后公开运行

初始 retrieval 契约允许 Retrieval(map_client) 却没有说明脚本化 MCP 客户端必须提供 session.call_tool。按常见 list_tools/call_tool 形状装配时发现成功，但执行公开 tool.failed 事实报告 AttributeError: 'ScriptedMCP' object has no attribute 'session'。真实 plan 仍返回 completed，但 tool_error_count=1；这说明“有最终回答”不能充当查询成功证据。详见 [首次失败原始事实](initial-failure.log) 和 commands.jsonl 中退出码为 1 的公开调用。

我没有读取资料内部修补答案，直接报告缺口。主代理独立核对实现后，在 docs/modules/retrieval.md 第 11 行明确 mcp.Client 的有效生命周期、list_tools(*, cursor=None)、session.call_tool 和结果字段。我重新只读更新契约并据此形成 [复验脚本](public_use_after_contract.py)，未读取 retrieval 内部。

复验真实执行生产 plan → Retrieval/ModelContext，并通过公开记录入口打开临时真实 SQLite：

- 首轮：2 次脚本化外部模型返回、1 次脚本化 MCP 查询；结构化结果 completed / reason=None / answer_source=model / tool_error_count=0。
- LocalRecords.accept、执行事实 observe_*、finish 和 snapshot 均成功；实际 travel.sqlite3 存在，大小 204800 字节。
- 原样传回不透明 continuation，再调用一次 plan，结果同样 completed / reason=None / tool_error_count=0；累计 3 次真实生产模型调用路径。
- 终态断言只读取 PlanningResult.fields()；保存依据 finish 成功返回。观察事件仅用于事实记录和故障诊断。
- SQLite 位于 TemporaryDirectory，退出删除；没有访问个人 .env/数据，没有取得数据库连接，没有自行维护两份消息或从文案推导终态。

结果见 [public-result.json](public-result.json)、[snapshot.json](snapshot.json)、[external-requests.json](external-requests.json)；首次脚本保留在 public_use.py。脚本不联网，仅在外部模型和 MCP 边界返回本机确定性响应，内部规划、调度、上下文及 SQLite 均为真实生产实现。复验命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/Users/jarviliu/projects/travel \
  /Users/jarviliu/projects/travel/.venv/bin/python \
  /tmp/potato86-independent-use/public_use_after_contract.py
```

本次未验证真实供应商内容质量、真实认证或付费服务；没有将脚本化运行称作这些外部系统的验收。

## 验收脚本公开导入遗漏与再次复验

主代理随后指出：此前脚本从 travel_app.planning 具名导入 ContextPolicy，虽然 Python 因模块内已导入该名称而允许执行，但它不在该入口的 __all__ 中。ContextPolicy 的显式公开入口为 travel_app.context。这是本验收者漏查公开导出造成的脚本错误，不能把此前执行成功视为严格只用显式公开导出的证据；它与前述缺失 MCP 装配协议的契约不足是两个独立问题。

原脚本、报告和运行结果已原样保存在带 before-import-fix 后缀的文件中，例如 [错误导入原脚本](public_use_after_contract-before-import-fix.py)、[原报告](report-before-import-fix.md) 和 [原结果](public-result-before-import-fix.json)。现有复验脚本已改为 from travel_app.context import ContextPolicy；仓库文件未改。

修正后重新执行同一生产规划、查询、上下文与 SQLite 保存链路：首轮及 continuation 均 completed / reason=None / answer_source=model / tool_error_count=0；累计 3 次模型、1 次 MCP，真实 SQLite 204800 字节，finish 成功。当前 public-result.json、snapshot.json、external-requests.json 和 public-observations.json 均来自这次重新运行，原始输出见 [严格公开导入复验日志](public-run-after-import-fix.log)。这次结果才作为严格公开导入调用的通过证据。临时内部调整实验及其已有 40 项前后回归不受影响，未重复执行。

[逐项导入核对证据](public-import-audit.json) 和 [可复现核对脚本](verify_public_imports.py) 静态解析脚本及对应入口 __all__，记录脚本与入口 SHA-256。核对结果：

| 项目导入 | 对应入口 __all__ |
| --- | --- |
| travel_app.planning.plan | 已明确列入 |
| travel_app.context.ContextPolicy | 已明确列入 |
| travel_app.retrieval.Retrieval | 已明确列入 |
| travel_app.local_records.LocalRecords | 已明确列入 |

核对也保留修正前 planning.ContextPolicy 的 in_all=false 证据。标准库和第三方 Anthropic/MCP SDK 导入逐项另列在 JSON 清单中，不套用本仓库能力入口的 __all__ 约束。

## 临时内部调整及调用方回归

公开上下文契约将状态协调定位到 context/runtime.py。我在 /tmp/potato86-independent-use/source 复制当前 travel_app、tests、scripts、architecture.json、所需文档和启动文件；使用原 .venv，frontend/node_modules 仅作符号链接。原工作区没有实验写入。

内部只调整 ModelContext.prepare_request：启用策略时复用已有 _measure_view 完成消息副本和计量，替代同样的重复两步逻辑；策略关闭时仍复制消息并使用 None 计量。没有改变公开签名、返回值、状态、预算或异常约定，没有新增导入，也没有修改调用方。精确证据：[internal.diff](internal.diff)、[修改前源码](internal-before.py)、[修改后源码](internal-after.py)。

相同命令在修改前后各执行一次：

```bash
cd /tmp/potato86-independent-use/source
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests \
  /Users/jarviliu/projects/travel/.venv/bin/python -m unittest \
  test_context_contracts test_context_budget test_agent test_call_budget -v
```

修改前 40 项，15.513 秒，OK；修改后 40 项，15.402 秒，OK。完整原始日志：[修改前](tests-before.log)、[修改后](tests-after.log)。包含公开上下文契约、容量/校准，以及规划调用方、混合批次预算、Web 会话停止与预算收尾；后两类覆盖跨能力协作。未新增镜像实现的测试。

[workspace-proof.json](workspace-proof.json) 与 [source-manifest.json](source-manifest.json) 记录 145 个复制文件的摘要比对：副本仅 context/runtime.py 有变化，所有调用方与测试不变；原工作区对应实验目标仍为复制前摘要。原工作区同期唯一已复制文件内容变化为主代理修正的 docs/modules/retrieval.md，已单独列明。最初“原目录所有文件零差异”的检查因此失败，原始失败保留在 commands.jsonl；随后只接受这一已知文档修正并再次验证，其余文件均未变。

临时源码树已删除，source_deleted=true；保留的前后单文件快照、diff、脚本及日志都是 /tmp 下验收产物。根目录新增的验收证据目录来自主代理，我未读取。当前分支 codex/deep-modules，起始 HEAD 6908da28021d3e7eb7247494307380fca42ac6da；本独立实验未提交、推送或创建 PR。

## 审计说明

[commands.jsonl](commands.jsonl) 保存命令、返回码和完整输出文件；长测试输出未因聊天截断而丢失。最初两条引导命令在审计运行器建立前执行，已补入记录：初次读取输出由相同 SHA-256 内容校验后重建，运行器建立输出来自原工具返回，均有明确注记。随后命令即时审计。access.jsonl 区分展示阅读和仅复制/摘要用途，不将机械复制算作独立理解。
