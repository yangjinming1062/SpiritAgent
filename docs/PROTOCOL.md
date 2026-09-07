# SpiritAgent 跨模块协议契约

> 本文收纳 Backend ↔ Client ↔ Runner 之间**跨模块共享的契约**。核心目的：当你改某个功能时，提醒你同时兼顾多个模块，避免只改一处导致遗漏。
> 架构动机（为什么这样设计）见 [ARCHITECTURE.md](ARCHITECTURE.md)；产品设计意图见 [DESIGN.md](DESIGN.md)；实现细节、文件路径、错误码、配置项见各模块 [README.md](../README.md)。

## 0. 契约总览

全链路采用 **JSON-RPC 2.0** 封装双向流量，三类链路共用同一信封：

| 链路 | 方向 | 传输 | 鉴权 | 见 |
|------|------|------|------|----|
| Backend ↔ Client | 双向 | WebSocket 长连接（/api/chat/ws） | 短时 ws-ticket（60s TTL，purpose=ws） | §1 |
| Client ↔ Runner | 双向 | 本地 OS IPC（Windows 命名管道 / macOS UDS）承载 WebSocket 帧 | 每次启动握手 token（失败 401） | §2 |
| Runner → Client → Backend（反向 RPC） | Runner → Client → Backend | 嵌套在 §2 上，经 Client 转发到 /api/llm/completion | Client JWT | §3 |

**信封四种形态**（JSON-RPC 2.0）：请求（带 id + method + params）、事件（无 id、method=event、带 type + payload + seq，不可被响应）、响应（带 id + result 或 error）、ACK（带 method=session.ack、params={seq: int}）。

**核心约定**：
- call_id 是整张表的**唯一 Future Key**——后端按 (user_id, call_id) 寻址，跨用户不共享。
- 事件无 id，不可被响应；请求与响应必须按 id 配对。
- 所有下发事件均附加递增序列号 `seq`（从 1 开始）；序列号与客户端 `lastReceivedSeq` 均为**连接级（Connection/User 级）**状态，跨 Session 共享。客户端维护 `lastReceivedSeq` 保证去重与有序消费。
- 客户端定期向服务端发送 `session.ack(seq)` 确认消费进度（带 id 的标准 RPC 请求），服务端自重放缓冲中修剪已确认帧。
- **心跳保活（session.ping）**：客户端在连接空闲 15s 时发送 `session.ping`（带 id 的标准 RPC 请求），服务端回 `{}`；若 30s 内无任何帧到达，客户端判定半开连接并主动 `close(4000, 'heartbeat')` 触发重连。该机制覆盖 NAT 超时、Wi-Fi 切换、VPN 抖动、笔记本合盖等场景，避免用户发消息后 120s 死寂。
- **全量重水化的防御性截断**：当全量重水化返回的消息数达到防御上限时，响应携带截断标记与更早历史的分页游标；客户端可通过会话消息 REST 端点按游标向后翻页拉取更早历史。该截断仅作为超大历史的负载防御兜底，优先仍走重放缓冲的无缝恢复。
- **水合消息必带创建时间**：后端向客户端下发会话历史消息时（会话恢复、主会话获取、分支派生、消息撤回、上下文压缩、会话清空及历史消息端点），每条消息必须携带创建时间的毫秒级时间戳。改此处需同步：后端消息重建与客户端对话水合。
- WS 关闭码 1008（鉴权失效）= 立即退出重连流程，不继续尝试。
- **WS 鉴权用短时 ticket**：客户端连接前持 Bearer JWT 调 POST /api/user/ws-ticket 现铸 60s TTL 的专用 token（purpose=ws），经查询串携带；长效 JWT 不进 URL（避免落入代理/访问日志）。`?token=` 直传 JWT 仅限后端内部调用。

### 1.0 后端不下发窗口开关与工位背景

生活空间与工作台两个入口的互斥开窗完全由客户端负责。后端仅暴露房间、时刻、日记、会话与工具等资源，不下发窗口像素指令，也不为工作台单独生成工位背景。工作台采用单一复合窗口一体化挂载伴工精灵；桌面精灵在工作台开启时收起，工作台关闭时恢复。常规对话生图仅产生会话媒体卡片，绝不触碰激活房间背景；更换房间必须走房间生成接口或专属换房工具。改此处需同步：后端房间端点、房间服务与客户端房间组件。

---

## 1. Backend ↔ Client 契约

### 1.1 通道分工（WS vs REST 路由原则）

后端与客户端同时暴露 JSON-RPC over WebSocket 与 HTTP REST。两套通道的**设计意图不同**——选错通道等于把一类语义错配到不属于它的链路：

| | WS（JSON-RPC） | REST |
|---|---|---|
| **设计意图** | 绑定进程内上下文的持续推送通道——事件、进度与小包数据流以 WS 为单一推送路径，关 WS 即丢弃运行时状态 | URL 寻址、无状态 CRUD——任何持有 JWT 的入口（Hub、CLI、脚本、第三方集成）可独立调用，与 chat 是否在线无关 |
| **承载语义** | 长会话、跨多次往返、需要进程内锚点的小包 | 幂等 CRUD、可独立寻址的对象、多 KB-MB 载荷上传/下载 |

**判别启发（顺序问）**：依赖进程内状态吗 → WS；需要在 chat 未连接时执行吗 → REST；是生产者/进度/状态推送吗 → 通知侧永远经 WS outbox + 事件帧下发（与命令端走哪条无关）。

**REST 镜像特例**：某条 WS 方法的读被一个不持有 WS 连接的 UI 表面（典型为 Hub）需要时，可保留 REST 镜像。镜像必须包装同一个服务函数、两端契约等价，任何漂移都要双端同步。

### 1.2 伙伴生命周期方法（方法级契约）

普通 chat / tool 类方法清单见 backend 代码（[services/gateway/handlers.py](backend/services/gateway/handlers.py) 的注册表）。以下是**伙伴生命周期**专用方法，客户端必须实现消费状态机（详见 [DESIGN.md §5–§6](DESIGN.md)）。**逐参数签名见 backend 代码，本文只锁定契约意图与「改这里要同步哪里」：**

