# 旅行者偏好能力迁移验收（#84）

本任务保持 [ADR 0002](../adr/0002-cross-session-traveler-preferences.md) 的行为，将管理、背景、资格和后台生命周期归入旅行者偏好能力。规格为 [#84](https://github.com/lll1024/Potato/issues/84)，公开使用见[偏好契约](../modules/preferences.md)。

## 已实现的边界

- 后端 `travel_app.preferences.Preferences` 拥有读取、编辑、删除、规划背景、停止、后台启动/退出及进程恢复规则。会话不再筛选 eligible 或协调提取、失败、提交。
- 本机记录以 `PreferenceState` 一致快照和 `preferences.write(expected, replacement)` 条件写入完成持久操作，不导入偏好实现、不暴露连接或 SQL。比较与偏好、歧义、保护、输入处理状态写入共用短事务；新输入和人工管理都能使旧条件失效。
- 接受输入与待处理登记、完成轮次与结束时间仍分别共同保存；SQLite schema v1 及已保存数据保持。
- 前端实现与样式移到 `frontend/src/preferences/`，从 `index.ts` 导出管理组件和类型。透明入口、版本冲突、编辑与删除交互保持。
- 旧偏好生产路径与 #84 对应豁免已移除；偏好 HTTP/进程测试按跨能力回归登记，业务依赖只走公开入口。

## 验证

迁移前运行 48 项偏好回归全部通过。迁移后定向回归包括原测试与两项新增公开接口测试，共 50 项全部通过；其中覆盖：

- 3599 秒不提取，3600 秒后提取；新输入重置、运行和停止中跳过，其他合格会话继续处理，前台回答不等待提取。
- 来源、类别、结构与不完整输出校验，输入顺序及去重、合并与歧义，管理水位保护，聊天删除保留偏好。
- 三次请求上限、30/120 秒退避、超时、存储故障停止接受输入及事务回滚。
- 真实服务 kill/restart：未完成与已保存任务、管理冲突、新输入、停止、重试次数和提交前锁住事务。
- 通过偏好公开类单独启动/停止后台并管理背景；通过公开记录接口验证新输入与人工管理后的旧条件写入被拒绝。

可重复命令：

```sh
PYTHONPATH=tests .venv/bin/python -m unittest discover -s tests -p 'test_preference*.py' -q
.venv/bin/pyright --pythonpath .venv/bin/python agent.py web.py travel_app
.venv/bin/python scripts/check_modules.py
PYTHONPATH=tests .venv/bin/python -m unittest test_module_boundaries -v
npm test --prefix frontend
npm run build --prefix frontend
npm run test:ui --prefix frontend -- preferences.spec.ts preferences-management.spec.ts
```

前端 13 项测试、生产构建、偏好浏览器 3 项、生产代码 pyright（0 错误）、模块依赖检查及 8 项结构反例均通过。浏览器首次在沙箱内启动受限，获准启动本机 Chrome 后三项全部通过。以任务开始时 `fa743a7b18d01a5a89d52144c1e21e18fa454852` 为固定点，仅审查 #84 增量：Standards 与 Spec 两轴各 0 项发现；规格审查独立复跑两项公开测试通过。首轮全仓库运行 353 项（396.419 秒），出现一项遗漏旧偏好测试入口与三项 #83 迁移相关失败；前者已定向复现并改用公开记录值，公开记录和偏好契约四项复验通过，双轴复审仍各 0 项发现。后续共同全量结果在本任务 Issue 完成记录中补充，不将首轮报告为全量通过。自然语言提取的模型语义限制沿用[原验收](traveler-preferences.md)；本次迁移不以替身测试宣称真实模型能完全区分所有临时要求和长期偏好。独立阅读及完整六能力验收属于 #86。
