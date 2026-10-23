# 六模块迁移状态与检查规则

实施规格为 [#79](https://github.com/lll1024/Potato/issues/79)，当前阶段为 [#80](https://github.com/lll1024/Potato/issues/80)：固定行为基线及建立校验。六项能力仍主要运行在浅层包中；目录、接口草图或检查通过均不代表能力已完成封装。

## 归属与阶段

机器可检查的唯一清单为根目录 [architecture.json](../../architecture.json)。`modules` 声明目标目录及依赖，`files` 声明检查工具、跨能力测试与共享支撑的身份；`legacy` 逐文件登记旧实现归属、原有精确导入及移除任务。

| 能力 | 目标后端目录 | 当前状态 | 迁移任务 |
| --- | --- | --- | --- |
| 助手会话 | `travel_app/sessions/` | 仍由 web、turn_runtime 及相关前端承担 | #81、#85 |
| 旅行规划 | `travel_app/planning/` | 仍由 agent、travel_dates、model_retry 承担 | #81–#83 |
| 资料获取 | `travel_app/retrieval/` | 仍由 amap、exa、travel_tools 及 HTTP adapter 承担 | #83 |
| 模型上下文 | `travel_app/context/` | 仍由 model_context、context_maintenance、context_summary、session_history 承担 | #82 |
| 旅行者偏好 | `travel_app/preferences/` | 仍由 preferences、turn_runtime 及 PreferencesView 承担 | #84 |
| 本机记录 | `travel_app/local_records/` | 仅只读上下文保存材料和记录中止类型已接入；SQLite 仍在 store/preferences | #81、#84、#85 |

相关前端目标目录位于 `frontend/src/`，使用同名能力目录。已有前端文件逐项列在清单，现阶段没有迁移前端或改变 UI。当前真实行为入口仍从[后端职责地图](../backend-map.md)定位；已落地的记录值见[本机记录契约](local-records.md)。后续任务实际迁入能力时再增加对应公开契约，避免把设计写成现有能力。

## 检查入口

```sh
.venv/bin/python scripts/check_modules.py
PYTHONPATH=tests .venv/bin/python -m unittest test_module_boundaries -v
```

扫描根 Python 入口、全部 `travel_app/`、`scripts/`、`tests/` 及前端生产源码、前端验证代码和配置文件。Python 使用 AST；前端使用项目安装的 TypeScript AST 和 tsconfig 解析。需要先安装开发依赖及 `npm ci --prefix frontend`。

新文件必须归属目标能力目录或在 `files` 明确指定身份。后端公开入口为目录的 `__init__.py`，静态声明 `__all__`；前端为 `index.ts`，显式列出导出。公开入口反向链接本能力契约。

生产代码跨能力只从公开入口导入已声明符号。依赖方向按清单约束：助手会话 → 旅行规划、旅行者偏好、本机记录；旅行规划 → 资料获取、模型上下文；模型上下文和旅行者偏好 → 本机记录。声明图和实际已迁移依赖均检查环路。

Python 动态导入解析 `importlib` 别名及静态相对包参数；同能力内部可以使用，跨能力使用静态具名导入以明确公开符号，无法静态解析的动态导入会失败。前端同时检查静态导入、再导出、命名空间访问、字面量动态导入和 CSS 导入。

装配身份仅属于明确登记的启动入口；同样只使用公开契约。共享支撑以 `support` 身份及显式公开符号登记，不得反向依赖业务运行实现。调用方和跨能力测试经公开入口；`internal-test` 必须登记所属能力，仅同能力内部 seam 可直接使用。验证工具自身可互相导入，但该身份不能豁免访问业务私有实现。

## 临时适配的退出条件

`legacy` 保留基线已有的精确导入（目标路径及符号），并非排除整个目录。新文件、新依赖以及目标能力新目录立即执行规则。每个旧文件的 `remove_by` 指定移除任务；迁移文件或导入后，同一提交删去相应豁免，已删除文件仍留豁免会使检查失败。#86 完成时清空旧实现清单。

两个特殊过渡项同样逐项登记：`tests/compatibility/entrypoint.py` 目前借旧装配符号注入外部服务、配置和时间，随着各模块公开装配入口落地修改，并最迟在 #86 移除旧依赖；`capture.py` 暂复用既有外部 HTTP 服务和 SSE 测试辅助，辅助归属迁移完成后改用明确测试支撑。它们不替换规划、上下文、查询 adapter、事务或后台偏好实现。

基线证据、归一化规则和旧数据复现见[行为基线验收](../acceptance/deep-modules-baseline.md)。每次迁移都运行目标回归、此检查及对应反例；本阶段的检查不能代替 #86 的全能力封装、独立阅读、调用与内部修改验收。