| 方法 | 用途 | 改动需同步的模块 |
|------|------|------------------|
| onboarding.get_state / onboarding.submit 与 GET /api/companion/onboarding/state | 查询/增量提交 onboarding 答案（断点恢复，支持 WS RPC 与 REST） | Backend 状态机 + Client 消费状态机 + DESIGN §5 流程 |
| avatar.regenerate | 重生头像（不使模型失效） | Backend + Client 头像展示 |
| tts.match_voice / tts.design_voice / tts.list_voices | 音色描述匹配 / 专属音色生成 / 目录枚举 | Backend TTS + Client 音色页 + 工具窗口 REST 镜像 |
| companion.set_timezone | Client 每次连接上报本地 IANA 时区——系统提示词日期与陪伴对话时间感知、夜间批处理与互动统计按用户本地日聚合的唯一时区来源；缺行时回落服务端 UTC，夜间流水线整段跳过 | Backend 持久化 + Client boot 上报 + DESIGN §6.2 |
| tools.sync | Client boot 与 Runner 重启时把 Runner 工具 schema 推送到后端网关，纳入该用户的本机工具池。缺该调用时本机工具对模型不可见。对应 WS RPC 注册于 [backend/services/gateway/handlers.py](../backend/services/gateway/handlers.py) | Backend handlers + Client boot 上报 |
| companion.check_affect / companion.interact / companion.should_act / companion.record_interaction_stats / companion.get_user_profile | 情境化情绪与心境 / 戳·摸头·眩晕反应 / 自主空间决策 / 互动统计 / 画像召回。成功时心境经 `companion.affect` 下发；RPC 的 `reason` 仅为跳过/失败诊断，不得当作心境展示 | Backend 推理 + Client 触发与消费 + DESIGN §6.3/§6.4 |
| POST /api/companion/portrait/confirm | 确认半身形象（幂等），解开正面全身立绘生成子阶段 | Backend 状态 + Client 流程 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front-2d | 按默认赛璐珞画风（自然站姿）与微调反馈生成/重绘 2D 正面全身图 | Backend 生成 + Client 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front-3d | 以半身头像种子（形象身份基准，同 2D 正面生成）为参考生成/重绘 A-pose、3D 画风的 3D 正面种子（3D 升级向导调用；形象锁定后仍可用——姿态/画风派生而非身份变更；不覆盖 2D 正面种子，重绘会使已派生背面种子失效） | Backend 生成 + Client 3D 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/back | 按 3D 正面种子（缺省回退 2D 正面种子）与微调反馈生成/重绘背面全身图（3D 升级向导调用；形象锁定后仍可用——视角派生而非身份变更；画风与 3D 正面种子成对，由系统按类人 CG / 非人写实自动推导） | Backend 生成 + Client 背面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/confirm-front | 确认 2D 正面全身图并解开音色/用户子阶段（引导期不生成 3D 种子图——正面与背面均为 3D 建模派生输入，准备见 [docs/PIPELINE.md §1](docs/PIPELINE.md)） | Backend 生成 + Client 流程 |
| GET/POST /api/companion/model | 查询 / 触发 3D 模型异步生成；输入、产物与动画映射契约见 [docs/PIPELINE.md](docs/PIPELINE.md) | Backend 生成管线 + Client 加载 + DESIGN §5.5 |
| GET/POST /api/companion/2d | 查询 / 触发 2D 形象生成流水线（see-through 双 provider 拆分，产物恒为分层 PSD）；产物契约见 [docs/PIPELINE.md §6](docs/PIPELINE.md) | Backend 生成管线 + Client puppet 渲染链 |
| GET/PUT /api/companion/persona | 人设读取与更新；响应含当前心境说明 | Backend persona + Client 人设水合 |
| POST /api/companion/render-mode | 切换并持久化伙伴渲染模式（`2d` / `3d`） | Backend 持久化 + Client 实时切换 |
| companion.model.retryDownload | 仅重试下载已付费的 3D 生成结果，不重新提交生成 | Backend 生成管线 + Client 失败态入口 |
| POST /api/companion/avatar（含 /from-image、/upload）、/avatar/{id}/select 与 GET /avatar/history | 半身头像生成（含上传参考图重绘、直接上传头像）/ 历史形象切换激活 / 历史查询 | Backend 生成与上传 + Client 头像确认与历史画廊 + DESIGN §5.4 |
| GET/POST /api/companion/outfits 与 POST /{id}/regenerate、/{id}/confirm、PUT /{id}/activate、DELETE /{id} | 2D 换装衣柜：外观列表（首次访问懒合成初始形象）/ 草稿生成（着装描述 + 可选服装参考图，身份恒为正面种子主参考）/ 微调重绘 / 确认转正并触发 2D 切分（failed 可重试切分）/ 即时穿着 / 删除（穿着中与切分中拒绝）。生成走独立小时级频控，不设数量上限 | Backend 生成管线 + Client 衣柜 + DESIGN §1.1 / §8 |
| GET /api/companion/room 与 POST /generate、POST /activate、PATCH /policy、GET /{id} | 生活空间房间背景：水合房间状态（active / history[≤5] / policy / pending）/ 用户主动生成（202 异步，不占角色配额）/ 激活回滚历史房间（着装指纹不一致回 409）/ 政策切换（locked / llm_may_replace）/ 房间详情 | Backend companion_room / room_backdrop_service + Client room-backdrop / 生活空间设置 |
| GET/POST /api/companion/moments 与 PATCH/DELETE /{id} | 生活空间时刻：游标分页查询（cursor/limit/kind）/ 用户与系统写入时刻 / 软隐藏或修改时刻 | Backend companion_journal / journal_service + Client moments-page |
| GET /api/companion/diary 与 GET /{date}、POST、PATCH /{id} | 生活空间日记：区间拉取日记 / 指定自然日日记查询 / 用户手工补写或编辑日记（支持段落追加保护） | Backend companion_journal / journal_service + Client diary-page |
| `command.dispatch` | Slash 命令分发：客户端在输入框敲 `/xxx` 时拦截，改走本 RPC 而非 `prompt.submit`。命令注册表权威源在 [backend/services/chat/slash_commands.py](backend/services/chat/slash_commands.py)；返回 `{command, result:{status, message, payload?, hydrate?}}`，同步广播 `command.result` 事件给同 session 订阅者（多窗口同步渲染）。详见 §6 |  |
| `command.list` | 列出可用 Slash 命令元数据（`{name, aliases, description, requires_confirmation}`，**不含 handler**），供客户端 `/帮助` 与调试面板消费 |  |
| `session.clear_messages` | 清空当前会话消息（保留会话行 + 写一条 `subtype='status_cleared'` 的 system marker）；强制要求 `confirmed=true`，否则 `-32001`。与 `/清理` Slash 命令共用底层实现 |  |
| `session.set_settings` | 写会话级参数覆盖（载荷 `{session_id, settings:{temperature?, context_compression_threshold?, reasoning_effort?}}`）。仅覆盖本会话与 `info.settings` 内存视图；持久化层以会话行为单位；下次 `session.resume` 会随 `info.settings` 一起回水合。客户端通常以 350ms 防抖批量提交，避免高频 patch 抖动 | Backend handlers / client chat-store + ChatParamsPanel / session-list-store（hydrate 路径）/ PROTOCOL.md |
| `session.compress_context`（别名 `session.compress`） | 强制压缩当前会话上下文（与自动阈值触发共用 `_do_compress_history`）；返回 `{compressed, messages?, reason?, replaced_count?, session_id?, summary?, usage?:{context_window?, total_tokens?}}`。`compressed=true` 时 `messages` 是压缩后的完整列表，客户端用 `hydrateChatMessages` 替换本地消息流；`usage` 用于刷新顶栏上下文胶囊。`/压缩` Slash 命令也走本路径（`command.result` + `hydrate=true`） | Backend handlers（共用 `_do_compress_history`）/ client ChatParamsPanel / PROTOCOL.md |
| `session.undo_to_message` | 就地截断：硬删除 `Message.id >= source_message_id` 的全部行（含锚点本身），同时把锚点行载荷以 `anchor` 字段返回（`text / content_type / media_json`），客户端把它落回输入框作为草稿；要求 `confirmed=true`，否则 `-32001`；in-flight 时拒绝；仅 `kind='standard'` 且锚点 `role='user'` 允许。返回 `{session_id, deleted_count, anchor, messages}`，并广播 `message.deleted` 事件做多窗口同步。镜像 REST：`POST /api/sessions/{id}/undo-to-message` 接受 `DesktopSessionUndoRequest{source_message_id, confirmed}`——REST 与 WS 共用 `do_session_undo` 共享实现（per-conversation 锁、in-flight 守卫、`message.deleted` 广播），错误码映射：业务校验 → 400；`UndoNotAllowedError` → 403；`SourceNotFoundError` → 404 | Backend handlers / client chat-dock-message-bubble / events.ts / session-list-store / connection.py（MANAGER 注册 runtime_sessions）/ PROTOCOL.md |

