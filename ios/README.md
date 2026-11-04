# 原生 iPhone 客户端

当前交付 #89：配置私人 HTTPS 地址、浏览助手会话、读取最近一页完整输入和回答。最低 iOS 17，Swift 6，iPhone；不在手机运行模型或保存后端完整历史。能力入口与状态归属见 [iOS 契约](../docs/modules/ios-sessions.md)。

## 打开与构建

使用 Xcode 打开 `Potato.xcodeproj`，选择 `Potato` scheme 和可用 iPhone 模拟器。工程已提交，不需要先安装 XcodeGen。仅修改工程结构时用 XcodeGen 2.46.0 执行 `xcodegen generate --spec ios/project.yml`。依赖精确版本和传递依赖提交记录保存在 `Package.resolved`，不自动升级。

命令行构建示例（替换设备 UDID）：

```sh
xcodebuild build -project ios/Potato.xcodeproj -scheme Potato \
  -destination 'platform=iOS Simulator,id=<UDID>' \
  -skipMacroValidation CODE_SIGNING_ALLOWED=NO
```

`-skipMacroValidation` 用于 CI 对已固定来源的 Swift 宏进行非交互构建；Xcode 中可审阅并信任这些包的宏后正常构建。它不关闭 HTTPS 校验。签名与 TestFlight 分发由 #95 验收，本任务不需要 Apple 账户即可在模拟器运行。

## 真实后端集成测试

`Integration/backend.py` 启动两个仅监听 `127.0.0.1` 的真实 FastAPI 服务和临时 SQLite：18489 含有可控模型生成的长输入/回答，18490 是真实空库。它只替换模型和查询资源，不复制后端 HTTP 业务；不读取用户的实际旅行数据库或密钥。

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

运行完用 Ctrl-C 停止验收服务，临时数据库自动删除。测试专用模拟器可删除或重置以移除 localhost 证书。实际服务、私网接入和本次验证范围见 [验收记录](../docs/acceptance/ios-readonly.md)。
