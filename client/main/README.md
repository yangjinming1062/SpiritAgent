# Client 主进程

主进程通过 `entry.ts` 装配桌面能力，通过 `preload.ts` 暴露受控桥。本文维护内部生命周期、准入和原生承载；共享缓存归 Client README，跨端契约归 PROTOCOL。

## 包边界

| 包或入口 | 职责 |
|---|---|
| `entry.ts` | 唯一组合根，显式装配，不展开业务逻辑 |
| `preload.ts` | 沙盒 preload，暴露 `window.spiritagent` |
| `backend` | 多账户凭据、会话与 HTTP |
| `runner` | 进程、本地 RPC、反向模型代理及 Runner 更新 |
| `lifecycle` | 窗口、托盘、退出与桌面更新 |
| `ipc` | 注册能力通道，装配 Runner 宿主及资产、历史、语音缓存 |
| `security` | sender、路径与能力准入 |
| `shared` | 叶子层，经组合根注入端口，不导入 backend／runner 实现 |

主进程产物为 ESM `entry.js`，沙盒 preload 为 CJS `preload.cjs`；混入 ESM import 会使 preload 桥失效。`main/shared` 与跨进程契约包 `client/shared` 分层独立。

## 启动与退出

`entry.ts` 按顺序调用各模块入口。ready 前依次为：[单实例锁](lifecycle/single-instance.ts)、[Chromium 开关](lifecycle/platform.ts)、确定 Home 并 `setPath('userData')`（须早于日志器、配置镜像与会话创建）、[随包技能同步](lifecycle/bundled-skills.ts)、[应用名与 AppUserModelID](lifecycle/menu.ts)；新增模块只导出函数，不在导入时产生副作用。

- `SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖下取 `<override>/spiritagent-home` 为 Home（[paths.ts](security/paths.ts)）；配置、日志与缓存不另找目录。
- 随包技能同步紧接日志器同步执行，早于会话恢复与 Runner 自动启动（含 200 ms 定时入口），使 Runner 与技能索引读到新文件。仅打包运行且桌面版本与 Home 下 `.bundled-skills-version` 不同时，把 resources 中的 `skills` 复制到 `$SPIRITAGENT_HOME/skills`：同名覆盖、不删其他内容，存在 `.no-bundled-skills` 时跳过；全部复制成功才写版本标记，失败只记日志、下次启动重试。
- 默认单实例；`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 只用于并行验证，第二实例事件在转发器就绪前折叠保存，之后兑现一次。
- 远程显示可禁用 GPU 并关闭精灵透明。
- Chromium 后台节流全局关闭，渲染功耗由引擎管理。
- 关窗不退出；[app-quit.ts](lifecycle/app-quit.ts) 在退出时发起配置 flush、`flushSync` 日志并有界等待 Runner 和收听记录写入，超时仍可能残留进程。重启安装更新在 `before-quit-for-update` 时置退出标志，再走同一退出链。

## 渲染面准入

### 凭据与请求

