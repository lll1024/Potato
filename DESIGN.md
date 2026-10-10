---
"name": "旅行助手"
"description": "明亮、克制的本机对话与执行轨迹工作台"
"colors":
  "accent": "#315cce"
  "accent-soft": "#e9efff"
  "accent-hover": "#234aaf"
  "workspace": "#fff"
  "sidebar": "#f0f1f5"
  "surface": "#f7f8fa"
  "text": "#242732"
  "text-muted": "#646a78"
  "line": "#e8e9ee"
  "control-text": "#404655"
  "control-border": "#dfe2e9"
  "field-border": "#d7dbe5"
  "placeholder": "#6c7484"
  "model": "#75549d"
  "model-soft": "#f0eaf7"
  "tool": "#a45610"
  "tool-soft": "#fff2e3"
  "ready": "#448064"
  "busy": "#b17a22"
  "unavailable": "#b44a55"
  "error": "#a42732"
  "selection-text": "#163e9d"
  "selection-bg": "#dfe8ff"
  "search-hit": "#fff0a6"
  "search-hit-text": "#473400"
  "model-timeline": "#9675b9"
  "tool-timeline": "#d08732"
  "scrollbar": "#c5c8d2"
"typography":
  "headline":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "26px"
    "fontWeight": 600
    "letterSpacing": "-.02em"
  "brand":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "22px"
    "fontWeight": 700
    "letterSpacing": "-.02em"
  "answer-title":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "19px"
    "fontWeight": 600
    "lineHeight": 1.5
  "title":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "16px"
    "fontWeight": 600
  "body":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "14px"
    "fontWeight": 400
    "lineHeight": 1.85
  "input":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "14px"
    "fontWeight": 400
    "lineHeight": 1.7
  "trace":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "12px"
    "fontWeight": 400
  "label":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "11px"
    "fontWeight": 400
    "lineHeight": 1.7
  "metadata":
    "fontFamily": "-apple-system, BlinkMacSystemFont, \"Segoe UI\", \"PingFang SC\", \"Microsoft YaHei\", sans-serif"
    "fontSize": "10px"
    "fontWeight": 400
  "code":
    "fontFamily": "ui-monospace, SFMono-Regular, Menlo, monospace"
    "fontSize": "12px"
    "fontWeight": 400
    "lineHeight": 1.7
  "payload":
    "fontFamily": "ui-monospace, SFMono-Regular, Menlo, monospace"
    "fontSize": "11px"
    "fontWeight": 400
    "lineHeight": 1.7
"rounded":
  "timeline": "1px"
  "badge": "3px"
  "field": "6px"
  "control": "7px"
  "code-block": "8px"
  "message": "12px"
  "composer": "15px"
"spacing":
  "2": "2px"
  "4": "4px"
  "6": "6px"
  "8": "8px"
  "10": "10px"
  "12": "12px"
  "14": "14px"
  "16": "16px"
  "18": "18px"
  "20": "20px"
  "24": "24px"
  "28": "28px"
  "32": "32px"
"components":
  "button-primary":
    "backgroundColor": "{colors.accent}"
    "textColor": "{colors.workspace}"
    "typography": "{typography.trace}"
    "rounded": "{rounded.control}"
    "padding": "7px 13px"
  "button-primary-hover":
    "backgroundColor": "{colors.accent-hover}"
    "textColor": "{colors.workspace}"
  "button-secondary":
    "backgroundColor": "{colors.workspace}"
    "textColor": "{colors.control-text}"
    "rounded": "{rounded.control}"
    "padding": "7px 13px"
  "button-secondary-hover":
    "backgroundColor": "{colors.surface}"
    "textColor": "#202534"
  "button-active":
    "backgroundColor": "{colors.accent-soft}"
  "button-icon":
    "backgroundColor": "transparent"
    "rounded": "{rounded.control}"
    "padding": "8px"
  "field":
    "backgroundColor": "{colors.workspace}"
    "textColor": "#343b4b"
    "rounded": "{rounded.field}"
    "padding": "7px 10px"
  "composer":
    "backgroundColor": "{colors.workspace}"
    "textColor": "{colors.text}"
    "rounded": "{rounded.composer}"
    "padding": "12px 16px 10px"
  "view-tab":
    "backgroundColor": "transparent"
    "textColor": "{colors.text-muted}"
    "rounded": "0"
    "padding": "12px 0"
  "view-tab-selected":
    "textColor": "{colors.accent}"
  "history-item":
    "backgroundColor": "transparent"
    "rounded": "{rounded.control}"
    "padding": "10px"
  "history-item-selected":
    "backgroundColor": "#e0e3ec"
    "textColor": "#1f2637"
  "badge-input":
    "backgroundColor": "{colors.accent-soft}"
    "textColor": "{colors.accent}"
    "typography": "{typography.label}"
    "rounded": "{rounded.badge}"
    "padding": "2px 7px"
  "badge-model":
    "backgroundColor": "{colors.model-soft}"
    "textColor": "{colors.model}"
    "typography": "{typography.label}"
    "rounded": "{rounded.badge}"
    "padding": "2px 7px"
  "badge-tool":
    "backgroundColor": "{colors.tool-soft}"
    "textColor": "{colors.tool}"
    "typography": "{typography.label}"
    "rounded": "{rounded.badge}"
    "padding": "2px 7px"
  "trace-row":
    "typography": "{typography.trace}"
    "rounded": "0"
    "padding": "9px 0"
  "trace-row-selected":
    "backgroundColor": "{colors.accent-soft}"
  "timeline-model":
    "backgroundColor": "{colors.model-timeline}"
    "rounded": "{rounded.timeline}"
    "height": "12px"
  "timeline-tool":
    "backgroundColor": "{colors.tool-timeline}"
    "rounded": "{rounded.timeline}"
    "height": "12px"
  "user-message":
    "backgroundColor": "{colors.surface}"
    "rounded": "{rounded.message}"
    "padding": "16px 20px"
  "delete-dialog":
    "backgroundColor": "{colors.workspace}"
    "textColor": "{colors.text}"
    "rounded": "{rounded.message}"
    "padding": "24px"
    "width": "min(420px, calc(100% - 32px))"
