硬违反：未发现可确证的仓库标准违反。已按 `AGENTS.md`、领域文档与术语表审查固定 diff；未重复报告已由工具检查的项。

判断性问题（1 项）：

- **[P3] 可能的 Duplicated Code：资料工具识别重复维护。** `query_materials.py:91–92` 新增 `name in ("tavily-search", "tavily_search", "tavily-extract", "tavily_extract")`，`store.py:278、408` 又复制同一集合；`tavily_mcp.py:17–19` 已定义 `SEARCH_TOOLS`、`EXTRACT_TOOLS` 和 `SOURCE_TOOLS`，且 `travel_tools.py:33` 已共用。注册、摘要展示和中断恢复因此各自维护相同识别规则，修正一个工具名时容易只更新分派、漏掉资料恢复或展示。建议这三处共用现有集合，搜索判断共用 `SEARCH_TOOLS`；无需增加新抽象。依据为 Fowler 的 Duplicated Code 启发式，以及 `AGENTS.md`“简单优先”中的可维护性与真实复用原则；这属于维护性判断，当前四种名称仍一致，不是已发生的功能缺陷。

标准轴合计：0 项硬违反，1 项判断性问题；最高优先级 P3。
