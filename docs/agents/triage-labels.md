# 分诊状态

工程技能使用五种标准分诊状态。本文件将它们映射到本项目本地 Markdown 任务中的状态标识。

| 标准状态 | 本项目状态标识 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待提交者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格完整，可由代理自主实施 |
| `ready-for-human` | `ready-for-human` | 需要人工实施 |
| `wontfix` | `wontfix` | 不予处理 |

当技能提及某个分诊状态时，在任务的 `Status:` 行中使用表内对应的状态标识。

如果项目采用其他命名，修改“本项目状态标识”列。
