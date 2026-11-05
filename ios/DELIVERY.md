# iPhone 安装、连接与日常维护

适用于本人使用的 Potato 原生客户端（Swift 6，最低 iOS 17）。Mac 保存助手会话与旅行者偏好，手机只保存连接、草稿和未确认提交。当前分发及实测状态以 [#95 验收](../docs/acceptance/ios-delivery.md)为准；下面的步骤是操作说明，不表示已经完成 TestFlight 交付。

## 准备 Mac 服务

在现有仓库按[根 README](../README.md)安装 Python、前端依赖并配置 `.env`。模型和查询密钥只留在 Mac。沿用电脑当前使用的数据目录；另选空目录会得到不同的数据集，而非原来的助手会话。

```sh
cd /Users/jarviliu/projects/travel
npm run build --prefix frontend
.venv/bin/python web.py --port 8000 --data-dir /Users/jarviliu/projects/travel/.travel-data
```

服务仅监听 `127.0.0.1`，保持现有单进程数据目录锁。若已有同目录服务在运行，复用该进程；不要同时启动第二份或删除锁文件。先用 Mac 浏览器打开 `http://127.0.0.1:8000`，确认现有会话可见，再检查公开状态：

```sh
curl --fail --show-error http://127.0.0.1:8000/api/snapshot
```

Mac 需联网、接电并保持唤醒，终端服务需持续运行。离开前可在另一个终端运行 `caffeinate -i`，保持该命令运行；它不能让关机、合盖或断网的 Mac 提供服务。终端退出或系统重启后，重新运行上面的启动命令。默认不安装开机服务；`Serve --bg` 也不会启动 Python。

停止服务用其终端的 Ctrl-C，等待在途请求及保存收束。再次启动必须使用同一数据目录；服务重启会恢复已提交事实，将被中断轮次按真实状态展示，不自动重放执行。备份时先停止服务，再复制完整数据目录；不在运行中单独拷贝 SQLite 主文件。升级前保留数据目录备份，按对应版本迁移说明恢复。

## 接通私人 HTTPS

1. Mac 与 iPhone 安装并登录 Tailscale，加入同一私人网络，在两端确认已连接。由本人完成登录和 iPhone VPN 授权。确认该私网的设备访问规则允许手机访问此 Mac 的 TCP 443；不增加公网入口。
2. Mac 先执行 `tailscale status` 和 `tailscale serve status`。若已有其他 Serve 配置，先核实端口及路径归属，不直接覆盖或 reset。
3. 首次启用 Serve 时，按 Tailscale 提示在管理页面启用所需的 MagicDNS 和 HTTPS。为本机 8000 服务启用根路径代理：

   ```sh
   tailscale serve --bg --https=443 http://127.0.0.1:8000
   tailscale serve status
   ```

4. 将下文尖括号占位替换为 Serve 的实际输出。使用输出中的完整 `https://<Mac>.<tailnet>.ts.net` 根地址。在 Mac 上先运行以下命令，再在已接入同一私网的 iPhone Safari 中打开相同 `/api/snapshot` 地址；两端均应收到真实 JSON：

   ```sh
   curl --fail --show-error 'https://<Mac>.<tailnet>.ts.net/api/snapshot'
   ```

5. 手机 App 的「连接配置」填写 HTTPS 根地址，点击「保存并连接」。不填写 `/api` 子路径、不填写 Mac 的 `localhost`，不输入模型或地图密钥。App 使用系统证书校验，无需安装开发用 localhost 证书。
6. 真机远程核对时关闭手机 Wi-Fi，保留蜂窝网络和 Tailscale VPN，再次从 Safari 与 App 读取。仅在 Mac 或同一 Wi-Fi 下可访问不能证明蜂窝访问通过。

使用 [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)，其服务范围为 tailnet；不启用 Funnel。`--bg` 使 Serve 配置在设备／Tailscale 重启后恢复，但 Mac 后端仍需单独启动。停用本代理可在确认其归属后执行 `tailscale serve --bg --https=443 off`，不要使用会清除其他代理的全局 reset。命令与持久化行为见 [Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve)（2026-11-05 核对）。

## 签名与 TestFlight 首次安装

需要本人有效的付费 Apple Developer Program 会员、App Store Connect 权限和日常 iPhone。本机无签名构建及归档只能验证编译与归档产物，不能安装、上传或替代签名验证。

1. 在 Xcode → Settings → Accounts 登录本人的开发者账号。注册可用的 App ID，在 App Store Connect 建立对应 iOS App 记录；选定 Team、唯一 Bundle ID，保留记录供后续更新。
2. 用 Xcode 打开 `Potato.xcodeproj`，选择 Potato scheme。在 Potato 的 Signing & Capabilities 选择付费 Team、Automatic Signing 和注册的 Bundle ID；不要选择免费 Personal Team 作为 TestFlight 交付。检查嵌入的 SessionKit framework 签名团队与 App 一致。
3. 持久配置需同步到 `project.yml` 再用 XcodeGen 2.46.0 生成工程，避免下次生成丢失。当前仓库的 `com.potato.travel` 是未注册验证的初始值，Team 尚未配置。不要把账号令牌、私钥或描述文件提交到仓库。
4. 目前工程尚未提供 AppIcon 资源。正式分发前在 Potato target 的 Assets.xcassets 新建 AppIcon 组，按 Xcode 要求填入图标槽（含 App Store 所需图标），将 App Icon Set Name 设为该组名称，并同步 project.yml／生成工程；核实隐私清单及第三方 SDK 报告、应用信息和出口合规问答。未经过 Organizer / App Store Connect 校验，不能认为上传条件已全部满足。
5. 保持部署下限 iOS 17、Swift 6 和提交的 `Package.resolved`。设定版本及递增的 build number（当前 `0.1.0 (1)`），选择 Any iOS Device，Product → Archive。正式归档不要使用 `CODE_SIGNING_ALLOWED=NO`。
6. 在 Organizer 检查签名归档，执行 Validate App，再 Distribute App → App Store Connect → Upload。根据当前界面完成签名及合规步骤；上传、处理和可测试是不同状态。保留版本、build、Git 提交、归档日期、校验与上传结果，不将本地 Archive Succeeded 写成上传成功。
7. 构建处理完成后，在 App Store Connect → TestFlight 的内部测试组添加本人并分配构建；完成所需的测试信息和合规问答。本人属于有该 App 权限的 App Store Connect 用户时可使用内部测试；外部测试按 Apple 当前要求处理审核，不建立公开邀请链接作为本次默认方案。
8. iPhone 安装 Apple TestFlight，使用受邀账号接受邀请、安装 Potato，记录实际设备与 iOS 版本，再按下节完成五项场景。首次安装成功后验证一次覆盖更新，确认草稿和连接仍保留；不要通过卸载重装测试更新保留。

操作依据：[上传构建](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/)、[Xcode 分发](https://help.apple.com/xcode/mac/current/en.lproj/dev2539d985f.html)、[TestFlight 概览](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/)（2026-11-05 核对）。登录、会员开通及手机授权由本人完成；不要向 Issue 提供密码或签名材料。

## 五项真机验收

使用同一签名构建、真实 Mac 后端与实际旅行记录，在日常 iPhone 上关闭 Wi-Fi、启用蜂窝数据与 Tailscale 后执行。先记下 App 版本/build、Git 提交、手机型号／系统、Mac 后端版本、时间及时区、数据集 `stream_id` 和私网地址（公开证据可脱敏）。每项均记录步骤、预期、实际、轮次／提交身份及截图；自动化和模拟器证据另列，不能替代此表。

| 场景 | 操作与通过条件 | 需保留的事实 |
| --- | --- | --- |
| 1. 电脑开始，手机续聊 | 电脑 Web 发送一条当前旅行要求；手机蜂窝打开同一会话，阅读并发送修改；电脑刷新读取相同完整回答 | 两端相同 `session_id`、各轮 `turn_id`、输入／回答与真实终态；确实使用蜂窝 |
| 2. 后台／锁屏恢复 | 手机发送并确认接受，在仍执行时切到其他 App，再做一次锁屏；Mac 继续运行；回前台立即核对同轮进度或完整回答 | 接受、离开及返回时间；同一轮次；手机离开期间 Mac 执行／完成事实。若轮次太快而未跨后台，重做该项 |
| 3. 发送中断网 | 发送前后制造实际断连，出现未确认状态；另编辑较晚草稿；恢复网络先核对原提交。若未查到，明确选择重试同一提交 | 原 `submission_id`、完全一致的原文及原始目标；后端只有一个接受轮次；晚草稿保留；重连未自动 POST。未产生结果未知的断网不算该项通过 |
| 4. 手机停止电脑轮次 | 电脑发起尚在执行的轮次；手机捕获并停止它；等待真实收束后再发送。另核对原轮次结束后旧停止目标不会停止后来轮次 | 原目标 `turn_id`、停止请求与释放快照、保存的真实终态、后续轮次身份。请求停止不保证最终一定是停止态，以保存事实为准 |
| 5. Mac 离线与草稿 | 手机输入未发送草稿，停止 Mac 服务使其不可达；手机明确连接不可用，离线编辑并重开 App 仍保留草稿；Mac 恢复同数据目录后刷新 | 离线提示、恢复后的原文；恢复不自动发送。另做 Mac 休眠／断网检查，区分后端退出与设备离线 |

可从 Mac 的公开 `GET /api/snapshot`、`GET /api/submissions/{id}` 和完整会话历史核对身份及保存事实，HTTP 参数与状态见[会话契约](../docs/modules/sessions.md)。不要靠手机的“已接受”字样推断执行完成，也不要仅凭回答内容相似断言没有重复执行。实际模型／查询的执行事实可在电脑 Web 执行轨迹核对；证据避免包含密钥和私人旅行全文。

完成后将结果追加到 #95，关联证据；签名、上传、本人安装、覆盖更新、私人 HTTPS、五项蜂窝场景和最低系统实跑各自记状态。有未完成项时保留 #95 打开；不自动关闭父规格 #88 或合并 PR。

## 更新与到期

TestFlight 构建最多可测试 90 天，具体剩余时间和到期日以 TestFlight / App Store Connect 显示为准。当前没有已上传构建，因此没有可填写的实际到期日。每次发布都记录上传时间、到期日、版本/build 与 Git 提交；建议每 60 天准备下一构建，到期前至少两周完成安装核对。这是维护节奏建议，本仓库未创建自动提醒。[Apple TestFlight 说明](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/)

后续更新复用同一 Team 和 Bundle ID，递增 build number，运行回归后重新签名归档、校验、上传并分配到测试组。依赖保持锁定；只有专门升级并验证兼容性时才更新。手机在 TestFlight 更新，核对服务地址、既有草稿及未确认提交恢复；升级不能重发未确认输入。保留上一个归档及 dSYM，记录故障对应 build。

到期无法启动时先在 TestFlight 安装新构建，再恢复原连接和核对原提交；后端已接受的轮次不会因手机构建到期而自动停止。不要先卸载 App：卸载会丢失本机草稿及核对材料。手机没有完整历史离线副本，Mac 仍是会话和偏好的唯一持久来源。

## 出现问题时

| 现象 | 按顺序处理 |
| --- | --- |
| 尚未配置／地址未保存 | 使用 Serve 输出的 HTTPS 根地址；保存失败先保留输入，检查手机可用存储。切勿填密钥或忽略证书错误 |
| 连接不可用／超时 | 先在 Mac 核对本机 `/api/snapshot`，失败则处理服务或 Mac；本机正常再查两端 Tailscale、Serve 状态及私网 HTTPS；Safari 正常但 App 失败时核对 App 地址、客户端／后端版本，再刷新。最后在蜂窝网络重复验证 |
| Mac 关机、休眠、服务退出 | 恢复 Mac 网络和唤醒状态，重新启动同目录后端，检查 Serve 代理。手机可继续保存离线草稿，恢复不会自动发送 |
| 发送结果未知 | 保留待确认面板中的原文与归属，点击核对；只有明确未查到时才选择“重试同一提交”。不要新建身份、卸载或清空本机记录来绕过核对 |
| 已删除／内容冲突／归属隔离 | 已删除不允许同提交重跑；冲突保留原材料；隔离时切回原服务／数据集核对。不要把旧输入自动投到新数据集 |
| 正在停止／暂不能确认 | 等新快照确认原目标真实释放；连接恢复后先刷新。不要从页面位置或后来活动轮次猜停止目标 |
| 后端保存故障／未保存事实 | 保留草稿及原提交身份，暂停新输入。在 Mac 检查磁盘、权限和服务输出，保留故障记录，修复后用原数据目录重启并核对历史。未保存回答不能当作已完成历史，不能靠重复发送修复保存失败 |
| 本机草稿读取／保存失败 | 先复制当前输入到安全位置，检查可用存储，再用页面提供的重读／重试保存。未保存时不发送；不要用重装作为首选排障 |
| 构建到期／更新不可用 | 在 TestFlight 核对到期状态；按更新流程上传并安装有效构建。若无构建可用，在 Mac Web 继续使用后端，保留手机本地数据等待更新 |

维护入口是 [#95](https://github.com/lll1024/Potato/issues/95) 的未完成验收记录及[仓库 Issues](https://github.com/lll1024/Potato/issues)。反馈需附版本/build、实际系统、复现步骤、时间及时区、连接层检查结果和可脱敏的提交／轮次身份。崩溃附对应 build 的系统崩溃报告或 TestFlight 反馈；不上传 `.env`、完整数据库、私钥或账号凭据。