---

# Design System: 旅行助手

## Overview

**Creative North Star: "明亮的本机工作台"**

明亮的本机工作台以低装饰、清楚定位和稳定阅读为视觉核心。浅灰助手会话导航与白色工作区形成温和层次，细灰分隔线组织操作区域；蓝色只承担操作、当前视图、选中位置与焦点的强调。

中文系统无衬线字体适应本机浏览器。回答用舒展行距组织长内容，执行轨迹用紧凑记录行保留数据密度。主工作区保持平面，底部输入容器用轻柔阴影提示可继续输入；删除确认借助原生对话框建立必要的临时层次。

**Key Characteristics:**

- 白色工作区、浅灰导航和细分隔线。
- 统一蓝色操作与定位状态，模型和工具各有类型颜色。
- 舒展回答与紧凑执行轨迹并存。
- 真实数据、明确文字状态、原生控件和可见键盘焦点。

## Colors

配色以冷中性色为底，强调蓝负责操作，紫色与橙色负责执行记录的类型辨认；具体数值以 frontmatter 为准。侧车的八阶 OKLCH 色带为面板展示合成，不是新增的界面色彩 token。根级自定义属性来自 `frontend/src/style.css`，类型条和搜索命中来自 `frontend/src/trace.css`。

### Primary

- **定位蓝**（`accent`）：发送、当前标签、链接、选中时间条、光标与键盘焦点；`accent-soft` 承担轨迹选中行和用户类型标签，`accent-hover` 用于发送悬停。
- **模型紫 / 工具橙**（`model` / `tool`）：类型文字与对应浅色标签底；时间条使用各自 `model-timeline` / `tool-timeline`，定位时转为蓝色。它们是信息分类色，不新增第二套操作强调色。
- **真实状态色**（`ready` / `busy` / `unavailable`）：服务状态小圆点；`error` 用于错误信息和确认删除。均配合状态文字。
- **搜索与选区**（`search-hit` / `search-hit-text`、`selection-bg` / `selection-text`）：前者标出搜索命中，后者用于浏览器原生文本选区；均不承担按钮选中语义。

### Neutral

- **工作白、导航灰、内容浅灰**（`workspace` / `sidebar` / `surface`）：分别用于主区、助手会话导航，以及用户输入、代码与窄屏详情。
- **正文墨灰 / 辅助灰**（`text` / `text-muted`）：正文与状态说明、时间、辅助标签。
- **细分隔线 / 控件边界**（`line` / `control-border` / `field-border`）：区域与记录行使用细线，按钮和表单各使用自己的边界。
- **控件文字、占位提示与滚动条**（`control-text` / `placeholder` / `scrollbar`）：保留原生操作的清楚边界和可读提示，滚动条为细型透明轨道。

**The 统一定位 Rule.** 当前视图、选中执行记录与常规控件的键盘焦点共享强调蓝；底部输入容器使用灰色焦点提示。模型紫与工具橙用于识别记录类型，不能替代选中态。

## Typography

**Body Font:** 中文系统无衬线栈（`-apple-system`、`BlinkMacSystemFont`、`Segoe UI`、`PingFang SC`、`Microsoft YaHei`、`sans-serif`）。没有另设展示字体。

