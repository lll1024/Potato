# #117 终态物理撤空诊断与修复边界

当前源码为024（Native8654342＋Web2c275c8）。真实query87的物理红保留在[视觉修订](../query-final-confirmed/visual-review.json)，不是修后实际query成功。未重发该查询；以下是既有外部fixture沿生产HTTP／SSE／SQLite与App原界面的受控回放。

## 可证伪假设与实际定位

依次核对三个假设：①整个页面loading重建；②完成与历史异步采用导致loaded/running时正文投影为空；③正文始终存在，仅底部滚动／Markdown布局失锚。原四块候选与加热候选marker0均没有复现，不能作红。按实际4203字及750个去重公开累计更新的相对16.1秒时序（不是2522revision条事件），不加history完成屏障后，paced录屏捕获四个物理红帧。AX50个样本全绿，证实不能只采AX非空。

[原paced日志](long-handoff-paced.log.gz)、[四红及前后帧](paced-red-frames/manifest.json)、[离线标记输出](potato117-long-handoff-paced-terminal-marker.json)：53.128333、53.160、53.191667、53.226667秒正文input重入。导航栏也有同marker，探针排除顶12%，人工确认正文区域input／查询进度和“电脑端仍在处理”，不是OCR假阳性。

临时边界日志只记录caller／状态／尝试／长度，不写全文；最终源码已清除DEBUG。[caller日志](handoff-caller-runtime.log.gz)显示refresh首先采用空snapshot，Presenter为loaded/running、preview=nil／active=nil；随后持久finish才到，已无可授权正文。这否定仅修改scroll task的方案，支持假设②。首diag标准stdout无App console，不能据无DEBUG断言未执行；旧diag录屏横跨中断数小时，未沿旧video-start秒数猜对应帧。

原生refresh沿SessionAccess既有history读取／采用协调：严格同身份、原活动有合格正文、终态snapshot释放且无storagefault时先核真实terminal再公布snapshot；不会将null本身授权保存。公开[首2红](http-ending-red.log.gz)→[绿](http-ending-green.log.gz)及[8边界](http-boundaries-green.log.gz)覆盖running／unknown／500／404／新SSE与切换，失败留上一已公布一致状态／原错误，不加轮询或后台模型。Web对应公开红绿见[联合Web验收](../../web-completion-handoff.md)。

此外[独立SSE公开审计](potato109-sse-completion-order-audit.md)实证旧batch背压下先空预览后完成trace；仓库公开test_answer_streaming新增真实ASGI发送屏障在首trace／首state两位置红绿，当前完整408项通过。它与实际paced主caller为HTTP分开，不声称此前唯一根因已由猜测证明。

## 修后物理窗口与复制

首次修后固定copyY断言仍红：中间AX坐标相差117.667pt，但78实际帧无input重入，49.13／49.163像素没有该位移。不同Markdown末批／页脚中间位置不能当稳定基准；[原红日志](long-handoff-green.log.gz)保留。测试继续记录每个copyY／bodychars及NotNil，80pt界限用于稳定terminal，额外完整视频marker／尾部检查。没有仅改宽阈值。

最终[测试日志](long-handoff-final.log.gz)、[完整连续帧](final-paced-frames/manifest.json)、[总览](final-paced-contact-sheet.jpg)、[93个不同actual帧OCR](potato117-long-handoff-final-marker.json)、[CTA物理坐标](potato117-final-copy-pixel-positions.jsonl)与[原AX／首次复制附件](final-paced-attachments/manifest.json)共同核对：53.065—56.373333秒marker0、正文尾部持续，无准备/input撤空；末批55.213秒短暂布局未收束，55.281起真实尾部／CTA／输出上限页脚进入视口，55.68为实际点击动画。首次partial label AX3536，预置独立clipboard哨兵后点击真实复制，粘贴等于可靠全文4203。未修改ReadingTransition来解释陈旧AX。

此结论为原四红撤空模式未再观察到、稳定终态尾部和真实copy一致；不保证每一帧完美跟底或亚帧无任何变化。最终controlled App源码与024一致；当时受控backend旧进程仍加载87，backend新次序另由当前408与024的11相关UI验证，不冒称该旧进程已加载新后端。

## 复核命令与原始路径

源数据：ios/Integration/handoff-answer.md 与 handoff-answer-timing.json；外部fixture模式query-handoff-paced，无付费模型。

```sh
xcodebuild test-without-building -project ios/Potato.xcodeproj -scheme Potato \
  -destination 'platform=iOS Simulator,id=AB9010DF-6F36-4079-8492-3CE8B5393EF7' \
  -derivedDataPath /tmp/potato117-derived \
  -only-testing:PotatoUITests/ReadingNavigationTests/testLongMarkdownTerminalHandoffKeepsFollowingViewport \
  -skipMacroValidation CODE_SIGNING_ALLOWED=NO
python3 /tmp/potato109-native-position-probe.py /tmp/potato117-long-handoff-paced-frames \
  --jobs 4 --dedupe --start 51 --marker 'HANDOFF117 INPUT' --output /tmp/red.json
python3 /tmp/potato109-native-position-probe.py /tmp/potato117-long-handoff-final-frames \
  --jobs 4 --dedupe --start 53 --end 58 --marker 'HANDOFF117 INPUT' --output /tmp/green.json
```

原红探针exit1；最终exit0，12.51秒／93实际帧。请求到58秒但后段actual重复，未当额外帧或额外增长。工具副本[probe](potato109-native-position-probe.py)与[完整离线报告](potato109-native-position-probe.md)保留；Path.resolve处理Mac Leptonica对/tmp路径问题。marker0仅未见红信号，必须结合真实尾部／CTA／copy。

原始视频分别 `/tmp/potato117-long-handoff-paced.mp4`、`/tmp/potato117-long-handoff-green.mp4`、`/tmp/potato117-long-handoff-final.mp4`，xcresult同前缀，保留在/tmp；完整旧原帧目录也保留。最终当前plain视频／结果及路径见[plain artifact](../plain-final-confirmed/artifact-paths.json)。最新相关UI首11项10过1红＋原样单项复验绿，间歇latest仍未定位，见[最终结果](../regressions/final-results.json)。
