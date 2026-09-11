# Client 模块代码审查清单

依据 [RULES.md 代码规范](../RULES.md#代码规范) 七条原则，对照 [client/README.md](../client/README.md)、[ARCHITECTURE.md](ARCHITECTURE.md) 与 [PROTOCOL.md](PROTOCOL.md) 对 `client/main`、`client/shared`、`client/renderer` 做静态审查。不含 `node_modules`、`dist*`、`release`、`experiments`。

严重级别：高 = 安全边界 / 明确契约违规 / 必现缺陷路径；中 = 规范违规或高概率潜在 BUG；低 = 可维护性债务。

---

## 高

### 1. `getConnection` 把会话 JWT 发给渲染进程

- 位置：`shared/ipc/contracts.ts:87-94`、`main/ipc/connection.ts:115`、`main/entry.ts:430-500`
- 违反：ARCHITECTURE §3「渲染进程隐藏凭证」；client README §4「激活码加密持久化，会话 JWT 仅内存」。
- 事实：`SpiritAgentConnection.token` 为 `string | null`；`ipcMain.handle(IPC.invoke.connection)` 直接返回 `ensureBackend()` 的完整对象。渲染端实际只用 `wsUrl`（`resolveGatewayWsUrl` 类型为 `Pick<SpiritAgentConnection, 'wsUrl'>`），从未读 `token`。
- 风险：渲染进程被 XSS / 依赖投毒后可直接读出 JWT。鉴权广播路径已正确剥离 token（`DesktopAuthSnapshot` 只有 `hasToken`），说明边界本意是「不下发 token」。
- 建议：`getConnection` 返回去掉 `token` 的投影，或改由 `getGatewayWsUrl` 单独暴露 WS URL；同步收紧契约类型。

### 2. `puppet-store` 无桥 `fetch` 死分支

- 位置：`renderer/2d/puppet/puppet-store.ts:98-132`
- 违反：原则六（死代码）；client README §4「生产渲染禁止裸 fetch」。
- 事实：注释自认「无桥防御分支：桥在时永不执行」。生产 Electron 始终有 preload 桥；该分支只会在无桥环境（本仓库不支持）跑 `fetch(info.manifestUrl)`，且用 `eslint-disable` 绕过限制。
- 风险：死路径增加攻击面与维护成本；后续改 manifest URL 时容易误以为有第二条合法取数路径。
- 建议：删掉 `else` 分支，桥不存在时直接走已有的失败回退。

### 3. `living/` ↔ `companion/` 双向依赖（文档与 ESLint 均未拦住）

- 位置：`renderer/companion/events.ts:52`（companion → living）；`living-root.tsx:10`、`living-rail.tsx:4`、`living/settings/*` 等约 13 处（living → companion）
- 违反：client README §3 依赖方向 `companion/ → living/`；原则五。
- 事实：ESLint 只禁 living→workbench 与 workbench→living，未禁 living→companion。companion barrel 又大量导出 living 设置页需要的 persona / wardrobe / spatial 状态。
- 风险：改 companion 内部可能牵动生活空间多页；与 README 声明的单向依赖矛盾，后续模块拆分会踩循环。
- 建议：要么把共享状态上移到 `shared/`（或独立 identity store），要么改 README/ESLint 为明确的双向允许并说明理由；二选一，不要现状。

### 4. `connection` 401 依赖错误文案前缀匹配

- 位置：`main/ipc/connection.ts:12-16`
- 违反：原则一（脆弱假设）；潜在 BUG。
- 事实：`notifyAuthExpiredOn401` 用 `message.startsWith('401 ')` 判断鉴权失效；该文案由 `fetchFromBackend` 手工拼出。其他调用方若改错误格式，会话过期事件会静默丢失。
- 建议：让 `fetchFromBackend` 抛结构化错误（如 `{ status: 401 }`），按 status 判断，不解析字符串。

---

## 中

### 5. `entry.ts` 过重，业务逻辑堆在入口

- 位置：`main/entry.ts`（约 990 行）
- 违反：原则五「入口目录只放入口、配置和数据模型；业务逻辑下沉」。
- 事实：同文件含单实例锁、精灵窗创建、`ensureBackend` 缓存、托盘、auth 广播、多处 IPC 注册与 boot 装配。
- 风险：改窗口策略易误伤连接缓存；单文件难做边界测试。
- 建议：至少拆出 `ensureBackend`/连接缓存、窗口创建、boot 装配三块。

### 6. 硬编码颜色绕过主题 token

- 位置：`renderer/onboarding/egg-stage.tsx:123-173`；`renderer/living/channels-page.tsx:282`；`renderer/companion/vfx.tsx:140`
- 违反：client README §4「新增控件不得硬编码颜色」「窗口控件统一消费 `--ui-*`」。
- 事实：蛋壳 `#ffd166`、`#1a1a2e`、腮红 `#ff9999`；二维码 `#ffffff/#000000`；VFX 文字 `#93c5fd` 直接写在 TSX。
- 风险：主题切换后这些层颜色不跟；深浅色对比度不可控。
- 建议：迁入 theme registry / CSS 变量；二维码若必须固定黑白，用注释标明业务约束。

### 7. OPFS 缓存外层 `catch { return null }` 吞掉全部读错误

- 位置：`renderer/shared/lib/opfs-blob-cache.ts:206-208`
- 违反：原则四「异常只在系统边界捕获」——OPFS 是边界，但应区分「未命中/损坏」与「权限/配额/浏览器错误」。
- 事实：内层已单独处理 meta 损坏与 size mismatch；外层空 catch 把 `SecurityError`、`QuotaExceededError`、`NotSupportedError` 一律当 miss。
- 风险：磁盘配额或权限问题被静默当成「无缓存」，反复走网络且难排查。
- 建议：外层 catch 按 `DOMException.name` 分类，至少 `log.warn` 一次再返回 null。

### 8. `$chatSessionKind` 用宽泛 `string`

- 位置：`renderer/chat/chat-store.ts:64`
- 违反：原则四「显式标注类型；结构化数据用原生强类型」。
- 事实：`atom<string>('standard')`，而语义是会话 kind 枚举（至少 standard / companion / im…）。
- 建议：改为与后端一致的字面量联合类型。

### 9. `onConnectionReady` 空实现仍下发完整连接对象

- 位置：`renderer/companion/root.tsx:56-60`；`use-gateway-boot.ts:114-116`
- 违反：原则六（死回调）；与问题 1 叠加。
- 事实：唯一调用方传 `() => {}`，但 `publish(conn)` 仍把含 token 的连接对象送进渲染层回调链。
- 建议：删掉该回调参数，或真正消费连接信息（且不含 token）。

### 10. `files.ts` 附件读取可读任意用户可达路径

- 位置：`main/security/hardening.ts:138-234`；`main/ipc/files.ts:35-77`
- 违反：潜在安全边界问题（非直接 RULES 条文，但触碰「渲染层只经 IPC 使用能力」的扩大面）。
- 事实：`resolveRequestedFilePath` 对绝对路径直接 `path.resolve`；仅拦截 `.ssh`、`.env`、私钥等黑名单。渲染进程可读用户目录下任意未列入黑名单的文件（如其他项目的 `.git/config`、聊天导出、浏览器配置旁的 JSON）。
- 风险：配合 XSS 或恶意依赖可扩大本地文件外传面；产品上附件本意多半是「用户挑选的文件」。
- 建议：默认限制在用户显式选择（`dialog`）或拖拽路径白名单；IPC 侧拒绝未经选择器的任意绝对路径。

### 11. `activity.ts` 深夜时段在客户端硬编码跳过 affect

- 位置：`renderer/companion/activity.ts:72-77`
- 违反：原则七边界需再对齐；ARCHITECTURE §5.1 称夜间与打扰档位正交、由服务端闸门/夜间总控裁决。
- 事实：`hour >= 23 || hour < 7` 时不发起 `companion.check_affect`，与档位无关。
- 风险：客户端与后端夜间政策可能不一致；改服务端时区/策略后客户端仍按本地钟静默吞请求。
- 建议：确认该行为是否已在 DESIGN 写明；若权威在服务端，客户端只保留 idle/可见性/档位门控。

### 12. `living/diary-page` 深挖 companion 内部路径

- 位置：`renderer/living/diary-page.tsx:7` → `@/companion/persona-store`
- 违反：原则五「跨模块调用必须走目标模块的公共入口」；与 README「特性间调用只走公共 barrel」一致，但此处绕过 `@/companion` barrel。
- 建议：改为 `import { $persona } from '@/companion'`。

---

## 低

### 13. 大量「空 catch」但多数有注释、属边界容错

- 位置：`opfs-blob-cache.ts`（并发 remove）、`json-rpc-gateway.ts:361,479`（socket.close）、`storage.ts:227,239`、`bridge.ts:42-48` 等
- 说明：ESLint 允许 empty catch；多数注释写明竞争移除/已解绑。不作为必改项，但 `opfs-blob-cache` 外层（问题 7）例外。

### 14. `use-main-process-listener` 整文件关闭 exhaustive-deps 与 React Compiler

- 位置：`renderer/shared/hooks/use-main-process-listener.ts:11-25`
- 说明：注释写明「deps 由调用方管理」，属有意逃逸。风险是调用方漏传 deps 时静默陈旧闭包。建议在 hook 类型或调用约定上再收紧，或按通道拆专用 hook。

### 15. 渲染层两处「有理由」的裸 fetch

- 位置：`glb-opfs-cache.ts:35-36`（`spiritagent-media://`）；`puppet-store.ts:114-115`（死分支）
- 说明：GLB 例外逐行注释 URL 来源，符合 README；puppet 死分支见问题 2，应删除而非保留 disable。

### 16. 离线反应池 / 仪式行走台词符合原则七例外

- 位置：`companion/reactions/reaction-audio.ts`；`companion/ritual-walk.ts:23-26`
- 说明：注释指向 DESIGN 离线/机械降级；LLM 失败才回落本地池，未见程序化伪造实时心情。无需改。

### 17. 会话 kind / 包装类型可再收紧

- 位置：`chat-store.ts:64`（见问题 8）；`events.ts` 内 `decodePayload` / `as` 断言若干处
- 说明：事件 payload 多为后端驱动的运行时数据，边界断言可接受；新增方法时优先加收窄函数。

### 18. `entry.ts` 中 `sameWindowButtonPosition` 等小工具

- 位置：`main/entry.ts:414-419`
- 说明：单一调用点，按原则二可内联；随问题 5 拆文件时一并处理即可。

---

## 已核对、未发现问题的要点

- chat 不依赖 companion/2d/3d/living/workbench；shared 不反向依赖特性（ESLint 与源码一致）。
- 生产渲染数据/资产主路径走 `window.spiritagent.api*` 桥；媒体 `createObjectURL` 在 effect cleanup 有 `revokeObjectURL`。
- Runner 握手 token 走环境变量不进 argv；endpoint 文件 chmod 0600；资产拉 token 有 trusted baseUrl 守卫。
- 序列号去重/ACK、心跳半开检测实现完整；登出路径会清 OPFS/缓存并停 Runner。
- 鉴权广播不下发 JWT；问题仅在 `getConnection` 旁路。

---

## 建议修复顺序

1. 收紧 `getConnection` / 删除渲染侧无用连接投影（问题 1、9）。
2. 删除 puppet 死分支（问题 2）。
3. 明确 living↔companion 依赖策略并改 ESLint/README/代码其一（问题 3、12）。
4. 401 判定结构化（问题 4）；附件路径收紧（问题 10）。
5. 主题 token、OPFS 外层日志、entry 拆分、类型收紧（问题 5–8、11）。

---

## 审查范围说明

- 静态阅读 + 全库检索；未跑运行时/集成测试，未覆盖 `dist-electron` 产物与安装器联动。
- 子代理并行审查因环境错误未返回结果，结论均来自本回合对源码的直接核对。
- 大文件抽样：`entry.ts`、`chat-store.ts`、`events.ts`、`opfs-blob-cache.ts`、`json-rpc-gateway.ts`、`asset-disk-cache.ts`、`media.ts`、`session.ts`、`bridge.ts`、`surfaces.ts`、`interaction.ts`、`ritual-walk.ts`、`speech-text.ts` 等。