**Label/Mono Font:** 标题与标签沿用正文栈；代码与完整载荷使用系统等宽栈（`ui-monospace`、`SFMono-Regular`、`Menlo`、`monospace`）。

字体的任务是方便中文阅读和记录对照。层级依据实际组件提取，没有假设等比字号比例；未在 CSS 中声明的行高不额外固定。

- **空状态标题**（`headline`）：强调开始输入的入口；窄屏降为（23px）。
- **品牌与回答标题**（`brand` / `answer-title` / `title`）：品牌、Markdown 二级标题与会话标题；Markdown 一级标题沿用品牌字号，三级标题沿用 title 字号，标题行高为（1.5）。
- **回答正文**（`body`）：中文长回答，段落下方间距（14px）；用户输入与输入框采用 `input` 的行距。回复表格字号为（13px），不强行按正文行长截断。
- **执行记录与字段**（`trace`）：记录摘要、详情和搜索字段；操作标签与类型标签采用 `label`，元数据采用 `metadata`。少量图例补充文本为（9px），仅用于辅助数据说明。
- **代码与载荷**（`code` / `payload`）：等宽原文，保持可滚动与可换行切换。
- **数值**：时间、耗时、用量和详情值使用 `tabular-nums` 便于上下对照；继承系统字体，不另引入数字字体。

**The 双密度 Rule.** 回答正文使用 body 层级，执行记录使用 trace 与 label 层级；辅助指标的较小字号仅限元数据，不能扩展为正文规范。

## Layout

电脑优先的满高工作台使用（100dvh）：桌面左侧导航（260px），右侧为可收缩的主工作区。导航历史、对话正文与执行记录各自滚动；底部输入位于正文滚动区域之外，切换对话和轨迹后仍可使用。关闭导航时工作区占满宽度。

对话正文与输入容器围绕（780px）阅读宽度对齐。桌面正文左右留白至少（32px）；标题与标签栏左右留白（28px）。标签栏与轨迹工具栏最小高度（44px）。执行轨迹使用可用工作区宽度；选中记录后，详情列采用 `minmax(280px, .85fr)`，两列间距（20px）。

间距是实际使用的整数像素节奏，frontmatter 记录复用步长；紧凑记录采用小间距，内容块与轮次采用较大间距，不追认人为的统一等差或等比规则。回答轮次底部间距（32px），用户消息距下一块（28px），执行树逐级缩进（12px），工具记录再缩进（16px）。

- **至（1000px）**：导航降为（220px），主区留白收紧；执行详情移入选中记录内，轨迹成为单列，工具栏补充说明隐藏。
- **至（760px）**：导航与工作区互斥显示，导航占满宽度；标题和状态允许换行，标签图标隐藏，正文留白（18px），轨迹留白（14px）；筛选控件换行，树缩进降为（4px）。输入字号升为（16px），底部留白兼容 `safe-area-inset-bottom`。
- **宽内容**：回复表格最小宽度（420px），只在表格区域横向滚动；载荷区最高（400px），时间边界列表最高（240px），搜索结果最高（180px），均在自身区域滚动。

## Elevation & Depth

常驻工作区依靠白色、浅灰与细线分层。输入容器和删除确认使用柔和阴影，前者突出持续输入的位置，后者体现临时确认层；不将每条回答或执行记录提升为浮动卡片。

### Shadow Vocabulary

- **输入托层**（`0 3px 22px rgb(29 39 65 / 9%)`）：底部输入容器。
- **确认浮层**（`0 16px 60px rgb(20 29 54 / 18%)`）：原生删除 dialog；背景遮罩（`rgb(27 32 48 / 35%)`）。

**The 平面工作区 Rule.** 用底色与细线组织常驻工作区；轻柔阴影用于底部输入容器和删除确认，不扩展到每条记录。

## Shapes

轻圆角服务于可操作区域：控件采用 `control`，输入字段采用 `field`，类型标签采用 `badge`；用户输入采用 `message`，底部输入采用 `composer`。执行树与记录行保持平直边界，不套圆角卡片。时间条以 `timeline` 的微圆角区分可点击条目。

区域及行分隔统一为（1px）细线；Markdown 引用仅使用（1px）起始边线与浅灰底。SVG 图标默认（18px），发送（20px），品牌（27px），空状态（36px）；采用（24×24）viewBox、（1.6px）描边、圆端点与圆连接，继承文字颜色。树展开箭头由细线绘制，并随原生展开状态旋转。

## Components

### Buttons

清楚、轻量的原生操作。

