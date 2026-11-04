# iOS 只读链路验收（#89）

日期：2026-11-04。范围为 [#89](https://github.com/lll1024/Potato/issues/89)，父规格为 [#88](https://github.com/lll1024/Potato/issues/88)。构建、模拟器、真实后端和私网真机分别记录。

## 工具链与依赖

- Xcode 27.2（27B52），Apple Swift 6.4.2；项目使用 Swift 6 模式及严格并发检查。
- 最低部署 iOS 17；实际运行 iPhone 17 Pro 模拟器，iOS 26.5（23F77），arm64。没有 iOS 17 runtime，最低系统的实际运行未验证。
- XcodeGen 2.46.0；生成的工程与源配置同时提交。无需运行生成器即可打开工程。
- Alamofire 5.12.2、FactoryKit 3.5.2、GRDB 7.12.0、Swift Navigation 2.11.2、MarkdownUI 2.4.1、SnapshotTesting 1.19.7；精确版本与传递依赖 revision 在工程 `Package.resolved` 中。

## 自动化与模拟器

测试命令、临时 HTTPS 证书及真实服务启动见 [iOS README](../../ios/README.md)。真实服务来自公开 `create_app`、真实临时 SQLite 和已有可控外部资源；手机经 Alamofire 访问 Uvicorn HTTPS，不以复制业务规则的假后端代替唯一集成证据。

| 验证 | 本次结果 |
| --- | --- |
| 完整 iOS suite | 14 项测试、含参数化共 21 例，0 失败、0 跳过；[机器汇总](ios-readonly/test-summary.json) |
| 共享公开 interface | 地址落盘与重开、7 类无效地址、真实空库与不可达、已有会话全文、404、HTTP/解码错误、迟到成功/失败、较新刷新优先与两种换库顺序通过 |
| XCTest 导航 | 保存配置 → 列表 → 完整对话 → 系统返回 → 重启恢复地址 → 空库 → 服务不可用 → 清除配置通过 |
| SnapshotTesting | 浅色空列表与深色 accessibility3 大字号两张基准已审阅并通过；固定 390×844、中文及 iOS 26.5；截图夹具清理调整后单独重跑两项通过 |
| 构建 | 模拟器构建与 `generic/platform=iOS` arm64 无签名构建均通过；iOS 17 deployment target 未提高 |
| 原有 Web/CLI 回归 | 后端 361 项、前端呈现 13 项及浏览器 53 项通过，前端构建通过；pyright 0 错误，结构与导航检查通过 |

六库均实际使用：Alamofire 完成 HTTPS 请求和解码，FactoryKit 在 App 入口解析并持有共享实例，GRDB 保存配置并重开核对，Swift Navigation 的 item 绑定完成打开与系统返回，MarkdownUI 渲染真实长回答，SnapshotTesting 运行两张基准。Swift Testing 验证共享能力，XCTest 负责交互与截图；SnapshotTesting 放在单元测试目标，避免 Xcode UI 测试目标禁用 Swift Testing 所造成的模块加载问题。

命令与输出节选见 [验证摘要](ios-readonly/validation.txt)。首次前端浏览器运行因沙箱无法启动 Chrome 失败，允许浏览器进程后重跑 53 项全部通过。新验收夹具另以 `PYTHONPATH=tests .venv/bin/pyright --pythonpath .venv/bin/python ios/Integration/backend.py` 检查，0 错误；最终版本重新启动打印两个 HTTPS 服务的 `READY`，随后关闭。

实际模拟器交互截图：[会话列表](ios-readonly/sessions.png)、[完整输入与回答](ios-readonly/conversation.png)、[真实空列表](ios-readonly/empty.png)、[连接不可用](ios-readonly/unavailable.png)。基准图片位于 [浅色空列表](../../ios/PotatoTests/__Snapshots__/SnapshotTests/testEmptySessions.empty-sessions-light.png)和[深色大字号](../../ios/PotatoTests/__Snapshots__/SnapshotTests/testUnconfiguredDarkLargeText.unconfigured-dark-accessibility3.png)。截图中的旅行内容为明确的测试数据，不是已核实的真实行程建议。

## Standards

首次独立审查发现 1 项 P2：View 直接把后端状态转成展示文案。现已移到对应 Presenter，并由对话展示投影提供文本。复核确认原问题消除，未发现新增实质规范问题；未解决问题为 0。

## Spec

首次独立审查发现 1 项 P2：同一地址换库后仍可能接受迟到的旧历史。先通过公开 interface 复现旧页成功返回，再引入数据集身份与读取世代核对；补充历史先发现换库、旧刷新随后失败的相反顺序，两者通过。列表在身份变化后返回并重新读取，复核未发现新增实质规格问题；未解决问题为 0。

两轴首次各 1 项 P2，修正后 Standards 0、Spec 0；无遗留最高严重项。审查固定基线 `ce6f070`，仅比较本任务改动，保留工作区既有文档和调研材料。

## 私人网络接入

此环境未安装或登录 Tailscale，未连接真实 iPhone；下列为可操作步骤，不视为已完成的私网实测。模拟器 localhost HTTPS 通过不能证明蜂窝网络或 TestFlight 可用，完整交付归 #95。

1. 在 Mac 与 iPhone 安装 Tailscale，用自己的账户登录同一 tailnet；开启 iPhone 的 Tailscale VPN。确认 Mac 保持在线、不休眠，私人网络策略允许本人 iPhone 访问该 Mac。
2. 在 Mac 按仓库 README 配置并运行原后端：`.venv/bin/python web.py --port 8000`。保留原 `127.0.0.1` 监听及单进程数据目录锁；先用 `curl http://127.0.0.1:8000/api/snapshot` 验证本机服务。
3. 在 Mac 执行 `tailscale serve --bg http://127.0.0.1:8000`，按 CLI 提示启用 tailnet HTTPS；用 `tailscale serve status` 取得实际 `https://机器名.网络名.ts.net` 地址。此地址只在 tailnet 内可访问。不要执行 Funnel。[Serve 官方说明](https://tailscale.com/docs/features/tailscale-serve)、[命令说明](https://tailscale.com/docs/reference/tailscale-cli/serve)。
4. 在 App「连接配置」填写上一步的 HTTPS 根地址并「保存并连接」。成功后完成设置，在列表打开电脑已有的助手会话。App 无需模型、地图或搜索服务密钥。
5. 真实跨网络检查由用户在 iPhone 关闭 Wi-Fi、保留蜂窝数据和 Tailscale VPN 后执行；应能打开同一会话并读到保存的完整内容。停止 Mac 后端后，App 应显示连接不可用；恢复后手动刷新重连。本任务不包含发送、停止和前后台轮询。

故障定位顺序：先检查 Mac 本机 `/api/snapshot`，再检查两端 Tailscale 在线和 `serve status`，最后检查 App 地址。HTTP 错误、解码失败及网络不可达有独立说明；它们不能证明电脑端对话轮次已失败。正常 HTTPS 证书校验始终启用，不增加 ATS 例外。撤销本服务代理可用 `tailscale serve --https=443 off`。[官方 CLI](https://tailscale.com/docs/reference/tailscale-cli/serve)。

## 未验证范围

- Tailscale 私网、真实 iPhone 蜂窝网络、iOS 17 实际运行。
- Apple 会员、真机签名、归档上传及 TestFlight 安装（#95）。
- #90–#94 的发送、草稿、提交恢复、停止、轮询、更早分页及丰富阅读验收。
