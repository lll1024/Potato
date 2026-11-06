# 停止收束、未保存正文与未完成结果（#102）

2026-11-06，在 `codex/stream-final-answer` 上从 `b1d7f00` 继续 #97，覆盖故事 32–40、48、49、55。沿用 PR #105，已有用户领域文档不纳入提交。

## 结果与验收边界

Web 与 iOS 点击停止即冻结原轮当前展示，显示正在停止；实时连接仍核对累计正文，后端继续接收在途真实调用并保存。停止期间保持全局名额并禁止新尝试。普通在途回答正常保存后展示真实完整历史；预算收尾保留原停止检查。旧目标不会停止或冻结新轮次。首次进入已停止中的会话恢复已有正文后冻结，断线或无关存储故障不提前解除冻结。

原轮次实际收束后，最终保存或执行事实保存失败时，当前页面保留后端仍持有的正文，明确标注未保存并提供复制。历史保持最后持久状态，服务拒绝新输入；没有可靠正文时不伪造回答，不承诺刷新或重启恢复未保存正文。普通输出上限保留 `terminated / output_limit` 与部分模型回答；预算收尾的完整响应校验和预算终态保持。

后端通过公开 HTTP／SSE 运行真实规划、上下文与临时 SQLite，仅替换外部模型／查询资源，故障在真实 SQLite 触发器注入。iOS 通过公开 `SessionAccess`、真实 GRDB 与可控 HTTP 验证；界面在 iPhone 17 Pro／iOS 26.5 模拟器连接真实 localhost HTTPS／SQLite 后端。Web 使用 Chrome 公开协议和交互。生产模型实连、iOS 17、真机、私网／蜂窝及 TestFlight 不属于本次证据；生产模型联合验收继续由 #104 承担。

## 故事与证据

| 故事 | 验证 |
| --- | --- |
| 32、33 | Web 在停止 POST 响应屏障前冻结，远端停止同样冻结；SessionAccess 在 `requesting` 即冻结，既有 `capturedOldTargetNeverStopsLaterTurnOrChangesWithNavigation` 及公开 HTTP 原目标测试保持 |
| 34–36 | `test_stop_waits_for_stream_and_saves_real_normal_or_budget_outcome` 在真实流返回前停止，验证忙碌 409、名额保持、恰好 3／64 次模型请求；普通完成显示完整历史，预算收尾保留 `user_stop`；SessionAccess 参数用例与模拟器界面同时验证真实完成与未保存分支 |
| 37–40 | `test_stream_result_and_fact_storage_failures_keep_unsaved_body_without_history` 分别注入 `turn.finished`、`request.completed` 故障，确认正文仍在、历史没有回答、真实故障类别、503 禁止输入且没有后续调用；两端验证未保存提示与复制按钮，Web 另核对剪贴板全文 |
| 48、49、55 | `test_truncated_stream_keeps_incomplete_normal_and_budget_outcomes` 验证普通截断保留部分模型正文及 `output_limit`，第 64 次截断仍为应用预算说明，不放宽完整响应校验；Web 检查不完整提示及复制文案，SessionAccess 三种终态参数验证原状态／原因及迟到预览隔离，iOS Presenter 直接提供不完整状态 |

## 复现与修复

- iOS 原实现停止后继续显示新增正文，公开 SessionAccess 测试先失败后通过。Web 首个功能红灯为缺少正常生成期间的停止按钮，补入口与立即冻结后通过。首次 Chrome 沙盒启动失败不计为功能红灯。
- 审查发现首次进入已停止会话会冻结空正文；新增 SessionAccess 用例先复现，再改为核对快照正文后首次冻结。已有本机冻结保持，不被新快照替换。
- 审查发现任何存储错误都可能提前解除停止冻结；两端补充停止期间改名故障和后续分块，实际观察到追加后修复。现在仅在原活动轮次释放后恢复最终展示。
- Presenter 按停止目标轮次提供状态与明确的未保存／不完整标记，View 不解析中文文案，旧目标的迟到请求不会给新轮次标注暂停。
- 一次 Swift Testing 方法级筛选运行 0 项，未计为有效验证；随后整个 AnswerStreamingTests 套件实际取得上述红灯并覆盖修复。新增测试不替换既有停止、保存与预算断言。

## 界面证据

- [Web 未保存全文与复制](answer-outcomes/web-unsaved-desktop.png)
- [Web 手机宽度](answer-outcomes/web-unsaved-mobile.png)
- [iOS 停止冻结](answer-outcomes/ios-stopping.png)
- [iOS 停止后真实保存](answer-outcomes/ios-saved.png)
- [iOS 未保存正文与复制](answer-outcomes/ios-unsaved.png)

## 验证结果

- 后端全量 `.venv/bin/python -m unittest discover -s tests -v`：380 项通过，334.132 秒。
- 前端生产构建成功，呈现测试 13 项通过；审查修复后 Chrome 最终全量 62 项通过，1.5 分钟。审查后的停止／无关存储故障与真实终态定向回归 2 项通过。
- iOS 完整 `xcodebuild test`：75 项能力／集成、10 项截图／状态、16 项界面全部通过，762.973 秒。另补充 SessionAccess 的截断、预算、用户停止终态替换与迟到预览隔离，最终再次运行全部 PotatoTests：76 项能力／集成及 10 项截图／状态通过，21.659 秒。
- 后端及验收夹具 pyright 无错误；模块归属、公开文档链接、`git diff --check` 通过。界面机械检查无发现。

## Standards

初审发现停止中首次恢复的契约违反及 View 解析文案的状态归属问题，已修复。独立复审 0 项未解决问题。

## Spec

初审发现首次恢复正文被隐藏、无关存储错误提前解除冻结、旧目标迟到导致新轮次错误暂停文案三项，均已修复。独立复审 0 项未解决问题。

Standards 0 项，Spec 0 项；两轴均无未解决的最严重问题。
