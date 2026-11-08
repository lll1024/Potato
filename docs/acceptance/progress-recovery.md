# 查询进展与正文的恢复交接（#114）

2026-11-08，基于 `ffbc587` 在独立 `codex/progress-recovery-114` 工作树实施。范围为父规格 [#109](https://github.com/lll1024/Potato/issues/109) 的 [#114](https://github.com/lll1024/Potato/issues/114)，沿已确认公开 HTTP／SSE、真实规划／上下文／SQLite、外部连接故障与 SessionAccess、少量实际两端页面验证。

## 发现、修复与真实归属

Web 新增两条公开页面红绿：第一条在 `progress.preview` revision 8 后注入 revision 7 的旧状态，原实现提前开放发送；第二条在提交后的快照含旧正文／新进展时交错收到更新正文，原实现整体丢弃快照中的新进展。修复分别核对两类内容次序、独立交接累计对象，并保留已核对的较新状态。[旧状态红灯](progress-recovery/web-red.log.gz)、[交错快照红灯](progress-recovery/snapshot-red.log.gz)、[两条绿灯](progress-recovery/web-green.log.gz)。

iOS 原接收器只处理两类预览，不消费即时服务状态。公开 `SessionAccess` 新用例先收到下一轮进展、后收到确认新目标的 `service.state`，未推进普通轮询时原实现 1 项失败（5.024 秒）。现在同一订阅核对即时状态、保留尚待目标确认的累计候选；状态确认后交接候选并通过既有历史入口立即读取，不建立另一条轮询或规划。空对象、重复次序、旧状态／快照和旧连接不能复活内容。[红灯](progress-recovery/ios-red.log.gz)、[最终能力日志](progress-recovery/ios-capability.log.gz)。

停止、保存故障和真正终态仍由原会话规则决定；本票的即时状态接收复用原冻结入口，不把客户端状态变化写成保存事实。已保存历史优先，显示恢复标记直接呈现累计全文，原 #113 短暂过渡不重新播放。契约见[会话](../modules/sessions.md#查询进展与正文恢复交接114)与[iOS](../modules/ios-sessions.md#两类内容与即时服务状态交接114)。

## 本次验证

- 公开 HTTP／SSE 恢复组合 **38 项通过，35.873 秒**。新增双客户端在查询分片、真实工具及作答屏障间断开／重连，两类快照交接一致，工具只执行一次、真实模型请求仍为三次；关闭全部订阅不停止后端。[日志](progress-recovery/backend.log.gz)。
- 新增真实子进程重启 **1 项通过，2.880 秒**：先可靠保存行动说明和工具完成事实，再在正文未完成时终止进程。同库重启 `stream_id` 不变、`instance_id` 改变，两个内存预览均为空，可靠进展逐字段一致，回答按 `service_interrupted` 展示，外部模型／地图调用没有重放。[日志](progress-recovery/process.log.gz)。
- iOS 最终“构建并执行” **15 项公开能力测试全部通过**，包含运行中、离开期间已保存、同库重启三种进展恢复分支，以及已有配置／数据集／进程／会话切换、正文恢复、停止与未保存回归。原始日志明确包含三分支；中途一次 `test-without-building` 只发现 14 项，不作为新增恢复矩阵通过依据。[最终日志](progress-recovery/ios-capability.log.gz)。
- iPhone 17 Pro／iOS 26.5 模拟器实际执行 `testForegroundRestoresQueryProgressThenSavedAnswerWithoutReplay()` **1 项通过，25.293 秒**。真实 HTTPS 与 SQLite、仅外部模型／查询可控：查询阶段切后台后恢复完整累计说明，再在后台进入正文并恢复，最后后台完成后采用真实历史。[查询恢复](progress-recovery/ios-query-restored.png)、[正文与同轮工具状态](progress-recovery/ios-answer-restored.png)、[保存后](progress-recovery/ios-saved.png)、[截图清单](progress-recovery/ios-attachments.json)。
- 真实 Web HTTPS 页面恢复用例显式运行 **1 项通过，2.2 秒**：离线接收期间模型继续，重连恢复进展；再次离线期间真实保存，返回后采用完整回答；刷新和 390px 第二客户端展开内容与 `query_progress` 原文／顺序一致，读取未新增模型或工具执行。[执行日志](progress-recovery/web-real.log.gz)，可重跑入口为 [progress-recovery-live.spec.ts](../../frontend/tests/ui/progress-recovery-live.spec.ts)。该轮 JSON／截图曾生成在共享 Playwright 输出目录，随后常规回归清理该目录，未能持久保留，因此本记录只引用已实际通过的日志与断言，不补造观察序列或截图。

## 首跑限制与回归边界

首次 iOS 独立编译因磁盘耗尽失败，未进入测试；清理本次已完成任务的可重建缓存后，以串行构建完成。能力组合首跑中，新重启夹具全局替换状态字符串误把可靠工具的 `completed` 也替换为 `terminated`；仅改夹具为结构化修改轮次终态，最终 15 项通过，原可靠工具断言保持。[最终构建与能力结果](progress-recovery/ios-capability.log.gz)、[含夹具失败及已通过 UI 的首轮日志](progress-recovery/ios-ui.log.gz)。

真实 Web 初跑缺少正式静态构建，页面返回 404；原因是 TypeScript 未充分收窄可空旧快照，已补显式非空条件并成功构建。下一次已走通断线／保存／刷新，但移动第二客户端脚本没有先打开既有会话导航而等待隐藏按钮；只修正脚本的真实导航动作，未更改产品可见性或断言。[初跑日志](progress-recovery/web-real-first.log.gz)、[导航脚本失败](progress-recovery/web-real-navigation.log.gz)。

常规 Web 全量首跑 37 项中 36 项通过，既有“新事件不会打断滚动回看”在人工设置 `scrollTop=50` 后未出现“回到最新”，发生在本票事件交错注入前。未改滚动产品代码或放宽断言，定向重复三次通过；保留该次失败，不据此宣称偶发原因已被消除。[首跑](progress-recovery/web-first-full.log.gz)、[三次定向](progress-recovery/web-scroll-repeat.log.gz)。最终完整对话回归 **37 项通过，40.5 秒**；前端生产构建、模块／文档／diff 检查通过，局部 UI 机械检测无发现。[最终 Web 日志](progress-recovery/web-final.log.gz)。

本票未运行真实供应商模型、真机、TestFlight、iOS 17 或私网／蜂窝；上述模拟器、可控连接和 HTTP 机制证据不代替 #117 的当前真实模型双端联合验收。用户日常数据库与原工作区未被读取或修改。

## 用户故事与复现

| 父规格故事 | 本次证据 |
| --- | --- |
| 6、12、33、34、35、36、58 | 双客户端公开 HTTP、SessionAccess 单订阅恢复、真实两端离线／前后台界面；调用次数保持 |
| 27、28、29、30、59 | 可靠历史、刷新／重开／第二客户端与旧会话继承回归 |
| 31、32、41、46 | 同库真实进程重启只恢复可靠进展、真实中断与已保存历史 |
| 40 | 两类次序、空对象、旧连接、配置／数据集／进程／会话归属失效及原有切换回归 |

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_query_progress test_answer_streaming test_reconnect -v
npm run build --prefix frontend
npm run test:ui --prefix frontend -- tests/ui/conversation.spec.ts
# 启动独立 ios/Integration/backend.py HTTPS 夹具后，显式运行：
POTATO_RECOVERY_UI=1 npm run test:ui --prefix frontend -- tests/ui/progress-recovery-live.spec.ts --output=/tmp/potato-recovery-ui-evidence
```

原生按 [iOS README](../../ios/README.md) 准备模拟器与短期 localhost 证书，运行整个 `PotatoTests/AnswerStreamingTests` 及上述新 UITest（带 `()`），核对实际发现数量。结束后正常停止临时服务；证书、密钥和本机依赖链接不入库。
