# #117 原生终态跳位：离线像素观测

仅在 `/tmp` 写入工具和结果；未修改仓库，未启动 GUI／模拟器／剪贴板／模型／网络，也未读取生产实现猜根因。

## 已运行的一条红灯命令

```sh
python3 /tmp/potato109-native-position-probe.py /Users/jarviliu/.codex/worktrees/continuous-final-117/travel/docs/acceptance/continuous-final/query-final-confirmed/ios-terminal-frames --start 305.48 --end 305.90 --output /tmp/potato109-native-position-tight.json
```

实际退出码 **1**，用时 **1.511s**：

```text
305.485000s SOURCE_TABLE_TAIL
305.545000s EARLIER_INPUT_OR_TITLE RED
305.700000s EARLIER_INPUT_OR_TITLE RED
305.751667s EARLIER_INPUT_OR_TITLE RED
305.890000s SAVED_ANSWER_TAIL
红帧=3 未分类=0 总帧=5
```

也已运行全部42帧：红帧3，未分类0，12.773s；详细结果 `/tmp/potato109-native-position-observations.json`，控制台 `/tmp/potato109-native-position-probe.out`。检测使用已安装的 `tesseract eng --psm 11 tsv`，按图像真实高度排除顶栏与输入框；无须额外 Python 包。原始每帧480×1043。Apple Vision 中文离线识别两次返回 `nilError`（含CPU模式），未依赖它。

## 证据与正确解释

来源是工作树 `docs/acceptance/continuous-final/query-final-confirmed/ios-terminal-frames/manifest.json`；时间来自 `actual_seconds`，并非文件名／请求时间。末尾多张请求帧实际重复306.736667s，不能当作多个独立时刻。

- 305.485：仍有多个 `http://`／`https://`／`www.` 来源表链接，属于当前可见表格尾部。
- 305.545／305.700／305.751667：链接消失，视口中重新出现输入中的2026日期，后两张也出现回答标题日期。它们是已知回退，OCR红灯与真实像素一致。
- 305.890以后：有 `3.8` 与 `10:00—22:00` 餐饮尾段。真实 `histories.json` 的本轮回答长4203字，最后一节正是“## 四、餐饮建议”，以“评分和”截断。因此这一步是**终态真实尾部接管**，不能单凭章节改成餐饮就判仍在回退。

这说明“内容阶段变化”和“位置回退”必须分开：流式来源表→已保存回答餐饮尾部本身允许；中间短暂让先前输入／开头重入视口才是当前明确失败信号。以上仅描述可观察证据，不证明任何布局根因。

## 给受控fixture的机械红灯

`ticket117` 的唯一输入标记 `HANDOFF117 INPUT` 在长文跟随到底部时应位于视口外。提取终态附近本地帧后运行：

```sh
python3 /tmp/potato109-native-position-probe.py /tmp/YOUR-FRAMES --marker 'HANDOFF117 INPUT' --output /tmp/potato117-marker-observations.json
```

返回1表示至少一帧正文视口OCR读到了该标记；逐帧打印真实时间。返回0只说明采样未观察到，**不是保位通过证明**。此fixture marker尚未在新录屏验证，需由复现代理针对实际画面校准OCR。

可选离线视频抽帧器 `/tmp/potato109-export-frames.swift` 从现有exporter复制，保留真实时间，改用序号文件名避免60fps采样时0.1秒文件名碰撞，分辨率上限960×2200：

```sh
swift -module-cache-path /tmp/potato109-swift-module-cache /tmp/potato109-export-frames.swift /tmp/VIDEO.mp4 /tmp/YOUR-FRAMES 0.016666667 START_SECONDS END_SECONDS
```

抽帧器本轮未运行；无需GUI。若录屏本身是低变帧率，抽帧结果可能重复actual_seconds，分析时应据此解释。

## UI断言建议与边界

1. 红灯：完成前已处于长表尾部且无用户手势，直到终态尾部接管，唯一输入标记在每个实际呈现帧均不得出现；AX对象`exists`不能替代视口位置／像素。
2. 绿灯需另证：跟随模式用可见末尾锚点与滚动视口下边界／bottom distance观测；真实尾段新增导致章节改变允许。上翻阅读模式应保留稳定内容锚点的视口y，而不是要求跟到底部。
3. 归档分类器是针对本次证据的内容签名（链接、输入日期、餐饮数字），不应加入产品测试；它不能用于任意Markdown、任意日期和任意回答。无法从这些480px图像准确恢复contentOffset／contentSize，亦不能从OCR未命中推导连续保位。
4. 此离线红灯重放的是**既有失败像素**；它不是驱动当前产品路径的新复现，不能替代受控公开fixture在修复前／后的红绿与连续录屏验证。

