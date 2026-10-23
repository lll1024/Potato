# 助手会话交互、轨迹查看与事件续接验收

对应 [#85](https://github.com/lll1024/Potato/issues/85)，父规格为 [#79](https://github.com/lll1024/Potato/issues/79)。实施起点为 `fa743a7`，复用 `codex/deep-modules` 与 PR #87；同工作区的 #83、#84 分别提供资料获取/规划与偏好的公开协作接口。原有未提交领域文档不属于本次提交。

## 职责与兼容

- `travel_app.sessions` 的 `TurnRuntime` 提供接受核对、原目标停止、运行状态、历史/轨迹、搜索、载荷、改名、删除及通知。原 `web.py` / `turn_runtime.py` 的业务实现迁入会话目录，HTTP 层只校验请求并适配协议。
- 所有查看只调用本机记录公开一致读取；通知分别表达持久事实与瞬时运行状态，恢复游标、目录身份、故障与多订阅语义保持。停止与保存继续真实收束后释放名额，关闭页面不取消已接受工作。
- 前端 `main.tsx` 仅装配助手会话与旅行者偏好公开组件；会话内部协调提交不确定性、同身份核对/重试、草稿、当前会话和通知。通用 HTTP 与图标按显式共享支撑导出，不形成偏好对会话内部的依赖。
- 对话与执行轨迹保留各自显示投影，`useSessionPage` 与 `mergeSessionPage` 共同负责分页、去重、刷新与数据集失效；保留原滚动回看、搜索定位和桌面/移动布局，无视觉重设计。

## 回归与红绿证据

既有提交/停止/故障/续接/历史/搜索测试继续通过公开 HTTP/SSE 运行真实规划与 SQLite；新增 `test_session_contracts` 直接通过会话公开入口完成查看与管理。没有以假规划替换正式主流程。

新增浏览器验收 `frontend/tests/ui/session-pages.spec.ts`：

1. 桌面 1440 与移动 390：更换数据目录后丢弃迟到分页，清空旧草稿与缓存。
2. 两种布局：重叠轨迹页去重，搜索定位未加载轮次并读取完整载荷。
3. 切回会话时后台新增轮次造成历史间隙：旧游标永久跳过中间历史的回归先失败，改为保留可补齐前沿后通过。
4. 其他页面删除非当前会话：全局失效使当前分页被丢弃的回归先失败，按会话隔离失效后通过。
5. 定位请求迟到：较旧运行快照覆盖完成事实的回归先失败，按快照游标保留较新事实后通过。
6. 旧分页在新前沿发现后返回：旧页把新前沿置空的回归先失败，比较原请求 `before` 与当前前沿后通过。

以上 8 项浏览器回归全部通过。修复过程未削弱断言或扩大模型容量。

## 验证入口与适用范围

```sh
PYTHONPATH=tests .venv/bin/python -m unittest test_session_contracts test_submissions test_stop test_reconnect test_history test_trace test_search test_storage test_migration_baseline test_web_entrypoint -q
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app tests/test_session_contracts.py
npm test --prefix frontend
npm run build --prefix frontend
npm run test:ui --prefix frontend
.venv/bin/python scripts/check_modules.py
```

公开会话、HTTP/SSE、停止、故障、历史搜索、固定基线和正式启动入口共 **61 项定向后端通过**；生产与新增测试 pyright **0 错误**，前端 **13 项通过**、TypeScript / 生产构建通过，模块检查通过（剩余 54 项旧迁移条目留给 #86）。

首次完整后端运行 353 项，1 项失败、3 项错误，均在并行迁移装配、规划参数或偏好记录契约中显现，已交相关能力修复；初次完整浏览器 49 项中 47 通过、2 项上下文真实服务失败。保留[初次失败日志](session-interactions/initial-full-backend.log.gz)，最终验证结果另列下文，不能把初次运行报告为通过。

上下文浏览器两项与 #82 已记录的原夹具失败一致；使用 `git archive fa743a7` 的真实旧后端和相同输入做公开 HTTP 对照，前后均估算 **28,153 tokens > 28,000 输入预算**，轮次为 `terminated/context_budget`、零模型调用。见[精确预算对照](session-interactions/context-budget-comparison.json)及[#82 原始限制](model-context.md)。未修改夹具、扩大窗口或跳过后续断言后宣称成功。

## 双轴审查

以 `fa743a7` 为固定起点，两位独立代理分别只读规范与规格。规范初审 1 项、规格初审 2 项；规格复审另发现分页前沿竞态，均通过对应浏览器红绿验证修复。最终规范轴 0 项、规格轴 0 项；无范围外行为。审查范围为 #85 及其公开协作，不替代 #83/#84 自身算法审查或 #86 的独立阅读验收。

本次外部模型、地图和网页使用可控响应与真实 adapter，数据使用临时 SQLite 与合成旧库；这些结果证明执行与交互兼容，不代表外部旅行资料已核实，也未证明真实供应商性能变化。