**关键约束**（跨模块语义，非实现细节）：
- **断点恢复**：角色子阶段答完即标记角色已定稿；onboarding 整体只在全身形象确认且音色 + 用户信息齐后才算完成；未确认形象时按半身头像 → 全身立绘逐步恢复，确认后按音色先于用户信息路由。全身立绘子阶段的已生成正面种子图随形象行持久化，断点恢复直接重放到正面预览；正面种子草稿确认前停留 temp-media，确认时才转存正式存储，草稿过期按未生成处理由客户端重新生成。
- **形象锁定**：形象确认即锁定，物种/性别/基础外貌不可再改，3D 模型/头像重新生成路径与历史头像切换激活一并关闭（切换激活等于换掉已确认的视觉身份）。
- **关系不外溢到分析与形象**：引导期录入的用户与伙伴关系（知己好友、赛博管家等）只渲染进对话系统提示词供交互参考；不进入性格标签分析与头像/立绘提示词生成——关系是用户与伙伴之间的，不是伙伴自身属性。
- **下载失败可恢复（已付费结果绝不丢）**：下载失败态随 `model.failed` 事件下发可重试标记与模型标识；客户端必须据此提供"重试下载"入口，而非引导重新生成。持久化与恢复语义见 [docs/PIPELINE.md §3](docs/PIPELINE.md)。
- **心境说明透传**：人设水合响应与情境情绪事件均可携带当前心境说明，供生活空间身份轨展示。静止档不发起、不消费主动情绪与心境推送。RPC 跳过/失败诊断不得当作心境。
- **生活空间房间图联动与保护**：房间背景将角色绘制进场景中，身份基准由头像种子图锚定、当前穿着由着装描述锚定。换装成功后（`worn=true`）自动比较着装指纹，不一致时下发 `companion.room.invalidated` 并自动触发重建，防止画面穿帮。房间政策为 `locked` 时，拒绝角色自主换房，但放行换装联动与用户显式请求；历史房间保留最近 5 张供回滚，回滚时若服装指纹与当前穿着冲突则返回 409。
- **时刻与日记分层不变量**：底层 `memories` 向量表仅用于混合语义检索与系统提示词注入，不对客户端暴露为可读列表；生活空间消费独立的 `moments`（时刻）与 `diary_entries`（第一人称日记）。夜间批处理静默提炼日记，若当天已被用户编辑过则采取尾部段落追加而非覆写；工作预设会话中严格禁止记录生活时刻。
- **内置专属工具门控**：
  - `room_backdrop_update`（args: intent, notes?）：角色自主换房。静止档禁止调用；locked 政策拒绝；每日角色自主换房成功 ≤ 1 次；常规档限 decorate/mood，rebuild 仅自主档或用户显式操作放行。
  - `moment_create`（args: title, body, emotion?, kind?）：角色主动记录时刻。静止档禁止调用；每日角色主动配额 ≤ 3 次；工作预设会话中禁止调用。
  - `diary_write`（args: body, mood?, date?）：角色主动写日记。静止档禁止调用；工作预设会话中禁止调用。

### 1.3 事件类型

| 事件 type | 触发时机 | 消费者 |
|-----------|----------|--------|
| companion.affect | 非言语的情境化情绪与心境反应（载荷 `{emotion?, mood?}`；无非中性情绪时只更新心境） | Client：非中性情绪切 EMOTIONAL，有心境说明则更新身份轨（静止档不透传——主动情绪推理在源头已停） |
| avatar.regenerated | 头像重生最终结果 | Client 替换头像或展示失败 |
| model.ready / model.gen.progress / model.failed | 3D 模型就绪 / 进度 / 失败；载荷契约与产物映射见 [docs/PIPELINE.md](docs/PIPELINE.md) | Client 加载与状态展示 |
| companion.2d.ready / .failed | 2D 拆分就绪 / 失败；载荷包含 manifest_url 与图层签名 URL 字典。manifest 恒为分层 PSD 描述符（`kind=psd`，产物契约见 docs/PIPELINE.md §6.1） | Client 水合 puppet 渲染路径 |
| companion.render_mode.changed | 用户在设置中或多端同步切换渲染模式（`2d` / `3d`） | Client 切换展示画布 |
| companion.assets.updated | 伙伴实时创建了新表情（注册自创情绪并后台生成头像图） | Client 重拉 /expressions（自创情绪注册表：白名单、表情胶囊） |
| companion.outfit.updated / .failed | 换装外观状态变化（切分就绪 / 穿着翻转 / 删除，载荷含 outfit_id 与 worn 标记）/ 切分失败（含原因） | Client 重拉衣柜列表；worn 变化时重水合 2D 渲染层（与 2d.ready 双触发幂等，事件只当刷新触发、列表端点是真相源） |
| companion.room.progress | 房间图生图三段进度（brief / imagine / store），载荷 `{backdrop_id, stage}` | Client 在生活空间显示生成进度条 |
| companion.room.ready | 房间图就绪，载荷 `{backdrop_id, url, brief, origin, outfit_fingerprint}` | Client 把生活空间背景切到新图 |
| companion.room.failed | 房间图生成失败（无供应商名），载荷 `{backdrop_id, utterance}` | Client 用取色玻璃底展示失败态，并朗读 utterance |
| companion.room.invalidated | 当前激活背景因换装 / 政策变更失效，载荷 `{reason, active_backdrop_id?}` | Client 切回玻璃底或继续等 companion.room.ready |
| companion.moment.created | 生活空间新增一条时刻 | Client 增量 push 到时间线 |
| companion.diary.upserted | 一篇日记被写入或更新 | Client 在打开日记页时才消费（不发主动气泡） |
| video_gen.completed / .failed | 视频生成结果；completed 载荷含 task_id / url / session_id / media，兼作后台视频的异步送达通道（见下方「对话内生成媒体」） | Client 对话窗媒体卡与提示跳转 |
| channel.status | IM 通道绑定状态变化（connected / login_required / error 等，载荷 {channel, status, account_name?, error?}） | Client 通知/toast；Hub 状态以 REST 读为准（Hub 窗口无 WS） |
| channel.peer_request | 陌生对端首次来信触发配对审批（载荷 {channel, peer_id, peer_name, preview}） | Client 通知引导主人到通道设置审批 |
| `command.result` | Slash 命令执行结果（载荷 `{command, result:{status, message, payload?, hydrate?}}`）；必带 `session_id`（从 command.dispatch 调用中隐式继承）。客户端用 `hydrate=true` 替换本地消息列表（payload.messages），用 `hydrate=false` 仅插一条 status pill。**幂等**：同一调用会同时下发 RPC result + 此事件，前端接住任意一路即可触发渲染 | Client 聊天窗：hydrate 替换消息列表、push status pill（`status_cleared` / `compress_summary` 等） |
| `compress.completed` | 自动上下文压缩（orchestrator 命中阈值）完成后下发；载荷 `{subtype:'compress_summary', text, message_id}`，`text` 与持久化 `Message.content` 同源；必带 `session_id`。手动 `/压缩` 仍走 `command.result`+`hydrate=true` 替换整列，二者语义互补（自动 = 单行插入不打断流；手动 = 替换列表强一致） | Client 聊天窗：`pushStatusPill('compress_summary', text)` 单行插入，渲染端走 `compress_summary` 分界线式可折叠卡片分支（详见 `chat-dock-message-bubble.tsx` 的 `COMPRESS_CARD_SUBTYPES`） |
| `message.persisted` | 用户消息落库后、流式开始前下发；载荷 `{role:'user', message_ids}`，`message_ids` 为本轮提交的全部用户行 id（含 batch 前导，按插入序）；必带 `session_id`。终端助手行 id 挂在 `message.complete.message_id`（中间工具调用助手行不回写）。活路径气泡据此绑定，无需等 hydrate | Client 聊天窗：把 id 绑到当前会话末尾尚未绑定的对应用户/助手气泡 |
| `message.reasoning.delta` | 助手推理过程流式增量；载荷 `{text}`；必带 `session_id`。与 `message.delta` 并行，不进入正文、不进下一轮 LLM 输入 | Client 工作台：累加到当前助手气泡的推理区；生活空间不展示 |
| `message.deleted` | `session.undo_to_message` 的多窗口广播：载荷 `{session_id, deleted_count, messages}`，`messages` 是截断后的完整消息列表；发起窗口已通过 RPC 路径 hydrate，其它窗口经此事件用 `payload.messages` 替换本地列表；必带 `session_id`，由 `events.ts` 在会话闸门内消费 | Client 聊天窗：`hydrateChatMessages(messages)` 替换本地消息列表 |

**事件投递范围（session_id 语义）**：session_id 就是 conversation_id 的字符串形式（见 §6）。聊天会话事件（message.* / tool.start / tool.complete / error）必带 session_id、只属于该会话，渲染端必须按 session_id 过滤；outbox 事件（上表）不带 session_id，投递到该用户的 desktop、与打开哪个会话无关，照常处理（video_gen.completed 的 session_id 在载荷内部，渲染端自行比对决定落卡还是提示跳转）。

**`tool.call` 是用户级设备指令，不是会话事件**（改此处需同步：backend services/chat/tool_dispatch.py 与 services/gateway/emitter.py、client companion/events.ts、本文档）：它不渲染进任何气泡、不参与会话状态机，只按 `call_id`（§6 定义的唯一 Future Key）与 `tool.result` 配对，因此**不带信封级 session_id**、由后端直接推给该用户的 desktop 派发器。这条豁免是必需的而非优化——IM 遥控回合、cron 自主回合与子 agent 回合的会话用户都不可能正在查看，带上信封 session_id 就会被渲染端的会话闸门丢弃，后端白等到 IPC 超时。载荷含 `{name, args, call_id, session_id}`，其中 `session_id` 是**信息字段而非路由闸门**：客户端拿它与当前查看的会话比对，判断该回合的 `tool.start` / `message.complete` 是否会被闸门拦下，从而决定精灵工作态是否需要由该分支自持。用比对而非枚举回合类型，是因为枚举漏一种就表现为「机器在动、精灵发呆」且无任何报错。

