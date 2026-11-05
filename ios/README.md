# 原生 iPhone 客户端

当前交付 #89–#94：配置私人 HTTPS 地址、浏览助手会话、新建及续聊、约两秒前台核对、分页读取完整输入和回答，以及按服务和会话持久保存的本机草稿、未确认提交核对与同身份显式重试，以及跨设备停止和前后台恢复。最低 iOS 17，Swift 6，iPhone；不在手机运行模型或保存后端完整历史。能力入口与状态归属见 [iOS 契约](../docs/modules/ios-sessions.md)。

本人安装、两端 Tailscale Serve、Mac 启动恢复、TestFlight 更新到期、故障定位及五项蜂窝真机验收步骤见[交付说明](DELIVERY.md)。#95 当前仅完成本机验证与说明，签名上传、TestFlight 安装、私网及真机尚未验收；分层结果与 50 条故事映射见 [#95 验收](../docs/acceptance/ios-delivery.md)。

## 打开与构建

使用 Xcode 打开 `Potato.xcodeproj`，选择 `Potato` scheme 和可用 iPhone 模拟器。工程已提交，不需要先安装 XcodeGen。仅修改工程结构时用 XcodeGen 2.46.0 执行 `xcodegen generate --spec ios/project.yml`。依赖精确版本和传递依赖提交记录保存在 `Package.resolved`，不自动升级。

命令行构建示例（替换设备 UDID）：

```sh
xcodebuild build -project ios/Potato.xcodeproj -scheme Potato \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  -skipMacroValidation CODE_SIGNING_ALLOWED=NO
```

`-skipMacroValidation` 用于 CI 对已固定来源的 Swift 宏进行非交互构建；Xcode 中可审阅并信任这些包的宏后正常构建。它不关闭 HTTPS 校验。签名与 TestFlight 分发由 #95 验收，模拟器运行不需要 Apple 账户。

## 真实后端集成测试

`Integration/backend.py` 启动六个仅监听 `127.0.0.1` 的真实 FastAPI 服务和临时 SQLite：18489 含有可控模型生成的长输入/回答，18490 是真实空库。18492 用于提交集成，已通过真实偏好后台保存「不吃辣」。18493 独立用于恢复竞争和模型执行次数核对。18494 用事件控制真实在途模型返回，用于双客户端停止、前后台恢复和长会话操作验证。18495 预先通过真实 HTTP 保存 53 个会话与一段 55 轮历史，使用 `Integration/reading-answer.md` 的中文标题、列表、宽表格与预约入口样例。它只替换模型、查询资源和偏好时钟，不复制后端 HTTP 业务；不读取用户的实际旅行数据库或密钥。

测试使用 HTTPS，App 保持系统证书校验。为测试模拟器创建短期 localhost 证书并安装；不要把此证书用于私人网络服务，也不要提交私钥：

```sh
mkdir -p /tmp/potato-ios-tls
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout /tmp/potato-ios-tls/key.pem -out /tmp/potato-ios-tls/cert.pem \
  -days 7 -subj '/CN=localhost' \
  -addext 'subjectAltName=DNS:localhost,IP:127.0.0.1' \
  -addext 'basicConstraints=critical,CA:TRUE'
xcrun simctl boot <UDID>
xcrun simctl bootstatus <UDID> -b
xcrun simctl keychain <UDID> add-root-cert /tmp/potato-ios-tls/cert.pem
.venv/bin/python ios/Integration/backend.py \
  --cert /tmp/potato-ios-tls/cert.pem --key /tmp/potato-ios-tls/key.pem
```

服务打印 `READY` 后，在另一个终端从仓库根目录运行：

```sh
xcodebuild test -project ios/Potato.xcodeproj -scheme Potato \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  -skipMacroValidation CODE_SIGNING_ALLOWED=NO
```

运行完用 Ctrl-C 停止验收服务，临时数据库自动删除。测试专用模拟器可删除或重置以移除 localhost 证书。实际服务、私网接入见 [#89 验收](../docs/acceptance/ios-readonly.md)，发送及轮询证据见 [#90 验收](../docs/acceptance/ios-submission.md)。

草稿每次编辑同步保存到本机，切换会话、返回列表或重启后可继续；离线可编辑，恢复连接不会自动发送。输入框显示真实保存状态，失败时保留编辑内容并可重试保存。迟到接受只清理匹配的旧草稿版本，不清除较晚输入；新建页接受后当前编辑内容随页面归入真实会话。验证见 [#91 验收](../docs/acceptance/ios-drafts.md)。

发送前先在手机 GRDB 原子保存原始请求；接受表示电脑已登记，完整回答以后端保存历史为准。发送结果未知时保留原身份与输入；重连或重启后先核对服务和数据集，再查询原提交。已接受可打开原会话，未查到接受记录时由你明确选择“重试同一提交”；不会自动发送。重试保持原身份、完整原文和原始目标，404 后仍在途的旧发送由后端幂等约束合并。会话已删除、内容冲突和网络未知分别提示，不开放同提交重试。切换服务或数据集后原文和归属在待确认面板可取回，切回原归属后继续核对。见 [#92 验收与演示](../docs/acceptance/ios-recovery.md)。

手机可查看并停止电脑发起的当前轮次，列表和对话共用全局状态。停止请求固定使用操作时捕获的原轮次；“正在停止”期间仍等待外部调用与保存收束，后端释放名额后才允许发送。连接断开显示暂不能核对，不把任务改成失败。App 切入后台停止客户端轮询，电脑继续执行；回到前台立即刷新并补齐完整结果。验证见 [#93 验收](../docs/acceptance/ios-lifecycle.md)。更早会话与历史均可在手机继续加载，支持横向阅读表格、系统浏览器打开链接和复制完整 Markdown 回答。验证见 [#94 验收](../docs/acceptance/ios-reading.md)。
