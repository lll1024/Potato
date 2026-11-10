# 连续正文与查询进展最终联合验收（#109／#117）

当前源码检查点为 `024b24bad05ca09cc7cb9b77377431d18cd4d753`。它修复完成事实、HTTP 历史和预览清除竞争，并包含后端 SSE 背压次序修复。当前无查询实际模型场景双端通过并立即同库重启，新增 SDK 调用 0；查询实际模型保留 `87a4141` 的真实结果：取得资料、持续正文、4203 字不完整回答已保存，但原生终态画面仍红。该缺陷修后以同正文／公开时序的受控 paced 界面和物理帧验证，未重新付费发查询。不能将两者简写为“当前构建两实际场景全绿”。[60 条故事映射](continuous-final-mapping.md)与下文保留这一边界，间歇 UI 失败交最终审查评估。

原反馈只确认“本机默认”，没有原输入或原二进制；本次验证同类默认配置，不声称完整复现未知输入。生产 `configured_planning`／`create_app`、实际模型参数、预算及隔离临时 SQLite 原样执行。受控响应回放仅用于诊断与修复验证，和真实调用分开标记。

## 构建与观察口径

真实查询使用 `87a41417655ab4941341bc11c8fb32911f06bece`；当前原生修复 `8654342` 合入 Web `2c275c8` 后为 `024b24b`，当前 plain、完整后端／Web／类型和相关原生 UI 均按此联合代码运行。最终提交只归档文档与证据。实际环境元数据记录各次 HEAD，不以旧检查点冒充当前构建。

环境为 `.env` 配置的 `deepseek-v4-flash`／`api.deepseek.com`、Anthropic SDK 1.11.0、Python 3.14.7、Chrome 156、iPhone 17 Pro／iOS 26.5 Simulator、Xcode 27.2／Swift 6.4.2。仅专用模拟器安装临时 HTTPS 根证书。未提交 `.env`、证书私钥、SQLite、依赖链接或原模型协议。原用户 8000 服务和数据库不参与本次验收。

两场景都先执行真实简短准备轮，iOS 导航到已存会话，Web 确认同一会话后，由原生原界面发送目标轮。两端复制以屏障串行使用共享系统剪贴板。调用总账把 preparation、target、无法关联的旧后台调用分开。

| 层 | 证据及边界 |
| --- | --- |
| SDK | 透明包装真实 stream／create，原事件原样传递；只存文本长度、时间、次序和返回，不延迟／分块／替换上游，不存推理或工具原参 |
| 后端 | ASGI 实际 SSE send 的公开安全元数据；SDK 返回、公开发送与可靠保存事实分别命名 |
| Web | 当前 plain 同一个 EventSource 的真实接收与 rAF／100ms DOM 样本；查询87原 DOM／EventSource 数组丢失，仅留原视频和保存后只读核验，不从后端发送补造浏览器接收 |
| iOS | 一次公开 app.snapshot() 后遍历不可变 AX 树；旁路 URLSession 明确只是 runner 核对，不称 App 收到 SSE |
| 画面 | 原录屏、生成期代表／全过程帧与完整终态窗口；AX／DOM 缺样和物理撤空分开判断 |
| 保存／复制 | 公开历史、可靠进展、用户按钮复制后粘贴、刷新／重开；进程内指纹只作请求一对一关联，不导出密钥或原载荷 |

SDK 为 code point，Web innerText 为 UTF-16，iOS AX 为 Swift grapheme。它们是排版树长度，不是物理视口字符数，不能跨层相减。样本数和正增长次数分开，生成期与历史重新挂载分开。实际 AX 间隔不能证明精确 200ms 上界；该上界沿 #113 公开可控时钟及界面验证。

## 实际模型结果

| 项目 | 87 实际查询 | 024 当前无查询 |
| --- | --- | --- |
| 输入 | 2026-11-14 杭州一日，两成人杭州东站往返，实际查西湖／茶博、交通、开放与预约 | 仅给定信息，十主题行前清单与十行检查表，输入明确全文约1500字；生产预算未改 |
| SDK 尝试 | 准备2＋目标9＝11，11/11关联；目标6查询阶段请求＋3正文请求 | 准备2＋目标2＝4，4/4关联 |
| 查询 | 20 工具调用均真实返回 completed；可靠进展24条。工具返回不等于已核实全部旅行事实 | 0工具，可靠进展空数组 |
| 正文 | 前两正文请求 SDK9／10：EMPTY_RESPONSE，各0文本事件；成功 SDK11：2647文本事件，4203字 Markdown | 成功正文1369文本事件，2148字 Markdown，delta最大8字 |
| 终态 | terminated／output_limit／model，真实不完整标签；非 completed | completed，真实完整标签 |
| 保存／复制 | Web只读保存页与原生独立哨兵复制均等于4203字持久正文；重开查询进展一致 | 双端真实复制及重开一致；同库立即重启历史相等、stream不变、instance变、新SDK0 |
| 自动入口 | 原Web1失败：严格预期completed实际terminated，非超时；原生1失败含2严格状态／完整label断言。补只读复制不把原红改成pass | Web1通过；原生1通过78.712秒 |
| 物理终态 | 原生305.545／305.700／305.751667准备轮/input重入，故事52红；305.890餐饮是正确保存尾部。Web148终态帧未见同类撤空 | 亲看90.2—94.5秒完整窗，段落→检查表逐行增长→完整复制，未见输入重入或正文撤空 |

