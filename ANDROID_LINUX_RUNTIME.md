# Kira + NapCat Android 本地宿主

**当前打包版本为 1.0.1 / versionCode 22；code 21 已由用户于 2026-11-16 确认通过真机验收。code 22 纳入最新界面源码、更新地址及旧地址迁移修复，真机结论另行记录。下文 code16 的验收记录属于更早版本。** 左上角菜单按钮打开左侧抽屉，集中放置面板工具、Kira 备份管理、NapCat 与 APK 更新、日志、后台和环境变量设置；底部固定独立“关于”按钮。外壳不提供 Kira 程序更新入口，更新交给 Kira 官方面板。底栏仅保留状态、Kira、NapCat 三项，选中项展开文字并以 420ms 弹性过渡。状态页与菜单工具统一采用彩色渐变、半透明圆角、白色细边与轻阴影；工具弹窗从底部弹入，可拖动回弹或关闭，背景缩放和模糊联动。输入框使用浮动标签与行内错误轻摇，按钮提供按压反馈。材质参考 [Auroraqua-UI](https://gitee.com/micromimo/Auroraqua-UI) 及用户附图，没有复制上游 React 源码。关于页显示实际外壳版本、已读取的组件版本，并提供项目主页和外壳更新入口。

code16 已通过 ARM64 构建、同签名、566 个官方源码文件、13 个原生库及七个宿主模块打包绑定核验。完整宿主回归70项，65通过、5个平台跳过；Android 11 x86_64 模拟器在普通屏幕、320dp / 130% 字体和系统关闭动画三种条件下，各41项抽屉、工具弹窗、环境变量、备份导出与删除检查通过。备份界面使用可校验的合成 tar，导出按真实字节比对；没有启动真实组件。Android 9–11 使用内存中的低分辨率模糊背景，Android 12+ 使用系统 RenderEffect；后者尚未在对应设备验证。最终 ARM64 包的界面 DEX 与资源和实际测试的通用包逐字节一致。没有恢复 QQ 或模型测试，ARM64 包尚未在手机覆盖安装；正式在线更新清单保持 0.5.0/code10。证据位于 `.runtime/ui-glass-backups-20261106`，直接 APK 交付目录为 `dist/android-kira-host-0.6.3-code16-20261106`，不另套 ZIP。code15 和 code14 的历史 UI 证据分别保留在 `.runtime/ui-drawer-20261106` 与 `.runtime/ui-shell-20261106`。

**以下真机运行证据来自 0.6.0 / versionCode 13 历史候选。** 包名沿用 `top.kira.pythonprobe`。2026-11-05，用户确认修订计划并恢复实施后，第三轮修复统一了成功清理后的进程归属释放。五项真实进程交接回归通过；完整宿主测试 47 项中 44 项通过、3 项平台跳过。code13 包含空值显示修复和 Android 私有目录复制适配；复制专项在 ARM64 手机上 15 项全通过，在 Windows 上 14 项通过、1 项跳过。手机已完成 code13 同签名覆盖及 26,038 个程序、数据和偏好条目的保留核验；code12 仅构建、未安装。code13 上 Kira v3.1.2 和 NapCat v4.18.42 已启动，两个官方 HTTP 面板均可访问。单独停止 NapCat 后，跟踪的 9 个所属进程全部退出，再次启动获得新进程；Kira 的 PID、运行标识和健康状态保持不变。以下实现与短时检查不表示完整 QQ 或长时间后台闭环通过。

APK 在私有目录内提供 Debian + PRoot，运行官方 Kira、Linux QQ 和 NapCat，无需 Android Root 或外部 Termux。目标为 Android 9+、ARM64；通用构建另含 x86_64，供对应测试环境使用。Android 层负责环境、启停、状态、日志、前台服务及 WebView；嵌入式 Python 只调度宿主。Kira 使用 Linux Python venv、原版 requirements 和官方 main/supervisor。NapCat 使用官方 Linux QQ + Xvfb 路线及原样官方安装器，不另行修改 Kira 或 NapCat 源码。官方提供 Debian PRoot 部署说明，但这不证明所有 Android 9+ 手机兼容。[NapCat 官方部署](https://napneko.github.io/guide/boot/Shell.html)

## 版本、路径与持久化

Kira 首装种子在构建准备时从官方 GitHub `releases/latest` 解析，源码与 `dist.zip` 使用同一发行版。构建从对应 tag 的 Git 原始对象归档源码及 requirements，并根据 release 资产元数据校验面板大小与 SHA-256；实际版本、提交及摘要记录在构建清单中。`verify_apk.py` 核对官方源码、面板、归档摘要和原生库。种子只用于首次部署，后续保留官方更新结果。

NapCat 首装基线为 `v4.9.43`，Linux QQ 为 `3.2.34-53644`，安装器固定到提交 `cbc0f10212a6a4d460b70861a5c8e2b6012119e5`。安装器、Shell 归档和对应架构 QQ 包均绑定准确大小与 SHA256。已有较新版本（包括旧来源的 `v4.18.42`）不因 APK 内置基线而自动降级。`NapNeko/NapCatQQ` 已不再提供发布资产，Shell 归档和更新检查自 2026-11-14 起改用 `xxynet/NapCatQQ`；原 `NapNeko/NapCat-Installer` 仓库同日已返回 404，固定安装器改从 `xiwangly2/NapCat-Installer` 的同一提交下载，大小与 SHA256 绑定不变，内容不一致即拒绝；该仓库是个人 fork，失效时需再次换源。[NapCat 基线发布](https://github.com/xxynet/NapCatQQ/releases/tag/v4.9.43)

下表 `files/` 指应用私有 files 目录；guest 路径位于持久化 rootfs 中，实际 rootfs 相对路径记录在 `files/linux-runtime/status.json`。

| 内容 | 固定路径 |
| --- | --- |
| Kira 程序、venv | guest `/opt/KiraAI`、`/opt/KiraAI/venv` |
| Kira 配置、插件、聊天 | `files/kira-core`，绑定 guest `/var/lib/kira` |
| Linux QQ | guest `/root/Napcat/opt/QQ` |
| NapCat 程序、配置、插件 | guest `/root/Napcat/opt/QQ/resources/app/app_launcher/napcat` |
| QQ 登录及用户数据 | guest `/root/.config/QQ` |
| NapCat 下载缓存 | `files/linux-runtime/napcat/downloads` |
| NapCat 安装尝试记录 | `files/linux-runtime/napcat/attempts/<本次标识>` |
| 自定义环境变量 | `files/linux-runtime/environment.json` |
| Kira 显式备份 | `files/linux-runtime/backups/kira-*.tar` |
| Kira 备份恢复的暂存区、切换记录和被替换的内容 | `files/linux-runtime/restore/` |
| NapCat 更新备份 | `files/linux-runtime/napcat/backups/<本次标识>` |
| 卸载本机 NapCat 时保留的配置与插件、卸载记录 | `files/linux-runtime/napcat/preserved/`、`napcat/uninstall.json` |
| 启动时自动清理的结果 | `files/linux-runtime/retention-report.json` |
| 基础环境迁移的备份、记录与回退副本 | `files/linux-runtime/migrations/<新基础环境 SHA-256>/` |

Kira 入口为 `/opt/KiraAI/venv/bin/python -u /opt/KiraAI/main.py --data-dir /var/lib/kira`，内部更新和重启交给官方 supervisor。NapCat 入口为 `xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox`，HOME 固定 `/root`。同包名、同签名且递增 versionCode 的 APK 覆盖升级保留现有程序、配置、聊天、插件及 QQ 数据；卸载或清除应用数据会删除它们。

## 环境安装、取消与重试

Debian 和 Node 按 ABI 随 APK 内置，归档用 `.payload` 避免 AAPT 展开，复制及解包前校验摘要。**首次准备仍需联网**安装系统库、Python requirements、Linux QQ 和 NapCat；调试时预置缓存不能证明全新安装免联网。

两个组件共用环境管理器，系统包和环境准备串行执行，避免 APT/dpkg 争用。宿主显式安装 NapCat 依赖，再以 `--skip-deps` 调官方安装器；依赖安装中断后先处理未配置完成的软件包。两组件分别记录状态、版本、进程归属、日志及错误，提供单独启动、停止、重启和一键启停。生命周期回归覆盖创建后取消成功／失败、归属变化、正常停止再启动和安装取消时另一组件存活。

状态页及通知展示实际阶段、整体进度和当前依赖传输。pip 使用公开 raw 进度，APT 使用 `APT::Status-Fd`；下载展示真实字节及增量速度，未知总量显示不定进度，不把单文件百分比当作整个环境进度。

候选取消流程先终止所属 guest 进程，再清理 PRoot tracer，保留已验证缓存。成功清理后只在进程锁内释放仍匹配的待登记归属；失败则保留归属、日志并禁止再次启动。NapCat 每次安装使用新的独立 scratch，失败记录保留；复制中断后的 pending 记录要求完成同一绑定版本，不能仅凭版本号认定成功。安装后逐文件比较官方归档并核对 QQ 版本，不把退出码 0 当作完整验收。生命周期自动化通过后，仍按阶段验证目标手机上的双组件运行及恢复。

NapCat 的缓存复制和更新备份保留文件正文、权限、时间及符号链接，不复制 Android 安全扩展属性；复制失败或取消时不生成完整备份标记。目标手机已复现并修复 `shutil.copystat` 路径中的权限失败，官方安装器与上游程序保持原样。

## 国内镜像与下载校验

APT 的 Debian bookworm、更新与安全源默认使用清华 TUNA HTTPS 镜像，保留 Debian 官方签名 keyring。pip 默认使用清华 PyPI 镜像，npm 使用 `registry.npmmirror.com`；用户可在环境变量中明确覆写 pip/npm 设置。Node 固定归档优先使用 npmmirror。固定版本的公开 Debian 归档、NapCat 安装脚本和 Shell 归档使用 GitHub 下载加速入口，网络或 HTTP 失败时有界回退到官方地址；QQ 仍使用腾讯官方 CDN。GitHub 加速服务不保证物理机房位于中国大陆，NapCat 最新版本元数据仍从官方读取。

镜像只替换下载传输地址，不改变 Artifact 的官方身份、固定长度、SHA256、断点校验或 HTTPS 要求；摘要不符直接失败，不接受该文件。只为白名单中的公开归档加速，不转发自定义地址、查询参数或私有凭据。五个公开镜像端点 HEAD 检查返回 HTTP200，不代表手机全新联网安装已经通过。

## 三入口与本机连接

底栏提供“状态”“Kira”“NapCat”三个入口。左上角菜单按钮打开侧边抽屉，包含面板令牌复制、环境与组件日志、备份、更新、后台设置及环境变量；“关于”固定在抽屉底部。点击遮罩、关闭按钮或系统返回均可收起抽屉，原页面与选中项保持；选择具体功能时先收起抽屉，再打开功能。两个官方面板保留各自认证，WebView 不提供原生 JavaScript 业务桥。

## 自定义环境变量与测试遥测

从“菜单 → 环境变量”按每行 `NAME=VALUE` 编辑，无需引号；值中的空格、等号和 shell 字符均按字面值保留，`#` 开头的整行可作备注。设置保存在应用私有目录，原子替换后供两个组件的新 guest 进程读取，正在运行的组件需要重启。变量直接加入 Linux `/usr/bin/env -i` 的参数，不改变 Android PRoot 加载环境，也不拼接进 shell 源码。支持最多128项、64KiB UTF-8 JSON；重复名称、非法格式和空字符会被拒绝，保存失败保持原文件。

`KIRA_LINUX_RUN_ID`、`KIRA_LINUX_HOST_PID`、`KIRA_NODE_AVAILABLE` 由宿主管理，不能自定义，以保留可靠停止所需的进程归属和能力事实。坏的环境文件会阻止新进程创建并留下不含变量值的宿主诊断；编辑器允许用户重新填写后明确保存修复。

默认 `KIRA_TELEMETRY_ENABLED=0`，缺少设置文件或清空自定义项时仍保留该默认。官方 Kira v3.1.2 在初始化遥测网络客户端前读取它，`0`、`false`、`no` 均关闭启动遥测；用户可显式覆写。本轮测试保持关闭：测试运行核验过摘要的官方 seed 源码，并以网络构造陷阱证明禁用路径未创建客户端、worker或UUID初始化任务。模拟器最终设置也仅保留该变量为 `0`，未启动真实组件。当前没有核实 NapCat／QQ 的等效关闭开关，不能将 Kira 的结果视作它们或所有插件的遥测均已关闭。

| 面板 | 本机地址 |
| --- | --- |
| Kira 官方面板 | `http://127.0.0.1:5267/` |
| NapCat 官方面板 | `http://127.0.0.1:6099/` |

OneBot 独立于这两个面板端口，初始正向 WebSocket 为 `ws://127.0.0.1:3001`，消息上报格式 `array`，使用认证 token。启动前检查所有官方账户配置，启用的非本机端点或空认证会阻止启动，已有业务配置不会被静默重写。官方面板运行期间仍可修改网络设置，因此初始化检查不能替代实际监听核验。[NapCat 官方配置](https://napneko.github.io/config/basic)

用户在 NapCat 官方面板登录 QQ，在 Kira 官方面板配置模型、QQ 适配器及允许名单，填入一致的本机地址和 OneBot token。QQ 登录后官方可能轮换面板 token，外壳复制时读取当前配置。Kira 默认允许名单需加入指定测试 QQ 或群。面板可打开、OneBot 已连接、事件已收到、名单已放行和模型已回复应分别验收，正文结合会话页核对。[Kira 官方 QQ 配置](https://docs.kira-ai.top/zh/configuration/adapter/qq.html)

## 后台运行

宿主使用独立 Android 前台服务及常驻通知。候选服务在准备、运行期间持有 CPU `PARTIAL_WAKE_LOCK`，全部组件停止、维护结束或服务退出时释放；通知提供“停止全部”。运行期间持锁与停止后释放已在目标手机核验；锁屏恢复及连续八小时运行仍须分别验收。

code13 现场发现目标手机休眠后部分同应用进程进入 `__refrigerator` 冻结等待，前台服务仍有效，面板请求曾超时；正常唤醒后已重新验证双面板可达。此项保留为后台兼容性问题，不能以界面状态为 ready 计作后台通过。临时延长亮屏的尝试在读取原设置时超时，没有修改手机屏幕设置。

随后已用外壳复制令牌进入 NapCat 官方 WebUI。用户最终确认手机登录 QQ `2695590911` 作机器人，QQ `1910749503` 作测试对象。通过 Kira 官方 API 新增本机适配器，原公网适配器保留并停用；本机连接 `ws://127.0.0.1:3001`，私聊允许名单仅含测试对象，群聊允许名单为空。手机 NapCat 主动发送的一条测试已得到成功回执并由用户确认收到；用户回复已进入 Kira 指定会话。该主动发送不计作 Kira 模型生成回复通过。

正常停止全部组件已核验无所属子进程并释放 CPU 唤醒锁；通过界面再次启动后，配置、测试聊天和 QQ 快速登录账号保留，点击官方快速登录可恢复在线，无需重新扫码。QQ 自动登录以及 Kira 无人工重连的恢复链路仍未通过。

旧模型及 AMD `DeepSeek-V4.1-Flash` 的完整会话曾返回 HTTP 429。AMD 小型工具调用和手机 Kira 官方模型健康检查通过，但不能抵销真实会话失败。当前通过官方配置使用 AMD 服务，默认 LLM 与快速 LLM 选用 `DeepSeek-V4.1-Flash`，输出上限为 2048 token；其他模型配置保留，密钥仅存手机私有配置。用户明确要求暂不测试并保留当前配置，故输出上限调整后的真实自动回复尚未验证，最小完整闭环未通过。

“菜单 → 后台与电池设置”显示电池优化状态并打开系统设置；用户还需按厂商应用管理允许后台活动。前台服务及 CPU 唤醒锁不能保证绕过 Doze、厂商省电、内存回收或强制停止，切应用、锁屏及恢复后的连通性须在目标手机实测。[Android 唤醒锁](https://developer.android.com/develop/background-work/background-tasks/awake/wakelock)、[Doze 与 App Standby](https://developer.android.com/training/monitoring-device-state/doze-standby)

## 更新、备份与恢复边界

Kira 更新由官方面板和 supervisor 完成，外壳不再设置 Kira 程序更新入口。备份管理仅从“菜单 → Kira 备份管理”进入，提供新建、刷新、导出，以及明确确认后的恢复和删除。点击“新建备份 → 开始备份”暂时停止 Kira，备份完整程序和数据，排除可重建的 venv 及 `__pycache__`，完成后恢复此前运行意图。确认备份成功后再从官方面板更新；直接在官方面板更新不会自动触发外壳备份。

新备份先写 `.partial`，完整读取 tar 内容、核对程序与数据根及 SHA256 后发布 `.tar` 和完成记录。成功发布后最多保留2份已验证完整备份，优先保留本次新备份，再清理最旧的完整备份；旧格式归档必须先验证才能参与清理。失败、未知文件、符号链接和未核验归档保留；提交后取消或清理失败时可能暂时超过2份，并保留日志，不能把它们当作已清理成功。菜单读取完成记录并实际重算 SHA256，旧备份或记录不匹配时显示“旧备份／待核验”。导出使用系统文档选择器，实际复制并校验字节；不自动恢复数据。

“菜单 → 更新 NapCat”需用户明确点击。宿主停止 NapCat，备份程序、配置、插件和完整 QQ 数据，执行官方更新后恢复此前运行意图，原本停止的组件保持停止。不盲传 `--force`，并检查更新后版本，防止自动降级。更新、备份和恢复运行意图的完整流程仍须单独验收。

失败时保留数据、日志、安装记录和已经形成的备份。**没有自动数据回滚**；恢复必须由用户在备份管理里对某一份已校验备份点击“恢复”并确认，避免旧程序或数据覆盖已更新内容。NapCat 的更新备份没有恢复入口。

恢复一份 Kira 备份时，宿主先停止 Kira，按完成记录核对归档 SHA256 和空间，再把归档解包到 `files/linux-runtime/restore/stage-*`。解包不跟随归档内的任何链接：路径越界、设备等特殊对象、重复文件、穿过链接写入、指向两棵目录之外的链接一律拒绝；树内的相对链接、指向 `/opt/KiraAI` 或 `/var/lib/kira` 的客体绝对链接和硬链接保留。暂存内容必须含完整的官方入口文件且不含 venv，之后才进入切换。取消、归档损坏、空间不足或写入失败发生在切换之前，当前程序和数据不变。

切换是一组同卷改名：当前程序和数据移入 `restore/previous-*`，暂存的两棵目录就位，原有 venv 移回新程序目录，由下次启动按 `requirements.txt` 摘要决定是否补装依赖。切换前写入 `restore/journal.json`；改名中途失败立即回滚，进程被杀则在下次启动、读取程序或数据之前回滚或补完。被替换的内容只保留最近一次恢复留下的那一份，更早的一份在新一次恢复成功后删除。恢复本身不删除任何备份归档。该流程目前只有宿主 Python 层的单元测试证据，APK 内运行和恢复后启动官方 Kira 尚未验收。未完成标记和 `.partial` 文件不能当作可用完整备份。

APK 使用独立 `android-host-update.json`，channel 为 `android-linux-host`，与手机控制助手的发布资产分开。更新器核对包名、递增版本、大小、SHA256 和签名，再交系统安装器确认；支持 HTTPS 清单及镜像前缀。候选 APK 验收后仍须同步清单版本和摘要并实际发布，手机才能在线发现；当前不声称 0.6.3 已发布。三类更新均保留现有程序和固定数据目录。

## 卸载本机 NapCat、自动清理与基础环境迁移

以下三项在 2026-11-15 实现到宿主逻辑层并有单元测试，尚未打包上机。

**卸载本机 NapCat（R5C4）。** 菜单“卸载本机 NapCat”先停止 NapCat，再把嵌在程序目录里的 `config` 与 `plugins` 复制到 `napcat/preserved/` 并逐个核对摘要，之后把程序目录改名移开再删除；QQ 登录数据默认保留，勾选删除时还要二次确认。改名是提交点：提交前取消或失败，程序和数据不变；提交后进程被杀，下次启动先补完卸载。安装尚未完成、目录是符号链接、上次保留的配置还没恢复时拒绝卸载。之后重新安装本机 NapCat，官方安装器完成后先把保留的配置与插件放回（覆盖安装器默认值，安装器新增的文件保留），再执行本机安全配置。共享的系统依赖、KiraAI 和 NapCat 更新备份都不动。

**启动时自动清理（R5C3）。** 每次启动在任何组件运行前执行一次，规则经用户确认：组件日志超过 8 MiB 轮转并保留 2 份旧日志（运行中每 5 分钟检查一次，超限时复制后原地清空）；NapCat 安装记录只留最近 3 次，失败或取消的记录只列出不删除，有未完成安装时全部保留；NapCat 下载缓存只留当前已装版本（未安装时保留内置基线）；旧面板备份留 2 份；APT/pip/npm 缓存只在可用空间低于 1 GiB 时清理；更新 APK 由应用在安装后（运行版本已不低于它）或 7 天未用时删除。KiraAI 与 QQ 数据、插件、KiraAI 备份、无法识别的文件和符号链接一律不动，结果显示在“设备状态 → 自动清理”。单项失败只记录，不影响启动。

**基础环境迁移（R2B）。** `base_migration.py` 只在宿主收到明确请求时运行，启动时遇到基础环境变化仍按原规则停在 `base_change_requires_migration`。迁移先停止全部组件并取得独占执行权，把旧环境里的 KiraAI 程序（不含 venv）、宿主工具、NapCat 程序和 QQ 数据备份并核对摘要；在暂存目录解包新基础环境、复制并再次核对，安装系统依赖、重建 venv、按需安装 NapCat 依赖，并用 KiraAI 数据的临时副本验证解释器；全部通过后才发布新目录并切换绑定。旧环境保留不删，每一步中断后可再次执行续接。回退只在新环境里的 KiraAI 程序与迁移时一致（没有经过官方更新）时允许，并把迁移后 NapCat 与 QQ 的数据带回旧环境；KiraAI 已更新时拒绝回退，应改用 KiraAI 备份恢复。应用界面暂未提供迁移入口，真实数据迁移须另行批准并上机验收。

## 构建与验收记录

`android-kira-host/build.ps1 -Abi arm64` 构建手机包，`-Abi universal` 另含 x86_64。本机先执行 `. 'D:\BaiduNetdiskDownload\kira这是缓存之类\Prepare-KiraTestWorkspace.ps1'` 同步当前源码并切换到外部测试副本；脚本引用的 `.runtime` 和 `build` 只能在该副本生成。工具链、测试环境、编译产物与日志的唯一根目录是 `D:\BaiduNetdiskDownload\kira这是缓存之类`，WSL 对应 `/mnt/d/BaiduNetdiskDownload/kira这是缓存之类`，不可回退到源码目录或系统缓存。验证须绑定实际 APK 大小、SHA256、签名及安装结果，区分宿主自动化、模拟器与各款手机证据。

本轮曾因成功取消后残留 `_process` 归属触发两轮修复停点，证据保留在本地停点报告中。用户批准修订计划后，第三轮把成功释放集中到 `_cleanup_owned`，耗时终止在锁外，锁内仅核对进程身份并释放归属；失败锁存和正常登记转移保留。正式回归通过真实新进程验证取消后的再次启动，不依赖测试手动清空归属。旧手机 TIME_WAIT 检查在连接创建阶段超时，仍作为单独的平台差异保留，不计为通过。

完整用户路径仍需目标手机实测：全新安装联网准备、两个官方面板启动、QQ 登录及模型／允许名单配置、在用户指定对象上接收消息并由 Kira 回复和发送、停止重启后保留配置及登录数据，再补充切应用和锁屏测试。当前不能声称真机完整闭环通过或所有 Android 9+ 设备兼容。

PRoot 共享 Android 内核，Node、QQ Electron 及原生扩展均受实际内核影响。Node 按官方基线与本机 node/npm/npx、子进程探测记录能力；失败不直接阻止 Linux Python Kira，对应插件仍需单独验证。[Node 固定版本构建要求](https://github.com/nodejs/node/blob/v24.22.2/BUILDING.md)

本工程纳入 AOCI，`.runtime`、`dist` 和上游副本继续按仓库规则排除；受管理对象稳定后由宿主统一维护索引。

本轮 AOCI 维护的范围预览、审批、Entry 更新与最终校验回执单独保留在 `.runtime/kira-napcat-20261105-resume`。本地 ADB 凭据的排除规则保留，不读取凭据正文。维护状态以最终机器回执为准；源码与 APK 验证结果不能替代认知对齐证明。中途的观察复核阻塞及处理记录保留在同目录的历史报告中。
