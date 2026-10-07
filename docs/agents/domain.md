# 领域文档

本项目采用单上下文布局：术语表为根目录的 `GLOSSARY.md`，架构决策记录保存在 `docs/adr/`。

## 探索代码库前阅读

- 根目录的 `GLOSSARY.md`。
- `docs/adr/` 下与当前工作范围相关的架构决策记录。

若这些文件尚不存在，直接继续工作。`/domain-modeling` 技能会在术语或决策得到明确后按需创建文档；该技能也可由 `/grill-with-docs` 和 `/improve-codebase-architecture` 调用。

## 文件布局

- `GLOSSARY.md`：共享领域术语。
- `docs/adr/NNNN-<decision-slug>.md`：架构决策记录。

## 使用术语表中的命名

在任务标题、重构提案、假设或测试名称中提及领域概念时，使用 `GLOSSARY.md` 定义的术语。

若术语表尚未包含某个概念，先检查项目是否使用这一表达。确有术语缺口时，记录下来供 `/domain-modeling` 处理。

## 明确指出决策冲突

若输出与已有架构决策记录冲突，明确指出冲突，并解释为何需要重新讨论该决策。