**设备指令的重复投递**：`tool.call` 与其它事件一样进重放缓冲，WS 断连重连会重发。本机副作用不可撤销（删文件、跑命令），因此**客户端必须按 `call_id` 去重**，重复帧直接丢弃——后端的 `resolve_future` 只会丢弃迟到的结果，拦不住已经发生的副作用。

**对话内生成媒体**（改此处需同步：backend 工具与聊天持久化、backend/README.md、client 渲染层与 client/renderer/companion/README.md、DESIGN §6）：
- 聊天回合经图像/视频生成工具产出的媒体，随对话完成事件以 media 数组（元素为 image / video 类型 + 本服务媒体 URL）下发，并持久化在对应助手消息行；后台完成的视频另以 status_media 送达行落库，实时事件与历史水合看到同一形状。
- 渲染端在**对话窗**以媒体卡内联预览、点击放大播放；精灵气泡只承载轻量文本，收到媒体时仅提示「点击查看」并支持点击打开对话窗（必要时切到目标会话）——富媒体统一在对话窗展示，不进气泡。
- 精灵画/拍自己（生成工具 subject='self'）：身份参考由后端自动注入**半身头像**（种子图编排与参照基准见 [docs/PIPELINE.md §1](docs/PIPELINE.md)）。

**用户侧聊天附件**（改此处需同步：backend 网关校验与附件生命周期模块、backend/README.md、client 附件 UX 与 client/renderer/companion/README.md、DESIGN §6.1）：
- 图片附件以 `data:image/*` data URL 随 `prompt.submit` 的 attachments 直发（不落盘）；视频附件因 base64 远超 WS 单帧上限，客户端必须先经 `POST /api/media/videos`（multipart：file + session_id，容器白名单 mp4/mov）换取附件 URL，再以 `{"type": "video", "file_url": ...}` 提交——附件 URL 只认本会话（跨会话引用直接拒绝），绝对形态仅认 `public_base_url` 前缀（第三方绝对 URL 会被拒绝，防止借供应商发任意请求）。
- 服务端点 `GET /api/media/videos/{session_id}/{file_id}` 公开（file_id 为不可猜测 token；公网模式下供应商需直接拉取）。
- 供应商消费分双模式，由后端 `public_base_url` 配置决定：留空时构造请求前把最近 2 个内联为 data URL（单文件 50MB 上限）；配置可公网访问地址后以绝对 URL 直发供应商自拉（单文件上限=会话配额 512MB，且该地址必须真能被供应商服务器访问）。
- 附件文件按 512MB/会话滚动配额：超限从最旧剔除；压缩/夜间摘要检查点之前与历史截断删除之前的视频确定性清理。两类清理都把所属消息行的 `input_video` part 改写为 `[视频已清理]`，渲染与 LLM 上下文不残留死链。
- 持久化 part 形状为 `input_video`（扁平 `video_url`，与 `input_image` 同构）；每请求内联超限或文件缺失时降级 `[video]` 文本占位（与旧图 `[screenshot]` 同构）；视频回合的链筛选由供应商能力位（`supports_video`）决定——Responses 网关不支持 `input_video` 的供应商（mimo）不参与视频回退链。

### 1.4 Affect 与空间契约

**语义/渲染解耦**——Backend 只产出情绪 + 可选场所语义，绝不指定渲染方式或像素坐标。

**emotion 枚举**（22 项，权威源 backend/services/chat/affect.py）：happy / sad / surprised / excited / confused / concerned / shy / proud / grateful / playful / bored / lonely / sleepy / curious / embarrassed / apologetic / neutral / pout / angry / smug / scared / relieved。

**locale 枚举**（3 项，权威源在后端白名单）：home / perch / roam。

**客户端内部场所与表面状态**：
- `target`：仪式性行走（[DESIGN.md §3.6](DESIGN.md)）目标旁，由本地工具调用触发，永不来自云端。
- `workbench`：打开工作台窗口时，桌面精灵舞台收起，工作台内部复合挂载伴工精灵插槽，与工作台绑定为单一实体整体移动。
- **生活空间打开状态**：生活空间打开期间，桌面透明精灵舞台自动收起隐藏并暂停桌面自主走位（不触发空间移动调度，保持渲染管线热备）；关闭生活空间后恢复。

**spatial target**（可选，仅 perch 时有意义）：窗口/进程名关键字。客户端经窗口枚举解析为窗口几何后计算 perch 点。注意：此处的 target 是空间 cue 的**窗口关键字**，与 Client 内部的场所 target（仪式行走目的地）是**两个不同概念**——后者由工具调用本地触发、不在本协议枚举内（见 [DESIGN.md §3.2](DESIGN.md)）。

**inline 空间 cue 规则**：LLM 在回复前自填空间 cue，由解析器解析后附加到 message.complete 的场所/目标字段。后端解析后下发，客户端决定是否落位（仅自主档兑现 + 对话开启抑制）。

**`companion.should_act` 动作枚举**（自主空间决策 RPC，权威源在后端 `ALLOWED_ACTIONS`，与上述 locale 枚举是两套词表）：roam / perch / approach / stay。`approach`（走过去搭话，仅自主档 + 智能驱动开）附带 `params.text`（10–30 字开场白，后端截断至 80 字并设 30 分钟冷却，冷却内或无有效文本整体降级 stay）与可选 `params.emotion`；开场白由后端经 `companion.message` 通道投递（含 status_proactive 持久化与主动外联状态机），RPC 响应只承载走位动作——客户端不消费 params，走位规则见 [client/renderer/companion/README.md §10](../client/renderer/companion/README.md)。

**动作 tag（[action:NAME]）**：LLM 可另附最多 3 个结构化动作名（snake_case，各占一行、按播放顺序排列），且只能来自后端注入的可请求动作清单——白名单外与超额的 tag 在后端流式解析时丢弃；解析后以 `affect.actions` 数组随对话完成事件下发（2D 依序播放，3D 取首个）。清单来源与客户端兑现规则见 [docs/PIPELINE.md §5–§6](docs/PIPELINE.md)。

**action 白名单（2D 路径）**（权威源 [backend/services/companion/mesh2d/actions.py](backend/services/companion/mesh2d/actions.py) 的 `DEFAULT_ACTIONS`，LLM 注入清单为 `DEFAULT_ACTIONS − NON_LLM_ACTIONS`；下表标 ★ 的键为客户端本地触发、LLM 不可请求）：

| key | 描述 | 默认绑定 emotion |
|---|---|---|
| `wave_right` / `wave_left` | 单手举起挥手 | happy / excited / 告别 |
| `present_right` / `present_left` | 单手抬起展示 / 指向 / 拿东西 | helpful（"帮我拿杯子"） |
| `point_right` / `point_left` | 抬臂指向屏幕目标（仪式行走抵达后按方位播放） | helpful / curious（"看这个"） |
| `hands_on_hip` | 双手叉腰 | smug / pout / proud |
| `hair_touch` | 抬手整理头发（带轻挠关键帧） | shy / thinking（害羞拨发） |
| `spread_arms` | 双臂展开做展示 | happy / excited / proud |
| `look_away_left` / `look_away_right` | 头脸避开视线 | shy / embarrassed / sad |
| `turn_body_left` / `turn_body_right` | 整个上半身转向 | 切换朝向 / 仪式行走 |
| `lean_forward` | 上半身微微前倾 | curious / thinking |
| `shy` | 低头侧脸 + 前发微盖 | shy / embarrassed |
| `petting` | 享受抚摸：微微歪头闭眼 + 舒服蹭蹭 | happy / grateful（摸头手势触发） |
| `dizzy` | 眩晕：脑袋发懵轻晃 + 圈圈眼 | confused / tired（高频连戳触发） |
| ★ `edge_cling` | 贴边趴姿（双手扒边、上半身探入） | curious / playful（屏幕贴边吸附触发） |
| `idle_glance` | 短瞥一眼回中 | idle 变体 |
| ★ `click` | 伸手触碰 / 点击姿态 | neutral（仪式行走飞抵目标触发） |
| ★ `long_press` | 长按凝视姿态 | neutral（用户长按精灵触发） |
| ★ `drag_end` | 拖拽松手就地的站稳微沉 | neutral（拖拽释放定居触发） |