凭据存储与换号语义见 [PROTOCOL](../../docs/PROTOCOL.md#凭据落盘)，多账户存储在 [session.ts](backend/session.ts)。凭据读取须在 `app.whenReady()` 后触发，避免 safeStorage 尚未就绪时缓存解密失败。托盘账户操作留在主进程，preload 不暴露持久凭据读取。`api()` 仅访问受控相对路径，拒绝绝对 URL、协议相对地址与穿越；白名单见 [api-allowlist.ts](security/api-allowlist.ts)。`api()`、资产读取、`ws-url` 签发与 401 通知集中在 [connection.ts](ipc/connection.ts)。

### 文件与媒体

- 文件读取仅接受选择器或拖拽登记的路径，渲染层不能自行授予白名单；敏感路径另行阻断。读取统一经 [readUserSelectedFile](security/hardening.ts)，新增读文件通道不绕过它。
- 白名单仅进程内且有容量限制，重启须重新选择，历史附件路径不自动恢复权限。
- `chat:set-pending-feed` 信箱不校验白名单，取用方仍须走受控读取。

## 网关宿主与代理

- 只有宿主可获取网关票 `ws-url`、上报网关状态、注入事件、答复代理 RPC 和派发 Runner 工具，入口核对 sender；`ws-url` 与 Runner 派发、取消经 [assertGatewayHost](security/ipc-trust.ts)。
- 其他表面只能请求代理能力；`tool.call` 不转发到其他窗口，TypeScript 类型不能代替运行时校验。

[代理等待](ipc/gateway.ts)有超时，网关 closed / error 时统一 reject；宿主渲染器崩溃、重载或销毁时由主进程同步关闭状态并终止等待，状态仅在变化时广播至代理窗口。代理 RPC 使用结果信封透传业务错误的 `code`、`message` 与 `data`，渲染代理还原错误类型；桥接失败与超时保留普通错误。[连接缓存](backend/ensure-backend.ts)只保存后端地址与就绪结果，凭据按请求读取；reset 使在途解析失效。票据签发前后核对鉴权会话，拒绝换号后的迟到结果。

## 窗口与几何

[surfaces.ts](lifecycle/surfaces.ts)串行裁决开关，生活空间与工作台最多一个可见；账户身份变化时收起完整入口并恢复窗口模式的伙伴窗，避免沿用上个账户的窗口状态。每个窗口与其面板、侧边区域及定时器由同一记录持有。内容面板矩形是几何锚点，侧边区域只调整原生窗口边界；程序调整与最大化还原期间不把中间事件写回面板。显示器变更同时校正还原位置，窗口关闭时清理几何定时器。完整入口移动时伙伴窗跟随显示器，渲染状态不传递几何。

入口面快照仅在实际状态变化时增加版本并广播；重复几何与显隐事件不重复发布。`surface:open` 完成或失败仍回灌当前快照，纠正渲染层的乐观意图；新窗口通过 `surface:get-state` 取得当前版本。

生活空间窗口按 [surface-companion.ts](lifecycle/surface-companion.ts) 的初始尺寸打开并可自由缩放，不强制画幅；呈现语义见 [窗口与会话](../../docs/DESIGN.md#窗口与会话)。

[surfaces.ts](lifecycle/surfaces.ts) 另负责播放认领（`surface:claim-play`）与锁屏跟踪：同一 `play_id` 只由一个可见舞台认领，认领随账户变化清空，规则见[播放契约](../../docs/PROTOCOL.md#动作目录与播放)。

[伙伴偏好](lifecycle/surface-companion.ts)在创建窗口前读取，独立保存在本机，不经云同步；原子保存失败向调用方报告，内存继续保留原值。侧边伙伴的偏好与实际可见状态、桌面精灵窗的实际显隐 `spriteVisible`（窗口存在、未隐藏且未最小化）都经 [IPC 快照](../shared/ipc/contracts.ts)传递，渲染层不得用迟到快照覆盖较新版本。精灵窗每次创建都经 [tray.ts](lifecycle/tray.ts) 的 `installCloseInterceptor` 在显示、隐藏、最小化与还原时发布快照，托盘、快捷键与右键隐藏无需各自发布；精灵窗关闭了后台节流，页面可见性 API 不反映隐藏。激活卡片限命中区域，未认证唤起须更新渲染状态，不只 raise 窗口。

[sprite.ts](ipc/sprite.ts)管理精灵窗位置（保存为 Home 下的 `companion-position.json`）、窗口场景快照、目标换算与跨屏移动。场景快照、目标换算与跨屏移动只在窗口模式接受精灵宿主 sender；桌面模式没有独立精灵舞台。拖拽跨屏（`moveToCursorDisplay`）与精灵位置读写保持原入口。默认显示比例经它广播到各窗口。

[快捷键](ipc/shortcuts.ts)返回注册冲突与失败。Windows 关窗隐藏到托盘，macOS 保留 Dock；多屏、透明命中见 [Client](../README.md#窗口与主题)，用户行为见 [DESIGN](../../docs/DESIGN.md#窗口与会话)。

## 桌面承载与恢复

[desktop-presentation.ts](lifecycle/desktop-presentation.ts)管理 desktop 生命周期、交互屏、舞台所有权与受控 IPC；living／workbench 只代表窗口入口。交互界面使用完整 preload，账户、配置、更新与启动器仅授权交互界面；交互屏背景使用 [preload-background.ts](preload-background.ts)，仅接收缓存媒体与播放状态并回报播放生命周期。其他屏幕不创建背景窗口。网关票及 Runner 派发仍只授予隐藏精灵宿主。背景媒体由交互界面提交已认证资源引用，主进程通过账户资产缓存读取原始字节；播放命令与回执按账户和呈现代次隔离，锁屏、全屏及减少动态效果时暂停。

呈现切换 IPC 允许精灵宿主、生活空间、工作台和交互桌面；托盘复用主进程同一串行切换入口，菜单按实际模式与准备状态更新。桌面组件缺失时在关闭旧窗口前拒绝启用，广播实际窗口模式及失败原因。

[explorer-desktop-host.ts](lifecycle/explorer-desktop-host.ts)通过有界 JSON 协议调用 [Rust helper](../desktop-host/README.md)。界面与交互屏背景先在限时内报告界面就绪，再复核账户与窗口存活并交给 helper 接管；任一阶段失败均回到恢复流程。原生事务、窗口身份校验与 journal 归 helper。

桌面窗口固定页面缩放，使顶栏和 Dock 的 DIP 高度与工作区一致；退出时恢复窗口模式保存的缩放。挂载后用 `showInactive()` 同步 Electron 可见状态；原生层级、几何复验与工作区恢复由 [helper](../desktop-host/README.md#工作区与层级)负责。

真实主指针释放请求输入焦点；生活／工作显式导航还调用 `focus()`、`moveTop()` 置前，全屏时跳过激活。主进程与 helper 在各自串行队列执行时核对窗口、账户、舞台代次和锁屏。前台广播不抢焦点，渲染层手势不能代替权限校验。焦点请求超时终止 host 并恢复，防止迟到请求继续改动焦点；视图资格与状态同步见[呈现契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

账户失效、呈现切换、显示器变化、渲染器失败与退出都先恢复系统，再销毁桌面窗口。准备期间的换号、退出、拔屏或 renderer 失败立即使当前接管失效；界面就绪与页面加载共享限时预算。休眠或唤醒时恢复窗口模式并保留桌面偏好，须由用户重新选择桌面模式，避免接管 guardian 已恢复的系统窗口。

启动时先处理遗留恢复记录；异常标记使已由 guardian 恢复的会话也停留窗口模式。主进程检测到的失败另存独立标记，成功手动重试只清该标记，不覆盖 guardian 原因。恢复失败保留记录和错误，不阻断窗口模式启动。原生恢复信号、兼容与限制见 [helper](../desktop-host/README.md#恢复)。

恢复已保存的桌面偏好仅加载已有媒体；`prepareDesktopMedia` 是本次会话的制作许可，默认关闭，用户显式选择桌面后开启，退出或换号清除，切屏重挂载保留。渲染层据此区分启动水合与用户启用，启动不提交付费素材制作。

真实平台门禁见 [Windows 桌面验收](../../scripts/README.md#windows-桌面验收)，挂载探测入口见 [helper](../desktop-host/README.md#原生验收)。

## Dock 与应用目录

[desktop-dock.ts](ipc/desktop-dock.ts) 合并固定配置、运行快照与图标投影；[windows-app-catalog.ts](ipc/windows-app-catalog.ts) 只负责目录和图标，[windows-installed-apps.ts](ipc/windows-installed-apps.ts) 负责系统查询。原生窗口采集归 helper，渲染层只接收不透明条目与窗口 ID。

- 普通应用以规范化 exe 路径归并，快捷方式解析目标；同名 exe 的父子安装目录关联启动器与窗口进程，无关目录保持独立。固定去重、目录已添加标记和运行显示复用此规则，打包应用以 AUMID 归并。
- 固定时优先保留目录快捷方式及参数，无目录的普通程序使用已验证 exe；打包应用保存 `shell:AppsFolder\AUMID`，激活前在完整系统目录核验。v1 `desktop-dock.json` 只保存固定项，运行状态不落盘。
- 添加、修复和启动均复核目标；修复保留 ID 和顺序，取消不写配置。投影或启动失败保留条目，写盘失败保留原配置。运行查询失败保留最近快照，并报告不可用状态。
- `revision` 覆盖投影变化，`pinnedRevision` 只随固定配置变化；运行和图标刷新不重取选择面板目录。桌面结束或目录重扫使在途元数据失效。
- 目录合并开始菜单、桌面快捷方式、App Paths 与 AppsFolder，按 exe 或 AUMID 去重、快捷方式优先；排除非现存 exe、网页快捷方式、`.msc`、虚拟和隐藏入口。各来源独立限时并报告失败；展示上限不限制已保存应用核验。
- 目录随机句柄仅本次扫描有效，重扫废弃句柄与图标缓存。固定、运行与目录图标共用 [windows-application-icon.ts](ipc/windows-application-icon.ts)：打包应用优先用清单图标，快捷方式保留指定图标及资源索引；exe / DLL 图标经限时、分批的 Windows 原生提取，兼容全 PNG 图标资源，失败再用系统文件图标。目录与产品账户无关，不上云。

激活、关闭和启动的授权与结果边界统一归 [本机启动器契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

## 配置镜像

[runner-config.ts](ipc/runner-config.ts) 的整份配置与路径修改只接受工作台或交互桌面；其他入口使用窄通道。`prefs.ts` 只允许 `companion.*` 点键，主题、技能与工具集走 `mutate`，云端水合走 `applyCloudMirror`，账户隔离清理走 `clearSyncedMirror`。

[runner-config-store.ts](shared/lib/runner-config-store.ts) 串行落盘与推送；`patch` 拒绝原型链路径和非 JSON 值。普通修改失败回滚内存，云端水合与账户隔离清理即使落盘失败仍保留新内存值。云同步以 `flushQueued` 补跑在途编辑、`authEpoch` 隔离旧账户并抑制水合回环。白名单、所有权与冲突语义归 [配置契约](../../docs/PROTOCOL.md#配置所有权与云同步)。

## Runner 生命周期

[ipc/runner.ts](ipc/runner.ts) 持有端点、桥与自动启停，token 经环境变量传递。[bridge.ts](runner/bridge.ts) 用同一队列完成握手、配置推送与工具清单读取；连接探活用 WS ping/pong。可执行就绪与工具撤销统一遵循 [握手契约](../../docs/PROTOCOL.md#握手与工具同步)。

| 路径 | 桥内处理 |
|---|---|
| 首次或重新握手 | 强制 full config，成功读取清单才就绪；最多三次尝试，配置 RPC 5 秒、清单 RPC 10 秒，启动等待 60 秒 |
| 运行中保存配置 | 比对 Runner 最近成功应用的快照；忽略仅桌面消费的 `ui`、`shortcuts`、`companion`、`language`、`sync`，未知节仍参与 |
| 推送成功、清单刷新失败 | 保留原清单和就绪状态，留下 `toolsRefreshPending`，下次保存继续读清单 |
| 握手耗尽或运行期配置推送失败 | 撤销新执行资格，保留连接供保存配置、`runner_ready` 或 `autoStart` 原位重试 |
| WS／进程启动失败、初次等待超时 | 收尾端点、连接和进程；断连废弃旧清单，重连未报告 ready 时可重新启动 |
| 停止与重启 | 等待在途启动及回滚完成，操作代次阻断旧启动清理新实例 |

派发入口透传 `call_id` 并分类结果；取消定位该调用的 RPC `req_id`，窗口等直调不带 `call_id`。`tools.sync` 归 renderer 宿主，不由桥发送。取消、日志恢复和结果未知的定义归 [调用契约](../../docs/PROTOCOL.md#调用日志与未知结果)。

[session-runtime.ts](backend/session-runtime.ts)负责懒创建及登录恢复回调；首次 getSession 等待凭据恢复。恢复结果由 [auth.ts](ipc/auth.ts) 广播器直接广播，仍是当前会话才自动启动 Runner；应用就绪后另有 200 ms 定时入口，须与恢复路径共同保持幂等。

[更新器](runner/updater.ts)优先用 Home 下的 uv，再回落 PATH，在原 venv 安装；不承诺原子切换或自动回滚，损坏环境由 Installer 修复。待装资产由 [auto-updater.ts](lifecycle/auto-updater.ts) 在创建精灵窗前安装。wheel 与 `server.py` 的导入面一致性由构建期 [check_runner_facade.py](../../scripts/check_runner_facade.py) 门禁，验签顺序见 [更新契约](../../docs/PROTOCOL.md#自更新签名)。

## 桌面更新

[ipc/update.ts](ipc/update.ts)持有更新状态、失败阶段与检查、下载、安装通道；[auto-updater.ts](lifecycle/auto-updater.ts)管理更新源与 Runner 预取。流程契约见 [自更新签名](../../docs/PROTOCOL.md#自更新签名)。

- electron-updater 的 error 事件不带来源，阶段按最近发起的检查、下载或安装归属；新增触发入口须同步设置阶段。
- 更新源在每次检查和下载前按保存的后端地址核对，不锁定首个地址；Runner 预取使用发现该版本时的更新源。
- 安装包下载完成先广播 `preparing`，Runner 预取校验通过才广播 `downloaded`；入口只接受生活空间或交互桌面 sender。
- 安装入口先等待桌面恢复，再调用 `quitAndInstall`；Windows updater 会先启动安装器再发退出事件，不能仅靠退出钩子保证恢复顺序。
- 更新状态广播给全部窗口，不假定主窗口存在；实际消费方由渲染层 `update-bridge` 挂载位置决定。

## 网络与缓存

字节缓存键与落盘范围见 [Client](../README.md#资产与历史缓存)。账户隔离及取消由缓存各自的所有者实施。

资产入口共用下载与鉴权处理，缓存返回字节和 MIME；仅 `apiAsset` 在返回时编码 data URL，`apiAssetBuffer` 直接返回字节。

收听记录由 [voice-playback.ts](ipc/voice-playback.ts) 校验鉴权会话与载荷，[voice-playback-store.ts](ipc/voice-playback-store.ts) 串行原子写盘并单向合并已听状态；跨窗口广播仅携带当前账户的记录，清理代次阻断旧写入。记录独立于历史快照，缓存生命周期见 [Client](../README.md#资产与历史缓存)；两者的账户代次与按会话串行写入由 [account-queue.ts](ipc/account-queue.ts) 共用。
移除账户先等待旧下载与写入再删目录，同账户新请求等待清理；下载超时覆盖响应体读取。

[hardening.ts](security/hardening.ts)按路径和方法配置请求等待，同步生成入口有更长超时：头像、全身参考与外观的生图链取长预算以覆盖供应商回退，候选身体特征分析按后端分析时限，提示词整合、采纳与确认类入口及语音重试各有较长超时。新增入口须核对后端该入口的同步时限与可能的用户锁等待。超时不代表后端任务已停；401 按结构化状态处理，不匹配错误文案。

### 语音与附件

- STT / TTS 经 [媒体入口](ipc/media.ts)调用云端，使用有界队列、并发和速率控制；TTS 内存与在途合并、磁盘命中不入队（即时返回），仅云端调用与落盘走队列，缓存命中不耗云端额度。
- 图片附件按[文件入口](ipc/files.ts)的尺寸与字节上限降采样。

## 构建产物

tsup 构建 main / preload，共享 IPC 通过 alias 解析；开发监听只覆盖 main 与 shared。修改导出或路径须核对实际文件名与 `package.json` 的 main。

开发 CSP 允许 Vite 所需能力，生产保持严格策略，不能为修复开发白屏放宽生产 CSP。mac entitlements 开 JIT、关库校验，见 [security](security/)。

## 验证入口

命令见 [Scripts](../../scripts/README.md#按改动选择验证)。覆盖错误 sender、未授权路径、换号、连接 reset 后迟到结果、退出超时和更新失败；preload 与入口变更另查实际构建产物。
