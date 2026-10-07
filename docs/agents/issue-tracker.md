# 任务跟踪：本地 Markdown

本项目的任务和规格以 Markdown 文件保存在 `.scratch/`。

## 约定

- 每个功能使用独立目录：`.scratch/<feature-slug>/`。
- 规格保存在 `.scratch/<feature-slug>/spec.md`。
- 每个实施任务单独保存为 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号，不合并为一个任务文件。
- 在每个任务文件顶部附近使用 `Status:` 行记录分诊状态，具体状态标识见 `triage-labels.md`。
- 评论和对话记录追加到文件底部的 `## Comments` 标题下。

## 当技能要求“发布到任务跟踪系统”时

在 `.scratch/<feature-slug>/` 下创建文件；目录不存在时一并创建。

## 当技能要求“获取相关任务”时

读取指定路径的文件。用户通常会直接提供文件路径或任务编号。

## Wayfinder 操作

供 `/wayfinder` 使用。探索地图保存为一个文件，每个子任务各有一个文件。

- 地图：`.scratch/<effort>/map.md`，包含 Notes（笔记）、Decisions-so-far（已有结论）和 Fog（待澄清问题）。
- 子任务：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 开始编号，正文记录问题。使用 `Type:` 行记录类型（`research`、`prototype`、`grilling` 或 `task`），使用 `Status:` 行记录 `claimed` 或 `resolved`。
- 依赖：在文件顶部附近使用 `Blocked by: NN, NN` 行列出依赖任务。所有依赖文件均为 `resolved` 时，任务解除阻塞。
- 待办选择：扫描 `.scratch/<effort>/issues/`，选择尚未完成、未被阻塞且未被认领的任务；优先处理编号最小的任务。
- 认领：开始工作前，将状态设为 `Status: claimed` 并保存。
- 解决：在 `## Answer` 标题下追加答案，将状态设为 `Status: resolved`，然后在 `map.md` 的 Decisions-so-far 部分追加结论摘要和任务链接。