> 注：3D 路径走 GLB clip map；2D 路径走 [PuppetStage](client/renderer/companion/puppet/PuppetStage.tsx) 定时包络（白名单键同源，通道由包络内部定义）。同一 action key 在各路径上语义一致但兑现方式不同。
>
> 走路 / 跳跃（locomotion）：2D 路径有程序化复合步态（基于逐帧位移积分相位驱动躯干起伏、侧倾摆动与朝向微倾，叠加发束与全身次级物理），飞行维持滑行语义。如需移动角色，用空间目标或仪式行走而非动作。

**表情契约**：自创情绪经工具注册后并入白名单，并按后台生成语义预热头像图；渲染分工见 [DESIGN.md §1.1](DESIGN.md)。

**连续气泡分隔**：LLM 需要在一回合内连发多条短回复时，用单独一行 `---` 分隔；Backend 流式解析为 `message.break` 事件（带 session_id）并**自行控制 0.5–1.5s 的分段节流**——停顿在后端流内完成，Client 按帧到达顺序收尾当前气泡再渲染下一气泡，双端无需各自计时。

**推理过程**：供应商若产出独立推理过程，后端以 `message.reasoning.delta` 流式下发，并在 `message.complete` 可选附带本轮推理全文（多段工具循环已拼接）。会话水合消息列表用 `reasoning` 带回已落库的推理过程。该内容只给工作台展示，不进入下一轮 LLM 输入。多气泡回合里增量落在到达时的当前气泡；完成帧与正文一样，不把整轮推理覆盖到最后一格。

**2D 命中区域与手势交互协议**：
- `companion.interact` RPC payload 的 `kind` 字段支持 `poke`（戳击）、`pet`（摸头抚摸）、`dizzy`（激怒/眩晕）。
- `companion.interact` RPC payload 的 `region` 字段允许传下列白名单之一（不传 = 整精灵矩形命中）：

| region | 含义 |
|---|---|
| `head` | 头部（含 face） |
| `face` | 脸部（head 子区域） |
| `arm_L` / `arm_R` | 左 / 右手臂 |
| `body` | 躯干 |
| `back_hair` / `front_hair` | 后发 / 前发 |
| `skirt` | 下装 / 裙子 |

命中区域与手势影响：（1）前端手势/物理反馈——摸头享受、怒气、眩晕与发区抖动的触发阈值与粒子反馈见 [DESIGN.md §6.3](DESIGN.md)；（2）LLM 反应上下文——`kind` 与 `region` 字段透传到 LLM，让回应可针对"摸头" vs "戳脸" vs "拍手" vs "眩晕"做不同文案。两条渲染路径都做可见像素级命中——3D 走 silhouette hit（离屏 alpha 回读）；2D 走 [PuppetStage](client/renderer/companion/puppet/PuppetStage.tsx)（当前帧部件网格精确点测，区域 = 最上层命中部件的映射，CPU 轻量，经命中区域总线 `$mesh2dHitmap` 下发）。

**扩展协议**：每次扩展 emotion / locale 须同步更新 **后端白名单 + 客户端表情/场所映射 + 本文档**三处；未覆盖项一律按 neutral / home 处理（2D puppet 链的情绪→面部参数映射随词表同步）。情绪枚举 22 项（含 neutral）。action 扩展须同步更新 **后端 [actions.py](backend/services/companion/mesh2d/actions.py)（DEFAULT_ACTIONS / NON_LLM_ACTIONS）+ 客户端 [PuppetStage 包络表](client/renderer/companion/puppet/PuppetStage.tsx) + 本文档**三处。

### 1.5 资产 URL 签名与传输缓存

| 资产 | TTL |
|------|-----|
| portrait 头像 | 5 分钟 |
| 3D 模型 GLB | 5 分钟 |
| 2D 部件 PNG / manifest.json | 5 分钟 |
| 2D 分层 PSD（分层切分产物，puppet 链消费） | 5 分钟 |
| 换装外观全身立绘（草稿期为 temp-media 免鉴权路径，确认后转正式签名） | 5 分钟 |
| 生活空间房间背景图（room_backdrop，含角色的 16:9 生成背景图） | 5 分钟 |

**契约要点**：资产端点支持双通道鉴权——已登录 Client 携带有效 Bearer JWT 时可直接访问归属资产；未携带令牌时按 URL HMAC 签名校验（每次签名 5 分钟 TTL，换设备/过期需重新签名）。服务端模型/资产端点支持 HTTP Range 断点续传 + ETag + 不可变缓存头；Client 按内容哈希（SHA-256）在本地磁盘缓存，命中即跳过网络，未命中/中断走断点续传。

### 1.6 错误信封

REST 端点异常路径返回统一结构：error（短码）+ reason（分类，可空）+ status（HTTP 状态）。WS JSON-RPC 错误使用标准错误码（-32700 到 -32603）。**关键契约**：内部错误抛至前端前必须脱敏，严禁包含数据库账号、服务器本地路径等栈帧细节；统一错误分类决定恢复策略，见 [backend/README.md](../backend/README.md)；流式调用（chat TTS 流）一旦首 chunk 已发，任何供应商失败都不切换 fallback。

### 1.7 IM 通道桥接（/api/channels）

外部 IM（微信 iLink）经后端进程内的通道桥与同一伙伴对话：入站消息驱动**无头 chat 回合**（不依赖用户 WS——桌面离线也能回），回复经格式化（去 markdown、按 `weixin_reply_max_chars` 分片）从原渠道送出。产品语义与渠道路线见 [DESIGN.md](DESIGN.md)；实现与已知限制见 [backend/README.md](../backend/README.md)。

**会话契约**：所有渠道共用 `im` 这一种 conversation kind；**每用户每渠道一条专属 im 会话**，由 `channel_bindings.conversation_id` 唯一外键锚定（渠道间不混流）。im 会话对桌面端**只读**：出现在会话列表与历史中，但 `prompt.submit` 拒写（后端守卫 + 客户端输入禁用）；人设/长期记忆/情感与桌面回合共享（按 user 加载，无需任何同步动作）。

**遥控契约（IM 驱动本机工具）**：IM 回合与桌面回合共用同一编排器与同一工具注册表，因此桌面在线时伙伴在 IM 上**能调用本机 runner 工具**——手机是遥控器，能力叠加在陪伴之上而非替代它。三条边界：

- **本机工具依赖桌面 WS，回合本身不依赖**：回合无头执行（桌面离线也能回），但其中的 runner 工具经上述 `tool.call` 设备指令通道兑现，桌面不在线即整体不可用。
- **在线与否作为环境事实进系统提示词**：判定同时要求「WS 可用」与「注册表已有该用户的 runner 工具」——只看前者会在 tools.sync 未完成或 Runner 崩溃时让伙伴声称工具可用却无 schema 可调。离线时伙伴须如实说明电脑未连接，不得含糊搪塞或假装做过。
- **授权边界就是对端白名单**：已审批对端等同本人，无额外的逐次授权层。审批一个对端 = 允许它操作本机，通道设置页的审批文案须体现这一分量。

**回合节奏**：单飞行锁保证每绑定同时只有一轮；进行中回合到达的消息合并进下一轮前导批。带工具调用的中间迭代产出的话术在每个工具批次开始时先行送出（best-effort，投递失败不得打断工具派发），终局文本另行送出——两者内容天然不相交，无需去重。发起回合的对端可用停止指令（全词白名单、非包含匹配，判定先于入站频控——用户连发几条后正好把窗口打满时，刹车不能跟着一起丢）中止该回合；中止须同时清空排队并 resolve 其 future。**中止只终止后端等待，不撤销已下发到本机的工具**——已经在跑的命令会执行完，其结果落到无人认领的 call_id。

**REST**（Bearer JWT，前缀 `/api/channels`）：

