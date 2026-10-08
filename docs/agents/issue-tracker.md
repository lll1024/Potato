# 任务跟踪：GitHub Issues

自 2026-10-08 起，新任务和规格保存在 [lll1024/Potato](https://github.com/lll1024/Potato/issues) 的 GitHub Issues 中，使用 `gh` CLI 读写。命令显式指定 `-R lll1024/Potato`。

## 任务与规格

- 每个功能的规格保存为一个父 Issue，每个实施任务保存为一个子 Issue。单个独立任务可以直接创建 Issue。
- 子任务正文包含目标、范围和验收标准，并链接父规格；使用 GitHub 子任务关系组织功能。
- 分诊状态使用标签，具体约定见 `triage-labels.md`。任务生命周期使用 GitHub 的打开／关闭状态。
- 讨论、实施与验证记录追加为 Issue 评论。验收完成后关闭任务；关闭父规格前确认其子任务均已完成。
- 任务依赖使用 GitHub 原生阻塞关系。所有阻塞任务关闭后，任务解除阻塞。

## 常用操作

- 发布任务或规格：`gh issue create -R lll1024/Potato --title "标题" --body-file <正文文件>`。多行正文先写入临时文件，发布后以 GitHub Issue 为准。
- 创建子任务：在创建命令中增加 `--parent <父Issue编号>`；为已有任务建立父子关系使用 `gh issue edit <父Issue编号> -R lll1024/Potato --add-sub-issue <子Issue编号>`。
- 读取任务：`gh issue view <编号> -R lll1024/Potato --comments`。
- 查询待办：`gh issue list -R lll1024/Potato --state open --limit 100`；按分诊状态增加 `--label <标签>`。结果超过限制时分页获取。
- 评论：`gh issue comment <编号> -R lll1024/Potato --body-file <评论文件>`。
- 设置依赖：`gh issue edit <编号> -R lll1024/Potato --add-blocked-by <阻塞Issue编号>`；多个编号使用逗号分隔。
- 检查依赖：`gh issue view <编号> -R lll1024/Potato --json blockedBy,blocking`，核实阻塞任务的打开／关闭状态。
- 认领：确认任务尚未被认领且依赖已完成，然后执行 `gh issue edit <编号> -R lll1024/Potato --add-assignee @me`。
- 完成：先追加验收与验证记录，再执行 `gh issue close <编号> -R lll1024/Potato`。

如果仓库功能或权限限制导致无法建立子任务关系，在子任务正文添加 `Part of #<父编号>`，并在父 Issue 正文维护任务清单。阻塞关系不可用时，在正文顶部维护 `Blocked by: #<编号>, #<编号>`，执行前逐一检查阻塞任务是否关闭。

## 历史档案

`.scratch/` 中已有规格、任务和验证记录保留原路径，作为历史档案随 Git 提交和推送。新任务及其进展使用 GitHub Issues。

继续处理历史任务时，创建对应 GitHub Issue，链接原文件并注明当前范围和状态。旧文件中的 `Status:` 仅表示历史分诊状态，不能据此认定任务尚未实施；核实实施和验收记录后确定新 Issue 状态。

## 外部 PR 分诊

**PRs as a request surface: no.** 外部 PR 不作为功能请求进入分诊队列。

## Wayfinder 操作

供 `/wayfinder` 使用，探索地图是父 Issue，决策任务是子 Issue。

- 地图：正文包含 Notes（笔记）、Decisions-so-far（已有结论）和 Fog（待澄清问题），使用 `wayfinder:map` 标签。
- 子任务：使用 `wayfinder:<type>` 标签，类型为 `research`、`prototype`、`grilling` 或 `task`；首次使用时创建所需标签。
- 待办选择：读取地图的子任务，选择仍打开、无打开的阻塞任务且未被认领的任务，按地图中的顺序处理。
- 认领：开始工作前将任务分配给当前开发者。
- 解决：追加答案评论并关闭子任务，然后在地图的 Decisions-so-far 中追加结论摘要和任务链接。
