# 查询进展与正文的重试、降级和预算交叉（#115）

2026-11-08，基于集成 `ffbc587` 的独立 `codex/progress-retries-115` 实施 [#115](https://github.com/lll1024/Potato/issues/115)，范围为父规格 [#109](https://github.com/lll1024/Potato/issues/109) 的用户故事16、17、18、31、37、38、39、40、46、55、56、57、58、59。本票沿既有公开HTTP／SSE、真实规划／上下文／SQLite与SessionAccess测试边界；查询和正文继续使用同一轮次和请求账本。当前实际模型联合验收由 #117 独立交付。

## 发现与最小修复

查询初次流式拒绝后，真实规划已转为完整响应，但当前查询条目未携带交付方式；两端直到回答阶段才解释降级。新增公开HTTP用例先在0.264秒失败，实际 `delivery=None`；查询模型条目附带当前真实方式后，0.250秒通过（[红灯](query-retries/query-fallback-red.log.gz)、[通过](query-retries/query-fallback-green.log.gz)）。工具条目、预算、连接参数和错误分类没有因此改变。

空查询 `retrying` 条目原先被内容过滤，失败旧说明虽然失效，页面缺少明确重试解释。两端现在在对应轮次说明查询进展正在重试；新尝试重新生成，可靠工具事实继续显示。明确拒绝流式时在查询完整等待阶段即显示既有降级原因，不把完整响应作流式平滑播放。旧服务缺少可选字段时仍沿原阅读路径。Web最小复现见[红灯](query-retries/web-red.log.gz)，修改后桌面／窄屏两项通过。

## 公开行为与交叉证据

- 查询失败前已经可靠保存的行动说明与工具完成事实，在空失效／重试和下一尝试之间保持；实际工具只执行一次，重试参数一致，失败说明不拼接到新尝试、不进入保存回答或后续上下文。
- 网络、超时、空流、429、503按原分类有限重试；401、402和无明确流式归因的400保持原失败。只有明确流式拒绝才转完整请求；六次耗尽或退避中停止后释放真实名额，不继续启动调用。
- 第63次查询明确拒绝流式时，仅能使用第64次无工具完整收尾；第64次自身失败没有重试、降级或后续请求。查询／作答共用64次模型、128次工具和8次历史回读限制。
- 故意畸形但已收到的失败查询响应仍保留真实诊断载荷；随后通过公开历史搜索取得空匹配，已知记录身份直接回读被拒绝，不能把失败行动说明作为可靠资料。
- Web使用可控时间核对大块进展仍在展开时发生重试：旧队列立即失效，可靠工具事实保留，新尝试从自身身份开始；完整等待前没有回答，停止／保存故障不被重试提示覆盖。[桌面](query-retries/web-desktop.png)、[窄屏](query-retries/web-mobile.png)。

本树最终流式正文、查询进展、调用预算及固定迁移基线 **52项通过，58.045秒**（[日志](query-retries/backend.log.gz)）。固定gzip数据和通用规范化未改，CLI和旧库续聊按原真实入口比较。首次52项有一条新增断言错误（错误包含搜索词回显，而实际matches为空）和一次既有128工具测试的异常包装；原始记录保留（[首跑](query-retries/backend-first.log.gz)），正确断言只检查实际匹配与直接读取拒绝，未放宽正文隔离。

Web完整会话首跑36项通过、1项新窄屏测试因导航前置错误超时；修正为沿既有用例先进入会话再切窄屏后，两种视口新增用例均通过，耗时8.2秒（[首跑](query-retries/web-first.log.gz)、[复验](query-retries/web-final.log.gz)）。实际画面经一次桌面／窄屏检查，无溢出或遮挡；局部机械检测为空。

同一查询投影原有四项类型错误（可选回调及查询状态可能未绑定）也在本票以显式初始化和回调断言收敛，行为不变。指定Python3.14和当前venv的pyright由[4项错误](query-retries/types-red.log.gz)转为[0错误](query-retries/types-green.log.gz)，原公开查询专项[14项通过，9.513秒](query-retries/query-typed.log.gz)，未增加镜像测试。

## 合入 #114 后的最终交叉验证

最终分支先合入 `1b3fa44`（#114），合并点 `eacef67` 保留两票新增用例与契约；未改 #114 状态防旧或恢复规则。随后在相同代码上完成：

- 公开HTTP／SSE查询专项 **16项通过，13.675秒**（[日志](query-retries/query-merged.log.gz)）；Web查询重试桌面／窄屏及进展次序防旧 **3项通过，7.4秒**（[日志](query-retries/web-merged.log.gz)），前端[构建通过](query-retries/web-build-merged.log.gz)。同范围pyright仍为[0错误](query-retries/types-merged.log.gz)。
- iPhone 17 Pro／iOS 26.5模拟器增量[编译成功](query-retries/ios-build-merged.log.gz)后，`AnswerStreamingTests` **16项通过**；真实HTTPS／SQLite界面方法 **1项通过，41.500秒**，该方法实际执行查询重试和明确拒绝流式两场景。总计17个测试标识，0失败、0跳过（[日志](query-retries/ios-merged.log.gz)、[xcresult摘要](query-retries/ios-results.json)）。屏幕核对失败说明及待显示队列清空、新尝试独立、空查询等待有准确提示、仅一次真实工具、保存结果可读；[重试](query-retries/ios-retry.png)、[完整等待](query-retries/ios-fallback.png)、[保存后](query-retries/ios-saved.png)。
- 同一独立HTTPS夹具的真实Web页面，通过界面接受输入后仅在外部模型边界控制失败和返回，分别执行查询重试／完整降级；两场景均核对4次真实模型请求、1次工具、失败说明不进入可靠历史、复制全文等于SQLite保存正文（[日志](query-retries/web-https.log.gz)、[公开记录](query-retries/web-https-observations.json)）。[桌面重试](query-retries/web-https-retry-desktop.png)和[窄屏完整等待](query-retries/web-https-fallback-mobile.png)均经画面检查，没有提示溢出或控件遮挡。这是实际页面与后端、可控外部模型的集成证据；当前生产模型持续时序仍属于 #117。

原生复验入口是 `xcodebuild test-without-building`，唯一目的地 `platform=iOS Simulator,id=AB9010DF-6F36-4079-8492-3CE8B5393EF7`，过滤 `PotatoTests/AnswerStreamingTests` 与 `PotatoUITests/ReadingNavigationTests/testQueryRetryAndCompleteFallbackReplaceFailedExplanationBeforeAnswer()`，`-parallel-testing-enabled NO`。夹具使用既有 `ios/Integration/backend.py --cert … --key …`，全程独立临时SQLite；两场景不与另一界面并发控制夹具。测试结束已正常停止自有夹具，确认18489–18498无监听并释放共享模拟器。

## 环境与适用范围

iOS首次独立测试编译因磁盘空间耗尽失败（[日志](query-retries/ios-build-space-failure.log.gz)）；该次没有执行能力或界面用例。释放旧编译缓存后，[首次编译成功](query-retries/ios-build-first-success.log.gz)，首次独立能力14项及界面1项均通过（[能力](query-retries/ios-capability-first.log.gz)、[界面](query-retries/ios-ui-first.log.gz)）；上述最终证据另在合入 #114 后重新执行。一次最终查询复验申请曾因自动审批服务额度失败而未执行，用户继续后实际重试通过，不将未执行批次计作测试结果。

模块依赖、当前文档链接与 `git diff --check` 均通过。没有调用当前生产模型，没有读取或改写日常数据库，真机／TestFlight不在本次证据范围。
