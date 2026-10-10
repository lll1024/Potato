---
version: 1
slug: "frontend-src-main-tsx"
primary_target: "frontend/src/main.tsx"
related_targets: ["frontend/src/style.css","frontend/src/TraceView.tsx","frontend/src/trace.css","frontend/src/ConversationRounds.tsx","frontend/src/HistorySidebar.tsx","frontend/src/TraceTimeline.tsx"]
---

# 本机旅行助手工作台

- 目标：`frontend/src/main.tsx`；模式：Operate。
- 用户在旅行前于电脑浏览器规划，或核对模型与地图调用；日常阅读环境适合明亮、低装饰的工作台。
- 本次构建采用代码直接实现；应用界面不需要图像概念稿。用户提供的两张参考图是已确认的结构和视觉依据。
- 保留真实数据、完整载荷、提交与停止语义、阅读跟随、搜索及分页。移动端提供可折叠左侧导航。

## Direction contract

THESIS：左侧助手会话导航与主工作区域分离；主区域以对话和轨迹标签切换，取消原来的右侧历史和嵌套大容器。

OWN-WORLD：参考 DeepSeek Harness 的浅灰侧栏、白色工作区、蓝色选中标签、细灰分隔线；系统中文无衬线字体，正文舒展、轨迹紧凑；输入区是底部唯一带轻柔阴影的容器。

STORY：新建或回看助手会话，输入旅行需求，阅读结构清楚的回答，继续调整；遇到疑问由本轮入口转入轨迹，定位模型请求或工具，查看完整原始载荷。

FIRST VIEWPORT：桌面约 260px 左侧导航，右侧满高工作区。顶部小尺寸会话标题与真实状态，下方 44px 标签栏；可滚动正文居中保持阅读宽度，轨迹占满工作区。底部输入区始终可见，历史可独立滚动。空白页直接呈现旅行需求输入；载入历史时保持真实回答，不用示例替换。

FORM：用户固定的参考图优先于随机方案；方向种子 c6820e91 已运行，其指派与挑战方案均不替换用户明确选择的结构和视觉。标志性交互是从对话轮次定位到轨迹，并以一致蓝色选中态保持当前定位。

FINISH：unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## 验证

生产构建、Markdown 安全与排版检查、相关后端回归、成组桌面和手机浏览器检查；独立设计复核后记录设计系统。实施与验收追踪：lll1024/Potato#21。
