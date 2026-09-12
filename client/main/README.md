# Main 主进程

Electron 可信主进程：持有凭据、窗口与表面生命周期、Runner 进程与 OS IPC、配置镜像与云同步、磁盘缓存、自更新与托盘。渲染层只经 preload 暴露的 `window.spiritagent` 使用这些能力。客户端全局与产品侧主进程决策见 [client/README.md](../README.md)，跨进程通道定义在 `client/shared/ipc/`（`@ipc`），信任域与平台策略见 [ARCHITECTURE.md](../../docs/ARCHITECTURE.md)。

## 包边界

- `entry.ts` 是唯一组合根：装配窗口、托盘、IPC 注册、Runner bridge 与配置同步；业务逻辑不在入口内展开。
- `preload.ts` 是渲染层唯一桥：输出 CJS（`dist-electron/preload.cjs`），主进程输出 ESM（`dist-electron/entry.js`）。沙盒 preload 不按 ESM 解析，混入 import 会让整桥失效。
- `backend/` 会话与 HTTP：激活码加密持久化、JWT 内存刷新、连接缓存（token / 窗口态失效重建）。
- `runner/` 进程与本地 IPC：Client 作 RPC Server，Runner 启动后连入；握手 token 准入；反向 LLM 代理与两阶段更新。
- `lifecycle/` 窗口与应用生命周期：精灵窗、生活空间 / 工作台互斥管理、托盘 / 菜单、自更新、`spiritagent-media:` 协议、启动进度状态机。
- `ipc/` 通道注册：一文件一能力面（auth、gateway、media、prefs、shortcuts、sprite…），在 entry 中显式挂载。
- `security/` 准入与加固：API 白名单、可读文件白名单、敏感路径拦截、sender 窗口校验、`SPIRITAGENT_HOME` 解析。
- `shared/` 叶子层：配置镜像存储与云同步、MIME、工具函数、后端端口类型。不 import `backend/` 或 `runner/` 实现，避免循环。

依赖方向：`entry` → 各子包；`ipc/*` / `lifecycle/*` / `runner/*` → `security` + `shared`；`shared` 只暴露结构端口（`backend-port.ts`），由 entry / bridge-deps 注入实现。

## 启动与装配

- `SPIRITAGENT_HOME` 经 `SPIRITAGENT_DESKTOP_USER_DATA_DIR` 覆盖或平台默认路径解析，随后 `app.setPath('userData', …)`——会话、配置镜像、缓存、日志共用同一 home，避免散落多份 userData。
- 默认单实例锁；`SPIRITAGENT_DESKTOP_DISABLE_SINGLE_INSTANCE_LOCK=1` 仅用于并行调试。第二实例事件在完整 forwarder 就绪前先折叠成标志，就绪后再兑现一次，避免 `whenReady` 竞态丢事件。
- 检测到远程显示时禁用 GPU 硬件加速，降低闪烁。
- Chromium 后台节流全局关闭：精灵窗 7x24 常驻，不能靠浏览器默认降频；渲染侧功耗自管见 [renderer README](../renderer/README.md)。
- `window-all-closed` 不退出（托盘常驻）；真正退出走托盘 / 菜单 `app.quit` → `before-quit` 配置 flush → `will-quit` 有界等待 Runner 停止（约 3s）后 `app.exit(0)`，避免 fire-and-forget 孤儿进程。

## 关键设计决策

### 凭据与渲染面隔离

- 激活码经 `safeStorage` 加密落盘，会话 JWT 仅内存并主动刷新；渲染与 preload 永不接触持久令牌存储接口。规则见 [PROTOCOL.md §5.3](../../docs/PROTOCOL.md)。
- 渲染层 `api()` 只放行相对路径与固定前缀（`/api/channels`、`/api/companion`、`/api/config`、`/api/sessions`）；绝对 URL、协议相对、路径穿越一律拒绝，防止凭据被打到任意 endpoint。
- 文件读取走用户选择白名单：对话框选择或拖拽解析成功即登记；不向渲染层暴露任意注册 API，防 XSS 自授后外传。敏感路径（`.ssh/`、`.env*`、证书私钥等）另有一层硬拦截。
- `spiritagent-media:` 自定义协议只流式读出 `SPIRITAGENT_HOME` 下 `cache/` 与 `audio/`，并校验可读与扩展名；渲染层不能经协议探测任意绝对路径。

### 网关宿主—代理

