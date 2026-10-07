# 手机远程页面

由 Backend 在 `/remote/` 同源托管的独立浏览器入口。账户授权、多端会话与工具准入语义见 [PROTOCOL](../docs/PROTOCOL.md#远程访问)，构建与部署见 [Scripts](../scripts/README.md#手机远程页面构建)。

| 入口 | 内部职责 |
|---|---|
| `api.ts`、`App.tsx` | 身份、请求撤销与连接生命周期；退出或授权失效收敛全部资源 |
| `Chat.tsx` | 挂载期间缓冲事件，按历史水位合并；保留未确认提交标识与草稿 |
| `Feed.tsx`、`Companion.tsx`、`Assets.tsx` | 动态与伙伴内容，按原任务恢复制作，保留记忆冲突草稿 |
| `ui.tsx` | 页面任务与媒体资源生命周期 |

与 Client 仅共用根 `shared/protocol.ts` 的纯协议类型，不导入桌面 store、IPC 或窗口组件。异步回写须校验当前账户与页面代次，录音和音频在切页、隐藏与退出时释放。`dist/` 由 Backend 部署，不随桌面安装包发布；检查与实机验收统一见 Scripts。
