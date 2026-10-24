# 全仓库归属与依赖检查

六项能力已迁入独立目录，当前职责和公开契约从[总览](README.md)进入。[architecture.json](../../architecture.json) 是机器检查的唯一归属清单：`modules` 声明目录与允许依赖，`files` 明确装配、共享支撑、验证入口及内部测试。`legacy` 为空，检查器拒绝新增迁移豁免。#80–#85 的迁移过程保留在[阶段验收索引](../acceptance/README.md)，不是当前调用规则。

## 检查与归属

```sh
.venv/bin/python scripts/check_modules.py
PYTHONPATH=tests .venv/bin/python -m unittest test_module_boundaries -v
.venv/bin/python scripts/check_docs.py
```

需要已安装 Python 开发依赖与前端 `npm ci --prefix frontend`。扫描根 Python 启动入口、全部 `travel_app/`、`scripts/`、`tests/`，前端生产代码、测试与配置；Python 使用 AST，前端使用 TypeScript AST 与项目解析规则。没有按活动目录排除的旧实现或适配。

每个后端能力的 `__init__.py` 静态声明 `__all__`，前端能力的 `index.ts` 具名导出，入口反向链接同一份契约。跨能力只导入公开入口中声明的符号；静态导入、再导出、命名空间访问、可解析动态导入和 CSS 导入都受检查，无法证明路径的动态导入失败。自身内部可直接引用本能力实现。

允许依赖：会话 → 规划、偏好、记录；规划 → 资料、上下文；上下文和偏好 → 记录。声明图与实际图都检查方向及无环。根入口与显式 `assembly` 同样只使用公开入口；共享 `support` 有明确导出，不得反向依赖业务。共享支撑保留实际复用的工具名称、模型事实采集、错误判别和常量，不持有业务运行状态。

调用方和跨能力测试按 `caller-test` 登记，通过公开能力验证行为。`internal-test` 必须明确归属六项能力之一，仅该能力内部 seam 可用；例如上下文增量回归沿用原 `TokenMeter` 计量 seam，前端呈现回归归属会话。故障注入在真实 SQLite 或外部客户端边界进行，保留原断言、容量及身份检查。

测试辅助使用 `test-support` 和显式 `__all__`，按 HTTP/进程、外部响应、上下文夹具、偏好夹具、日期及记录故障分别集中在 `tests/*_fixtures.py` 和 `tests/record_faults.py`。验证入口经这些明确辅助装配公开能力；生产、启动装配及共享生产支撑不得依赖测试支撑。工具身份只允许同一工具内协作，不豁免对业务私有实现的访问。

## 固定基线与历史复现

[基线规则](../acceptance/deep-modules-baseline.md)固定 `4e01678` 的原始观察与合成旧库。当前 [capture](../../tests/compatibility/capture.py) 和 [entrypoint](../../tests/compatibility/entrypoint.py) 只使用公开装配及显式测试支撑。重采集旧生产树时，将固定提交 `81ed92f` 的历史装配脚本导出到临时目录，以 `--entrypoint` 显式传入；历史脚本不作为当前生产或测试的内部调用路径。

结构正反例覆盖前后端、装配、共享支撑、验证入口、内部归属、禁止生产依赖测试支撑和拒绝旧豁免。结构通过证明可检查的依赖规则，职责集中、公开调用及渐进披露另由[最终集成验收](../acceptance/deep-modules-final.md)的行为与独立任务验证。
