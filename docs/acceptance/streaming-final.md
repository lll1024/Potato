# 真实模型与 Web／iOS 联合验收（#104）

2026-11-06，延续 `codex/stream-final-answer` 与 PR #105，审查基点为 `30238ba`。范围是父规格 [#97](https://github.com/lll1024/Potato/issues/97) 的最终集成验收及集成中发现的问题；原有未提交领域文档与研究材料不纳入本次提交。逐条覆盖见 [58 条故事映射](streaming-final-mapping.md)。

## 环境与证据边界

真实服务使用项目 `.env` 当前实际配置：`api.deepseek.com` 的 `deepseek-v4-flash`，Anthropic Python SDK 1.11.0。后端运行 macOS 27.0、Python 3.14.7、真实规划／上下文／会话和隔离的临时 SQLite，连接与查询发现采用生产装配。验收提示仅要求通用行前准备清单，未要求实时查询；不以本次正文证明旅行资料查询质量。

Web 使用 Chrome 与生产构建；iOS 使用 Swift 6、Xcode 27.2 SDK 和 iPhone 17 Pro／iOS 26.5 模拟器，连接同一 localhost HTTPS 服务。仅测试证书安装在模拟器，保留系统证书校验；证书与密钥不随仓库提交。真实服务未插入分块、放行屏障、固定输出延迟或响应替身。两端观察同一真实提交和作答请求。

