# Client

桌面应用的工程入口。进程职责、状态权威和平台支持归 [ARCHITECTURE](../docs/ARCHITECTURE.md)，本文维护代码导航、跨窗口装配及本地缓存；内部约束分别见主进程和渲染层 README。

## 任务入口

| 要修改的功能 | 实现起点与联动 |
|---|---|
| 登录、换号与会话过期 | [session.ts](main/backend/session.ts)、[session-runtime.ts](main/backend/session-runtime.ts)、[auth.ts](main/ipc/auth.ts) → [account-lifecycle.ts](renderer/app/workflows/account-lifecycle.ts)、[auth store](renderer/shared/store/auth.ts) |
| 网关、Runner 派发与断连 | [gateway.ts](main/ipc/gateway.ts)、[runner.ts](main/ipc/runner.ts) → [host-runtime.ts](renderer/app/runtime/host-runtime.ts)、[tool-dispatch.ts](renderer/app/runtime/handlers/tool-dispatch.ts) |
| 历史、语音与资产缓存 | [session-history.ts](main/ipc/session-history.ts)、[asset-disk-cache.ts](main/ipc/asset-disk-cache.ts)、[voice-playback.ts](main/ipc/voice-playback.ts) → [conversation](renderer/modules/conversation/)、[conversation-speech.ts](renderer/app/workflows/conversation-speech.ts) |
| 场景与背景 | [scene-store.ts](renderer/modules/scene/scene-store.ts)、[scene-backdrop.tsx](renderer/app/features/living/scene-backdrop.tsx)；后端沿 [场景改动链](../backend/services/application/generation/README.md#场景改动链) 核对 |
| 窗口、拖拽、主题与动作 | [主进程窗口](main/README.md#窗口与几何)、[渲染层呈现](renderer/README.md#角色呈现契约) |
| Windows 桌面模式、生活视频与 Dock | [desktop-presentation.ts](main/lifecycle/desktop-presentation.ts)、[desktop-dock.ts](main/ipc/desktop-dock.ts)、[桌面生活模块](renderer/modules/desktop-videos/)、[desktop renderer](renderer/app/windows/desktop/)、[原生宿主](desktop-host/README.md) |
| 设置同步与 Runner 更新 | [runner-config.ts](main/ipc/runner-config.ts)、[config-sync.ts](main/shared/lib/config-sync.ts)、[updater.ts](main/runner/updater.ts) |
| 桌面应用更新 | [update.ts](main/ipc/update.ts)、[auto-updater.ts](main/lifecycle/auto-updater.ts) → [update-bridge.ts](renderer/shared/lib/update-bridge.ts)、[about-page.tsx](renderer/app/features/living/settings/about-page.tsx) |

## 进程与代码边界

| 代码 | 内部职责 |
|---|---|
| [main](main/README.md) | 组合根、preload、身份、窗口、Runner、配置与磁盘资源 |
| [renderer](renderer/README.md) | 各物理窗口独立运行；桌面内部面板共用 renderer 和会话 runtime，桌面背景视频由受限背景表面消费主进程缓存媒体 |
| [desktop-host](desktop-host/README.md) | Windows Explorer 挂载、运行窗口观察、工作区与异常恢复 |
| `shared/ipc` | 跨进程通道和载荷，main、preload 与 renderer 共用 |
| `shared/runtime.ts`、`shared/speech-text.ts` | 两个进程共用的运行时原语与朗读文本清理 |
| `scripts` | 开发、构建与打包钩子；操作归 [Scripts](../scripts/README.md#client-开发与打包脚本) |

主进程不导入依赖窗口环境的 `renderer/shared`。main 与 preload 由 tsup 构建，renderer 由 Vite 构建；产物与 preload 限制见 [主进程包边界](main/README.md#包边界)。

## 连接与设备就绪

精灵宿主持唯一聊天 WS；每次连接向主进程请求新票据，失败保留原错误。其余窗口用 `IpcGatewayProxy`，各自水合视图和读取斜杠目录。主进程持有代理等待表，宿主失效时收尾全部等待；重连不自动重放业务请求。

Runner 握手、配置与清单读取归 [主进程桥](main/README.md#runner-生命周期)。宿主直接消费就绪事件携带的清单并串行 `tools.sync`，停止或失败时同步空清单；WS 建立后另读当前清单，补齐订阅前的就绪状态。准入、调用恢复及当前能力消费限制统一归 [本机工具契约](../docs/PROTOCOL.md#本机工具)。

## 窗口与主题

窗口几何、开关、附件信箱和播放认领由主进程维护。各窗内存独立，角色默认比例与表面快照经 IPC 同步；不能仅更新来源窗口 store。物理窗口与桌面面板的呈现规则归 [窗口与会话](../docs/DESIGN.md#窗口与会话)。

- 主题在 `loadURL` 前从配置镜像写入入口参数，renderer 加载时优先消费并移除；无镜像时读取 localStorage，再回落 `day-clear`，非法值同样归一。云端水合负责收敛。
- 原生窗口和首帧 body 都保持透明，圆角与阴影由 CSS 绘制；鼠标穿透按窗口登记精灵与弹层区域。
- 精灵窗使用 `floating` 层置顶，覆盖当前显示器工作区；跨屏统一换算原点，恢复先选屏再定位，拔屏回到可用显示器。

## 资产与历史缓存

账户缓存保留与移除、迟到结果边界归 [资产访问与缓存](../docs/PROTOCOL.md#资产访问与缓存)，以下只说明本地实现。

### 资产字节与目录

- [asset-disk-cache.ts](main/ipc/asset-disk-cache.ts) 按 `accountId` 分目录；`preferCache` 请求和 `/api/companion/asset/` 下的资源可落盘，其余直连。键取有效 SHA-256 `contentHash`（调用方提供时），否则取剥除签名参数、排序剩余 query 的 URL 摘要；当前调用方未提供 `contentHash`。
- `preferCache` 命中无需等待后端；普通缓存请求用 ETag 复验，网络、正文或写盘失败可保留旧副本，鉴权失败仍上报。`cacheOnly` 只读本地，未命中返回 `null`。
- 同账户、同资源的并发下载合并。有效字节不按数量或容量自动淘汰；损坏文件可重新获取，空间不足报告写入失败。
- 动作目录保留最近成功 manifest，先恢复再网络校准；外观页缓存列表并预取。renderer 的片段、遮罩和图片解析缓存另守卫账户清理代次，失败结果不保留。
- 历史语音只在播放时下载，后续及重启后优先本地读取；`speak` 合成只合并内存与在途请求，`speakScripted` 按内容落盘。

收听状态与断点由 [voice-playback-store.ts](main/ipc/voice-playback-store.ts) 独立保存于 `cache/voice-playback`，不随历史快照失效清除。主进程串行合并、写盘并广播版本快照；每秒保存进度，中断和结束立即保存，退出有界等待。消息删除只清理服务端已确认删除的记录，历史截断不作为删除依据。

### 历史同步

[session-history-cache.ts](renderer/modules/conversation/session-history-cache.ts) 先显示账户与会话快照，再以末条消息 ID 请求增量；锚点失效改为全量。快照不保存实时回合，不能用旧 seq 请求帧重放；重连 `last_seq` 只用于仍保有实时数据的列表。

| 变化 | 本地处理 |
|---|---|
| 已有消息音频或媒体终态变化 | 保留界面，废弃磁盘快照并强制全量；与在途读取交错时重读，超出预算报告失败 |
| 缓存含等待视频，或事件无法重放 | 不使用消息 ID 增量锚点，全量校准 |
| 编辑、撤回或带 hydrate 的历史变化 | 覆盖快照，包含清空和手动压缩 |
| 自动压缩 | 只插入摘要卡 |
| 删除会话 | 删除对应快照 |

媒体终态可能先于完成帧：先暂存，再与完成帧或历史合并；重复完成事件的空音频不能覆盖已就绪音频。

## 契约与验证

`shared/ipc` 是通道与字段真源，`IPC` 使用扁平 camelCase 键以约束叶子通道。改动同步类型、preload、[global.d.ts](renderer/shared/types/global.d.ts)、主进程及消费方，并覆盖换号、断连和迟到结果。命令与真实平台验收归 [Scripts](../scripts/README.md#按改动选择验证)。

## 远程网页

桌面 [remote-page.tsx](renderer/app/features/living/remote-page.tsx) 提供扫码与授权设备管理；窗口和导航标识统一为 `remote`。二维码轮询及设备查询按组件生命周期和账户代次守卫，查询失败显示实际状态，不用旧结果覆盖当前账户。

手机应用归独立 [Remote](../remote/README.md)，桌面与其仅共用根 `shared/protocol.ts` 的纯协议类型。授权与恢复规则见 [远程访问](../docs/PROTOCOL.md#远程访问)。