| 端点 | 用途 |
|------|------|
| `GET /api/channels` | 渠道注册表能力位 + 当前用户绑定状态（凭据字段永不出现） |
| `PUT /api/channels/{channel}` | 创建/更新绑定（config 落 config_json）并重启适配器 |
| `DELETE /api/channels/{channel}` | 停用并删除绑定（peers 级联；im 会话行沉淀为历史） |
| `GET /api/channels/{channel}/peers` | 对端白名单/待审批列表 |
| `POST /api/channels/{channel}/peers/{peer_id}` | 对端审批（approve / block / delete） |
| `POST /api/channels/weixin/login` + `GET /api/channels/weixin/login` | 微信 QR 登录启动/轮询：state ∈ wait\|scaned\|confirmed\|expired\|error\|login_required，wait 帧附 qr_image（二维码图片内容，直渲染或链接兜底）；适配器未运行时按绑定态回放 login_required |
| `POST /api/channels/{channel}/logout` | 渠道登出：清凭据转 login_required，绑定与 im 会话保留 |

**访问控制**：默认拒绝——未知对端首条消息收到一次性固定配对回复并落 pending 行（`channel.peer_request` 事件），仅主人审批放行；blocked 静默丢弃；每 peer 进程内限速（`channels_inbound_rate_per_minute`）。

**渠道能力差异**（产品语义级）：微信 iLink 为 reply-only（伙伴**不能**主动发起微信消息，回复须回显入站 context_token，过期后等用户下一条消息刷新）。

**改此处需同步**：backend services/channels 与 modules/channels、backend/README.md、client 通道设置页与只读守卫（client/renderer/companion/README.md）、DESIGN.md、ARCHITECTURE.md §5.4。

### 1.8 系统预设对话（5 套并列的特殊会话）

每位用户 onboarding 完成时刻一次性创建 5 条 `kind='special'` 的系统预设对话（companion / developer / product_manager / copywriter / language_teacher），由 `Conversation.system_preset_id` 标识；产品意图见 [DESIGN.md §9](DESIGN.md)，装配规则见 [ARCHITECTURE.md §6.1](ARCHITECTURE.md)。**会话数据契约**（DB 列 + 列表展示语义，非实现细节）：

| 字段 | 契约 |
|------|------|
| `Conversation.system_preset_id` | 5 个预设 id 或 NULL；NULL = 用户自建普通对话。**非 NULL 时** 5 套会话与预设一一对应，每用户每预设至多一条（部分唯一索引 `uq_conversations_user_preset`） |
| `Conversation.kind` | 值集合 `{special, standard, im, cron}`；`special` 严格对应 `system_preset_id IS NOT NULL`，im 渠道为 `im`，cron 独占会话为 `cron`，用户自建普通对话为 `standard` |
| `Conversation.is_deletable` / `is_renamable` | 系统预设对话一律 False；用户对话默认 True |
| 系统预设对话的标题 | 由代码预设常量名（陪伴 / 工程师 / 产品经理 / 文案秘书 / 语言老师）固定，客户端应忽略 PATCH /title |
| 列表排序 | 工作台置顶 4 套专业系统预设对话（developer→product_manager→copywriter→language_teacher），排在所有用户对话与手动置顶之前；生活空间独占 companion 陪伴会话（见 DESIGN.md §9） |

**对话级 REST**（现有 `/api/sessions` 端点的隐式约束）：

- `PATCH /api/sessions/{id}`：当 `kind='special'` 或 `is_renamable=False` 时返回 403（系统预设对话不可改名与变更）。
- 用户改名走同一 PATCH 端点的 `title` 字段；标题一旦非默认值，自动标题生成不再覆盖（`auto_generate_title` 仅在 `title == 'New Conversation'` 时写入）。**改此处需同步**：client `companion/chat/session-drawer.tsx`、`companion/session-list-store.ts`、`shared/strings/index.ts`。
- `DELETE /api/sessions/{id}`：当 `kind='special'` 或 `is_deletable=False` 时返回 403（系统预设对话不可被删除）。
- `session.get_main`（WS RPC）：现存的方法指代「**用户的主对话**」，返回 `system_preset_id='companion'` 的特殊对话。
- 既有 `session.create` 等方法对系统预设对话同样适用（用户能在 5 套预设之上再 clone 一个变体；但 5 套预设本身不可被 PATCH/DELETE）。

**派生会话（forked）**：`session.fork`（WS RPC）与 `POST /api/sessions/{id}/fork`（REST 镜像）由用户在 `kind='standard'` 源会话的某条历史消息节点上派生新会话；复制行不再打 `Message.draft_anchor`（消息要么在历史要么在输入框，不允许两者并存），新会话的所有复制行按已发送历史对待；新会话 `parent_id` 指向源、`is_deletable`/`is_renamable` 默认 True、标题追加「 — 副本」。源 `kind ∈ {special, im, cron}` 时返回 INVALID_PARAMS / 403（系统预设、IM 桥接、cron scratchpad 不可派生）。**改此处需同步**：backend services/conversation/fork.py、services/chat/history.py、services/gateway/handlers.py、api/v1/sessions.py、modules/conversation/models.py（`Message.draft_anchor` 列保留仅作向后兼容）、alembic baseline；client session-list-store.ts、chat-store.ts、chat-dock-message-bubble.tsx、chat-message-fork-button.tsx。

**新建对话预设选择**：客户端在工作台用户点 「新建」 时弹出选择器，供用户在 4 种专业工位预设中选 1（无默认，确认按钮必选中才启用；生活空间独占「陪伴」会话不支持新建或切换）。经 WS RPC `system.list_presets` 获取预设元数据（`id` / `name` / `description` / `icon_key`，**不含 body**；body 永远不下发到客户端）；`session.create` 传选中的 `system_preset_id`（必须 ∈ `BUILTIN_PRESETS` 键集合；非法值抛 INVALID_PARAMS；省略/空串 = 未传，行为同旧版 `kind='standard'`，`system_preset_id=NULL`，chat 时按 `resolve_preset` 降级到 `companion`）。新建的会话始终是 `kind='standard'`，可改名/删除/派生，系统提示词由 `system_preset_id` 在 chat 时间锁定。`DesktopSessionInfo.system_preset_id` 与 `system_preset_icon_key` 暴露给客户端用于侧边栏徽标（NULL 时 `system_preset_icon_key` 已降级为 `companion.icon_key`）。**改此处需同步**：backend services/gateway/handlers.py（`system.list_presets` handler + `session.create` 校验）、modules/system/schemas.py（`PromptPresetSummary` / `PromptPresetListResponse`）、modules/conversation/schemas.py（`DesktopSessionInfo` 新字段）、api/v1/sessions.py（`_conversation_to_session_info` 填充）；client companion/chat/preset-picker-modal.tsx（新建）、companion/chat/session-drawer.tsx（picker 挂载 + `SessionRow` 徽标）、companion/session-list-store.ts（`createNewSession(systemPresetId?)` + `fetchSystemPresets` + 3 个 atom）、shared/types/spiritagent.ts（`SessionInfo` 字段 + `SystemPresetSummary` / `SystemPresetListResponse`）、shared/strings/index.ts（`chat.presetPicker.*`）；本文档。

### 1.9 Slash 命令（对话内元动作）

对话输入框以 `/` 开头的文本触发会话级元动作（清空、压缩等），不经 `prompt.submit` 而经独立的 WS RPC 派发。命令语义与 system prompt 模板（preset）、LLM 工具调用都正交——`/压缩` 不是「让 LLM 帮我压缩」，而是「我现在就要压缩」。这避免了把回合外副作用塞进 prompt 路径产生的 in-flight / 审计 / 跨窗口同步问题。

**首期命令**：

| 命令 | 别名 | 影响历史 | 需确认 | 备注 |
|---|---|---|---|---|
| `clear` | 清空 / reset | 是 | 是 | 清空消息保留会话行，写 `status_cleared` marker；system_preset 也允许执行 |
| `compress` | 压缩 / ctx | 是 | 否 | 复用 `session.compress_context` 强制压缩路径 |
| `remember` | 记住 / 记忆 / remind / memo / memory | 否 | 否 | 主动将指定内容写入长期记忆（`recall:manual`）并生成向量嵌入，参与后续 Hybrid Recall |

