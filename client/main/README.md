# Client 主进程

可信主进程持有凭据、窗口、Runner、配置镜像、磁盘缓存与更新。渲染层通过 `window.spiritagent` 和 `spiritagentWebUtils.getPathForFile` 使用受控能力。跨窗口协作见 [Client](../README.md)，通道类型见 [shared/ipc](../shared/ipc/)。

## 包边界

| 包或入口 | 职责 |
|---|---|
| `entry.ts` | 唯一组合根，显式装配，不展开业务逻辑 |
| `backend` | 会话与 HTTP |
| `runner` | 进程与本地 RPC |
| `lifecycle` | 窗口、托盘与更新 |
| `ipc` | 按能力注册通道 |
| `security` | sender、路径与能力准入 |
| `shared` | 叶子层，经装配层注入结构端口，不导入 backend / runner 实现 |

主进程产物为 ESM `entry.js`，沙盒 preload 为 CJS `preload.cjs`；混入 ESM import 会使 preload 桥失效。`main/shared` 与跨进程契约包 `client/shared` 分层独立。

## 启动与退出

`entry.ts` 按顺序调用各模块入口。ready 前依次为：[单实例锁](lifecycle/single-instance.ts)、[Chromium 开关](lifecycle/platform.ts)、确定 Home 并 `setPath('userData')`（须早于日志器、配置镜像与会话创建）、[应用名与 AppUserModelID](lifecycle/menu.ts)；新增模块只导出函数，不在导入时产生副作用。

- `SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖下取 `<override>/spiritagent-home` 为 Home（[paths.ts](security/paths.ts)）；配置、日志与缓存不另找目录。
- 默认单实例；`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 只用于并行验证，第二实例事件在转发器就绪前折叠保存，之后兑现一次。
- 远程显示可禁用 GPU 并关闭精灵透明。
- Chromium 后台节流全局关闭，渲染功耗由引擎管理。
- 关窗不退出；[app-quit.ts](lifecycle/app-quit.ts) 在退出时发起配置 flush、`flushSync` 日志并有界等待 Runner，超时仍可能残留进程。重启安装更新在 `before-quit-for-update` 时置退出标志，再走同一退出链。

## 渲染面准入

### 凭据与请求

凭据存储与换号语义见 [PROTOCOL](../../docs/PROTOCOL.md#凭据落盘)。托盘账户操作留在主进程，preload 不暴露持久凭据读取。`api()` 仅访问受控相对路径，拒绝绝对 URL、协议相对地址与穿越；白名单见 [api-allowlist.ts](security/api-allowlist.ts)。

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

[伙伴偏好](lifecycle/surface-companion.ts)在创建窗口前读取，独立保存在本机，不经云同步；原子保存失败向调用方报告，内存继续保留原值。偏好与实际可见状态分别通过 [IPC 快照](../shared/ipc/contracts.ts)传递，渲染层不得用迟到快照覆盖较新版本。激活卡片限命中区域，未认证唤起须更新渲染状态，不只 raise 窗口。

[快捷键](ipc/shortcuts.ts)返回注册冲突与失败。Windows 关窗隐藏到托盘，macOS 保留 Dock；多屏、透明命中见 [Client](../README.md#窗口与主题)，用户行为见 [DESIGN](../../docs/DESIGN.md#窗口与会话)。

## 配置镜像

[runner-config.ts](ipc/runner-config.ts)只接受工作台 sender，读取整份配置、按路径修改字段；[配置存储](shared/lib/runner-config-store.ts)拒绝触及原型链的路径与非 JSON 值，落盘失败回滚内存镜像，串行落盘与推送 Runner，云同步防抖且水合期间抑制回环。字段白名单、账户隔离与冲突语义见 [配置契约](../../docs/PROTOCOL.md#配置所有权与云同步)。

## Runner 生命周期

[ipc/runner.ts](ipc/runner.ts)的 host 管端点、桥和自动启停；token 经环境变量传递，不进 argv。握手配置、工具同步与资格撤销遵循 [PROTOCOL](../../docs/PROTOCOL.md#握手与工具同步)，迟到查询不能恢复旧资格。

[session-runtime.ts](backend/session-runtime.ts)负责懒创建、token 重接及登录恢复回调；首次 getSession 等待凭据恢复。恢复结果由 [auth.ts](ipc/auth.ts) 的广播器直接广播，不进鉴权操作队列，广播后仍是当前会话才自动启动 Runner；另有启动后 200 ms 的定时入口建立会话，已有 token 即自动启动。无 call_id 不记日志，限制见[调用契约](../../docs/PROTOCOL.md#调用日志与未知结果)。

[更新器](runner/updater.ts)优先用 Home 下的 uv，再回落 PATH，在原 venv 安装；不承诺原子切换或自动回滚，损坏环境由 Installer 修复。待装资产由 [auto-updater.ts](lifecycle/auto-updater.ts) 在创建精灵窗前安装。验签顺序见 [更新契约](../../docs/PROTOCOL.md#自更新签名)。

## 桌面更新

[ipc/update.ts](ipc/update.ts)持有更新状态、失败阶段与检查、下载、安装通道；[auto-updater.ts](lifecycle/auto-updater.ts)管理更新源与 Runner 预取。流程契约见 [自更新签名](../../docs/PROTOCOL.md#自更新签名)。

- electron-updater 的 error 事件不带来源，阶段按最近发起的检查、下载或安装归属；新增触发入口须同步设置阶段。
- 更新源在每次检查和下载前按保存的后端地址核对，不锁定首个地址；Runner 预取使用发现该版本时的更新源。
- 安装包下载完成只广播 `preparing`，预取校验通过才广播 `downloaded`；重启安装只认该状态与生活空间 sender。

## 网络与缓存

字节缓存键为内容哈希或规范化 URL；历史与账号清理见 [Client](../README.md#资产与历史缓存)。下载、写盘和回调均须遵守取消与用户代次。

资产入口共用下载与鉴权处理，缓存返回字节和 MIME；仅 `apiAsset` 在返回时编码 data URL，`apiAssetBuffer` 直接返回字节。
清理资产缓存先取消并等待旧下载与写入，再移除目录；新下载等待清理结束。下载超时覆盖响应体读取。

`cacheOnly` 只查询本地缓存，不请求 Backend；资产未命中返回 `null`，由调用方按缺少本地副本处理。

[hardening.ts](security/hardening.ts)按路径和方法配置请求等待，同步生成入口有更长超时（avatar、outfit、voice 等）。新增入口须核对超时规则。超时不代表后端任务已停；401 按结构化状态处理，不匹配错误文案。

### 语音与附件

- STT / TTS 经 [媒体入口](ipc/media.ts)调用云端，使用有界队列、并发和速率控制；TTS 内存与在途合并不入队，磁盘命中与云端调用走队列，缓存命中不耗云端额度。
- 窗口不复制这套限制。
- 图片附件按[文件入口](ipc/files.ts)的尺寸与字节上限降采样。

## 构建产物

tsup 构建 main / preload，共享 IPC 通过 alias 解析；开发监听只覆盖 main 与 shared。修改导出或路径须核对实际文件名与 `package.json` 的 main。导入面一致性在构建期检查。

开发 CSP 允许 Vite 所需能力，生产保持严格策略，不能为修复开发白屏放宽生产 CSP。mac entitlements 开 JIT、关库校验，见 [security](security/)。

## 验证入口

命令见 [Scripts](../../scripts/README.md#按改动选择验证)。覆盖错误 sender、未授权路径、换号、连接 reset 后迟到结果、退出超时和更新失败；preload 与入口变更另查实际构建产物。
