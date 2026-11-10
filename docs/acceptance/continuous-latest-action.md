# #109「回到最新」审查 P2 闭环

结论：原生首批 `testProgressFoldingPreservesPausedReadingAndLatestAction` 的真实失败已定位为测试点击与保存布局竞争。保存时运行状态行收起，按钮下移；XCTest 已选取的旧中心随后落到按钮上方，没有触发操作。受控重放逐值重现该坐标，重新取得当前按钮中心后立即恢复跟随。生产代码无改动；测试改为核对公开保存状态后重新点击，并补生成期间恢复跟随的后续批次观察。[机器摘要](continuous-latest-action/results.json)。

## 原始失败与定位依据

原 [11项首批日志](continuous-final/regressions/ios-final-reading-first.log.gz) 的 10过／1红仍保留；原样24.391秒复验和本次[未修改的5次通过](continuous-latest-action/unchanged-baseline-five.log.gz)仅说明间歇性，均不作为修复证据。

从原始 xcresult 导出的[合成触摸原件](continuous-latest-action/original-synthesized-tap.plist)独立解码得按下／抬起均为 `(331.833333, 664.833333)`，间隔50ms，无拖动位移。失败后的 AX 按钮为 `x273.6667, y673.3333, width116.3333, height34.3333`；该点在按钮上方8.5pt。[原始活动树](continuous-latest-action/original-activities.json.gz)和[时间／来源hash](continuous-latest-action/original-event-timeline.json)可复核。

原录像[48.532秒帧](continuous-latest-action/original-before-48.532.jpg)仍有“正文尚未保存…”运行状态行，[48.595秒帧](continuous-latest-action/original-after-48.595.jpg)该行和停止入口已消失，按钮下移约25.667pt；正文仍停在上方。[47—51秒原编码短片](continuous-latest-action/original-click-47-51.mp4)保留该窗口。录像帧是实际媒体时间，活动树是宿主记录时间；不以二者相减声称亚帧精度。原始事件的旧中心与下述受控观测逐值相同，是定位的主要交叉证据。

## 可证伪对照

先提出四项假设：保存布局移动导致旧坐标漏点；动作后被拖动重新暂停；动作后旧布局／保位器覆盖滚动；事件被系统拦截。实际触摸无位移已不支持拖动假设；受控操作进一步区分“没有触发”与“触发但滚动失败”。

使用现有 `ios/Integration/backend.py` 的 `query-reading` 模式，经真实本机HTTPS／SSE、规划及临时SQLite，到最后一批正文时先读取当前按钮中心，再释放最终响应。等待公开完整复制标签后重放原中心；随后用正常 `latest.tap()` 重新取得当前按钮位置。[临时诊断源码](continuous-latest-action/temporary-coordinate-diagnostic.swift.txt)仅为留档，已从测试 target 删除，不是最终回归。

[坐标附件](continuous-latest-action/controlled-coordinate-observation.txt)给出：

| 观察 | 实际结果 |
| --- | --- |
| 保存前按钮 | y647.666667，高34.333333；中心y664.833333，与原事件相同 |
| 保存后按钮 | y673.333333；向下25.666667pt |
| 重放旧中心 | `copyHittable=false`，仍停上方；[真实截图](continuous-latest-action/controlled-stale-tap.png) |
| 重新点击当前中心 | 尾部完整复制可见，“回到最新”消失；[真实截图](continuous-latest-action/controlled-current-center.png) |

[唯一前缀动作日志](continuous-latest-action/controlled-action-runtime.log.gz)只在 fresh 点击时记录一次 `action`（14:31:52.446），随后 `follows=true`；旧坐标点击没有动作。日志中的68pt几何外框包含按钮外层留白，按钮命中比较采用独立AX的34.333pt矩形。

[诊断原红日志](continuous-latest-action/controlled-coordinate-red.log.gz)：实际执行1项，24.682秒，仅旧坐标后的 `copy.isHittable` 故意硬断言失败；同一用例后续 fresh 点击的可见与恢复跟随断言通过。此红用于重现测试漏点机制，不能称为产品缺陷被生产代码修复。

## 精准修改与最终验证

原 folding 用例保留上翻、折叠锚点误差小于3pt、新增长期间暂停、完整复制按钮可见等原功能断言。只在最终响应后，先等待公开“复制完整回答”与停止入口消失，再重新查询并点击按钮；未扩大超时、重试点击、放宽位置阈值或更改产品滚动逻辑。新增恢复跟随后入口消失断言。

新增 `testLatestActionResumesFollowingForSubsequentAnswerBatches`：真实生成期间主动上翻、确认尾部不可见、点击最新恢复，随后分别释放五次真实批次／最终返回，先分别观察“日期”“周六”“第四批阅读标记”“最新一批旅行建议”实际出现在可访问性树，再确认尾部仍可见且未暂停，最后采用完整保存标签。这保留生成期间点击的独立覆盖，避免只测试静态历史。

[最终构建](continuous-latest-action/final-build.log.gz)通过；[最终相关UI](continuous-latest-action/final-related-ui.log.gz) **2通过／0失败，51.791秒**：生成期29.600秒，原folding22.192秒。[生成期终态截图](continuous-latest-action/final-generating-follow.png)／[保存后主动最新截图](continuous-latest-action/final-saved-latest.png)。生产 `ConversationView.swift` 相对 `8fe93f4` 无diff；所有临时DEBUG与故意红测试已清除。

本次只改UI测试及本P2证据文档，未重跑此前408后端、116Web＋2skip、87能力＋10快照、全11项UI或真实query/plain；不把这些旧结果冒称本次重跑。模块、文档、diff及新证据安全检查结果见机器摘要。原真实query87、受控paced与plain024的分层限制继续有效；未验收真机、iOS17、TestFlight和私网蜂窝。

## 环境故障与清理

最初诊断阶段Xcode等待模拟器启动／安装，之后一次Runner启动因 `XCTDaemonErrorDomain Code18: Timed out waiting for AX loaded notification` 失败，均无TestCase执行；[AX原日志](continuous-latest-action/environment-ax-init-timeout.log.gz)、[启动等待栈](continuous-latest-action/environment-startup-wait.sample.gz)和[设备boot](continuous-latest-action/environment-device-boot.log.gz)保留，不计红绿。

恢复仅涉及本任务专用AB9010设备、其测试Runner和经root明确授权的宿主CoreSimulatorService重建，[实际恢复日志](continuous-latest-action/environment-host-recovery.log.gz)显示同设备完成boot。未erase、清缓存、新建设备、修改宿主信任、关闭用户Chrome或Preview。自动审批发生过用量失败／超时，相关命令当时未执行，后授权重试才执行；不计测试结果。最终实际诊断和两项UI均在原iOS26.5设备完成。

全程使用现有受控fixture及临时SQLite，无新真实模型调用、用户8000或原数据库访问。归档只含固定fixture正文、按钮坐标、测试／系统状态与日志，不含私钥、认证头、真实模型协议或内部推理。服务、runner和唯一前缀日志由本任务停止，未给用户留下新模拟器。
