# iOS 最终回答实时阅读（#99）

2026-11-06，本记录覆盖父规格 #97 的 iOS 首个纵向任务 #99，基于 #98 的公开快照和 SSE。模拟器为 iPhone 17 Pro／iOS 26.5，Swift 6；测试使用可控外部模型、真实 FastAPI／HTTPS、后端临时 SQLite 和手机 GRDB 文件。生产模型实连留给 #104，完整重连竞争、故障体验及阅读打磨分别由 #100–#103 继续验收；真机、iOS 17 实跑和 TestFlight 状态沿用原交付记录。

## 交付与测试边界

共享 `SessionAccess` 拥有唯一前台正文订阅，先读取当前快照和历史再接收 SSE 累计对象；页面和 Presenter 只投影正文。暂存预览独立于历史，保存前标注“尚未保存”，保存后使用真实持久终态。切后台、锁屏或返回列表只取消客户端读取，不发送停止请求。原提交身份、草稿、全局执行名额与按原目标停止保持。

测试沿用规格指定的公开 `SessionAccess` 与 HTTP 传输注入边界。受控传输将同一 SSE 事件切在 UTF-8 中文字节中，验证完整事件才投影；累计对象整体替换，旧次序、其他轮次、过期连接及已保存终态不互相覆盖。后台取消由 URLProtocol 接收取消事实验证，恢复前台先取离开期间的累计正文；重复前台通知只创建一次连接。实时错误单独投影，持久轮次事实不被伪造成失败。

`ios/Integration/backend.py` 的独立 18497 服务仅在实际外部模型边界设置屏障：三次放行依次生成标题和两段正文，第四次才返回最终模型响应。真实共享能力和 SQLite 贯通整条链路。双客户端测试分别由电脑 HTTP 与手机发起，在模型未返回、历史仍为 running 且无 answer 时观察多批正文；后台继续生成，恢复前台补齐累计正文，最终两客户端历史完全一致。

| 父规格故事 | 证据 |
| --- | --- |
| 2、7、8 | `AnswerStreamingTests` 的完整／分块 SSE 与 `SubmissionIntegrationTests.realStreamingAcrossClientsSurvivesBackgroundAndMatchesSavedHistory` 的真实模型屏障；界面第一、第二批正文出现时最终响应尚未返回 |
| 3 | 同一后端接受身份和轮次，分别由手机与电脑 HTTP 发起；对端读取运行历史和保存后的完整回答 |
| 9、10 | 保存后预览消失，公开历史正文一致；界面返回列表重新进入仍显示同一完整回答 |
| 15 | 前台唯一 SSE 订阅直接投影多批正文；两秒轮询只继续核对持久事实，测试未推进轮询等待也可观察正文 |
| 16、17、19 | 后台取消接收且没有停止 POST；真实后端在后台继续生成，恢复补齐；退出页面取消旧订阅，配置／数据集／进程／会话变化使旧消息失效 |

## 界面证据

模拟器界面测试 `ReadingNavigationTests.testAnswerReallyGrowsBeforeCompletionAndThenBecomesSavedHistory` 已通过。它逐次放行真实模型流，检查标题、第一段、第二段逐批出现，保存前与保存后的复制标签不同，并重新打开历史核对最终正文。

- [第一批正文](ios-answer-streaming/first-batch.png)：只有标题，标记“正在生成 · 尚未保存”，复制当前正文。
- [第二批正文](ios-answer-streaming/second-batch.png)：第一段已增加，仍未返回最终响应。
- [保存后](ios-answer-streaming/saved.png)：两段完整正文，复制完整回答。

## 验证记录

首轮红灯：公开 `SessionAccess` 尚无正文投影，新测试编译失败；实现后分块能力测试通过。真实服务首轮发现测试静态页面挂载覆盖控制路由，修正验收装配后真实流与界面场景通过。三条旧提交集成测试的模型请求计数从每轮一次更新为两次：#98 已将查询判断和无工具作答分开；原接受幂等、单轮历史及最终身份断言保留，重复执行会产生四次请求。

- 后端全量 `.venv/bin/python -m unittest discover -s tests -v`：367 项通过，367.815 秒。
- iOS 全量 `xcodebuild test`：69 项 Swift Testing 能力／真实集成测试和 10 项 XCTest 截图／状态测试通过；14 项界面用例中首轮 13 项通过，表格用例两条显示范围断言失败。失败截图中表头已移到可视区上方。
- 审查后的最终代码另行运行 `-only-testing:PotatoTests`，以及流式界面和失败的表格用例：69 项能力／集成测试、10 项截图／状态测试、2 项界面测试全部通过，115.714 秒。表格排版和原断言未改动；不能据此断言首轮失败完全由游标接入调整解决。14 项界面用例均有通过证据，保留首轮滚动不稳定记录。
- `.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app`：0 errors；前端 `npm run build --prefix frontend` 成功，`npm test --prefix frontend` 13 项通过，`npm run test:ui --prefix frontend` 54 项通过。Web 界面首次受沙盒 Chrome 启动权限限制，正常应用权限下完整复验通过。
- `.venv/bin/python scripts/check_modules.py`、`.venv/bin/python scripts/check_docs.py`、`python -m py_compile ios/Integration/backend.py`、`git diff --check`：通过。
- Standards／Spec 分别由独立审查者核对并复核，均无未解决问题。

## Standards

固定基点 `4d25e26`，按仓库授权在提交前审查本次工作区差异，排除用户原有领域文档及研究文件。独立规范审查与复核：0 项未解决问题。已集中正文身份重置，并使用历史游标初始化订阅，避免恢复时重放整库轨迹；已有预览时底部状态明确为尚未保存。

## Spec

独立规格审查与最终复核：0 项未解决问题。#99 的共享订阅、保存前多批正文、前后台取消、恢复快照、身份失效及持久终态优先均有实现和公开边界测试；完整故障／重连／阅读矩阵保持后续任务范围。