**拦截与歧义处理**（契约级）：
- `//xxx` 视为普通文本（注释 / 路径引用场景）
- `/X` 中 X 非 ASCII 字母或 CJK → 普通文本
- 未识别命令不退回 `prompt.submit`，toast 提示"未知命令"（避免 `/foo` 被 LLM 误当真发出去消耗 token）
- 需确认的命令前端必须弹 confirm，后端再次校验 `confirmed=true` 才会执行——客户端本地元数据仅用于 UI 优化，**不是安全边界**

**错误码**（与 JSON-RPC 标准错误码分离）：
- `-32001` 命令要求 confirm 但客户端未传 `confirmed=true`；`data.requires_confirmation=true`
- `-32002` 命令影响历史但当前回合仍在生成中
- `-32003` 命令 handler 内部异常兜底

未识别命令回 `-32602`，`data.suggestions` 给最相近的主名列表。

**事件 `command.result`**（必带 `session_id`）：payload 含 `{command, result:{status, message, payload?, hydrate?}}`。`hydrate=true` 时用 `payload.messages` 替换本地消息列表；否则 push 一条 status pill（与 `daily_summary` / `compress_summary` / `status_cleared` 同渲染集合）。同一调用同时下发 RPC 响应与事件——前端接住任一路即可，幂等处理。

**改一处需同步**：
- 命令注册表 / 元数据 → [backend/services/chat/slash_commands.py](backend/services/chat/slash_commands.py) + [client/renderer/shared/lib/slash-commands.ts](client/renderer/shared/lib/slash-commands.ts) 镜像
- 拦截逻辑 / 弹层 → [client/renderer/companion/chat-dock.tsx](client/renderer/companion/chat-dock.tsx) + [client/renderer/companion/chat/slash-command-popover.tsx](client/renderer/companion/chat/slash-command-popover.tsx)
- 错误码 → [backend/components/constants.py](backend/components/constants.py) + [backend/components/__init__.py](backend/components/__init__.py) + 客户端 `slashErrorToMessage`（chat-dock.tsx）
- 状态 pill 渲染 → `status_command_result` 加入 `chat-dock-message-bubble.tsx` 的 status 渲染分支
- 自动压缩事件 → [backend/services/chat/orchestrator.py](backend/services/chat/orchestrator.py)（orchestrator 命中阈值后 push）+ [backend/services/gateway/emitter.py](backend/services/gateway/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/companion/events.ts](client/renderer/companion/events.ts)（`compress.completed` switch）+ `chat-dock-message-bubble.tsx` 的 `COMPRESS_CARD_SUBTYPES` 折叠卡片分支 + 本文档 §1.3
- 活路径消息 id 回写 → [backend/services/chat/persistence.py](backend/services/chat/persistence.py) + [backend/services/chat/orchestrator.py](backend/services/chat/orchestrator.py)（`message.persisted`）+ [backend/services/gateway/emitter.py](backend/services/gateway/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/companion/events.ts](client/renderer/companion/events.ts) + `chat-store.ts` 绑定 + 本文档 §1.3
- 推理过程事件 → [backend/services/chat/streaming.py](backend/services/chat/streaming.py) + [backend/services/chat/persistence.py](backend/services/chat/persistence.py) + [backend/services/gateway/emitter.py](backend/services/gateway/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/companion/events.ts](client/renderer/companion/events.ts) + `chat-store.ts` + 工作台气泡 + 本文档 §1.3

**手动撤回不走 slash 命令**：消息级粒度的「撤回」由用户在历史用户气泡旁点击撤回图标触发，直接走 `session.undo_to_message` RPC（slash 命令无法承载消息级粒度 + 需要服务端精确路由到具体 source_message_id）。详见 §1.2 与 §1.3 的 `message.deleted` 事件。

---

## 2. Client ↔ Runner 契约

### 2.1 链路与鉴权

- Runner **主动**连客户端提供的 IPC 端点（Windows 命名管道 / macOS UDS，权限 0600）。
- 端点路径与 token 由客户端**单向下发**（启动参数 + 落盘文件）；Runner 重连间重读文件以在客户端重启后拾取新端点与新 token。
- 启动后发 runner_ready 握手通知；鉴权走 upgrade 头，校验失败客户端回 401、不完成握手；Runner 收到 401 后丢弃内存缓存端点与 token、等待重读文件。token 为每次启动新生成的 256-bit 随机值，**不是 Backend 凭据**。
- 安全模型：Windows 命名管道命名空间对本机进程可枚举、且无自定义 DACL 接口——token 是实际闸门；macOS 侧 0600 socket 为主闸门、token 为纵深防御。OS IPC 不经网络栈，无端口监听面。

### 2.2 RPC 方法清单

| 方法 | 方向 | 用途 | 改动需同步的模块 |
|------|------|------|------------------|
| runner_ready | Runner → Client | 启动握手，携带 version + capabilities + capabilities_health + reconnect_streak | Runner 探测 + Client 功能门控 + 重连降级展示 |
| tools_changed | Runner → Client | 工具 schema 变更通知，Client 重拉并同步到 Backend | Runner + Client + Backend 工具表 |
| get_tools | Client → Runner | 获取工具 schema（已过滤禁用项） | Runner 过滤 + Backend 过滤 + Client |
| spiritagent.info | Client → Runner | 完整运行快照 | Runner 上报 + Client 诊断 |
| execute_tool | Client → Runner | 执行工具调用 | Backend 路由 + Client 中转 + Runner 执行 |
| spiritagent.cancel | Client → Runner | params.req_id 可选；指定则取消该 RPC，缺省取消当前进行中工具；并对目标 req_id 设置中断标记 | Client 中断 + Runner 任务取消 + 请求级隔离 |
| spiritagent.config.update | Client → Runner | 推送完整配置（云端为真源，Client 是镜像持有者与唯一推送方，见 §2.4） | Client 设置 + Runner 内存配置 |
| request_llm | Runner → Client | 反向 RPC 借大脑 | §3 |

**工具集 id 权威枚举**（跨模块公共事实，本表为唯一 owner；各模块目录只做 id → 自有工具名的映射，不复述清单）：
`browser_automation`、`file_operations`、`terminal`、`code_execution`、`process_management`、`skills_system`、`memory`、`web_tools`、`image_generation`、`messaging`、`scheduled_tasks`、`agent_delegation`、`computer_use`、`media_analysis`。

禁用语义：UserSettings 点键 `toolsets.disabled` 持有被禁用的 id 集合。Runner 侧在 `get_tools` 源头过滤自有工具；Backend 侧在工具注册表读取时过滤 backend/memory 桶（各自的 id → 工具名映射见模块代码）。无工具集归属的工具（如 `search_tools`、`create_expression`、`video_generate`）不受开关影响。

### 2.3 runner_ready capabilities 与 health 状态

capabilities 与 capabilities_health 来源于 Runner 的运行时探测（探测设计见 [runner/README.md §2](../runner/README.md)）：前者是向后兼容的布尔映射，后者按子能力给出可用性与失败原因。致命探测异常置 probe_failed；客户端按能力缺失做局部降级或给出可操作提示。

`reconnect_streak` 是自上次成功握手以来的连续重连次数（握手成功后重置为 0）。客户端可据此感知连接状态但保持 Runner 存活。生命周期累计重连计数通过 `spiritagent.info.reconnect_count` 上报，不重置。

### 2.4 配置所有权与云端同步

**Backend 的 user_settings 是用户配置真源**（REST 为 `GET/PUT /api/config`，按点键 upsert、永不删除键）；Client 是同步代理与 Runner 的唯一推送方。本地 `desktop-settings.json` 是云端镜像（本地/离线使用 + 供 Runner 推送），内容 = **同步节白名单**（toolsets / skills / browser / security / debug / tool_output / computer_use / file_state / audio / companion / ui，节内本机键如 `browser.profile_dir` 不上云）+ **仅本机节**（terminal、spiritagent 等机密与设备相关节及未知节——**永不离开本机**，红线见 §5.3）。镜像带归属戳（sync.user_id），换号残留按不信任处理：水合前清空同步节、不上传。