- **发送**：强调蓝底、白字；图标与文字并存，最小高度（35px）。悬停使用更深强调蓝。
- **普通操作**：白底、细控件边界；悬停转浅灰底和深文字，按下转浅蓝。新会话为整行按钮，最小高度（43px），字体权重（550）。
- **紧凑操作 / 图标按钮**：记录操作（11px）及最小高度（28px）；图标按钮至少（36×36px），透明底，保留可访问名称。
- **共有状态**：颜色过渡（150ms），禁用透明度（0.5）且恢复默认光标；焦点使用（2px）蓝色 outline、外移（3px）。遵循 `prefers-reduced-motion: reduce` 停用过渡。链接的下划线偏移（3px）。

### Chips

执行类型标签是信息标识，没有伪装成筛选按钮。

- 用户使用浅蓝底和蓝字；模型使用浅紫底和紫字；工具使用浅橙底和橙字。
- 标签沿用 `badge` 圆角与 `label` 字体；真实状态另以文字表达。

### Cards / Containers

容器按内容职责划分。

- 用户输入为浅灰圆角块，左侧缩进（12%），窄屏降为（6%）；最终回答直接置于白色工作区。
- 空状态保留标题和旅行规划说明，不显示示例提示；代码块采用内容浅灰底和内部滚动。
- 输入容器采用 `composer`；删除 dialog 采用 `delete-dialog`，使用原生 backdrop，最大高度为可用视口减去（32px），确认按钮使用错误红。

### Inputs / Fields

白色字段保留原生文本与筛选操作。

- 普通 input/select 为细边框与 `field` 圆角；搜索字段使用（12px）字号，占位提示使用专用灰色，光标使用强调蓝。
- 底部 textarea 在输入容器内透明、无边框；最小高度（52px）、最大高度（160px），允许垂直调整大小。保留隐藏 label、required 与显式发送按钮，不追加未实现的快捷键。
- 底部输入区的占位提示和常驻说明使用较浅灰色（`#707784`），实际输入文字保留正文色；提交和连接状态仍使用原有辅助色。保留 Enter 发送、Shift+Enter 换行的功能，界面不显示快捷键提示。
- 底部 textarea 的焦点通过输入容器的灰色 outline（`#8c939f`）提示位置，其他控件沿用共有蓝色 outline；提交、服务及连接错误以实际文字提示和 `role="alert"` / `role="status"` 展示，不发明整框错误样式。

### Navigation

左侧导航管理助手会话，主区域标签切换同一会话的内容视图。

- 历史行默认透明，当前会话使用柔和灰蓝底；标题单行省略，时间与元数据为紧凑数字文字。
- 左侧顶部的项目名称显示为 Potato。
- 对话/轨迹标签默认辅助灰，当前标签使用蓝字、权重（600）与底部（2px）蓝线；保留 `aria-pressed` 与导航名称。
- 导航收起/展开使用按钮与 `aria-expanded`、`aria-controls`；手机打开时导航占满画面，选择会话后回到工作区，Escape 可收起并恢复焦点。

### 执行树与实际时间线

层级、类型和定位共同支撑排查。

- 原生 details/summary 表达会话、对话轮次和模型请求的展开状态；摘要行细分隔线、文字省略、状态在尾部，完整内容可在详情阅读。
- 选中摘要、工具行和时间边界使用浅蓝背景；时间条悬停或选中使用强调蓝。时间条两条泳道分别为模型和工具，高度（12px），最小宽度（5px）；位置与宽度来源于实际时间，没有伪造未结束时长。
- 完整载荷沿用等宽文字，保留换行、分段读取和完整复制操作；数字与状态文字保留真实未知值。搜索命中高亮在原文中显示。
- 回看、展开或定位时暂停自动跟随，通过可见的「回到最新」恢复；从对话轮次进入轨迹后保留对应位置和焦点。

## Do's and Don'ts

### Do:

- **Do** 使用既有语义色和根级 CSS 自定义属性，保持视图切换、轨迹定位及焦点的一致性。
- **Do** 让回答表格和完整载荷在自身区域滚动，保留长内容及可选择、可复制的原文。
- **Do** 使用原生按钮、表单、details/summary 和 dialog，并保留文字标签、焦点及键盘操作。
- **Do** 配合状态颜色显示真实状态文字，区分未知、零值、未调用、中断和完成。
- **Do** 在窄屏按已有断点收起导航、换行筛选并将执行详情放到选中记录内。

### Don't:

- **Don't** 将蓝色、紫色或橙色扩展成大面积装饰底色，覆盖白色工作区的阅读层次。
- **Don't** 把紧凑执行记录包装成多层阴影卡片；保持细线与层级缩进。
- **Don't** 用 Unicode 字符代替现有 SVG 操作图标，或让无文字图标按钮失去可访问名称。
- **Don't** 仅依赖颜色表达状态，或用视觉进度补造尚未采集的时间与指标。
- **Don't** 把对话轮次结束后出现的完整回答表现成实时文本流，或让新记录打断正在回看的阅读位置。
