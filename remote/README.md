# 手机远程页面

由 Backend 在 `/remote/` 同源托管的独立浏览器应用。身份与多端恢复归 [远程访问](../docs/PROTOCOL.md#远程访问)，产品交互归 [DESIGN](../docs/DESIGN.md)。本文维护浏览器侧状态、请求和资源生命周期。

## 任务入口

| 改动 | 入口 |
|---|---|
| 登录、退出、连接和请求 | [App.tsx](App.tsx)、[api.ts](api.ts) |
| 会话、提交与话题反馈 | [Chat.tsx](Chat.tsx)、[TopicLink.tsx](TopicLink.tsx) |
| 动态、日记与已读 | [Feed.tsx](Feed.tsx) |
| 生活近况、事项与阅读成果 | [LifeSummary.tsx](LifeSummary.tsx)，由 [Companion.tsx](Companion.tsx) 装配 |
| 资料、记忆与声音 | [Profile.tsx](Profile.tsx)、[Memory.tsx](Memory.tsx)、[memory-state.ts](memory-state.ts)、[Voice.tsx](Voice.tsx) |
| 素材、动作与影像 | [Assets.tsx](Assets.tsx)、[Actions.tsx](Actions.tsx)、[Visuals.tsx](Visuals.tsx) |
| 草稿、轮询、保存核对与媒体 | [ui.tsx](ui.tsx)、[save-result.ts](save-result.ts) |

## 请求与状态边界

与 Client 共用根 `shared/` 的生成契约、HTTP、WS 核心及中性文案／错误归类，不导入 Electron 桥、桌面 store 或窗口组件。业务操作经 `RemoteApi.invoke`，适配层管理同源 Cookie、CSRF、撤销与登录失效；切换身份先废弃旧请求，迟到结果不得更新新账户。HTTP 操作不依赖 WS 当前是否在线。

`RemoteGateway` 只协调事件、连接状态和会话快照。聊天先缓冲事件再应用基线，按水位合并；重放成功保留已有基线，失效才回源。未确认提交保留请求身份并先查询回执，不能借重连重复提交。话题反馈按会话去重和分批读取，同一话题共用反馈与保存状态。

生活组件读取同账户的权威快照，刷新不推进判断或制作；同一查询失败保留可读内容，选中事项与详情按 ID 对应。动态和日记独立分页及标记已读，确认前复核页面、可见性和焦点。生活事实见 [生活契约](../docs/PROTOCOL.md#生活状态与生活事件)。

## 草稿、制作与媒体

文本草稿、筛选、子页及阅读位置按账户与设备保存在浏览器会话，参考图片仅留在本次登录内存。后台刷新不覆盖草稿或编辑起点；写入结果不明时重读并比对，确认提交值已生效才清理草稿，真实失败保留修改与原版本。记忆搜索、分页和展开编辑由 `memory-state.ts` 管理，收起条目不丢草稿。

制作进度以服务端任务为准，离页后从原任务恢复，未知提交先核对资产或任务再决定后续操作。影像库可主动选择素材制作、上传原件、预览和维护成品，手机不提交正式播放回执；创作请求身份和文本草稿按账户保存，上传文件只保留在本次登录内存，重试原件由服务端哈希去重。上传后显示持久分析进度、素材匹配依据、提取结果与呈现资格，支持继续/取消分析、修正关联及撤销本次入库；歧义与技术失败分别展示。图片加载失败重取媒体，不重做作品。切页、隐藏、退出及身份失效停止录音并暂停音视频，释放对象 URL 和订阅。

## 开发与验证

[package.json](package.json)、[vite.config.ts](vite.config.ts) 定义开发与构建；本机开发端口为 `5175`，`/api`（含 WS）代理到 Backend `10620`，`dist/` 由 Backend 部署。请求诊断与复制说明归 [browser.ts](browser.ts)，样式归 [style.css](style.css)。

命令见 [手机远程页面构建](../scripts/README.md#手机远程页面构建)。协议变更同时核对桌面消费者；会话恢复、未知提交、保存冲突和账户切换分别验证。扫码、麦克风、移动浏览器后台恢复需要 iOS Safari 与 Android Chrome 实机证据。
