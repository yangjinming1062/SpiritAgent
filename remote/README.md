# 手机远程页面

由 Backend 在 `/remote/` 同源托管的独立浏览器入口。账户授权、多端会话与工具准入语义见 [PROTOCOL](../docs/PROTOCOL.md#远程访问)，构建与部署见 [Scripts](../scripts/README.md#手机远程页面构建)。

| 入口 | 内部职责 |
|---|---|
| [api.ts](api.ts)、[App.tsx](App.tsx) | 身份、请求撤销与连接生命周期；退出或授权失效收敛全部资源 |
| [Chat.tsx](Chat.tsx) | 会话列表、按预设新建与聊天；挂载期间缓冲事件，按历史水位合并，保留未确认提交标识与草稿 |
| [Feed.tsx](Feed.tsx)、[Companion.tsx](Companion.tsx)、[Assets.tsx](Assets.tsx) | 动态／日记、伙伴功能入口、资料、记忆及窗口背景／衣柜；创作、详情与列表分层，按原任务恢复制作 |
| [ui.tsx](ui.tsx) | 页面任务、账户内草稿与媒体资源生命周期；加载失败只重试媒体加载 |

与 Client 仅共用根 `shared/protocol.ts` 的纯协议类型，不导入桌面 store、IPC 或窗口组件。异步回写须校验当前账户与页面代次；切页、隐藏与退出时停止录音并暂停音视频。`dist/` 由 Backend 部署，不随桌面安装包发布；检查与实机验收统一见 Scripts。

主导航为聊天、伙伴、动态、设置；伙伴以功能入口进入资料、衣柜、窗口背景和记忆，日记属于动态的独立子页，两者分别校准已读状态。手机不提供桌景管理。创作默认进入 AI 描述，上传为次要入口，外部制作说明按需展开；衣服确认入柜后，首次穿着须明确准备动作，已有动作直接启用。文本草稿和子页选择按账户与设备保存在当前浏览器会话，参考图片仅保留在本次登录内存；服务端任务仍是制作进度的事实来源。提交结果未知时须刷新原资产或列表并核对，再开始新制作，不自动重做；记忆冲突保留原版本及草稿。

开发入口为 [package.json](package.json) 与 [vite.config.ts](vite.config.ts)：前端监听本机 `5175`，`/api`（含 WS）代理到本机 Backend `10620`。设备授权仍要求同源 HTTPS、Secure Cookie 和 Origin 校验，开发 HTTP 代理不能代替完整扫码验收。样式在 [style.css](style.css)，共享协议变更须同时核对 Client 与 Remote 消费方。
