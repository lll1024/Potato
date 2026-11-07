# iPhone 本机集成与交付准备（#95，尚未完整交付）

最新补充见[2026-11-07 图标交付准备](#2026-11-07-图标交付准备88--95)：AppIcon 已补齐，完整 iOS 回归和无签名归档通过；用户已有 iPhone、尚无付费开发者会员，TestFlight 及真机交付仍未完成。下文 2026-11-05 的结果保留为历史证据。

2026-11-05（Asia/Shanghai），继续 `codex/ios-client` 与 [PR #96](https://github.com/lll1024/Potato/pull/96)。复验生产基准为 `f5ca6eb821fa3768e88c632a3088651dac5a6b48`，已集成 #89–#94；#95 的原生阻塞任务 #92、#93、#94 均已关闭。本次补交付文档、导航和证据，并修正 App target 的仅 iPhone 配置；保留工作区原有领域文档改动，不修改后端、Web／CLI、电脑数据库或会话业务实现。

用户在本次实施中明确：**尚未配置，先完成本机验证和交付说明**。因此 [#95](https://github.com/lll1024/Potato/issues/95)保持打开，父规格 #88 不修改、不关闭，PR 不自动合并。本文不是完成 TestFlight 交付的声明。

## 本次交付材料

- [iPhone 交付说明](../../ios/DELIVERY.md)：Mac 同目录服务启动／恢复、Tailscale Serve 两端配置、私人 HTTPS 分层诊断、付费签名与 TestFlight 安装／覆盖更新、90 天到期维护、连接／保存故障处理、五项蜂窝真机操作和留证要求。
- [50 条用户故事逐项映射](ios-delivery-mapping.md)：每条指向实现、公开行为或原生交互测试，并明确尚缺的真机／私网／分发证据；另核对共享单例、VIPER、技术栈、协议、存储及范围限制。
- [iOS 公开契约](../modules/ios-sessions.md)与根／iOS README 已同步当前功能和交付入口。

## 分层验证状态

| 层级 | 本次结果与边界 |
| --- | --- |
| 自动化公开行为、原生交互与快照 | 完整 iOS suite **82 项／含参数化 115 例通过，0 失败、0 跳过**；[机器汇总](ios-delivery/test-summary.json)、[完整日志](ios-delivery/full-suite.log.gz) |
| 真实服务集成 | 六个本机真实 HTTPS / FastAPI 服务及临时 SQLite；外部模型、查询和偏好时钟可控；不使用本人生产数据或密钥。完整套件通过。覆盖两个客户端原提交幂等、404 与在途接受竞争、接受响应丢失、真实停止收束、前后台恢复和完整分页；[服务就绪记录](ios-delivery/full-suite-backend.log) |
| Release iPhone arm64 归档 | 修正设备范围后 `xcodebuild archive` 无签名通过；[完整日志](ios-delivery/archive.log.gz)、[产物元数据](ios-delivery/archive-summary.json)。仅证明本机 Release 编译和归档，不证明签名、上传或可安装 |
| 最低系统 | 工程及归档部署下限 iOS 17；**未在 iOS 17 实际运行**。唯一可用 runtime 是 iOS 26.5（23F77），不能用它替代最低系统验收 |
| 签名／TestFlight | **未完成**。本机 `security find-identity` 为 0 个有效身份；未配置付费账号／App Store Connect App，未签名、未上传、未处理、未安装、未测覆盖更新；无实际上传／到期日 |
| Tailscale 私人 HTTPS | **未完成**。本机存在 Tailscale CLI／App，但 `status --json` 与 `serve status` 均在 10 秒超时，未取得可核实状态；没有配置或更改用户私网，不以本机 localhost HTTPS 替代私网 |
| 真实 iPhone／蜂窝五场景 | **全部未完成**。`devicectl` 仅列模拟设备；用户账号／日常手机尚未配置。模拟器切后台不是锁屏／蜂窝真机验收 |
| Python／Web／静态检查 | Python **361/361**；Web 构建与呈现测试 **13/13**；iOS 夹具 pyright **0 错误**；模块归属通过。日志：[Python](ios-delivery/python.log.gz)、[Web 构建](ios-delivery/web-build.log.gz)、[呈现测试](ios-delivery/web-test.log.gz)、[类型](ios-delivery/types.log.gz)、[模块](ios-delivery/modules.log.gz)。公开导航链接、四份交付文档本地链接、50 条故事完整性与格式检查通过（[导航](ios-delivery/docs.log)、[交付文档](ios-delivery/delivery-links.log)） |

环境检查见 [environment.txt](ios-delivery/environment.txt)。Xcode 27.2（27B52），Apple Swift 6.4.2，工程语言模式 Swift 6、严格并发，iPhone 17 Pro／iOS 26.5 模拟器。依赖沿用 `Package.resolved` 的精确发布及 revision，无升级。完整套件在本次开始时的工程构建上执行（会话业务代码未改）；发现并修正仅 iPhone 配置后，另以重建的六个测试后端复验最终工程的全部单元／集成／快照与连接导航，**72 项／含参数化 105 例通过，0 失败、0 跳过**，见[最终工程汇总](ios-delivery/final-project-summary.json)、[日志](ios-delivery/final-project.log.gz)和[重建服务记录](ios-delivery/final-project-backend.log)。

## 归档适用范围与已知分发缺口

最终归档位于本机 `/tmp/potato-95-iphone.xcarchive`；临时目录不作为长期交付物，二进制不提交 Git。保留压缩构建日志与归档元数据，重建命令见 [validation.txt](ios-delivery/validation.txt)。归档没有签名，不可导出为本次已验证的安装包；没有执行 Organizer Validate App、App Store Connect Upload 或 TestFlight 处理。

初次归档发现 App 实际支持 iPhone 与 iPad（`UIDeviceFamily=[1,2]`），不符合规格的“仅 iPhone”。原因是 XcodeGen 的 target 默认值覆盖 project.yml 全局设置；已在 Potato target 明确设为 `1` 并重新生成工程。实际产物断言先失败后通过，见 [修正前元数据](ios-delivery/archive-before.json)、[红日志](ios-delivery/family-red.log)、[最终元数据](ios-delivery/archive-summary.json)与[绿日志](ios-delivery/family-green.log)。App 与 SessionKit 的 Mach-O 均为 arm64 / IOS / minos 17.0；最终不再出现完整方向警告。生成时同步四个既有快照资源引用，图片及会话实现未修改。

当时仍缺注册验证的 Bundle ID、付费 Team、签名／描述文件及 AppIcon 资源；AppIcon 已在下文 2026-11-07 补充中完成。AppIntents 元数据提取跳过是无该框架依赖的提示。后续签名前须检查隐私清单／SDK 报告、合规及应用信息，再执行实际分发校验；无签名归档通过不能消除这些缺口。具体操作见[交付说明](../../ios/DELIVERY.md)。

## 后续验收入口

| 项目 | 状态 | 继续条件 |
| --- | --- | --- |
| 付费签名、归档校验、上传、本人安装与覆盖更新 | 未完成 | 本人会员／Team、注册 App、可用签名身份、图标及分发校验、日常 iPhone |
| iOS 17 实跑 | 未完成 | iOS 17 runtime／实际设备；记录系统版本及关键交互，不把部署目标当实跑 |
| 私人 HTTPS | 未完成 | 两端登录同一 tailnet、Mac 后端在线、Serve 与访问规则核对 |
| 1. 电脑开始、蜂窝手机续聊、两端相同结果 | 未完成 | 真实 Mac 服务、已安装构建、蜂窝与私网 |
| 2. 发送后切 App／锁屏，返回读取同轮结果 | 未完成 | 实际跨后台及锁屏的在途轮次 |
| 3. 发送断网再核对，无重复且保留晚草稿 | 未完成 | 制造真实结果未知，保存原提交与唯一接受事实 |
| 4. 手机停止电脑原轮次，真实释放后续聊 | 未完成 | 记录原目标、停止收束及后来轮次不受旧目标影响 |
| 5. Mac 离线明确不可用，草稿保留且不自动发送 | 未完成 | 真实服务离线及设备离线，恢复原数据目录 |

每项按[五项真机验收步骤](../../ios/DELIVERY.md)追加实际版本、时间、身份、操作、结果和证据到 #95，分别记录通过／失败／未完成；不预填通过。直到账号、签名、上传、安装、私网及真机证据齐备，#95 均不关闭。

## Standards

以 `f5ca6eb` 为固定基准，对本次暂存变更独立审查。初审及最终复核均为 0 项规范违例、0 项代码异味；示例 URL 增加引号并明确替换占位。生成工程与源配置同步，原有无关文档未纳入提交。

## Spec

独立对照 #88、#95 和用户本次“先完成本机验证和交付说明”的范围，初审及最终复核均为 0 项问题。50 条映射、两轮测试范围与机器汇总一致，真实归档确认设备范围与部署下限；签名、分发、最低系统实跑、私网及五项蜂窝场景保留明确缺口。

双轴结论：Standards 0 项，Spec 0 项；两轴均无遗留最高严重问题。该结论不改变尚未完成完整交付的状态。

## 2026-11-07 图标交付准备（#88 / #95）

本次从最新远程主分支 `b8e451e6f8c57d6136e8c7ce33a8c924e112e99b` 建立 `codex/ios-delivery-88`；#89–#94 已完成，原客户端 PR #96 已合并。用户再次要求实施 #88，并确认已有 iPhone、尚无付费 Apple Developer 会员，选择蓝白旅行路线标记。本次补齐可独立处理的 AppIcon 及交付材料，不修改会话业务、依赖、后端或 Web。原有未提交的领域文档保留。

- [AppIcon](../../ios/Potato/Assets.xcassets/AppIcon.appiconset/AppIcon.png) 为不透明的 1024 × 1024 PNG；[绘图源](../../ios/Design/generate-app-icon.swift)可在仓库根目录重新生成。PNG 与 XcodeGen 工程连续重建逐字节一致。`project.yml` 显式指定 AppIcon，生成工程将资源目录加入 Resources。图标独立视觉审查为 `ship`，范围仅资产的小尺寸辨识与系统裁角空间；既有视觉系统和原生页面保持原样。
- 完整 iOS suite **87 项／含参数化 127 例通过，0 失败、0 跳过**；实际为 iPhone 17 Pro / iOS 26.5（23F77）模拟器。包含 `SessionAccess` 公开边界、真实文件 GRDB、七个本机 HTTP／HTTPS FastAPI 服务与临时 SQLite、原生交互及快照；外部模型和查询可控。证据：[机器汇总](ios-delivery/icon-tests-summary.json)、[完整日志](ios-delivery/icon-tests.log.gz)。这不替代真实私网、付费模型或蜂窝真机验收。
- Release 无签名归档通过；真实 `Info.plist` 的 `CFBundleIcons` 指向 AppIcon，`Assets.car` 包含 1024 × 1024、`Opaque=true`、`Idiom=phone` 的图标。`UIDeviceFamily=[1]`；App 与嵌入 SessionKit 均为 arm64 / IOS / minos 17.0。归档包含既有 GRDB、Alamofire、FactoryKit 隐私清单，但未执行分发合规校验。证据：[产物及环境元数据](ios-delivery/icon-summary.json)、[归档日志](ios-delivery/icon-archive.log.gz)、[重建与验证命令](ios-delivery/icon-validation.txt)。临时归档 `/tmp/potato-88-icon.xcarchive` 和 xcresult 不提交 Git。
- 模块归属、18 份公开导航与契约链接、格式检查通过。纯客户端图标与说明变更未重跑 Python／Web 全量 suite；上文旧结果不作为本次重跑结果。

本次本机仍为 0 个有效签名身份，设备查询仅见模拟设备，未连接真实 iPhone。Tailscale `status --json` 和 `serve status` 分别在 10 秒后超时，未取得可核实连接及代理状态，未修改私网配置。**付费会员／Team、注册 Bundle ID、签名、Organizer 校验、上传、TestFlight 安装及覆盖更新、iOS 17 实跑、私人 HTTPS 和五项蜂窝场景均未完成**；无实际上传或到期日。图标补齐后，#88 和 #95 继续保持打开，剩余步骤按[交付说明](../../ios/DELIVERY.md)由本人账号和真机条件继续，不自动合并。

### Standards

以 `b8e451e` 为固定基准，按先审查后提交的顺序，独立审查全部 14 个暂存文件。最终 0 项规范违例、0 项值得报告的判断性代码异味，无最高严重问题。

### Spec

独立对照 #88、#95 及用户本次确认，复核原始归档、xcresult 与提交证据；最终 0 项发现，无最高严重问题。图标补齐与本机检查不等同于完成 TestFlight 或真机交付，两个 Issue 均保持打开。
