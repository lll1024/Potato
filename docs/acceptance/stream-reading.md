# 两端流式 Markdown 阅读与滚动跟随（#103）

2026-11-06，在 `codex/stream-final-answer` 从 `aa18029` 继续父规格 #97，覆盖故事 7、41–47、52；复用 PR #105。原有用户领域文档保持原样，不纳入本次提交。

## 行为与公开边界

两端继续使用现有 React Markdown／MarkdownUI，收到真实批次累计正文后重排，未闭合代码、表格、链接允许随后续正文完成。没有客户端逐字播放。保留中文、表格横向阅读、字号、系统选择及安全链接处理。Web 已有正文版本跟随，本次以桌面和窄屏交互回归确认；iOS 改为观察实际排版高度，同一轮正文增长也跟随到底部。两端上翻暂停后均可主动回到最新恢复跟随；iOS 另支持手动到底部恢复，横向表格拖动不误触发暂停。

复制使用当前展示的 Markdown 原文，不去标记或截断。当前正文、未保存正文、不完整回答、完整回答沿用各自文案；iOS 在保存／完整性标记变化时也清除旧的已复制状态，避免正文未变化时仍误称新状态已经复制。共享会话同步、规划、保存、重试和停止边界保持。

## 故事与证据

| 故事 | 验证入口 |
| --- | --- |
| 7 | Web 公开 `answer.preview` 累计事件分批更新；iOS 真实 HTTPS／SQLite 服务仅在外部模型边界通过 HTTP 屏障释放每批正文和最终响应 |
| 41、42 | 两端界面从未闭合代码到闭合代码，从表头到完整表格，从半成型链接到可打开链接；Web 断言渲染结构，iOS 实际渲染后横向滑动表格 |
| 43 | 两端长中文正文和宽表格；复用既有 iOS 中文回答、深色大字号表格截图和横向末列阅读测试 |
| 44–46 | 同轮增长后底部可见，上翻后后续批次不改变阅读位置，点击回到最新并再次增长／保存后继续跟随；iOS 横向阅读不暂停 |
| 47 | Web 两次读取系统剪贴板核对当前 Markdown 全文；iOS 复制当前正文后与公开快照核对，保存后仍要求文案恢复为“复制完整回答”，通过系统粘贴核对原文 |
| 52 | 复用现有 Web HTML／危险链接／中文裸网址安全呈现、iOS 外链及后端跨块脱敏回归；本次不增加另一套渲染或链接解释规则 |

测试入口：[Web 界面](../../frontend/tests/ui/conversation.spec.ts)、[iOS 界面](../../ios/PotatoUITests/ReadingNavigationTests.swift)、[真实模型分块夹具](../../ios/Integration/backend.py)、[中文与大字号截图](../../ios/PotatoTests/SnapshotTests.swift)。

## 复现与验证

iOS 新增测试在原实现上失败：首批长正文出现后，复制按钮留在屏幕外；同一轮后续表格也未跟随。中间偏好值方案读到零高度，改为直接观察内容几何尺寸并等待布局提交。失败复现留下的隔离活动轮次在重跑前完成释放或重启临时服务，未将该类环境污染计为功能通过。

- Web：`npm run build --prefix frontend` 成功，Markdown 呈现 13 项通过，Chrome 全量 64 项通过（1.7 分钟），含新增桌面／窄屏流式阅读两项。
- iOS 定向新增界面测试 1 项通过（36.540 秒），实际使用真实 HTTPS 分块与系统复制粘贴。
- Python 生产代码与验收夹具 pyright 均为 0 错误；模块、文档链接与差异检查通过，界面机械检查无发现。
- 后端全量 `.venv/bin/python -m unittest discover -s tests -v`：380 项通过（337.684 秒）。
- iOS 全套运行 821.198 秒：76 项能力／集成、10 项截图／状态通过；17 项界面中 16 项通过，既有 `testRestartRecoversOriginalSubmissionAndOpensSavedConversation` 1 项失败，不能称全套首跑通过。新增流式阅读用例在全套中再次通过（35.640 秒），中文／大字号、横向表格、长历史分页、复制外链、停止及未保存复制均通过。
- 该导航失败时仍在列表，控件树显示“服务或读取目标已变化，请重新刷新。”；后端原会话与回答已经保存。同一构建单独重跑通过（37.293 秒），随后 `test-without-building -only-testing:PotatoUITests/NavigationTests/testRestartRecoversOriginalSubmissionAndOpensSavedConversation -test-iterations 2` 连续两次通过（39.568／38.317 秒），共三次定向复验通过。此次未修改列表导航或共享读取规则，不把未确认根因的偶发失败归因于环境，也不将其掩盖为全套首跑成功。

## 界面证据

- [Web 桌面](stream-reading/web-desktop.png)、[Web 窄屏](stream-reading/web-mobile.png)
- [iOS 未闭合代码和长正文底部](stream-reading/ios-code.png)
- [iOS 上翻阅读与回到最新入口](stream-reading/ios-paused.png)
- [iOS 回到最新并转为已保存回答](stream-reading/ios-latest.png)

## Standards

独立审查 0 项发现。改动遵循精准修改和简单优先；阅读位置在 View 内，复制由 `AnswerView` 封装，测试通过公开协议及用户交互。无待处理的 Fowler 基线异味。

## Spec

初审 1 项 P3 文档发现，0 项功能发现：验收文档将手动到底部恢复跟随泛化到两端，实际仅 iOS 具备；已收窄描述。规格只要求主动“回到最新”恢复，无需扩展 Web 行为。

独立回核确认该问题已解决。最终 Standards 0 项、Spec 0 项未解决发现，两轴均无待处理的最严重问题。

## 证据适用范围

Web 使用 Chrome 1440／390 宽度，iOS 使用 iPhone 17 Pro／iOS 26.5 模拟器与短期 localhost HTTPS 证书；真实后端能力及临时 SQLite 保持。外部模型由可控批次替身提供。本次不构成生产模型流式实连、iOS 17、真机、私网／蜂窝或 TestFlight 证据；生产模型及两端联合验收由 #104 承担。
