# 分诊状态

工程技能使用五种标准分诊状态。本文件将它们映射到 `lll1024/Potato` 的 GitHub Issue 标签。

| 标准状态 | GitHub 标签 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待提交者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格完整，可由代理自主实施 |
| `ready-for-human` | `ready-for-human` | 需要人工实施 |
| `wontfix` | `wontfix` | 不予处理 |

当技能提及某个分诊状态时，在 GitHub Issue 上应用对应标签。同一任务同时最多保留一个分诊标签；切换状态时移除原分诊标签，保留类型等其他标签。

使用 `gh issue edit <编号> -R lll1024/Potato --add-label "<新标签>" --remove-label "<原标签>"` 切换状态；没有原分诊标签时省略 `--remove-label`。

分诊标签与完成状态分别记录。任务通过验收后关闭 Issue；旧 `.scratch/` 文件中的 `Status:` 保留为历史记录。

如果项目采用其他命名，修改“GitHub 标签”列，并同步仓库中的标签。
