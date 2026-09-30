# Client 主进程

可信主进程持有凭据、窗口、Runner、配置镜像、磁盘缓存与更新。渲染层通过 `window.spiritagent` 和 `spiritagentWebUtils.getPathForFile` 使用受控能力。跨窗口协作见 [Client](../README.md)，通道类型见 [shared/ipc](../shared/ipc/)。

## 包边界

| 包或入口 | 职责 |
|---|---|
| `entry.ts` | 唯一组合根，显式装配，不展开业务逻辑 |
| `preload.ts` | 沙盒 preload，向渲染层暴露 `window.spiritagent` |
| `backend` | 多账户凭据、会话与 HTTP |
| `runner` | 进程、端点与本地 RPC 桥、反向模型代理、Runner 更新 |
| `lifecycle` | 窗口、托盘、退出与桌面更新 |
| `ipc` | 按能力注册通道，也持有资产、历史快照与 TTS 合成音频三类磁盘缓存及 Runner 宿主 |
| `security` | sender、路径与能力准入 |
| `shared` | 叶子层，经装配层注入结构端口，不导入 backend / runner 实现 |

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

凭据存储与换号语义见 [PROTOCOL](../../docs/PROTOCOL.md#凭据落盘)，多账户存储在 [session.ts](backend/session.ts)。托盘账户操作留在主进程，preload 不暴露持久凭据读取。`api()` 仅访问受控相对路径，拒绝绝对 URL、协议相对地址与穿越；白名单见 [api-allowlist.ts](security/api-allowlist.ts)。`api()`、资产读取、`ws-url` 签发与 401 通知集中在 [connection.ts](ipc/connection.ts)。

### 文件与媒体

- 文件读取仅接受选择器或拖拽登记的路径，渲染层不能自行授予白名单；敏感路径另行阻断。
- 白名单仅进程内且有容量限制，重启须重新选择，历史附件路径不自动恢复权限。
- `chat:set-pending-feed` 信箱不校验白名单，取用方仍须走受控读取。

## 网关宿主与代理

- 只有宿主可获取网关票 `ws-url`、上报网关状态、注入事件、答复代理 RPC 和派发 Runner 工具，入口核对 sender。
- 其他表面只能请求代理能力；`tool.call` 不转发到其他窗口，TypeScript 类型不能代替运行时校验。

[代理等待](ipc/gateway.ts)有超时，网关 closed / error 时统一 reject。[连接缓存](backend/ensure-backend.ts)只保存后端地址与就绪结果，凭据按请求读取；reset 使在途解析失效。票据签发前后核对鉴权会话，拒绝换号后的迟到结果。

## 窗口与几何

[surfaces.ts](lifecycle/surfaces.ts)串行裁决开关，生活空间与工作台最多一个可见；账户身份变化时收起完整入口并显示桌面精灵，避免沿用上个账户的窗口状态。每个窗口与其面板、侧边区域及定时器由同一记录持有。内容面板矩形是几何锚点，侧边区域只调整原生窗口边界；程序调整与最大化还原期间不把中间事件写回面板。显示器变更同时校正还原位置，窗口关闭时清理几何定时器。完整入口移动时桌面精灵窗跟随显示器，渲染状态不传递几何。

[surfaces.ts](lifecycle/surfaces.ts) 另负责播放认领（`surface:claim-play`）与锁屏跟踪：同一 `play_id` 只由一个可见舞台认领，认领随账户变化清空，规则见[播放契约](../../docs/PROTOCOL.md#动作目录与播放)。

[伙伴偏好](lifecycle/surface-companion.ts)在创建窗口前读取，独立保存在本机，不经云同步；原子保存失败向调用方报告，内存继续保留原值。侧边伙伴的偏好与实际可见状态、桌面精灵窗的实际显隐 `spriteVisible`（窗口存在、未隐藏且未最小化）都经 [IPC 快照](../shared/ipc/contracts.ts)传递，渲染层不得用迟到快照覆盖较新版本。精灵窗每次创建都经 [tray.ts](lifecycle/tray.ts) 的 `installCloseInterceptor` 在显示、隐藏、最小化与还原时发布快照，托盘、快捷键与右键隐藏无需各自发布；精灵窗关闭了后台节流，页面可见性 API 不反映隐藏。激活卡片限命中区域，未认证唤起须更新渲染状态，不只 raise 窗口。

[sprite.ts](ipc/sprite.ts)管理精灵窗位置（保存为 Home 下的 `companion-position.json`）、窗口场景快照、目标换算与跨屏移动；场景快照、目标换算与跟随目标跨屏（`moveToDisplay`）只接受精灵窗 sender，拖拽跨屏（`moveToCursorDisplay`）与位置读写不校验 sender。默认显示比例经它广播到各窗口。

[快捷键](ipc/shortcuts.ts)返回注册冲突与失败。Windows 关窗隐藏到托盘，macOS 保留 Dock；多屏、透明命中见 [Client](../README.md#窗口与主题)，用户行为见 [DESIGN](../../docs/DESIGN.md#窗口与会话)。

## 配置镜像

[runner-config.ts](ipc/runner-config.ts)只接受工作台 sender，读取整份配置、按路径修改字段；[配置存储](shared/lib/runner-config-store.ts)串行落盘与推送 Runner，云同步防抖、水合写入抑制回环；在途 flush 以 `flushQueued` 补跑防丢编辑，换号以 `authEpoch` 丢弃旧账户上云；`patch` 另拒绝触及原型链的路径与非 JSON 值；`patch` 与 `mutate` 修改抛错或落盘失败时回滚内存镜像，云端水合 `applyCloudMirror` 与账户隔离清理 `clearSyncedMirror` 落盘失败保留内存结果并抛出，理由见配置契约。其他写入方各走带校验的通道：[prefs.ts](ipc/prefs.ts) 只接受 `companion.*` 点键；快捷键、托盘语言与上次完整入口经 `patch`，主题与技能、工具集开关经 `mutate`，云端水合经 `applyCloudMirror` 整节写入，账户隔离清理经 `clearSyncedMirror`。字段白名单、账户隔离与冲突语义见 [配置契约](../../docs/PROTOCOL.md#配置所有权与云同步)。

## Runner 生命周期

[ipc/runner.ts](ipc/runner.ts)的 host 管端点、桥和自动启停；token 经环境变量传递，不进 argv。[bridge.ts](runner/bridge.ts) 完成握手、配置推送与 `get_tools`；连接探活用 WS 层 ping（Runner 协议自动 pong），不用 JSON-RPC 通知。运行中配置推送成功后重新读取清单，变化时按重连同样发布。模型派发调用经 `ipc/runner.ts` 带 `call_id` 发出，按 Runner 回复分类为完成、明确失败、未执行或结果未知后交回宿主；取消只按该调用记录的 RPC `req_id` 作用于指定调用，由宿主收到 `tool.cancel` 时发起。窗口查询等直调不带 `call_id`。`tools.sync` 与撤销由渲染层宿主发起，见 [Client](../README.md#连接与设备就绪)。

[session-runtime.ts](backend/session-runtime.ts)负责懒创建、token 重接及登录恢复回调；首次 getSession 等待凭据恢复。恢复结果由 [auth.ts](ipc/auth.ts) 的广播器直接广播，不进鉴权操作队列，广播后仍是当前会话才自动启动 Runner；另有启动后 200 ms 的定时入口建立会话，已有 token 即自动启动。无 call_id 不记日志，限制见[调用契约](../../docs/PROTOCOL.md#调用日志与未知结果)。

[更新器](runner/updater.ts)优先用 Home 下的 uv，再回落 PATH，在原 venv 安装；不承诺原子切换或自动回滚，损坏环境由 Installer 修复。待装资产由 [auto-updater.ts](lifecycle/auto-updater.ts) 在创建精灵窗前安装。wheel 与 `server.py` 的导入面一致性由构建期 [check_runner_facade.py](../../scripts/check_runner_facade.py) 门禁，验签顺序见 [更新契约](../../docs/PROTOCOL.md#自更新签名)。

## 桌面更新

[ipc/update.ts](ipc/update.ts)持有更新状态、失败阶段与检查、下载、安装通道；[auto-updater.ts](lifecycle/auto-updater.ts)管理更新源与 Runner 预取。流程契约见 [自更新签名](../../docs/PROTOCOL.md#自更新签名)。

- electron-updater 的 error 事件不带来源，阶段按最近发起的检查、下载或安装归属；新增触发入口须同步设置阶段。
- 更新源在每次检查和下载前按保存的后端地址核对，不锁定首个地址；Runner 预取使用发现该版本时的更新源。
- 安装包下载完成只广播 `preparing`，预取校验通过才广播 `downloaded`；重启安装只认该状态与生活空间 sender。
- 更新状态广播给全部窗口，不假定主窗口存在；实际消费方由渲染层 `update-bridge` 挂载位置决定。

## 网络与缓存

字节缓存键与落盘范围见 [Client](../README.md#资产与历史缓存)。下载、写盘和回调均须遵守取消与用户代次。

资产入口共用下载与鉴权处理，缓存返回字节和 MIME；仅 `apiAsset` 在返回时编码 data URL，`apiAssetBuffer` 直接返回字节。

收听记录由 [voice-playback.ts](ipc/voice-playback.ts) 校验鉴权会话与载荷，[voice-playback-store.ts](ipc/voice-playback-store.ts) 串行原子写盘并单向合并已听状态；跨窗口广播仅携带当前账户的记录，清理代次阻断旧写入。记录独立于历史快照，缓存生命周期见 [Client](../README.md#资产与历史缓存)。
移除账户时先取消并等待该账户的下载与写入，再删除其目录；同账户新请求等待清理结束，其他账户不受影响。下载超时覆盖响应体读取。

`cacheOnly` 只查询本地缓存，不请求 Backend；资产未命中返回 `null`，由调用方按缺少本地副本处理。

[hardening.ts](security/hardening.ts)按路径和方法配置请求等待，同步生成入口有更长超时（avatar、outfit、voice 等）。新增入口须核对超时规则。超时不代表后端任务已停；401 按结构化状态处理，不匹配错误文案。

### 语音与附件

- STT / TTS 经 [媒体入口](ipc/media.ts)调用云端，使用有界队列、并发和速率控制；TTS 内存与在途合并不入队，磁盘命中与云端调用走队列，缓存命中不耗云端额度。
- 窗口不复制这套限制。
- 图片附件按[文件入口](ipc/files.ts)的尺寸与字节上限降采样。

## 构建产物

tsup 构建 main / preload，共享 IPC 通过 alias 解析；开发监听只覆盖 main 与 shared。修改导出或路径须核对实际文件名与 `package.json` 的 main。

开发 CSP 允许 Vite 所需能力，生产保持严格策略，不能为修复开发白屏放宽生产 CSP。mac entitlements 开 JIT、关库校验，见 [security](security/)。

## 验证入口

命令见 [Scripts](../../scripts/README.md#按改动选择验证)。覆盖错误 sender、未授权路径、换号、连接 reset 后迟到结果、退出超时和更新失败；preload 与入口变更另查实际构建产物。