- 仅精灵 / 主窗口可上报网关状态、灌业务事件、抢答 RPC；生活空间 / 工作台只能发 `gatewayRequest`，经主进程转发给宿主 WebSocket。非宿主广播直接丢弃并记日志。
- 代理请求挂起表带超时；网关 `closed` / `error` 时统一 reject，避免表面窗拿着已断连接的 future 悬挂。宿主身份与连接角色见 [client/README.md §4](../README.md)。

### 表面互斥与几何

- 生活空间与工作台同一时刻最多一个可见；`pendingChain` 串行化并发 open / close，避免竞态双开。
- 工作台几何相对当前显示器工作区上报，并驱动精灵窗跟随到同一显示器；生活空间不携带栖息坐标。
- 上次入口写入配置镜像 `ui.last_surface`，启动水合回灌，双击精灵按此开窗。

### 配置镜像与云同步

- `desktop-settings.json` 是本地镜像，云端 `user_settings` 为真源；同步节白名单见 [PROTOCOL.md §2.4](../../docs/PROTOCOL.md)。机密与设备节（terminal、spiritagent 等）永不上传。
- 写入经写锁串行化：原子落盘 → 推 Runner → 防抖上云。`applyCloudMirror` 期间抑制本地变更通知，防回环。
- 镜像带用户归属戳；换号时残留同步节不信任、不上传，防止 A 的编辑泄给 B。
- Runner 配置补丁 IPC 仅接受工作台窗口 sender，避免其他表面误写本机工具配置。

### Runner 生命周期

- Client 监听 OS IPC（Windows 命名管道 / macOS UDS），Runner 主动连入；握手 token 校验失败即 401，不进入业务帧。端点路径与 token 由 Client 单向下发，Runner 重连间重读配置以跟随 Client 重启。
- `bridge-deps` 把会话、进程、反向 RPC、WS Server、日志等全部收成参数注入，入口不持有隐藏全局；可变 `getAuthToken` 以 getter/setter 成对接入，保证会话切换后 bridge 读到最新 token。
- 连接缓存 `ensure-backend` 用代数标记：reset 时递增，在途 resolve 完成后若代数已变则不写回缓存，避免陈旧连接复活。
- Runner 更新语义是「装新 wheel + 覆盖 server.py」，一次性切到新版本，不在安装期做兼容 smoke 或回滚。wheel 与 `server.py` 的导入面一致性由构建期 `scripts/check_runner_facade.py` 门禁；`uv` 路径与 installer 对齐（`$SPIRITAGENT_HOME/bin/uv`，venv 通常不带 pip）。

### 网络与缓存

- 生产渲染禁止裸 fetch；资产与模型字节统一经主进程磁盘缓存。缓存键优先 `contentHash`，否则去掉签名查询参数后哈希——同内容不同签名 URL 命中同一文件。
- 默认请求超时 15s；头像 / 形象 / 媒体生成类 POST 放宽到 120s（供应商调用 + 重编码通常 15–25s），读路径不放宽。
- 401 按结构化状态判定并广播会话过期，不解析错误文案。

### 构建产物

- tsup 打包 main（ESM `.js`）与 preload（CJS `.cjs`），`@ipc/contracts` 经 esbuild alias 解析到 `shared/ipc/contracts`。dev 脚本显式 `--watch main --watch shared`，避免 tsup 配置级 watch 劫持全树。
- 改 preload 导出或主进程入口路径时同步检查 `dist-electron` 产物名与 `package.json` `main` 字段。

## 与外部的契约

| 参与方 | 定义位置 |
|---|---|
| 渲染层 ↔ 主进程 | preload 暴露面 + `@ipc/contracts`；信任校验用 `isSenderWindow` / 表面角色 |
| Client ↔ Backend | [PROTOCOL.md](../../docs/PROTOCOL.md)（会话、配置、资产、更新） |
| Client ↔ Runner | [PROTOCOL.md §2](../../docs/PROTOCOL.md) + [runner/README.md](../../runner/README.md) |
| 产品侧主进程决策 | [client/README.md §4](../README.md)（窗口、凭据、缓存、更新、快捷键等） |
| 渲染模块契约 | [renderer/README.md](../renderer/README.md) |

## 已知限制

- 用户选择路径白名单仅进程内、上限 256 条；重启后需重新选择，不能跨会话复用历史附件路径。
- 配置上云为按保存先后覆盖，多端并发编辑不做合并；离线编辑恢复后只播种云端缺失键。
- 远程显示模式下透明精灵关闭 GPU 加速，合成与滤镜表现与本机不同；调试透明 / 模糊问题前先确认是否处于该模式。
- Runner 停止等待有界超时；超时后进程可能残留至系统回收，日志中会记录 quit cleanup 失败。
