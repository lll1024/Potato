## Standards

审查范围：`67c87676043005604925815c0a0e2aa04e24ff33...30e68bc`。依据 `AGENTS.md` 的“简单优先／精准修改”、`docs/agents/domain.md`、`GLOSSARY.md`、`docs/adr/0001–0005`、`docs/agents/issue-tracker.md`、`README.md` 与前端 `DESIGN.md`；跳过工具可强制检查的格式和类型规则。

未发现可确认的文档标准硬违规。

- **[P3] 可能的 Duplicated Code（判断性，非硬违规）** — `/Users/jarviliu/projects/travel/context_summary.py:125` 和同文件 `:198`。两个新增 hunk 都构造 `{"model": parameters["model"], "system": SUMMARY_SYSTEM, "messages": [{"role": "user", "content": json.dumps(source, ensure_ascii=False)}], "max_tokens": min(2048, policy.input_budget * 15 // 100)}`；前者用于挑选可容纳的批次，后者用于实际请求。摘要包装与输出额度现有两处维护点，修改时容易让批次计量和发送参数失去一致性。建议在本文件抽出一个短的摘要请求构造函数供两处使用，保留发送前重新计量。该抽取已有实际复用点，符合 `AGENTS.md`“抽象应有实际复用需求”。

共 0 项硬违规、1 项低优先级判断性异味；最严重项为上述重复请求构造。只读审查，未运行全量测试或真实模型。
