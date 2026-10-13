# 验收索引

先读下表的结论，再按各文档链接追溯原始证据。历史记录只说明其注明的版本、日期、配置与场景；测试通过、接口成功及真实模型语义核对各有适用范围，不能相互替代，也不能视为当前版本重新运行的结果。

| 行为范围 | 结论入口 | 证据与适用范围 |
| --- | --- | --- |
| 本次后端包迁移、启动兼容与代码导航 | [后端包迁移](backend-package.md) | [本次运行证据](backend-package/)；迁移前后回归、资源与旧数据核对，覆盖 #67 |
| 上下文压缩、超限恢复、原文回读及旅行语义 | [上下文压缩](context-compaction.md) | [原始证据与失败记录](context-compaction/)；#56/#64 的机制、真实模型对照、用量、耗时与浏览器场景 |
| 普通模型请求重试与停止 | [模型重试](model-retries.md) | 文内自动与实际服务范围；可控故障验证不代表真实服务故障均发生过 |
| 本机页面、会话、提交、历史与恢复 | [页面首版](local-web.md) | 文内 23 项及真实服务限制；[当时双轴审查](code-review.md)，后续页面变化另见重设计 |
| 前端布局与交互 | [前端重设计](frontend-redesign.md) | 文内浏览器、截图与测试依据；范围为当时视觉和交互版本 |
| 交通路线的页面表达 | [交通路线表达](traffic-route-presentation.md) | 文内交通方式和路线显示范围，不代替交通信息实时核实 |
| 跨会话旅行者偏好 | [偏好验收](traveler-preferences.md) | 文内真实语义、公开接口及截图链接；[语义证据](traveler-preferences-semantic.json)、[失败记录](traveler-preferences-semantic-failures.json)、[审查](preferences-code-review.md) |
| 偏好闲置处理、失败与重启 | [偏好恢复](preferences-recovery.md) | 文内有限重试与恢复观察；[桌面](preferences-failure-desktop.png)、[移动](preferences-failure-mobile.png) 截图 |
| 故障、停止与保存事实 | [异常体验](exception-experience.md) | 文内自动、浏览器与真实服务依据；保留未知结果及诊断限制 |
| Exa 免费资料搜索与正文 | [Exa 接入](exa-sources.md) | [实连与验证目录](exa-sources/)；成功、修前语义失败与免费服务限制分开记录 |
| 国内旅行日期与官方资料 | [规格 #28](spec-28.md) | [原始证据](spec-28/)；包含当时 Tavily 失败、官方正文回填和日期修复，当前资料接入见 Exa |
| 旅行行程事实与回答差异 | [事实核对](itinerary-fact-check.md) | 文内逐项核对与限制；模型完成回答不能等同于信息已核实 |

[后端职责地图](../backend-map.md)关联代码与测试入口；[历史档案说明](../../.scratch/README.md)解释旧任务状态。当前任务与实施规格以 [GitHub Issues](https://github.com/lll1024/Potato/issues) 为准。