## 受控候选复核与工具修复

`ticket117` 首次候选 `/tmp/potato117-long-handoff-red.mp4` 尚未复现；名称不代表红。其帧目录 `/tmp/potato117-long-handoff-probe-frames` 在34.99–36.6区间（actual至36.49）共19采样，正文marker命中0；含重复actual_seconds，多数可读餐饮尾段数字。结果 `/tmp/potato117-long-handoff-marker-tight.json`。不将该结果计为复现或连续保位绿。

已修工具 `subprocess.run(..., errors='replace')`，并将输入帧目录 `Path.resolve()`：实际底层问题为本机Leptonica无法读取`/tmp`符号路径，使用`/private/tmp`可读取；乱码来自失败stderr。

另已验证导航栏title也出现`HANDOFF117 INPUT`，因此整图grep会假红；当前脚本仅扫描页面高度12%–86%的正文窗口，排除标题栏与底部输入框。

## hot候选：应按内容阶段设判定窗口

第二候选 `/private/tmp/potato117-long-handoff-hot.mp4` 的高分辨率帧 `/tmp/potato117-long-handoff-hot-frames` 包含151请求采样，按actual_seconds去重得到91实际帧，覆盖35.488333–39.521667s。请求终点40.5并不代表存在40.5的独立新像素。

全窗口OCR用12.63s：35.488／35.530／35.535／35.995读到了**正文里的**输入标记；此时尚处于初始输入和短正文阶段，像素与标题、行程假设、首节表格一致，不应算终态回退。36.051667起直到39.521667未读到正文标记。

以fixture代理给出的first AX37.6525／历史释放约38.5为依据，另外实际运行：

```sh
python3 /tmp/potato109-native-position-probe.py /tmp/potato117-long-handoff-hot-frames --jobs 4 --dedupe --start 37.5 --marker 'HANDOFF117 INPUT' --output /tmp/potato117-long-handoff-hot-terminal-marker.json
```

结果退出0，6.724s，48个不同actual帧，正文marker命中0；所有帧正文OCR至少425字符，31帧同时识别餐饮尾段数字签名。37.651667原像素明确是餐饮真实回答尾部及“正在核对本轮结果 · 当前正文尚未确认保存”，并非准备轮／输入重入。导航栏marker在此帧可由OCR识别，且被正文ROI正确排除。

因此这份hot候选**终态窗口仍未复现**原回退；退出0只能表示该采样无marker命中。早期正常marker命中同时提供了同一录屏的正向识别控制，不能把整个录屏的exit1误作终态红灯。测试必须先建立“长文尾部跟随、输入离开视口”的前置条件，再设`--start`门槛。

工具新增`--jobs 4`并行OCR及`--dedupe`避免同actual重复识别，不占GUI。全窗口结果 `/tmp/potato117-long-handoff-hot-marker.json`；终态窗口结果如上。

## paced候选：已捕捉真实受控红窗

第三候选 `/tmp/potato117-long-handoff-paced.mp4`，帧 `/tmp/potato117-long-handoff-paced-frames`。已运行同脚本`--start 51 --jobs 4 --dedupe --marker 'HANDOFF117 INPUT' --output /tmp/potato117-long-handoff-paced-terminal-marker.json`，9.974s，退出1：

- 53.086667，frame-000078.jpg：餐饮生成尾段，复制当前正文，正在生成。
- **53.128333／53.160000／53.191667／53.226667**，frame-000079..82.jpg：正文ROI读到输入marker；真实像素frame79明确显示“打开原会话”、输入气泡、查询进度、“电脑端仍在处理，最终作答时将实时显示正文”，此前正文撤空。已排除导航栏标题。
- 53.263333，frame-000083.jpg：保存餐饮全文尾部、复制不完整回答、处理上限说明接管。

已逐一查看078／079／083原始像素，红窗不是OCR假阳性。与代理给出的完成事实视频52.782857s相比，红窗在其后约345ms开始；这只是时间关联，不作根因推断。AX50样本全绿未捕捉此约100–177ms范围的短闪回，连续帧提供真实受控红灯。
