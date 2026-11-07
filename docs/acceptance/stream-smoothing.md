# 两端短暂平滑与阅读保位（#113）

2026-11-07，在独立 `codex/stream-reading-113` 实施 [#113](https://github.com/lll1024/Potato/issues/113)，已合入 #112 的真实持久进展／折叠组件及集成 `41040f8`。范围是已收到安全文字的显示过渡、复制与阅读位置；不改变规划执行、脱敏、预算、保存结果、HTTP／SSE 协议或 CLI，不新增模型请求、订阅和轮询。

## 时序策略及边界

两端采用同一策略：初次挂载、历史、累计恢复、文本替换、模型生成完成与真实终态均直接采用已有内容。只有仍在生成的后续追加超过 **64 个字素**时短暂展开，先显示至少 16 个字素，再按界面更新推进；普通小片段没有额外定时等待。浏览器使用 `Intl.Segmenter`，Swift 使用 `Character`，保持中文、表情和家庭组合表情的完整边界。

从首个大块追加开始设定 **200ms 的绝对逻辑截止**，后续文字共用这个截止而不续期，不形成按全文长度累计的播放队列。到期时在当前可用界面帧直接赶上最新全文；可控前台 Web 时钟验证 **216ms（期限加下一帧）已全部可见**。该界面调度边界不承诺操作系统冻结或后台仍持续绘制，后台／恢复使用累计内容直接接续。

`generated`、完整响应降级、保存成功／失败、截断等真实结果不等待剩余播放。显示层只截取已经收到的安全前缀，不生成新文字，不把最终完整回答到齐后的动画作为真实流式证据。Reduce Motion 直接呈现内容；没有新增动效依赖或改变现有视觉。

停止立即取消相关显示任务并冻结当前可见前缀；当前复制取这一前缀。真实保存终态随后接管全文；保存失败继续提供未保存正文及原有提示。尝试失效、替换、服务／数据集／会话变化、退出页面和隐藏页面清理旧任务，不能让旧队列追加到新目标。

## 恢复、折叠与阅读

Web 与 iOS 的共享接收入口增加本地只读 `contentRecoveryRevision`，在接受快照和订阅的首次累计更新时推进，使显示层直接恢复内容。它不是后端字段，不持久化，不推进轨迹游标，也不接管提交或模型上下文。新尝试整体替换；恢复已有文字不会从头播放。

#112 的折叠仍根据**原始非空正文**触发，不等待显示过渡。Web `ReadingScroll` 原有父级更新快照现在补充子树文字、原生 `details.open` 与高度变化的观察，处理局部平滑和局部折叠；用户滚轮、触摸和键盘意图继续暂停跟随。iOS 在现有 SwiftUI 阅读页面内用原生 `UIScrollView` 适配抵消可见锚点上方的布局变化，不逆转真实手势或显式定位。Markdown 临时重排碰到底部不能冒充用户要求恢复跟随；用户滚动到底部或“回到最新”才恢复。

当前正文、完整／不完整／未保存回答仍使用原有复制文案，复制保留对应 Markdown 原文，进展不混入回答。未闭合代码、表格、链接、宽表格、中文阅读以及上翻后新增正文保位均由现有界面边界回归核对。

## 原生真实帧证据

本次 iPhone 17 Pro／iOS 26.5 模拟器使用真实 HTTPS、规划、上下文和临时 SQLite，只在外部模型连接释放分片与完整返回。原生录屏保留在临时文件 `/tmp/potato113-native.mp4`，仓库保存可重建增长的帧及[辅助 OCR 时间序列](stream-smoothing/native-frames.json)。这段文字过渡代码与最终构建相同；录制当时发现的折叠终态定位问题单独修复并在最终组合重新验证。

| 视频时刻 | 可见状态 | 图像 |
| --- | --- | --- |
| 48.230 秒 | 大块仅部分可见，OCR 识别约 8 个重复短语 | [第一帧](stream-smoothing/native-48.230.png) |
| 48.267 秒 | 继续增长，约 12 个 | [第二帧](stream-smoothing/native-48.267.png) |
| 48.298 秒 | 继续增长，约 21 个 | [第三帧](stream-smoothing/native-48.298.png) |
| 48.330 秒 | 接近该块尾部，约 25 个 | [第四帧](stream-smoothing/native-48.330.png) |
| 48.382 秒 | “大块末尾。”完整可见，仍为当前未保存正文 | [第五帧](stream-smoothing/native-48.382.png) |

从首个采样的可见部分到尾部出现约 **152ms**。OCR 仅辅助定位，表情和分行可能导致识别计数与原文不同，不据此断言精确字符数；截图可以人工核对。外部模型完整返回仍被屏障扣留，随后才释放小片段和真实终态，不能把这段过渡解释为全文到齐后的播放。[后端可用更新](stream-smoothing/native-backend-timeline.jsonl.gz)与界面帧分别记录，录屏启动时钟和主机时钟有偏移，未把两者生硬当作同一时刻。

## 验证与失败记录

后端业务实现未因本票改变；主要验证沿已确认的 Web 界面与公开 `SessionAccess`，真实原生界面仍经公开 HTTP／SSE 和 SQLite。最终原生组合 **18 项通过，0 跳过**：13 项能力回归、5 项界面回归，执行总耗时 151.385 秒。见[执行摘要](stream-smoothing/ios-summary.json)、[完整日志](stream-smoothing/ios-final.log.gz)、[生成中](stream-smoothing/ios-burst-generating.png)、[折叠保位](stream-smoothing/ios-fold-paused.png)、[回到最新](stream-smoothing/ios-latest.png)。覆盖前后台恢复、大块后小片段、真实完成、停止后正常保存／SQLite 保存失败及未闭合 Markdown 阅读。

最终 Web 对话完整回归 **35 项通过，37.8 秒**，见[日志](stream-smoothing/web-final.log.gz)。其中可控时间直接记录追加的[三个可见长度样本](stream-smoothing/web-bounded-growth.json)：0ms／100ms／216ms；后续大块共用原截止，小片段即时，停止前缀与最终 Markdown 全文分别核对。两种宽度的真实折叠组件保位截图：[桌面](stream-smoothing/web-fold-1440.png)、[移动](stream-smoothing/web-fold-390.png)。前端生产构建、13 项呈现单测、模块归属、公开文档链接和差异检查均通过。

本票保留以下失败，不将首跑改写为通过：

- [Web 红灯](stream-smoothing/web-red.log.gz)：大块直接全文出现，新增时钟断言失败；实现有界过渡后通过。
- [首次完整 Web 运行](stream-smoothing/web-first-full-failure.log.gz)：29／31 通过，两种宽度的自动跟随失败。布局自动滚动被当作用户上翻，已区分实际阅读意图并复验。
- [原生首次最终组合](stream-smoothing/ios-first-failure.log.gz)：13 项能力及 4 项界面通过，折叠保位用例最后“回到最新”按钮消失。原因是 Markdown 临时重排触底误开启跟随；修复后完整 18 项重新通过。
- [新增严格 Web 保位用例](stream-smoothing/web-unsettled-frame-failure.log.gz)：34／35 通过。人工 `scrollTop` 后立即程序收合，尚未提交可见帧与滚动事件；用例改为先提交两个界面帧，再以同一 **3px** 阈值检查折叠，不放宽阈值。桌面／移动各连续三次、共 [6 项通过](stream-smoothing/web-fold-stability.log.gz)。初期滚轮脚本参数错误不作为产品通过或失败证据。

当前真实模型的完整 Web／iOS 查询进展联合验收由 #117 在后续全部实施后运行；本票可控分片与录屏不冒充真实上游输出。真机、iOS 17、TestFlight 和私网／蜂窝没有在本票实跑。

## 覆盖与复现

| 父规格故事 | 本次依据 |
| --- | --- |
| 7、8、11 | 小片段即时追加、短正文正常完成，现有无换行／无查询回归保留 |
| 9、10 | Web 受控时间的正文和进展期限／不续期、原生连续帧 |
| 12 | Web 累计快照直达、SessionAccess 前后台／断线恢复、原生恢复界面 |
| 42 | Web 队列中停止并复制可见前缀；原生停止及真实保存／未保存交叉 |
| 47、48 | 部分 Markdown、安全链接、宽表格、中文及组合表情；按字素取前缀 |
| 49、50、51、52 | 两种 Web 宽度与原生长文跟随／上翻／回最新、真实进展折叠保位 |
| 53、54 | 当前可见 Markdown 及最终全文复制，历史／未完成／未保存文案保持 |

```sh
npm run build --prefix frontend
npm test --prefix frontend
npm run test:ui --prefix frontend -- tests/ui/conversation.spec.ts
.venv/bin/python scripts/check_modules.py
.venv/bin/python scripts/check_docs.py
```

iOS 按 [README](../../ios/README.md) 启动独立 `Integration/backend.py` 后运行 `PotatoTests/AnswerStreamingTests` 与本票五个 `ReadingNavigationTests`：`testAnswerReallyGrowsBeforeCompletionAndThenBecomesSavedHistory()`、`testLargeBurstThenSmallAppendKeepsRealCompletionAndCopy()`、`testProgressFoldingPreservesPausedReadingAndLatestAction()`、`testStoppedStreamFreezesThenShowsSavedOrUnsavedCopy()`、`testStreamingMarkdownFollowsGrowthAndPreservesReadingPosition()`。使用干净夹具重跑，SQLite 故障场景不能沿用为下一次正常接受环境。
