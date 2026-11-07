# 安全小片段正文连续显示（#110）

2026-11-07，在基线 `df2c95b` 的独立 `codex/streaming-body-110` 工作树实施。范围为父规格 [#109](https://github.com/lll1024/Potato/issues/109) 的 [#110](https://github.com/lll1024/Potato/issues/110)：正文随真实生成持续显示，不加入平滑播放。查询进展、保存回看与最终真实查询双端联合验收由关联任务分别交付。

## 默认环境与诊断结论

用户确认原反馈环境为“本机默认”，未提供原始输入。只读核对发现日常服务为原仓库中运行的 Python 3.14.7 `web.py`，监听 `127.0.0.1:8000`；原仓库当前分支为 `codex/fix-empty-model-response`，HEAD `23b259e`。该进程的具体加载时刻与前端构建摘要没有可靠启动记录，不能据当前工作区 HEAD 推断进程恰好运行同一提交。本次未停止该服务或读取、更改日常会话数据库。

当前 `.env` 使用 `api.deepseek.com` 的 `deepseek-v4-flash`。本次验收重新构建本工作树 Web 和 iOS（Swift 6、Xcode 27.2、iPhone 17 Pro／iOS 26.5 模拟器），通过隔离临时 SQLite 和测试端口复验同类长回答。未实跑真机、iOS 17、TestFlight 或私人网络／蜂窝；没有宣称复现原始输入。

公开 HTTP 最小复现发现一个可重复的积压来源：预览脱敏把任何未闭合普通引号、Markdown `[` 标签和普通 `{` 备注都视作敏感结构，后续安全中文持续被扣留。新增 `test_unclosed_reading_punctuation_does_not_buffer_safe_chinese` 的三个子场景在原实现均失败，见[红灯记录](continuous-body/red.log.gz)。修复只让实际 JSON、未闭合带引号凭据值、尾部 URL、完整凭据头和已知密钥跨片前缀继续等待；普通阅读标点不阻止安全后续文字。本次未把该机制直接认定为用户原反馈的唯一根因。

## 可控外部连接、真实后端与两端界面

后端运行真实规划、上下文、会话和 SQLite，只在外部模型／查询连接控制分片与完整返回。公开 HTTP／SSE 的既有用例继续覆盖完整协议、双订阅、停止、重试、预算收尾、保存失败和重启。无查询中文对照没有工具执行；含查询场景最终回答按相同路径增长。

`ios/Integration/backend.py` 的 `continuous` 模式提供九批无换行中文，包含一个一直未闭合的普通双引号及表情。每一步仅释放外部模型一批，最后另一步才允许模型完整返回；本次并未增加客户端定时播放。现有控制路由先注册，再按需挂载本树 Web 构建，两端可读取同一真实临时会话。

- iOS 经公开 `SessionAccess` 和真实 HTTPS／SQLite，实际执行 `testContinuousChineseWithUnclosedQuoteIsReadableBeforeModelReturns()` **1 项通过，29.308 秒**。九次可见文字增长时始终为 `generating`、显示“尚未保存”和“复制当前正文”，最后才转为“复制完整回答”。[执行摘要](continuous-body/ios-result.json)、[九次界面观测](continuous-body/ios-visible.json)、[日志](continuous-body/ios.log.gz)、[生成中截图](continuous-body/ios-generating.png)、[保存后截图](continuous-body/ios-saved.png)。Swift 字符数将表情按字符计数。
- 同轮 Chrome 观察留下九次可见增长，轮次与 iOS 一致，均为 `generating`；见[同轮 Web 观测](continuous-body/same-turn-web-visible.json)。随后浏览器被关闭，观察脚本报页面关闭，因此这次脚本整体不计为通过。
- 另一次独立 Chrome 真后端运行完整通过：九次增长逐次核对外部分片屏障仍未返回、公开历史 `answer=null`、当前正文可见；最后才放行完整返回与原子保存，复制全文与真实历史逐字一致。见[九批及终态 JSON](continuous-body/web-controlled.json)、[生成中截图](continuous-body/web-generating.png)、[保存后截图](continuous-body/web-saved.png)。Web 使用 UTF-16 长度，不能直接与 Swift 字符数比较。
- 新增既有 Web 界面边界回归 `无换行中文与未闭合引号按连续小片段展示并保留最终原文`，九批当前正文与最终历史原文一致，实际 **1 项通过，1.2 秒**，见[日志](continuous-body/web-regression.log)。该稳定界面回归使用已有公开事件夹具，与上述真实 HTTP／SQLite 界面证据分别解释。

首次 iOS 运行失败，原因是本票最初把静态页面挂载在测试控制路由之前，控制请求被静态路由返回 405／404，未进入有效正文验收；[原始失败日志](continuous-body/ios-first-failure.log.gz)保留。修正挂载顺序后使用同一构建重跑得到上述 1 项通过，没有把首跑失败改写为成功。

## 当前真实模型的时序对照

独立 `127.0.0.1:18510` 服务使用生产 `configured_planning`、`create_app(resources=...)` 和临时 SQLite；在外部 Anthropic SDK 连接包裹层仅记录类型、累计字符数及 UTC 时刻，不记录模型内部推理、凭据、工具参数或原始载荷，不插入固定延迟、分片或响应替身。Chrome 使用当前本树构建，请求无外部查询的一千五百字中文行前准备清单。

[模型分片时间序列](continuous-body/real-model-timeline.jsonl.gz)、[Web 与后端预览采样](continuous-body/real-web-timeline.json)、[公开终态与请求事实](continuous-body/real-outcome.json)分开保留。采样先读取快照，再读取 DOM，两次读数不是原子操作；空预览旁的最初 8 字是等待正文的占位，不计入真实正文增长。Markdown 渲染后字符数也不同于原始文本。

本轮结果（Asia/Shanghai）：

- 16:10:54.658 开始原两阶段流程的首个完整响应请求；16:11:08.176 返回 1847 字。按当时仅最终作答预览的语义，这段查询阶段文字未进入正文区，之后开始作答请求。
- 16:11:24.980 收到第一段真正作答正文，至 16:11:28.281 收到末段，共 **1102 个 SDK 文本增量、1665 字原文，约 3.302 秒**；供应商前段等待与后段快速生成可从模型时序直接区分。
- Web 在模型完整返回前有 **27 次后台预览非空的可见增长观测**，从 96 字增长到 1480 字；采样间最大增量 88 字。16:11:28.285 模型完整返回，16:11:28.295 保存完成，随后显示完整历史。[首段界面](continuous-body/real-web-first.png)、[保存后界面](continuous-body/real-web-saved.png)。没有等到全文到齐才播放。

此对照证明本次真实服务的模型产出、后端可用正文与实际 Chrome 可见增长能够分别定位，也显示当前两阶段和供应商正文首段前仍有等待。它不保证供应商始终均匀返回，不证明原始输入已被复现，也不代替后续 #117 含真实资料获取的双端全过程验收。

## 回归与复现

最终公开 HTTP／SSE 流式专项 **22 项通过，27.942 秒**，见[日志](continuous-body/streaming.log.gz)。本树前端生产构建、修改实现类型检查、全仓库模块归属、公开文档链接与 `git diff --check` 均通过。

公开 HTTP 测试新增普通未闭合阅读标点，保留跨片已知密钥、认证头、带引号配置、带引号 JSON 字段片段、尾部敏感 URL、多文本块密钥、中文和表情的真实脱敏断言。最终历史必须与完成后的真实正文一致。

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_answer_streaming -v
npm run test:ui --prefix frontend -- tests/ui/conversation.spec.ts -g '无换行中文与未闭合引号'
npm run build --prefix frontend
.venv/bin/python scripts/check_modules.py
.venv/bin/python scripts/check_docs.py
```

模拟器按 [iOS README](../../ios/README.md) 启动本树 `ios/Integration/backend.py` 的 HTTPS 服务，定向执行上述 XCTest 标识（带 `()`）；服务依旧只替换外部连接，不接触日常数据库。真实模型额外观测装配与浏览器脚本保留在本轮临时目录 `/tmp/potato110-live.py`、`/tmp/potato110-real-web.cjs`，正式联合验收应基于后续完整实施重新运行，不能直接继承本轮结论。