同步语义：设置变更 → 镜像原子写 → spiritagent.config.update 推 Runner → 防抖后 PUT 云端；启动恢复会话、登录、换号时 GET 水合（云端值逐键覆盖镜像同名键；本地有而云端无的键回传上云，覆盖首跑播种与离线补传）。离线时镜像照常读写，恢复后自动补传；多端为按保存 last-write-wins、无合并，另一端的改动在下次水合时收敛。

生效打扰档位落 `companion.disturbance_tier` 点键经本管道上云，是后端主动闸门（主动消息、cron 自主回合、情绪/空间推理入口）的唯一档位来源（权威边界见 [ARCHITECTURE.md §5.1](../ARCHITECTURE.md)）；用户偏好另存 `companion.disturbance_preference` 供跨端恢复，水合只回写偏好、不回写生效值（生效值是设备派生的）。

Runner 侧不变：仅内存持有配置、每次工具调用读取，不读写磁盘配置文件。时序：Runner 就绪握手后、首个 execute_tool 前推一次 full config；此后每次设置保存再推一次；Runner 重启后内存配置清空，客户端在下次 runner_ready 时重新推送。**config 键明细见 runner 代码（utils/config.py）与 client（shared/lib/config-sync.ts 的白名单），本文只锁定所有权与同步契约。**

---

## 3. 反向 RPC 桥接（Runner 借大脑）

**核心约束**：Runner **零凭证运行**，不持有任何后端 Token；所有出站 LLM 请求必须向上借道客户端。本地 IPC 链路的握手 token 只守 Client↔Runner 之间，不是 Backend 凭据，不参与任何 Backend 请求的鉴权。

**链路**：Runner ──(本地 WS: request_llm)──> Client ──(HTTP POST: /api/llm/completion)──> Backend ──> LLM（Client JWT 鉴权）。

**速率守卫**：Client 转发前统计单会话请求次数与载荷大小（硬上限 200 帧 / 1MB），防止 Runner 工具逻辑失控刷爆 LLM 额度。

---

## 4. IPC Future 桥接（Backend 侧契约）

call_id 是 Backend IPC future 字典的**唯一 Future Key**，标识单次 RPC 生命周期。

**键结构**：按 (user_id, call_id) 二元组寻址，而非单 call_id——并发用户不共享 future；user_id 来自 JWT 解析（受保留键保护）；WS 断开时取消该用户所有未决 future。

**超时与快速失败**：默认 300s 超时返回 synthetic error；下发前做连接在线 → 工具可用 → 发送异常三层检查，通常毫秒级返回离线错误，仅绕过三层后才进入超时。

**JWT 过期边界**：token 在飞行途中过期时客户端回传被拒 → future 挂起直到超时；token 过期不触发 WS 断开，当前靠超时兜底。

---

## 5. 跨模块安全契约

> 架构原则（物理隔离、防御纵深）见 [ARCHITECTURE.md §7](ARCHITECTURE.md)；本节锁定跨模块**契约**。主动消息的 outbox 下发机制见 [ARCHITECTURE.md §5](ARCHITECTURE.md)，此处不重复。

### 5.1 Reserved Keys（防 LLM 入参注入）

LLM 工具入参**禁止**覆盖保留键：user_id / llm_config / user_settings（后端在工具入口静默丢弃）。**角色定义同等保护**：角色定义作为系统提示词的一部分，同样受此保护，防止用户对话内容注入改写伙伴人格。**新增保留键须在本文档 + 工具入口两处同步。**

### 5.2 不可信工具结果包裹

外部（Web 搜索 / 浏览器抓取）获取的字符串注入 LLM 上下文前强制包裹。短字符串不包——注入风险低 + 节省 token。

### 5.3 凭据落盘

激活码（base64 编码的 {baseUrl, token}）经 Electron safeStorage 加密落盘：Windows DPAPI / macOS Keychain（Linux 仅原理说明，Runner/Desktop 不支持）。session JWT **仅内存持有**——每次启动用激活码换新 session JWT；激活码是持久凭证，session JWT 用于日常 API 调用与 ws-ticket 签发。渲染与预加载进程不可访问 safeStorage 接口，阻断 XSS 窃取凭证。**IM 通道凭据**（微信 bot_token 等）沿 `user_model_configs` 同一后端明文先例落 `channel_bindings.credentials`，REST 永不回显原始值。

**设置同步红线**（§2.4）：本机明文机密（`terminal.sudo_password`、`terminal.ssh.password`、`terminal.credential_files` 等 terminal/spiritagent 节内容）永不进入 user_settings / 云端；客户端按同步节白名单上云，白名单外与节内本机键只留本机文件。

### 5.4 API Key Fingerprinting

用户的模型配置仅经管理端点管理（`/api/admin/model-configs` 系）；Client 无自助配置入口。原始 API key 永不离开后端：管理列表只返回 *_set 布尔 + 指纹（sk-…XX 形式），PUT 空 api_key = 保留原值（管理页看不到原始 key，留空必须等价于"不改"）。admin 写入强制 LLM 三字段（base_url / api_key / model_name）非空——半行配置会静默打断用户会话链。

### 5.5 自更新签名（Client ↔ Backend / Installer ↔ Backend）

| 通道 | 校验 |
|------|------|
| Electron 二进制自更新 | electron-updater RSA |
| Runner wheel 自更新 | SHA-512 + 公钥签名（ECDSA P-256）双重校验（签名不匹配在 Staging 阶段直接拦截） |
| Skills | 由 installer 首装 seed，client 自更新不下载 |

**两阶段更新契约**（避免升级中途断网/崩溃变砖）：Stage 1 预取（下载新版 Electron + Runner wheel 到 staging，强校验签名 + SHA-512，写 Sentinel）；Stage 2 安装（用户点 Restart & Install 后原地覆盖、导入冒烟测试、失败回滚旧版）。**核心约束**：Runner venv 目录**永不**重命名或移动，确保任意升级阶段崩溃时旧版 Runner 依赖树仍完全可用。

---

## 6. ID 语义

| ID 类型 | 格式 | 生命周期 | 唯一性范围 |
|--------|------|----------|------------|
| conversation_id | 整型 | 单次会话 | 全局唯一，DB 主键 |
| session_id | 字符串 | 与会话同生命周期（= conversation_id 的字符串形式，跨 WS 重连不变） | 全局唯一 |
| call_id | 字符串 | 单次 RPC 调用 | 整张表唯一（用作 Future Key） |
| task_id（视频生成） | 字符串 | 异步任务周期 | 单 (user_id, provider) 内唯一 |

**职责分立**：session_id 就是 conversation_id 的字符串形式——客户端侧始终用字符串、后端侧持久化为整型，通信边界完成两者转换；call_id 作为唯一 Future Key 标识生命周期（见 §4）。**conversation kind 枚举**：`main`（日常对话）/ `standard`（用户自开任务会话）/ `cron`（自主回合 scratchpad，不出会话列表）/ `im`（IM 通道桥会话，只读，见 §1.7）；每用户每渠道至多一条 im 会话，锚点在 `channel_bindings.conversation_id`。

---

## 7. 跨模块语言规则

LLM 在生成 affect / spatial 时按以下规则：
- affect 的 emotion 必须从 §1.4 枚举集选，LLM 在回复前自填，由解析器解析。
- spatial 的 LOCALE 从 §1.4 locale 枚举选，target 可选、仅 perch 时有意义。
- **后端不产出像素坐标**——客户端据 locale + 当前空间状态决定最终位置与移动方式；target 仅在 perch 时由客户端经窗口枚举解析为窗口几何后计算 perch 点。

---

## 8. 维护规约

- 本文档是**跨模块公共契约**——任何改动必须同时通知所有受影响的模块所有者。
- **契约变更即破坏性变更**：共享枚举/事件/方法的改动，同提交更新本文档与所有消费者；只允许向后兼容的扩展（新增枚举值、新增可选字段），删除/改名/收紧必须写明升级说明与版本策略，并同步各模块。
- 任何扩展 emotion / locale / 事件 type，必须在 **本文档 + 后端白名单 + 客户端消费代码**三处同步。
- 任何 Reserved Key 新增，必须在 **本文档 + 工具入口** 同步。
- 任何 user_settings 新键，必须在 **后端消费代码 + client 同步节白名单（shared/lib/config-sync.ts）** 同步；跨模块语义（如工具集禁用）另在本文档登记。
- 子模块 README 不重复本文档内容，只在需要时链接。
