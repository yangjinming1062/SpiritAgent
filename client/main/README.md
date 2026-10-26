# Client 主进程

[entry.ts](entry.ts) 是唯一组合根，显式装配账户、窗口、Runner、缓存与平台宿主；[preload.ts](preload.ts) 和 [preload-background.ts](preload-background.ts) 暴露不同权限的桥。业务契约归 [PROTOCOL](../../docs/PROTOCOL.md)，共享缓存归 [Client](../README.md#资产与历史缓存)，本文维护主进程内部准入和资源生命周期。

## 包边界

| 包 | 职责 |
|---|---|
| `backend` | 凭据、HTTP、账户 WS 与会话恢复 |
| `runner` | 本地进程、RPC 桥和 Runner 更新 |
| `lifecycle` | 窗口、托盘、退出、桌面宿主与应用更新 |
| `ipc` | 注册受控操作，持有缓存与本机能力服务 |
| `security` | sender、文件路径和接口策略校验 |
| `shared` | 主进程叶子类型与组件，通过注入端口协作，不导入 backend／runner 实现 |

`main/shared` 与跨进程契约 `client/shared` 分开。主入口输出 ESM `entry.js`，沙盒 preload 输出 CJS；preload 混入 ESM import 会使整座桥失效。新增模块只导出装配函数，不能在导入时创建资源。

## 启动与退出

启动先取得[单实例锁](lifecycle/single-instance.ts)、设置 [Chromium 策略](lifecycle/platform.ts)，再确定 Home 并设置 `userData`，随后创建日志、配置和会话。`SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖时 Home 为其下的 `spiritagent-home`，具体解析见 [paths.ts](security/paths.ts)。凭据读取须等 `app.whenReady()`，避免提前缓存 safeStorage 解密失败。

[随包技能同步](lifecycle/bundled-skills.ts) 位于日志器初始化之后、会话恢复及 Runner 自动启动之前。仅打包版本变化时覆盖同名技能，保留其他内容；`.no-bundled-skills` 跳过同步，全部成功才更新版本标记，失败下次启动再试。默认单实例，验证开关 `SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 仅用于隔离测试；转发器就绪前的第二实例事件折叠后兑现一次。

关窗与退出分别处理。[app-quit.ts](lifecycle/app-quit.ts) 负责配置、日志、Runner 与收听记录收尾，有界等待不保证残留进程已结束；更新重启提前设置退出标志并复用此链。后台节流全局关闭，renderer 自行管理功耗；远程显示的 GPU／透明回退由 `platform.ts` 决定。

## 渲染面准入

### 凭据与请求

[session.ts](backend/session.ts) 保管多账户凭据，preload 不暴露持久凭据读取，托盘账户操作留在主进程。[connection.ts](ipc/connection.ts) 统一接收操作标识和参数，[api-allowlist.ts](security/api-allowlist.ts) 按生成契约的 IPC 策略校验；WS 票据仅由账户网关申请。multipart 经专用媒体通道传字节或已授权路径，由主进程构建 `FormData`。存储和错误语义见 [凭据落盘](../../docs/PROTOCOL.md#凭据落盘)及[错误信封](../../docs/PROTOCOL.md#错误信封)。

macOS 钥匙串验证须从已登录图形会话启动，SSH 安全会话可能不可交互访问。macOS 15 及以上的局域网访问还需应用“本地网络”权限；用途声明在 [package.json](../package.json)。Terminal／SSH 连通不能代替图形客户端验收，平台依据见 [Apple TN3179](https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy)。

### 文件与媒体

文件读取统一经 [hardening.ts](security/hardening.ts) 的 `readUserSelectedFile`：只接受选择器或拖拽登记的路径，并另挡敏感路径。白名单仅在当前进程有效，重启后历史附件不自动取得许可。附件信箱 `chat:set-pending-feed` 只传递意图，消费者仍须走受控读取；不能由 renderer 自行补白名单。

## 账户网关与窗口订阅

[backend/gateway.ts](backend/gateway.ts) 持有账户唯一 WS、工具派发、取消及待确认结果；共享 [GatewayConnection](../../shared/gateway.ts) 管重连、心跳、重放和快照缓冲。窗口释放不关闭连接或已受理调用。刷新同一登录的凭据保留在途工作，换号废弃旧连接与迟到结果。

窗口先订阅再读取状态，IPC 携鉴权会话 ID；[ipc/gateway.ts](ipc/gateway.ts) 只开放连接状态与受控情境上报，不开放设备 RPC。`tool.call` / `tool.cancel` 直接派发 Runner。启动失败保留原因供重载后恢复，非 headless 工具工作态同样可恢复；连通后清除旧失败。同会话快照读取合并为完整基线。

工具结果保留至后端确认，重连只补交结果。未决调用按 `call_id` 查询 Runner 日志，不能通过再次执行取得旧结果；取消、未知结果与允许继续等待的范围归 [调用日志契约](../../docs/PROTOCOL.md#调用日志与未知结果)。[ensure-backend.ts](backend/ensure-backend.ts) 只缓存地址和就绪结果，凭据逐请求读取；reset 和票据签发前后均复核身份。

## 窗口与几何

[surfaces.ts](lifecycle/surfaces.ts) 串行管理唯一主窗口、锁屏及显隐快照；[surface-companion.ts](lifecycle/surface-companion.ts) 管创建前加载的本机偏好。打开、隐藏、最小化和模式交接的产品行为归 [窗口与会话](../../docs/DESIGN.md#窗口与会话)。

- 只有实际几何或显隐变化推进快照版本；已有窗口通过导航事件打开目标，不重载 renderer。新窗口读当前快照，完成和失败都回灌实际状态。
- Windows 透明主窗口自行保存内容区还原矩形并按工作区展开，不依赖可能失真的原生 `isMaximized()`；未手动改变时复用原矩形，避免高 DPI 取整漂移。macOS 使用原生最大化。
- 内嵌生活区不扩大原生窗口边界。主进程合并 sender、账户、偏好、窗口显隐、锁屏与 renderer 上报的舞台布局；renderer 还须即时撤销本窗隐藏资格，资源真正显示后才回执。
- 精灵显隐由 [tray.ts](lifecycle/tray.ts) 的关闭拦截器统一广播。背景节流已关闭，不能用页面可见性推断原生精灵是否隐藏。未认证唤起须更新激活视图，不能只 raise 窗口。
- [sprite-position.ts](shared/sprite-position.ts) 保存静止位置及屏幕原点；[ipc/sprite.ts](ipc/sprite.ts) 的位置写入和跨屏仅接受窗口模式精灵 sender。默认比例跨窗广播，本机偏好原子保存失败保留原值。

快捷键冲突由 [shortcuts.ts](ipc/shortcuts.ts) 返回调用方；主题与透明命中共享规则见 [Client](../README.md#窗口与主题)。

## 桌面承载与恢复

[desktop-presentation.ts](lifecycle/desktop-presentation.ts) 持有唯一模式状态与串行切换；`desktop-windows.ts` 持有窗口与就绪等待，`desktop-health.ts` 持有显示器／电源监听，`desktop-background-runtime.ts` 持有媒体字节、请求代次和回执。[desktop-host.ts](lifecycle/desktop-host.ts) 装配统一宿主端口；原生事务分别归 [Windows helper](../desktop-windows/README.md) 与 [macOS 模块](../desktop-macos/README.md)。

切换先核验 sender、账户与载荷，再经 [ui-state.ts](ipc/ui-state.ts) 捕获来源视图并等待目标恢复。快照按账户存于进程内，不包含持久文件许可；录音、转写和上传共同阻止切换。组件缺失或准备失败在释放来源前返回实际失败，页面加载与就绪等待共享预算。

交互桌面使用完整 preload，背景仅接收已认证的媒体字节和播放状态。静态环境与透明造型分别传入背景窗口合成，所有回执复核已派发请求、账户与呈现代次。显示器逻辑坐标只管布局；Windows 目标像素经 `desktop-display-resolutions.ts` 的只读原生查询取得，不从取整 DIP 反推。桌面窗口固定缩放，退出恢复窗口模式缩放。

Windows 的 [explorer-desktop-host.ts](lifecycle/explorer-desktop-host.ts) 等界面就绪后再交 helper 接管；主进程在准入、排队和回执时隔离旧会话。renderer 上报托盘槽位及滚动视口，主进程转物理坐标并保留固有尺寸，不能把裁剪后位置反馈为尺寸。样式确认后才发布就绪并用 `showInactive()` 校准 Electron 可见性；原生层级、托盘、AppBar 与 journal 由 helper 管理。`platform.ts` 关闭 DirectComposition 视频叠加层以参与页面模糊合成，保持硬件解码；修改须重启。

macOS 的 [desktop-macos-host.ts](lifecycle/desktop-macos-host.ts) 传全局 points，工作区变化只改布局；AX 不可用时仍可呈现，运行窗口投影独立报不可用。进入原生代码前落活动标记，正常退出清除，硬崩溃使下次启动停留窗口模式。失焦交互窗的鼠标位置由 `desktop-windows.ts` 采样转换为本窗 DIP，恢复焦点丢弃迟到采样；不合成点击、不请求焦点。

真实点击和显式导航才请求焦点；广播不抢前台，队列执行时重核账户、舞台和锁屏。Windows 焦点请求超时终止 host 并恢复；macOS 已提交 AX 操作不可撤回，保留未知结果。

退出顺序是停止宿主、恢复系统、销毁窗口。`stop()` 可供更新取消后重新启用，`dispose()` 幂等关闭准入并取消交接，持续接收退出期上报，`will-quit` 最后注销 IPC。换号、鉴权失效、拔交互屏、renderer 失败及休眠唤醒均收敛到窗口模式；迟到准备不能重新接管。活动屏与首选屏分开，缺屏回退不改首选，只有活动屏几何变化才重挂载。

Windows 启动先处理待恢复记录；guardian 的异常原因与主进程失败标记分开保存，手动重试不能覆盖 guardian 原因。恢复失败保留 journal 与诊断但允许窗口模式启动。恢复界面只读取已有呈现，不提交制作；完整切换和回执语义见 [呈现契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

## Dock 与应用目录

[desktop-dock.ts](ipc/desktop-dock.ts) 合并固定配置、运行快照与图标；[desktop-app-catalog.ts](ipc/desktop-app-catalog.ts) 装配平台目录。renderer 只取得不透明条目／窗口 ID，激活、关闭和启动语义归 [启动器契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

| 范围 | 实现约束 |
|---|---|
| Windows 身份 | [windows-app-catalog.ts](ipc/windows-app-catalog.ts)、[windows-installed-apps.ts](ipc/windows-installed-apps.ts) 合并开始菜单、桌面快捷方式、App Paths 和 AppsFolder；普通程序按规范 exe，打包程序按 AUMID 去重，同名 exe 的父子安装目录可关联启动器，其余目录分开 |
| macOS 身份 | [macos-app-catalog.ts](ipc/macos-app-catalog.ts)、[macos-application-target.ts](ipc/macos-application-target.ts) 使用解析符号链接后且保留大小写的 `.app` 路径，所有入口复核 bundle 和可执行文件 |
| 固定配置 | `desktop-dock.json` 只存固定项，Windows 优先保留快捷方式及参数，打包应用存 AppsFolder 入口；添加、修复和启动重核目标，修复保留 ID／顺序，写盘失败保留原值 |
| 运行投影 | 原生快照保留各屏归属，Dock 只投影交互屏；运行查询失败保留最近快照并报告不可用，不能用过期数据完成授权 |
| 扫描与图标 | 句柄限当前扫描版本，图标按可见项分批；固定标记更新复用同版目录，手动重扫才更新句柄，旧回执不能覆盖新扫描 |
| Windows 图标及安装校验 | [windows-application-icon.ts](ipc/windows-application-icon.ts) 共用提取链，支持全 PNG 图标资源；AppsFolder 安装查询独立缓存，失败不拿过期结果冒充成功，也不替换仍在使用的目录句柄 |
| Jump List | 按菜单懒读 `CustomDestinations` 的 Task；helper 重新核验不透明任务 ID 后启动原参数，解析失败保留普通菜单，任务 ID 不落盘 |

目录扫描各来源独立限时，过滤无效、网页、虚拟及隐藏入口；目录和固定项属于本机，不上云。启动与定位监听归桌面会话，目录启动不修改固定配置。

## 配置镜像

[runner-config.ts](ipc/runner-config.ts) 的整份配置和路径操作仅接受主窗口／交互桌面；偏好走窄通道。`prefs.ts` 仅接受 `companion.*` 点键，主题、技能和工具集用 `mutate`，云水合和账户隔离分别用 `applyCloudMirror`、`clearSyncedMirror`。

[runner-config-store.ts](shared/lib/runner-config-store.ts) 串行落盘，拒绝原型链路径及非 JSON 值；普通失败回滚内存，云水合和隔离清理即使写盘失败仍保留新内存值。[config-sync.ts](shared/lib/config-sync.ts) 用 `flushQueued` 补跑在途编辑、`authEpoch` 阻断旧账户及水合回环。语言修改共用 [client-language.ts](shared/lib/client-language.ts)，不推给 Runner。所有权与冲突规则归 [配置契约](../../docs/PROTOCOL.md#配置所有权与云同步)。

## Runner 生命周期

[ipc/runner.ts](ipc/runner.ts) 拥有端点与自动启停；[bridge.ts](runner/bridge.ts) 串行握手、配置推送及清单读取，token 仅经环境传递。可执行资格的跨端定义归 [握手契约](../../docs/PROTOCOL.md#握手与工具同步)。

| 状态 | 本地处理 |
|---|---|
| 初次／重连 | 强制 full config，清单读取成功才就绪；重试和等待有界 |
| 保存配置 | 比对最近成功快照，忽略仅桌面消费的节，未知节仍参与 |
| 推送成功、清单失败 | 保留原清单和就绪态，登记待刷新供后续保存续做 |
| 握手耗尽或配置推送失败 | 撤销新执行资格，保留连接以便原位重试 |
| 进程／连接启动失败或初次等待超时 | 收尾本次端点、连接与进程；断连废弃旧清单 |
| 停止／重启 | 等在途启动及回滚完成，操作代次阻止旧清理结束新实例 |

账户网关串行发送 `tools.sync`；业务派发透传 `call_id`，当前连接取消优先用 RPC `req_id`，恢复后按 `call_id` 定位，窗口直调不伪造业务调用身份。[session-runtime.ts](backend/session-runtime.ts) 的登录恢复和应用就绪后的延迟启动共用幂等入口。

[runner/updater.ts](runner/updater.ts) 在原 venv 安装单 wheel，不承诺原子切换或自动回滚，损坏环境交 Installer 修复。暂存保留 wheel 原名，安装前核摘要，安装后执行包内 `--check` 并核版本；待装资产在创建精灵前处理。源码和安装版共用模块启动，开发环境须 editable 安装；构建与验签见 [Scripts](../../scripts/README.md#构建安装器)及[更新契约](../../docs/PROTOCOL.md#自更新签名)。

## 桌面更新

[ipc/update.ts](ipc/update.ts) 持状态与失败阶段，[auto-updater.ts](lifecycle/auto-updater.ts) 管更新源和 Runner 预取。每次检查／下载重新核后端地址，预取绑定发现版本时的源；electron-updater 的无来源错误按最近发起阶段归属，新增入口必须设置阶段。

安装包下载后先广播 `preparing`，Runner 预取校验完成才广播 `downloaded`。安装只接受主窗口／交互桌面，先等待桌面恢复再调用 `quitAndInstall`；Windows 会先启动安装器再发退出事件，不能仅依赖退出钩子。状态向所有窗口广播，不假定主窗口存在。

## 网络与缓存

Electron 使用 `net.fetch`、`net.WebSocket` 保持系统代理和证书语义。请求预算由[共享 HTTP 核心](../../shared/api.ts)及操作契约决定，超时不代表后端任务停止；仅本站登录失效信封触发退出，供应商错误保留来源。

资产 IPC 和桌面背景共用字节读取器，`apiAsset` 返回 data URL，`apiAssetBuffer` 返回字节；图片复制和保存共用 [image-operations.ts](shared/lib/image-operations.ts)，URL 与文件准入仍由各入口负责。缓存键、持久范围、收听记录和账户清理顺序统一见 [Client](../README.md#资产与历史缓存)。

### 语音与附件

[ipc/media.ts](ipc/media.ts) 经 HTTP 入口调用 STT / TTS 和上传；语音队列限制并发与速率，磁盘命中直接返回、不入云调用队列，内存及在途合并仍核账户。[ipc/files.ts](ipc/files.ts) 按尺寸与字节预算缩放图片附件，不扩大文件许可。

## 构建产物

tsup 构建 main / preload，共享 IPC 经 alias 解析；开发监听 main 与 shared。主窗口 `main.html` 由 `renderer-paths.ts` 和产物检查共同引用，改入口须核对实际文件名及 `package.json` 的 main。开发 CSP 与生产策略分开，不为开发白屏放宽生产；mac entitlements 见 [security](security/)。

## 验证入口

按 [Scripts](../../scripts/README.md#按改动选择验证) 检查，并覆盖错误 sender、未授权路径、换号、reset 后迟到结果、退出超时及更新失败。preload 改动检查构建产物；原生层级、系统恢复与焦点分别执行 [Windows](../../scripts/README.md#windows-桌面验收)和 [macOS](../../scripts/README.md#macos-桌面验收) 实机验收。