可控外部模型证据来自公开 HTTP／SSE、真实后端和 SQLite，iOS 能力证据来自公开 `SessionAccess`、真实 GRDB 与 HTTP 传输故障注入；界面证据来自 Chrome 和模拟器。三类证据分别解释。iOS 17 实跑、实际 iPhone、私人网络／蜂窝与 TestFlight 没有在本次验收中完成，仍按 [#95 交付限制](ios-delivery.md) 解释。

## 实连发现与修复

首次真实作答返回合法 `thinking` 和 `text` 块，结束原因是 `end_turn`；普通作答的校验却将任何非 `text` 块都报告为“最终作答不能包含工具调用”，使轮次成为 `failed / model_error / UNKNOWN`。脱敏 [首次失败事实](streaming-final/initial-failure.json) 只保留块类型、错误及请求元数据，不收录内部推理内容。首次 Web／iOS 联合运行的最终保存断言失败，不能算作成功实连。

通过公开 HTTP 最小复现 `test_answer_accepts_reasoning_blocks_without_exposing_them_as_body`，原实现 0.243 秒失败（实际 `failed`，预期 `completed`）。修复仅允许普通作答的合法 `thinking`／`redacted_thinking` 块；正文流和最终聊天回答仍只提取 `text`，不允许工具调用。完整协议及执行事实保留原有规则。第 64 次预算收尾的纯文本、完整结束校验没有放宽。修复后首轮流式专项 20 项通过，包含工具隔离、停止、重试、脱敏与预算既有回归。独立规格审查另复现只有推理、没有正文也可能被误报为完成；新增公开 HTTP 用例在 0.255 秒失败后，增加非空文本检查，按既有 `EMPTY_RESPONSE` 分类和有限重试处理。下一次作答必须交付真实正文后才保存，重试请求输入保持相同。

一次定向 `test-without-building` 因筛选标识实际执行 0 项，另一轮 Web 因没有手机发起会话而等待超时；均排除在通过证据外。后续使用带 `()` 的完整 XCTest 标识，并核对实际测试数量。

## 真实分批展示结果

修复后实连由 iOS 发起，Web 同时打开新会话。两端共同观察会话 `e01a0a852e1d431b888f65183022dc8a`、轮次 `0a8fcd978de743e7818ea02f71568769`。手机在 2026-11-06 22:31:39／22:31:40（Asia/Shanghai）记录到预览 297／560 字，revision 190／355；两次均为 `generating / stream`，历史仍 `running`、`answer=null`，实际作答请求仍运行。两次本机可访问性树中的正文增长超过 50 字，随后转为“复制完整回答”。

Web 同样在保存前读取两批实际 DOM 正文增长，逐次核对上述公开状态；保存后要求 `completed`，并把系统剪贴板全文与保存历史逐字比较。Web 用例通过（39.4 秒），iOS 实际执行 1 项通过（51.320 秒）。未发生能力不支持降级；此次仅证明当前服务及当前模型配置在该请求中支持流式，不能推广到全部供应商。

原始证据：[iOS 两次观察 JSON](streaming-final/ios-observations.json)、[iOS 执行摘要](streaming-final/ios-live-result.json)、[Web 观察摘要](streaming-final/web-observations.json)。本轮 Web 默认列表报告器没有将内存 JSON 附件单独落盘，因此摘要明确使用原截图文件时间及实际通过的断言，不补造逐批字符数；验收入口现已显式写入 `live-evidence.json`，供后续复现。

截图：[Web 第一批](streaming-final/web-live-batch-1.png)、[Web 第二批](streaming-final/web-live-batch-2.png)、[Web 保存后](streaming-final/web-live-saved.png)；[iOS 第一批](streaming-final/ios-first.png)、[iOS 第二批](streaming-final/ios-second.png)、[iOS 保存后](streaming-final/ios-saved.png)。

## 交叉场景与兼容

- 新增 `test_reconnect_during_retry_then_stop_and_failed_save_preserves_real_body`：旧正文已出现后收到 429，观察空 `retrying`，关闭订阅，快照与新订阅均恢复正在重试；新尝试整体替换，已完成查询只执行一次。之后停止，下一轮返回 409；真实响应返回而 SQLite `turn.finished` 保存失败后，保留新尝试全文、历史仍为原运行事实且无回答，下一轮返回 503。实际模型请求恰好四次，没有因客户端恢复另发请求。
- Web 与 SessionAccess 的断线恢复矩阵新增 `retrying`：清除旧正文、恢复重试状态、采用新尝试正文，旧连接的高次序迟到正文和状态仍失效。SessionAccess 继续验证返回列表再进入相同会话。
- 复用并重新运行 #102 的停止／迟到正文／最终保存与未保存分支，两端冻结期间不追加，原轮次释放后才采用真实保存或可复制的未保存正文；保存故障、阅读和复制不是分别孤立推断。
- 旧库与续聊使用固定提交的合成旧库回归；CLI 通过实际入口与固定行为基线验证完整输出。提交核对、未确认重试、草稿、偏好、全局单轮及持久游标继续由完整回归覆盖，不读取或修改日常用户数据库。

## 复现入口

先按 [iOS README](../../ios/README.md) 准备可信任的短期 localhost 证书及模拟器，构建前端和 iOS 测试工程。真实模型验收显式调用当前 `.env` 的服务，可能产生模型费用；普通回归跳过此项。

```sh
npm run build --prefix frontend
.venv/bin/python tests/live_answer_eval.py \
  --cert /tmp/potato-ios-tls/cert.pem --key /tmp/potato-ios-tls/key.pem
```

确认 `https://localhost:18499/__acceptance__/environment` 返回实际模型元数据后，先运行 Web 观察端，再在另一个终端运行手机发起端。每轮使用新启动的临时服务，避免选到前一轮会话。

```sh
POTATO_LIVE_ANSWER=1 npm run test:ui --prefix frontend -- tests/ui/answer-model-live.spec.ts
xcodebuild test -project ios/Potato.xcodeproj -scheme Potato \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  '-only-testing:PotatoUITests/ReadingNavigationTests/testCurrentConfiguredModelShowsBatchesBeforeSavedHistory()' \
  -skipMacroValidation CODE_SIGNING_ALLOWED=NO
```

两端各记录两次可见正文增长，同时读取公开快照与历史，要求预览为 `generating / stream`、轮次为 `running`、回答为空且有一个模型请求仍运行；最终确认完整保存。Web 额外核对系统剪贴板全文与历史回答一致。JSON 与截图保存在 Playwright 输出及 xcresult 附件。运行结束停止真实模型验收服务，再运行常规 iOS 全套，避免再次调用实际服务。

## 本轮最终回归

- 后端最终全量 `.venv/bin/python -m unittest discover -s tests -v`：383 项通过，337.016 秒；[原始日志](streaming-final/backend.log.gz)。新增交叉用例单独通过，3.345 秒；推理块最小复现由失败转为通过，最终修复后 21 项流式专项通过，20.826 秒（[原始红灯](streaming-final/reasoning-red.log.gz)、[专项日志](streaming-final/streaming.log.gz)）。
- 前端 `npm run build --prefix frontend` 构建成功，`npm test --prefix frontend` 13 项通过；Chrome 最终常规全量 65 项通过（1.8 分钟），显式真实模型入口 1 项按设计跳过（[原始日志](streaming-final/web.log.gz)）。真实模型的单独联合运行实际 1 项通过，39.4 秒。
- iOS 测试构建成功；真实模型界面实际 1 项通过，51.320 秒（[实连日志](streaming-final/ios-live.log.gz)）。常规完整回归 783.207 秒：76 项能力／集成和 10 项截图／状态通过；18 项界面中 16 项通过、1 项真实模型入口按设计跳过、1 项既有完整复制粘贴失败（[完整结果](streaming-final/ios-full-result.json)、[日志](streaming-final/ios-full.log.gz)）。整体结果是失败，不能称首跑全绿。复制按钮显示成功后，系统粘贴得到了另一段短文本，原日志无关剪贴板文字已省略；没有据此认定为产品缺陷或环境原因，未修改复制实现或该用例断言。同一构建定向连续两次复验通过（228.605／223.264 秒，[执行摘要](streaming-final/ios-copy-recheck-result.json)、[日志](streaming-final/ios-copy-recheck.log.gz)）；没有据此改写首跑失败或声称偶发原因已被消除。其余停止／未保存复制、流式阅读及原提交恢复导航在本轮已通过。
- 生产 Python 与验收装配 pyright 均为 0 错误（iOS 外部夹具使用 `PYTHONPATH=tests`）；模块归属、公开文档链接及 `git diff --check` 通过。

审查修复后的首轮 383 项全量在 346.059 秒结束，382 项通过、1 项失败：既有日期测试把含随机身份字段的资料对象整体转字符串检查，某次 `tool_call_id` 恰包含 `2027`，实际资料中没有该年份（[原始失败](streaming-final/backend-assertion-failure.log.gz)）。断言已收窄到用户展示的 `title / entries / notes`，原有具体日期、提示、载荷读取和续聊断言全部保留；定向用例 0.763 秒通过，两轴独立复核确认未削弱规格验证。最后一轮完整 383 项通过，337.016 秒。

本轮未改动固定基线的规范化、预期值或旧 gzip 数据。旧库、CLI、提交核对、草稿、偏好与游标按本次完整回归中的原有断言验证。

## Standards

独立规范审查 0 项未解决发现；生产修复局限于普通作答的块类型校验，测试和验收采用已确认公开边界，预算收尾规则保持。审查建议将“真实工具”改称“工具隔离”，已修正文案，避免混淆可控查询响应与供应商实连。空正文检查和相应测试也经规范轴独立复核通过。

## Spec

初审发现 1 项 P2：只有推理而无正文可能保存为空白完成（[原始失败日志](streaming-final/empty-reasoning-red.log.gz)）。已补公开 HTTP 红绿回归并增加非空正文检查；独立复核确认解决，两项相关公开 HTTP 回归通过（0.982 秒）。最终 Spec 0 项未解决发现。58 行故事映射和引用、实连与模拟器证据及限制说明已核对，无范围扩张。

最终汇总：Standards 0 项，Spec 0 项未解决发现，两轴均无待处理的最严重问题。#104 的实连、映射、兼容和交叉验收已交付；父规格 #97 保持打开，未执行合并。
