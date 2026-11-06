# 流式失败重试与完整回答降级（#101）

2026-11-06，本记录覆盖父规格 #97 的故事 24、26–31、50、51、54、55。基点为 `f9ead02`，复用原功能分支和 PR；已有用户领域文档不纳入本次提交。

## 结果与范围

普通作答中途失败先清空旧尝试，允许重试时显示正在重试，新实际请求使用新身份从头输出。重试保留本轮输入、日期、偏好背景、已完成工具及其结果，不把失败正文或错误加入模型协议。继续使用最多追加五次、既有退避和停止优先；耗尽或不可重试错误结束为真实失败。停止先于在途作答失败时不再启动新尝试，真实失败终态沿用原有规则。

明确的流式能力拒绝才改用完整响应。降级请求经过同一容量检查和请求扣账，客户端在等待与完成后均可解释原因；方式来自可靠保存的请求开始事实，不污染回答或模型上下文，不增加数据库列。普通断线、超时、429、5xx 或普通非法请求保持原错误分类。第 63 次失败可进入第 64 次无工具收尾；第 64 次自身失败不追加降级、重试、摘要或恢复。

后端通过公开 HTTP／SSE 运行真实会话、规划、上下文与临时 SQLite，仅在外部模型连接注入分块和错误。另用真实 Anthropic SDK 与可控 HTTP 传输验证没有结束原因的截断流。iOS 使用公开 SessionAccess、真实 GRDB 文件和分块 HTTP，界面使用 iPhone 17 Pro／iOS 26.5 模拟器连接真实 localhost HTTPS 后端。Web 使用 Chrome 交互测试。生产模型故障实测、真机、私网／蜂窝和 TestFlight 不属于本次证据，生产服务联合验收仍由 #104 承担。

## 故事与证据

| 故事 | 验证 |
| --- | --- |
| 24、26、27 | `test_midstream_disconnect_retries_with_empty_preview_and_unchanged_input` 在真实正文之后断开，SSE 先交付空 `retrying`，随后新尝试身份与独立正文；Web 与 SessionAccess 用例验证清空和替换，模拟器界面验证后端重试 |
| 28、31 | 同一 HTTP 用例断言重试请求完全相同、已完成工具仅执行一次、保存协议没有失败正文；截断 SDK 流同样不进入保存协议 |
| 29、30、51 | `test_stream_errors_keep_classification_without_accidental_fallback` 覆盖读取超时、连接、429、503、401、额度、普通 400 和含 streaming 字样的无关错误；耗尽六次实际尝试后停止，退避及在途失败的停止竞争不再启动请求 |
| 50 | 明确文字、参数拒绝、结构化错误码三类证据均走计数的非流式请求；HTTP 验证历史保留方式，SessionAccess 验证空等待与历史字段，Web／iOS 界面同时显示原因和完整回答 |
| 54、55 | `test_fallback_at_63_uses_final_reserve_but_final_stream_never_retries` 断言两类边界均恰好 64 次，最后一次为无工具 `travel_final` 且不出现重试事件；既有预算／摘要／恢复回归保持 |

## 缺陷复现与验证记录

HTTP 首先复现底层流读取断线被归为 UNKNOWN、Web 缺少正在重试提示、SessionAccess 拒绝 retrying，以及真实 SDK 将没有结束原因的部分流当作成功回答；对应测试均先失败后修复。新增参数拒绝测试另复现两类明确证据未被识别，补充窄规则后通过。

第一次 Chrome 启动受沙盒应用权限限制，在正常应用权限下重跑取得有效功能红灯。一次 Swift Testing 筛选未包括新增测试、另一次选中不存在的套件，未计作有效验证；随后运行正确 AnswerStreamingTests，实际观察到 8 项中的新增测试失败，修复后全部通过。早期 HTTP 测试误读协议字段，已改为公开轮次的 messages；该夹具错误不作为生产缺陷。

完整回归指出既有规则要求在途真实模型失败保留 `failed / model_error`；撤回早期将其统一改为停止的额外行为，保留原有终态及停止后禁止新尝试的边界。固定迁移基线仅新增严格核对并剥离 `answer_delivery` 的阶段投影，原捕获与通用标准化保持原样。

两轴初审共同发现失败部分响应仍能被历史工具回读，Spec 另发现 200 空流触发 SDK 断言而未重试。补充实际红灯后修复：失败作答退出模型专用回读白名单，原载荷继续供执行轨迹读取；没有任何事件的流按空响应进入原有有限重试。最终 SDK 用例同时验证搜索与已知记录身份读取不泄漏失败文字。

## 界面证据

- [Web 清空失败正文并提示重试](answer-retries/web-retrying.png)
- [Web 手机宽度下保留降级原因及完整回答](answer-retries/web-fallback-mobile.png)
- [iOS 清空正文并提示重试](answer-retries/ios-retrying.png)
- [iOS 降级等待完整回答](answer-retries/ios-fallback-waiting.png)
- [iOS 已保存全文与降级原因](answer-retries/ios-fallback-saved.png)


## 最终验证

- 后端最终全量 `.venv/bin/python -m unittest discover -s tests -v`：377 项通过，323.633 秒。
- 固定基线、停止及流式公开 HTTP 定向回归：29 项通过，24.891 秒；最终 SDK 截断／空流及搜索／已知身份回读用例通过，2.182 秒。
- 前端 `npm run build --prefix frontend` 成功；`npm test --prefix frontend` 13 项通过，`npm run test:ui --prefix frontend` 60 项通过，1.9 分钟。
- iOS 首轮全量运行 73 项 Swift Testing 能力／真实集成、10 项截图／状态、15 项界面；能力与截图全部通过，界面 14 项通过，既有 `testRestartRecoversOriginalSubmissionAndOpensSavedConversation` 首次失败。失败层次显示读取目标已变化；未修改该用例或相关提交恢复代码，独立复跑通过，39.332 秒。首轮全量不能记为一次全绿。
- 最新后端重启后，再运行全部 `PotatoTests` 与本次真实 HTTPS 重试／降级界面：73 项能力／集成、10 项截图／状态和 1 项界面全部通过，63.151 秒。
- `.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app`：0 errors；模块归属、公开文档链接及 `git diff --check` 通过。界面机械检查无发现。

## Standards

初审发现 1 项失败作答历史回读资格问题，已通过公开 HTTP 复现并修复。独立复审及 SDK 定向回归通过，0 项未解决问题，无需处理的代码异味。

## Spec

初审发现失败正文回读与 200 空流分类两项问题，均已补充红绿回归。独立复审确认 0 项未解决问题，预算、失败终态、原有查询复用和任务范围保持。

最终汇总：Standards 0 项，Spec 0 项；两轴均无未解决的最严重问题。
