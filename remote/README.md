# 手机远程页面

由 Backend 在 `/remote/` 同源托管的独立浏览器入口。账户授权、多端会话与工具准入语义见 [PROTOCOL](../docs/PROTOCOL.md#远程访问)，构建与部署见 [Scripts](../scripts/README.md#手机远程页面构建)。

| 入口 | 内部职责 |
|---|---|
| [api.ts](api.ts)、[App.tsx](App.tsx) | 身份、请求撤销与连接生命周期；退出或授权失效收敛全部资源 |
| [Chat.tsx](Chat.tsx) | 会话列表、按预设新建与聊天；挂载期间缓冲事件，按历史水位合并，保留未确认提交标识与草稿 |
| [Feed.tsx](Feed.tsx)、[Companion.tsx](Companion.tsx)、[Assets.tsx](Assets.tsx) | 动态、日记、资料、按预设记忆及场景／衣柜；按原任务恢复制作，保留记忆冲突草稿 |
| [ui.tsx](ui.tsx) | 页面任务与媒体资源生命周期 |

与 Client 仅共用根 `shared/protocol.ts` 的纯协议类型，不导入桌面 store、IPC 或窗口组件。异步回写须校验当前账户与页面代次，录音和音频在切页、隐藏与退出时释放。`dist/` 由 Backend 部署，不随桌面安装包发布；检查与实机验收统一见 Scripts。

开发入口为 [package.json](package.json) 与 [vite.config.ts](vite.config.ts)：前端监听本机 `5175`，`/api`（含 WS）代理到本机 Backend `10620`。设备授权仍要求同源 HTTPS、Secure Cookie 和 Origin 校验，开发 HTTP 代理不能代替完整扫码验收。样式在 [style.css](style.css)，共享协议变更须同时核对 Client 与 Remote 消费方。