[查询精确指标](continuous-final/query-final-confirmed/metrics.json)、[查询视觉修订](continuous-final/query-final-confirmed/visual-review.json)、[Web 原视频抽帧](continuous-final/query-final-confirmed/web-video/manifest.json)保留来源与限制。查询原生生成期997个样本中32次正增长，间隔 p50约480ms／p95约667ms／max约678ms，最大正增623 AX字符；终态AX没有零样本却漏掉物理红，不能拿AX非零替代视觉连续性。Web视频保留正文前缀、标题、表格2→7行、交通／开放／来源与餐饮尾；301.92秒真实输出上限和不完整复制接管。缺失 DOM／EventSource、完整身份接收时间及原始 DOM 分布不能追回。

[当前 plain 指标](continuous-final/plain-final-confirmed/metrics.json)中 Web总1005样本／391正增，生成期997样本／390正增，间隔p50=17ms／p95=67ms／max=234ms、最大增30；原生总90样本／17正增，生成期85样本／14正增，间隔p50约722ms／p95和max约1146ms、最大增408。原生末1979与Web末1821是不同排版单位，复制原文2148字相等另证。两端终态零样本均空，另有[连续物理帧](continuous-final/plain-final-confirmed/ios-terminal-frames/manifest.json)、[总览](continuous-final/plain-final-confirmed/terminal-contact-sheet.jpg)及[视觉复核](continuous-final/plain-final-confirmed/visual-review.json)。

查询87库已过偏好默认闲置阈值，未为补验而启动，避免额外后台SDK；它没有本轮及时同库重启结果。保存后双端回看／复制已核；重启当前由[plain及时0SDK](continuous-final/plain-final-confirmed/restart/comparison.json)及[9e旧查询基线](continuous-final/query-final/restart/comparison.json)分别支持，后者不是当前查询87重新执行。

## 终态交接诊断与修复

第一批883／41a自动增长、保存、复制通过，但189.3秒准备轮／输入回退。第一交接9e仍在查询148.262／148.362秒及无查询80.110秒回退。早先仅看AX零值窄窗的结论被完整contact sheet纠正，旧自动通过不计故事52通过。

沿公开 SessionAccess／HTTP／SSE 逐层红绿，保留停止、保存失败、失效与身份边界：

1. 持久完成先到：独立内存 reading handoff 仅由同作用域真实 model完成或budget/output_limit授权；raw null仍采用。交接未获真实历史时仍称当前正文，不冒充已保存／完整历史。新正文尝试／失败／失效立即撤，query／summary不误撤answer；持久failed精确排除、晚到旧A不能借B完成。按目标轮observedAt而非全局分页cursor核完成。
2. 同尝试累计／state交错：宣布版本floor与raw内容版本分离，同attempt累计推进交接，旧累计不回退；查询累计历史旧invalidated不撤独立answer。停止冻结仍采用原真实前缀；服务／进程／数据集／会话／轮次／404失效沿原规则。
3. **HTTP结束空快照先于SSE完成事实**：受控paced真实帧锁住4红，但AX50样本全绿。唯一caller诊断证明refresh先采用raw-null，Presenter为loaded/running且answer=nil，约250ms后finish才到；不是仅滚动task。原生refresh在同身份、原活动轮有合格正文、无storagefault且新snapshot释放该轮时，先沿既有history读取核对真实terminal并采用，再发布snapshot。等待期间保留上一已公布一致状态，不把null授权为保存；scope／generation／SSE新状态变化丢旧snapshot，错误沿原处理。无新轮询、timer、后台执行或协议。Web同公开2红以useSessionPage相同窄路径修复，见[Web交接验收](web-completion-handoff.md)。
4. **SSE send背压**：旧batch发trace期间轮次完成，可先发送新inactive／双null再补finish。新公开ASGI屏障在首trace和首state两位置各复现，2subcase红→绿。发送瞬时状态前补齐相应已持久cursor，三通道在首次yield前一起编码，发送中新增事实下批先补；不新增协议、不把null当完成。[公开审计](continuous-final/position-diagnosis/potato109-sse-completion-order-audit.md)与[红](continuous-final/regressions/sse-order-red.log.gz)／[绿](continuous-final/regressions/sse-order-green.log.gz)。

[位置诊断](continuous-final/position-diagnosis/README.md)保留候选、caller和所有物理边界。真实4203字／750个去重公开累计正增长的paced回放不加history稳定屏障，修前53.128333／53.160／53.191667／53.226667四个实际帧正文撤空；修后[93不同实际帧](continuous-final/position-diagnosis/potato117-long-handoff-final-marker.json)无正文marker重入、尾部稳定，[完整窗口](continuous-final/position-diagnosis/final-paced-frames/manifest.json)与像素检查补AX。55.213末批增长有短暂尾部／CTA未收束，55.281起在视口；不声称每帧完美跟底或亚帧绝对保证。首次不完整label AX3536时实际点击复制，预置独立哨兵后粘贴等于4203字，避免陈旧AX长度误判复制内容。

