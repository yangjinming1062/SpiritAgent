# Client

桌面应用由主进程、渲染层和平台宿主组成。本文维护跨进程入口与共享缓存；产品行为见 [DESIGN](../docs/DESIGN.md)，状态权威和平台支持见 [ARCHITECTURE](../docs/ARCHITECTURE.md)。

## 任务入口

| 改动 | 实现入口 |
|---|---|
| 登录、换号、网关和本机派发 | [主进程](main/README.md#账户网关与窗口订阅)、[账户编排](renderer/app/workflows/account-lifecycle.ts) |
| 主窗口、桌面、Dock 与异常恢复 | [窗口与几何](main/README.md#窗口与几何)、[桌面承载](main/README.md#桌面承载与恢复)、[Windows](desktop-windows/README.md)、[macOS](desktop-macos/README.md) |
| 对话、语音、历史与媒体 | [会话与媒体](renderer/README.md#会话与媒体)、[历史同步](#历史同步) |
| 生活近况、事项与成果 | [生活读取与浏览](renderer/README.md#生活读取与浏览)、[生活页面](renderer/app/features/life/) |
| 伙伴资料、衣柜、环境、动作与影像 | [渲染层入口](renderer/README.md#分层与目录)；后端生产方按 [AGENTS](../AGENTS.md) 定位 |
| 设置、主题、声音与手机授权 | [settings](renderer/app/features/settings/)、[声音管理](renderer/app/features/companion/voice-manager.tsx)、[远程网页](#远程网页) |
| Runner、配置及桌面更新 | [Runner 生命周期](main/README.md#runner-生命周期)、[配置镜像](main/README.md#配置镜像)、[桌面更新](main/README.md#桌面更新) |

## 进程与代码边界

[main](main/README.md) 持有凭据、连接、窗口、Runner 与磁盘资源；[renderer](renderer/README.md) 持有会话投影、页面草稿和呈现。主窗口与交互桌面共用页面，背景窗口只消费受限媒体桥。平台宿主通过主进程统一端口接入，不拥有业务状态。

`shared/ipc` 是 main、preload 与 renderer 共用的契约；`main/shared` 是主进程内部叶子层，两者不混用。主进程不导入依赖窗口环境的 `renderer/shared`。main / preload 由 tsup 构建，renderer 由 Vite 构建，产物边界见[主进程包边界](main/README.md#包边界)。

## 连接与设备就绪

主进程持有账户唯一聊天 WS，窗口通过类型化 IPC 请求和订阅；关窗不释放连接或已受理调用。账户网关、恢复和工具同步由[主进程](main/README.md#账户网关与窗口订阅)统一处理，跨端身份与执行语义见 [本机工具](../docs/PROTOCOL.md#本机工具)。

## 窗口与主题

各窗口内存独立。页面、会话草稿与阅读位置通过主进程账户快照交接，位置、比例与实际显隐通过 IPC 同步，不能只修改来源窗口 store。交接与舞台资格归 [呈现契约](../docs/PROTOCOL.md#桌面呈现与本机启动器)，本地实现归[窗口生命周期](main/README.md#窗口与几何)。

主题在 `loadURL` 前由配置镜像写入入口参数，renderer 首帧读取后移除；缺省依次回落 localStorage 和 `day-clear`，云端水合随后收敛。原生窗口与首帧 body 保持透明，圆角和阴影由 CSS 绘制。精灵浮在工作区，跨屏计算使用所属屏幕原点；透明穿透由各窗口登记的命中区域决定。

## 资产与历史缓存

缓存是账户副本，保留与移除规则归 [资产访问与缓存](../docs/PROTOCOL.md#资产访问与缓存)。以下规则同时约束主进程读写和渲染层投影。

### 资产字节与目录

[asset-disk-cache.ts](main/ipc/asset-disk-cache.ts) 按账户隔离目录，以剥除签名参数后的 URL 摘要作键；`preferCache` 及账户资产接口可落盘，其余直连。同账户同资源下载合并，完整正文读取后才落盘。有效文件不按容量或数量淘汰；损坏可重取，空间不足报告失败。

`preferCache` 命中直接返回，普通读取用 ETag 复验，`cacheOnly` 未命中返回空值。网络、正文或写盘失败可保留旧副本，鉴权失败仍上报。下载预算覆盖响应体；传输中断最多重试一次并共享原期限，取消、超时和 HTTP 失败不自动重试。删除账户缓存须先等待旧下载和写入，新请求等待清理完成。

衣柜预取立绘，先展示可用缓存再校准；图片解码与 alpha 命中守卫账户代次。历史语音按需下载；普通合成只合并内存与在途请求，预制语音按账户和内容落盘。在途合成还绑定鉴权会话，换号不交付旧结果。

[voice-playback-store.ts](main/ipc/voice-playback-store.ts) 独立保存收听状态与断点，串行写入并广播版本；历史快照失效不清收听记录，历史截断也不表示消息已删除。定期及中断、结束时保存，退出有界等待。

### 历史同步

[session-history-cache.ts](renderer/modules/conversation/session-history-cache.ts) 先显示通过字段校验的账户／会话快照，再经主进程取得完整基线；同会话并发读取合并。磁盘快照不保存实时回合，其水位不能代替主进程的连接重放游标。重放成功且已有基线时不重拉历史。

| 变化 | 缓存与投影处理 |
|---|---|
| 已有消息的音频／媒体终态变化 | 保留界面，废弃磁盘快照并强制全量；与读取交错时有界重读 |
| 事件不可重放 | 取得完整基线 |
| 编辑、撤回、清空或带 hydrate 的历史变化 | 替换快照；自动压缩仅插入摘要卡 |
| 删除会话 | 删除所属快照 |

媒体终态可先于完成帧，须暂存后合并；重复完成事件的空音频不能覆盖已就绪音频。

## 契约与验证

[bridge.ts](shared/ipc/bridge.ts) 定义普通、背景和文件桥，preload 用 `satisfies` 校验实现；renderer 只经 [desktop.ts](renderer/shared/platform/desktop.ts) 的窄接口消费。修改通道须同步主进程与实际消费者，覆盖换号、断连、迟到回包及资源释放。检查和平台验收归 [Scripts](../scripts/README.md#按改动选择验证)。

## 远程网页

[remote-page.tsx](renderer/app/features/remote/remote-page.tsx) 负责桌面扫码与设备授权管理，轮询和查询守卫组件及账户代次。手机应用由独立 [Remote](../remote/README.md) 构建，不随桌面安装包发布；授权与恢复见 [远程访问](../docs/PROTOCOL.md#远程访问)。
