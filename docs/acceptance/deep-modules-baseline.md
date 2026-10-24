# #80：行为基线与分阶段迁移校验

固定实际生产基线为 `4e01678fdb036f034d72a858fadb226ea239a0c1`（2026-10-23 实施前核对 `origin/main` 与本地一致）。采集使用 `git archive` 得到的独立代码树；模型、地图、网页均由本机脚本化 HTTP/MCP 响应提供，SDK、生产 adapter、规划循环、上下文、SQLite、偏好后台和正式根入口保持真实。没有读取个人数据库或 `.env`。

## 数据与运行

[夹具目录](../../tests/fixtures/deep-modules/)保留原始 `behavior.json.gz`、基线生成的 `legacy.sql.gz` 及 `manifest.json`。manifest 记录基线提交、生产源码内容摘要、SQL 压缩夹具 SHA-256 及续聊身份。SQL 由基线真实服务生成后 `iterdump` 导出；测试在临时目录恢复真实 SQLite，不手工拼造旧 schema、摘要或偏好。

本次实现侧的原始采集单独保存为 [current-behavior.json.gz](deep-modules-baseline/current-behavior.json.gz)，源码指纹及对照结果见 [current-source.json](deep-modules-baseline/current-source.json)。两侧记录均保留，归一化比较完全相同；该实现侧指纹对应提交前工作区中的生产源码。

正式入口仍要求存在前端构建产物。独立复现（项目已安装 requirements-dev 和前端依赖）：

```sh
npm run build --prefix frontend
git archive --format=tar --output=/tmp/potato-80-baseline.tar 4e01678fdb036f034d72a858fadb226ea239a0c1
mkdir -p /tmp/potato-80-baseline
tar -xf /tmp/potato-80-baseline.tar -C /tmp/potato-80-baseline
cp -R frontend/dist /tmp/potato-80-baseline/frontend/dist
git show 81ed92f:tests/compatibility/entrypoint.py > /tmp/potato-80-entrypoint.py
PYTHONPATH=tests .venv/bin/python -m compatibility.capture \
  --entrypoint /tmp/potato-80-entrypoint.py \
  --root /tmp/potato-80-baseline \
  --source-commit 4e01678fdb036f034d72a858fadb226ea239a0c1 \
  --output /tmp/potato-80-regenerated
PYTHONPATH=tests .venv/bin/python -m unittest test_migration_baseline -v
```

重采集的身份及墙钟时间可以不同。日常对照只读取提交的基线夹具；需要调查差异时另行采集到临时目录，保持固定夹具不变。`--root` 可指定任意待比较代码树；工作区采集的 `--source-commit` 使用 `working-tree`，不能伪称基线提交。

## 保留的观察

| 场景 | 可核对事实 |
| --- | --- |
| 正常 Web | 接受及提交核对、地图和网页查询、工具结果、两次模型请求、回答、持久 SSE 与快照一致 |
| 有效上下文与偏好 | 40,000 token 配置下真实产生并采用摘要、随轮次保存；闲置时钟推进后后台提取“不吃辣”并保存 |
| 失败 | 模型 HTTP 401，保留失败请求、错误载荷及 `model_error` 终态 |
| 停止 | 在地图真实调用中停止，名额仍占用且拒绝新轮次；返回后保存真实工具结果并以 `user_stop` 结束 |
| 强制退出与重启 | 模型在途时杀死服务；重启仅追加恢复事实，未完成请求保持未知且不自动重放；原提交核对身份保持 |
| 旧数据续聊 | 加载基线 schema、轨迹、载荷、偏好和有效摘要；续聊模型输入含保存摘要、原酒店、已确定旅行日期与偏好，旧轮次保持一致 |
| CLI | 真实 `agent.py` 在同一进程完成两次输入及退出；记录 stdout、退出码、模型可见请求和外部查询，不扩大持久化范围 |

## 归一化与独立检查

[compare.py](../../tests/compatibility/compare.py) 是可执行规则。随机会话、轮次、请求、工具、载荷、重试组、事件与偏好身份按真实实体首次出现及主体内顺序稳定命名；相同身份的全部引用统一映射。时间戳字段和耗时替换为明确占位，`None` 保持未知；旅行日期、日期解释时钟、工具参数、输入内容、模型参数、用量、状态及原因保留原值。嵌套 JSON 文本只改变序列化格式及上述非行为字段。

持久事件游标及全局 sequence 在对照中不比较其绝对值；同一主体事件按原序比较，独立工具间允许完成次序不同。原始采集完整保留所有游标、序号和时间。归一化前独立验证：游标单调且无重复、会话序列连续、SSE 等于持久快照、身份和载荷引用有效、工具身份对应模型提出的调用、协议内工具结果配对、接受先于请求、请求完成先于工具登记、实际工具开始先于返回、返回先于轮次完成。反例主动破坏身份及因果顺序，必须失败。

归一化只处理真实记录元数据，模型消息及工具参数内同名的 `sequence`、`cursor`、`duration_ms` 和 `timestamp` 字段仍比较原值。额外反例验证最终模型返回先于轮次完成、前段工具全部结束后才启动下一模型请求。

这些对照证明固定合成场景的行为兼容，不能替代真实供应商质量、性能结论，也不证明六模块深化已完成。并发、重试、压缩故障、事务故障、停止及恢复的其他边界继续由现有回归覆盖；未改变原断言、容量或供应商间隔。

## 校验结果

实施前：后端 320 项全部通过；pyright 零错误；前端 13 项全部通过，TypeScript 与生产构建通过。原始基线及本次验证日志见 [deep-modules-baseline/](deep-modules-baseline/)。最终后端全量 336 项全部通过（315.378 秒）；生产与新增工具/采集代码 pyright 零错误，前端 13 项及生产构建通过，实际仓库依赖检查通过。新增 HTTP/CLI/旧库与结构定向测试 15 项通过。Standards 与 Spec 双轴复核均为 0 项遗留，详见[独立审查记录](deep-modules-baseline/code-review.md)。最终原始后端日志为 [final-tests.log.gz](deep-modules-baseline/final-tests.log.gz)。

已接入的记录值与中止类型范围见[当前契约](../modules/local-records.md)，文件归属及临时适配移除条件见[迁移清单](../modules/migration.md)。ADR 0011 的六模块完整实现仍按 #81–#86 分阶段推进。
