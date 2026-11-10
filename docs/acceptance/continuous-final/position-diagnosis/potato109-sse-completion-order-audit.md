# SSE 完成通知顺序只读核验

结论：**可达**。现有 `/api/events` 在 ASGI 发送背压期间实际完成轮次后，会在尚未补发 `turn.finished` 前发送最新 `service.state(active_turn_id=null)`、`answer.preview(preview=null)`。因此消费者不能假定同一 SSE 连接总是先收到 `turn.finished`、随后才收到空预览。

本次只形成这一条公开反例，不修改实现，不运行 GUI，不调用付费模型、不外发、不扩展研究。脚本和日志均在 `/tmp`。

## 运行范围

- 工作树：`/Users/jarviliu/.codex/worktrees/continuous-final-117/travel`
- 分支：`codex/continuous-final-117`
- HEAD：`87a41417655ab4941341bc11c8fb32911f06bece`；本任务未改仓库。
- 阅读：AGENTS、领域指引与术语表、仓库总览、sessions/local-records 公开契约、ADR 0011，以及 `/tmp/potato-spec-109.md` 与续接状态最新段。
- 使用正式 `create_app`、真实旅行规划/上下文、真实临时 SQLite、loopback HTTP/SSE。外部模型复用现有 `StreamingModel(query_rounds=0)`，地图连接复用现有 `MapService`；没有真实外部查询。
- 唯一运输控制为外层 ASGI `send` 包装：首条 `turn.accepted` 的 `http.response.body` 发送等待外部屏障。它不访问运行对象、数据库连接、私有方法，不替换 `notifications`，不注入伪造 SSE。
- 场景为已有旧持久游标的客户端重新订阅：先从 HTTP 快照拿到当前可读正文，再从先前实际处理游标 `after=0` 补齐持久事实。这种旧游标续接符合公开恢复契约。

## 一条重现命令

在上述工作树运行：

```sh
PYTHONPATH=.:tests .venv/bin/python /tmp/potato109-sse-completion-order-audit.py
```

脚本：`/tmp/potato109-sse-completion-order-audit.py`；原始执行输出：`/tmp/potato109-sse-completion-order-audit.log`。本次退出码 0，耗时约 1.49 秒。

## 实际输出

```text
BEFORE_SSE {"cursor": 4, "active": true, "answer_text": "## 杭州旅行\n\n", "answer_revision": 3}
WHILE_SEND_BLOCKED {"history_status": "completed", "history_answer_chars": 27, "cursor": 6, "model_requests": 2}
SSE {"index": 0, "kind": "turn.accepted", "cursor": 1}
SSE {"index": 1, "kind": "request.started", "cursor": 2}
SSE {"index": 2, "kind": "request.completed", "cursor": 3}
SSE {"index": 3, "kind": "request.started", "cursor": 4}
SSE {"index": 4, "kind": "service.state", "active_turn_id": null, "answer_revision": 7}
SSE {"index": 5, "kind": "answer.preview", "preview": null, "revision": 7}
SSE {"index": 6, "kind": "progress.preview", "preview": null, "revision": 4}
SSE {"index": 7, "kind": "request.completed", "cursor": 5}
SSE {"index": 8, "kind": "turn.finished", "cursor": 6}
RESULT REACHABLE inactive_index=4 answer_null_index=5 turn_finished_index=8
```

这些 `SSE` 行来自 `httpx` 客户端实际收到的数据，非仅对发送端状态的推算。屏障解除前，通过公开 `/api/sessions/{id}` 确认状态 `completed` 且最终回答等于全部模型片段，通过 `/api/snapshot` 确认活动轮次及预览为空。脚本硬断言 `inactive_index < answer_null_index < turn_finished_index`。

## 原因与用户影响

`travel_app/sessions/runtime.py:332` 先取固定持久 batch；`:348` 逐条 yield trace，经 ASGI `await send` 让出执行机会；`:349` 至 `:367` 然后读取最新状态和预览。屏障期间产生的完成事件尚不在原 batch，`:373` 至 `:378` 的 pending 补齐发生在最新瞬时通知发送之后，故下一次循环才补到 cursor 5/6。

用户影响：已从公开恢复快照展示正文的客户端，可能先被通知当前轮次释放及正文消失，此时尚未收到用来授权保存交接的 `turn.finished`。若客户端依赖“先 finish 再 null”建立阅读接续，它会存在提前清除可读正文、之后才由历史恢复的窗口。这是本次顺序证据支持的具体风险；本次未运行 SessionAccess/浏览器/UI，因此没有将风险冒充已复现的界面闪空，也不把它归为此前已确证 HTTP-first 红窗的原因。

本次结果只证明该后端顺序可达，不决定修复应位于后端还是客户端，也不自行提出新协议或修改实现。