## 当前回归与版本

[机器结果摘要](continuous-final/regressions/final-results.json)和压缩原日志可追溯：

| 当前检查 | 实际结果 |
| --- | --- |
| 024 后端原生全量 unittest discover | **408项通过，364.919秒**；本票确实修改SSE业务，不再沿旧404冒称未改 |
| 024 普通Web完整全量 | **116通过／2显式live跳过，2.2分钟**；skip不计pass |
| 024 production＋harness＋Integration Pyright | **0 errors／0 warnings**；模块、文档、diff另查 |
| 024前端生产build；呈现单测 | build通过；13呈现单测通过（公开组件版本未再改） |
| 当前清DEBUG原生能力／快照 | **87 Swift Testing／11 suites通过1.922秒；10快照通过16.686秒** |
| 024受影响UI首批11项 | **10通过／1失败，421.476秒**。folding上翻保位通过，点击latest后完整copy label在y2152.3屏外；真实截图仍顶部。fixture IPv4存活，::1候选refused也见于通过用例，不能据此归因服务死亡 |
| 唯一失败项同源原样复验 | **1通过，24.391秒**，未改生产／用例／断言。首批该项61.123秒，折叠前异常慢；间歇滚动失败未定位，不称“11项一次全绿”或“已修复” |

原生其余10项覆盖增长→保存、延迟历史保位、前台恢复、折叠重开、查询重试／降级、远端停止、停止保存／未保存复制、长Markdown跟随／上翻、大小块及paced4203终态。真实模型18499完全停止后才启常规fixture；无额外实际请求。

历史兼容证据保留版本：41a全量82能力＋10快照＋25UI通过／2live skip；9e为84＋10＋10相关UI，但其真实画面红。旧Web82过／2skip／2容量失败以背景300→290最小校准（原阈值和断言不改）后b2为84／2skip；随后9e93、a8 104、87 111皆带2skip，只作当时基线。当前116取代它们作为完整Web结果。旧404不替代当前408。

## 失败、总账与范围

[全部实际调用总账](continuous-final/run-ledger.json)为 **64 SDK调用尝试＝准备16＋目标45＋无法关联3**，不等同供应商已收费请求或价格。最新记录的查询87与plain024为15/15关联、unmatched0，但不同构建／有限结论不能合称两个当前场景均通过。旧537失败服务目标结束约一小时后3次complete无公开请求映射，30／120秒间隔符合偏好闲置／退避契约只是推断，未猜配用途、未归入目标增长；早期10调用和最终13调用两份均留。

首AX标识轮2断言失败、第二轮陈旧XCUIElement141.860秒失败及相应Web协调屏障失败均保留；模型已保存不等于观察器验收通过。后改一次不可变snapshot，先只读旧会话再发下一轮。883／9e自动绿但物理红、87 output_limit严格断言红／丢失DOM、首paced AX绿但4帧红、首次修后固定中间copyY差117.667红都未覆盖。后者物理帧无对应位移，保留所有Y采样，将80pt断言用于稳定terminal，额外完整物理marker／尾部／首次copy核验，不靠单纯宽阈值变绿。

公开红绿日志包括同attempt／floor首6issues、历史PG误撤4、分页2、HTTP3→SSE2回退4、attempt归属2、旧started撤新snapshot2、HTTP历史屏障2和SSE背压2subcase。一次新增测试硬编码实参、0项过滤不计pass；87首组stop处理屏障夹具问题保留，按同字节流后续state屏障收敛后全87通过，无生产停止变更。

环境与未执行另计：旧86组首次1失败是有限8个fixture响应跨批耗尽，公开history确为failed/model_error；重启同受控fixture后86＋10通过。ffmpeg／TLS文件缺失均在目标前，TLS轮WebECONNREFUSED、原生1skip、SDK0；Web helper缺.venv两ENOENT未有效执行。自动审批用量／超时拒绝未执行，继续后同路径重试；一次live负向入口风险审批拒绝后先公开证明无POST与18499关闭，加硬预检再同显式flag获批，未隐去flag或绕过。当前finally故障验证故意1失败，归档checks_completed=false／capture_error=not_initialized，SDK0，证明异常也写证据而非实际模型成功。

[安全审计](continuous-final/evidence-audit.json)覆盖全部新证据、报告、映射及harness，gzip先解压扫描；图片人工复核，不将二进制密钥比对等同OCR审计。原始视频／xcresult在各artifact-paths注明的/tmp保留，本票自有进程停机记录另存。未跑真机、iOS17、TestFlight、私网／蜂窝；外部模型与旅行资料会变化，查询返回不等于已核实所有事实。未定位的间歇latest UI失败、查询87缺DOM及未及时restart、修后查询仅受控复验均是最终审查必须保留的限制。
