# 跨模块协议契约

本文定义 Backend、Client、Runner 与 Installer 共同遵守的通信、状态、安全和交付语义，并标明变更需同步的参与方。结构、字段范围与注册清单以链接的源码为准；架构理由见 [ARCHITECTURE.md](ARCHITECTURE.md)，产品体验见 [DESIGN.md](DESIGN.md)，模块入口见 [AGENTS.md](../AGENTS.md)。

按任务阅读：通道与鉴权读 §0–§1.1，伙伴生命周期、事件和表达读 §1.2–§1.6，IM、预设、记忆与调度读 §1.7–§1.9，本机执行与配置读 §2–§4，安全、更新与备份读 §5，标识和语言读 §6–§7。

## 0. 契约总览

WebSocket 与本地 IPC 使用 JSON-RPC 2.0 信封；反向模型请求经 Client 转为 HTTP 请求。独立 REST 资源操作与上传下载不套用 RPC 信封，分工见 §1.1。

| 链路 | 方向 | 传输 | 鉴权 | 见 |
| --- | --- | --- | --- | --- |
| Backend ↔ Client | 双向 | WebSocket 长连接（/api/chat/ws） | 短时 ws-ticket（60s TTL，purpose=ws） | §1 |
| Client ↔ Runner | 双向 | 本地 OS IPC（Windows 命名管道 / macOS UDS）承载 WebSocket 帧 | 每次启动握手 token（失败 401） | §2 |
| Runner → Client → Backend（反向 RPC） | Runner → Client → Backend | 嵌套在 §2 上，经 Client 转发到 /api/llm/completion | Client JWT | §3 |

**信封四种形态**（JSON-RPC 2.0）：请求（带 id + method + params）、事件（无 id、method=event、带 type + payload + seq，不可被响应）、响应（带 id + result 或 error）、ACK（带 method=session.ack、params={seq: int}）。

**核心约定**：

- `call_id` 标识一次工具调用；后端等待表按 `(user_id, call_id)` 寻址，跨用户不共享。连接与会话标识不能代替调用标识，见 §4、§6。
- 事件无 id，不可被响应；请求与响应必须按 id 配对。
- 所有下发事件均附加递增序列号 `seq`（从 1 开始）；序列号与客户端 `lastReceivedSeq` 均为**连接级（Connection/User 级）**状态，跨 Session 共享。客户端维护 `lastReceivedSeq` 保证去重与有序消费。
- 客户端定期向服务端发送 `session.ack(seq)` 确认消费进度（带 id 的标准 RPC 请求），服务端自重放缓冲中修剪已确认帧。
- **心跳保活（session.ping）**：客户端在连接空闲 15s 时发送 `session.ping`（带 id 的标准 RPC 请求），服务端回 `{}`；若 30s 内无任何帧到达，客户端判定半开连接并主动 `close(4000, 'heartbeat')` 触发重连。该机制覆盖 NAT 超时、Wi-Fi 切换、VPN 抖动、笔记本合盖等场景，避免用户发消息后 120s 死寂。
- **全量重水化的防御性截断**：当全量重水化返回的消息数达到防御上限时，响应携带截断标记与更早历史的分页游标；客户端可通过 `GET /api/sessions/{id}/messages?before_id=<cursor>&limit=<n>` 拉取游标前紧邻的更早一页。REST 页内按消息 ID 升序交付且每条消息必带 ID；仍有更早历史时 `next_cursor` 为本页首条消息 ID，耗尽时为 null。该截断仅作为超大历史的负载防御兜底，优先仍走重放缓冲的无缝恢复。
- **水合消息必带创建时间**：后端向客户端下发会话历史消息时（会话恢复、主会话获取、分支派生、消息撤回与编辑、上下文压缩、会话清空及历史消息端点），每条消息必须携带创建时间的毫秒级时间戳。改此处需同步：后端消息重建与客户端对话水合。
- WS 关闭码 1008（鉴权失效）= 立即退出重连流程，不继续尝试。
- **WS 鉴权用短时 ticket**：客户端连接前持 Bearer JWT 调 POST /api/user/ws-ticket 现铸 60s TTL 的专用 token（purpose=ws），经查询串携带；长效 JWT 不进 URL（避免落入代理/访问日志）。ticket 关联签发它的登录记录，握手和每个入站帧都要求该记录与用户仍有效。注销、另一端激活或管理员停用会以 1008 关闭连接并终止该用户在途回合、工具等待与运行时状态；正常刷新只轮换同一登录记录的 Bearer 凭据，既有 ticket 与 WS 明确接续。`?token=` 直传 JWT 仅限后端内部调用。

## 1. Backend ↔ Client 契约

### 1.0 后端不下发窗口开关与工位背景

生活空间与工作台两个入口的互斥开窗完全由客户端负责。后端仅暴露房间、时刻、日记、会话与工具等资源，不下发窗口像素指令，也不为工作台单独生成工位背景。工作台采用单一复合窗口一体化挂载伴工精灵；桌面精灵在工作台开启时收起，工作台关闭时恢复。常规对话生图仅产生会话媒体卡片，绝不触碰激活房间背景；更换房间必须走房间生成接口或专属换房工具。改此处需同步：后端房间端点、房间服务与客户端房间组件。

### 1.1 通道分工（WS vs REST 路由原则）

后端与客户端同时暴露 JSON-RPC over WebSocket 与 HTTP REST。两套通道的**设计意图不同**——选错通道等于把一类语义错配到不属于它的链路：

|  | WS（JSON-RPC） | REST |
| --- | --- | --- |
| **设计意图** | 绑定进程内上下文的持续推送通道；断连后按重连宽限、重放与会话恢复规则清理或续接，不能立即丢弃全部状态 | 按 URL 寻址的独立资源操作；调用方仍须满足身份、权限与资源归属要求，不依赖聊天在线 |
| **承载语义** | 长会话、跨多次往返、需要进程内锚点的小包 | 资源增删改查、独立触发的生成与较大载荷上传下载；是否可安全重试由操作语义决定 |

选择通道时看状态依赖：需要持续会话锚点与流式交付的操作走 WS，独立资源操作走 REST。聊天回合事件沿会话 emitter 交付，需与业务状态共同提交的异步通知走 outbox；不能把所有推送都概括成 outbox。

**REST 镜像特例**：某条 WS 方法的读被一个不持有 WS 连接的 UI 表面（典型为 Hub）需要时，可保留 REST 镜像。镜像必须包装同一个服务函数、两端契约等价，任何漂移都要双端同步。

### 1.2 伙伴生命周期方法（方法级契约）

聊天与工具方法以[桌面注册入口](../backend/services/adapters/desktop/handlers.py)为准；伙伴 REST 见[端点](../backend/api/v1/companion.py)及[请求和响应 schema](../backend/modules/companion/schemas.py)，房间见[房间 schema](../backend/modules/companion/schemas_room.py)。下表按生命周期和会话操作定位语义与消费者，具体字段范围由对应定义维护。

| 方法 | 用途 | 改动需同步的模块 |
| --- | --- | --- |
| onboarding.get_state / onboarding.submit 与 GET /api/companion/onboarding/state | 查询/增量提交 onboarding 答案（断点恢复，支持 WS RPC 与 REST） | Backend 状态机 + Client 消费状态机 + DESIGN §5 流程 |
| avatar.regenerate | 重生头像（不使模型失效）；`mode="edit"` 微调当前头像（编辑上一版产物，反馈必填），缺省 `mode="regenerate"` 全量重绘 | Backend + Client 头像展示 |
| tts.match_voice / tts.design_voice / tts.list_voices | 音色描述匹配 / 专属音色生成 / 目录枚举 | Backend TTS + Client 音色页 + 工具窗口 REST 镜像 |
| companion.set_timezone | Client 每次连接上报本地 IANA 时区——系统提示词日期与陪伴对话时间感知、夜间批处理与互动统计按用户本地日聚合的唯一时区来源；缺行时回落服务端 UTC，夜间流水线整段跳过 | Backend 持久化 + Client boot 上报 + DESIGN §6.2 |
| tools.sync | Client boot、Runner 重启与状态变化时推送工具 schema。非空列表发布执行资格；Runner 断开、崩溃或开始停止时，网关仍连接的客户端发送空列表清空注册表，阻止新派发，不等断线宽限期清理。已派发调用仍按原有结果与超时规则收尾；缺同步或列表为空时本机工具对模型不可见且不可派发。对应 WS RPC 注册于 [backend/services/adapters/desktop/handlers.py](../backend/services/adapters/desktop/handlers.py) | Backend handlers + Client boot 上报 |
| companion.check_affect / companion.interact / companion.should_act / companion.record_interaction_stats / companion.get_user_profile | 桌面自主视觉表达 / 戳·摸头·眩晕反应 / 自主空间决策 / 互动统计 / 画像召回。`check_affect` 只返回结构化 `emotion/actions`，`should_act` 只返回空间动作与可选开场白；RPC 的 `reason` 仅为跳过/失败诊断 | Backend 推理 + Client 触发与消费 + DESIGN §6.3/§6.4 |
| POST /api/companion/portrait/confirm | 确认半身形象（幂等），进入独立全身种子图阶段 | Backend 状态 + Client 流程 |
| POST /api/companion/avatar/{avatar_id}/fullbody/reference | 根据头像与角色定义生成/重绘独立全身参考，接受可选微调反馈与用户参考图；只操作当前激活头像，形象锁定后仍可用，成功替换后由头像响应的 `seed_fullbody_url` 返回签名地址。`mode="edit"` 微调上一版（守卫拒绝 400：反馈必填、不与参考图同给、上一版缺失不可读 409），缺省 `regenerate` | Backend 生成与存储 + Client onboarding / 角色与记忆 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front-2d | 以独立全身种子图为参考，按默认高品质游戏 CG 精绘画风（自然站姿）与微调反馈生成/重绘 2D 正面全身图；全身种子图缺失时拒绝并提示先行生成。`mode="edit"` 微调上一版（反馈必填、无上一版拒绝），缺省 `regenerate` | Backend 生成 + Client 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/front-3d | 以全身种子图（形象身份与身材基准，同 2D 正面生成）为参考生成/重绘 A-pose、3D 画风的 3D 正面种子（3D 升级向导调用；形象锁定后仍可用——姿态/画风派生而非身份变更；不覆盖 2D 正面种子，重绘会使已派生背面种子失效）。`mode="edit"` 微调上一版，缺省 `regenerate` | Backend 生成 + Client 3D 正面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/back | 按 3D 正面种子（缺省回退 2D 正面种子）与微调反馈生成/重绘背面全身图（3D 升级向导调用；形象锁定后仍可用——视角派生而非身份变更；画风与 3D 正面种子成对，由系统按类人 CG / 非人写实自动推导）。`mode="edit"` 微调上一版，缺省 `regenerate` | Backend 生成 + Client 背面预览与微调 |
| POST /api/companion/avatar/{avatar_id}/fullbody/confirm-front | 确认 2D 正面全身图并解开音色/用户子阶段（引导期不生成 3D 种子图——正面与背面均为 3D 建模派生输入，准备见 [docs/PIPELINE.md §1](PIPELINE.md)） | Backend 生成 + Client 流程 |
| GET/POST /api/companion/model | 查询 / 触发 3D 模型异步生成；输入、产物与动画映射契约见 [docs/PIPELINE.md](PIPELINE.md) | Backend 生成管线 + Client 加载 + DESIGN §5.5 |
| GET/POST /api/companion/2d | 查询 / 触发 2D 形象生成流水线（see-through 双 provider 拆分，产物恒为分层 PSD）；产物契约见 [docs/PIPELINE.md §6](PIPELINE.md) | Backend 生成管线 + Client puppet 渲染链 |
| GET/PUT /api/companion/persona | 人设读取与更新；响应含当前心境说明 | Backend persona + Client 人设水合 |
| POST /api/companion/render-mode | 切换并持久化伙伴渲染模式（`2d` / `3d`） | Backend 持久化 + Client 实时切换 |
| companion.model.retryDownload | 仅重试下载已付费的 3D 生成结果，不重新提交生成 | Backend 生成管线 + Client 失败态入口 |
| POST /api/companion/avatar（含 /from-image、/upload）、/avatar/{id}/select 与 GET /avatar/history | 半身头像生成（含上传参考图重绘、直接上传头像）/ 历史形象切换激活 / 历史查询 | Backend 生成与上传 + Client 头像确认与历史画廊 + DESIGN §5.4 |
| GET/POST /api/companion/outfits 与 PATCH /policy、POST /{id}/regenerate、/{id}/confirm、/{id}/poses/{side}/regenerate、PUT /{id}/activate、DELETE /{id} | 2D 换装衣柜：外观列表同时返回自主换装政策；每项的 `asset` 为该外观最新成功的 2D 包（复用 `Companion2DModelResponse` 的签名 manifest、图层 URL 与整包哈希），非就绪项或无成功包返回 null，读取不激活外观、不生成资产；草稿生成（着装描述 + 可选服装参考图，参考图与文字要求先整合为一段着装描述、不直传生图，身份与身材恒为全身种子图唯一参考）/ 修改（draft 或 failed 可用：`mode="edit"` 微调上一版草稿（反馈必填、草稿过期拒绝），缺省 `regenerate` 按已整合的着装描述全量重绘；成功后置 draft，重新确认才生成资产）/ 确认转正并触发 2D 切分（failed 可复用原立绘重试）/ 单侧重生成一侧扶边姿态（`side` 取 `left` / `right`，仅就绪外观：替换该侧姿态纹理与描述符 `poses` 子树并重算整包哈希，PSD 与另一侧不动，失败保留旧姿态，与整包切分互斥、同一外观单飞）/ 即时穿着 / 删除（穿着中与切分中拒绝）。政策取值为 `locked` / `llm_may_replace`，只门控夜间自主穿着与添置，不限制用户操作。生成走独立小时级频控，不设数量上限。微调模式要求供应商链具备图像编辑能力（gemini / grok），否则显式报错不回退 | Backend 生成管线 + Client 衣柜 + DESIGN §1.1 / §8 |
| GET /api/companion/room 与 POST /generate、POST /activate、PATCH /policy、GET /{id} | 生活空间房间背景：水合房间状态（active / history[≤N] / policy / pending）/ 用户主动生成（202 异步，不占角色配额；接受场景要求与可选参考图，格式与大小按房间 schema 校验；参考图读取或解码失败拒绝调度）/ 激活回滚历史房间（着装指纹不一致回 409）/ 政策切换（locked / llm_may_replace）/ 房间详情 | Backend companion_room / room_backdrop_service + Client room-backdrop / 生活空间设置 |
| GET /api/companion/moments 与 DELETE /{id}、POST /{id}/comments、DELETE /{id}/comments/{comment_id} | 生活空间时刻（精灵主导的朋友圈）：游标分页查询（cursor/limit/kind）/ 软隐藏整条时刻 / 用户评论 / 删除本人评论，评论提交后精灵后台异步生成回复。响应媒体契约含 `media_url`、`media_type`（空串/image/video/audio）、可选 `audio_url`、`media_metadata` 与内嵌 `comments`（`role` 为 user/companion）；视频或图片可携同片刻配音。用户不可创建或编辑时刻 | Backend companion_journal / journal_service / application/moments + Client moments-page |
| GET /api/companion/diary 与 GET /{date}、POST、PATCH /{id} | 生活空间日记：区间拉取日记 / 指定自然日日记查询 / 用户手工补写或编辑日记（支持段落追加保护） | Backend companion_journal / journal_service + Client diary-page |
| `command.dispatch` | Slash 命令按名称执行元动作，返回结果并以 `command.result` 同步会话视图；命令声明见[桌面 handlers](../backend/services/adapters/desktop/handlers.py)，分发规则见 §1.9 | Backend 命令声明与分发 + Client 输入拦截与幂等消费 |
| `command.list` | 返回命令名称、别名、说明和确认标记，不下发 handler | Backend 注册元数据 + Client 命令选择器 |
| `session.clear_messages` | 清空当前会话消息（保留会话行 + 写一条 `subtype='status_cleared'` 的 system marker）；强制要求 `confirmed=true`，否则 `-32001`。与 `/清理` Slash 命令共用底层实现 |  |
| `session.set_settings` | 保存当前会话的温度、压缩阈值与推理强度覆盖；生效参数随会话水合，继承与恢复默认见 §2.4 | Backend 会话设置 + Client 参数面板与水合 |
| `session.compress_context`（别名 `session.compress`） | 与自动压缩共用处理路径；成功后返回压缩历史和用量，客户端替换本地列表并刷新上下文占用。Slash 命令的交付见 §1.9 | Backend 压缩与历史重建 + Client 消息与用量视图 |
| `session.undo_to_message` | 普通会话撤回指定用户消息及其后全部消息，返回锚点作为输入草稿；要求确认且无在途回合。REST 镜像共用锁、权限和删除广播，其他窗口按新历史水合；类型限制见 §1.8 | Backend 撤回服务与 WS/REST + Client 草稿恢复与消息列表 |
| `prompt.submit` 的 `edit_message_id` | 按消息 id 编辑最后一条用户消息并启动新回合；只接受 `text`，保留原附件，与 `batch` / `attachments` 互斥。支持普通和固定系统对话，IM 拒绝；与撤回、清空及手动压缩共用会话锁；服务端在锁内核对归属、最新用户行及无在途回合，原消息与后续历史删除、新用户行写入同事务提交。校验失败不改历史；已执行工具的外部副作用不随编辑撤销 | Backend 编辑领域服务与桌面入口 + Client 编辑草稿、提交、历史事件与缓存 |

**音色目录与选择**：目录请求携当前系统语言，后端汇总该用户供应商链中所有已配置 TTS 供应商，只返回该语言及多语言音色；每项携供应商身份。客户端将选择持久化为 `供应商:音色 id`，合成端按该引用优先路由到对应供应商，供应商失败仍沿既有 TTS 链回退。

**关键约束**（跨模块语义，非实现细节）：

- **断点恢复**：角色子阶段答完即标记角色已定稿；onboarding 在独立全身种子图就绪、2D 全身立绘确认且音色完成后视为完成，用户信息均为可选，缺失或后续遗忘不重启引导。没有激活头像则恢复到 `portrait`；头像确认后，缺少独立全身种子图或 2D 立绘时返回 `next_field=fullbody-reference`，客户端读取已有种子图，缺图才自动生成并沿用用户上传的参考图；全身种子图就绪且有 2D 草稿时返回 `fullbody` 并恢复预览，确认后先路由音色。2D 种子草稿确认前停留 temp-media，确认时才转存正式存储；预览下载失败可重试加载，重新加载不触发生图，草稿过期才需重生成。
- **形象锁定**：形象确认即锁定，物种/性别/基础外貌不可再改，关闭头像重新生成与历史头像切换激活。全身参考重绘、3D 正背面派生和模型生成保留独立入口，不解锁身份；其输入与失败恢复按 [PIPELINE](PIPELINE.md) 执行。
- **关系不外溢到分析与形象**：引导期录入的用户与伙伴关系（知己好友、赛博管家等）只渲染进对话系统提示词供交互参考；不进入性格标签分析与头像/立绘提示词生成——关系是用户与伙伴之间的，不是伙伴自身属性。
- **下载失败优先恢复已有结果**：下载失败态随 `model.failed` 事件下发可重试标记与模型标识；客户端必须据此提供"重试下载"入口，而非引导重新生成。持久化与恢复语义见 [docs/PIPELINE.md §3](PIPELINE.md)。
- **当前心情状态**：人设水合响应携带已持久化的 `current_mood`。生活空间陪伴回合完成后，独立状态推理写入该字段并发出 `companion.mood`；用户直接互动也可更新。该短语只供身份轨展示，不写入聊天消息，也不受主动打扰档位拦截。
- **生活空间房间图联动与保护**：房间背景将角色绘制进场景中，角色参考与穿着来源见 [PIPELINE §1](PIPELINE.md#1-3d-链拓扑)。首房间在 2D 立绘确认后调度，避免早于独立全身参考生成。换装成功后（`worn=true`）自动比较着装指纹，不一致时下发 `companion.room.invalidated` 并自动触发重建，防止画面穿帮。房间政策为 `locked` 时，拒绝角色自主换房，但放行换装联动和用户显式请求；历史房间保留最近 N 张供回滚，回滚时若服装指纹与当前穿着冲突则返回 409。
- **聊天参考图换房**：`room_backdrop_update` 用 `reference_image_index` 选择当前模型上下文中最近一条带图用户消息的图片，从 1 开始；用户要求参考图片时必须指定，未指定不使用用户图，越界或图片不可读时失败，不退回纯文字生成。图片与用户回合标识由编排层注入并作为保留参数保护，模型只提供序号与场景要求。用户回合的显式换房按 `user_request` 调度，放行锁定与打扰档位且不占自主配额；自主回合仍受原门控，并拒绝用户参考图。改动须同步聊天编排、工具运行时、换房工具与生成服务。
- **夜间自主活动**：`User.nightly_activity_enabled` 总控，房间与外观政策及 `companion.autonomous_media`、`companion.autonomous_voice` 分别控制对应能力，默认开放。模型从运行时可用目录选择少量活动或空计划，服务层按外观 → 房间 → 片刻/媒体 → 联系执行，每项开始前重读政策；无当日消息不阻断规划，在线打扰档位不参与夜间判断。
- **夜间恢复与交付**：可查询任务沿原业务记录恢复，结果未知的在途动作保留中断状态，不重新提交；成功事实才能进入记忆、日记与问候。片刻及对应陪伴消息、outbox 同事务提交，媒体持久化为永久资产路径。夜间房间活动独立于在线换房配额；次日问候以一次性 `special` 任务等待，仅在线且非静止时交付，最晚保留至目标本地日结束。内部账本与恢复窗口见 [Backend 夜间批处理](../backend/README.md#夜间批处理)，证据并发约束见[记忆模块](../backend/services/domains/memory/README.md#来源与并发)，体验见 [DESIGN §6.2](DESIGN.md#62-主动陪伴与打扰档位)。
- **时刻与日记分层不变量**：底层 `memories` 用于检索与上下文注入，人工管理入口按预设展示事实、状态和证据，不暴露向量表示；生活空间消费独立的 `moments`（时刻）与 `diary_entries`（第一人称日记）。夜间批处理静默提炼日记，若当天已被用户编辑过则采取尾部段落追加而非覆写；工作预设会话中严格禁止记录生活时刻。片刻完全由精灵发起，产生通道有三：白天自主冲动（每用户每 24h ≤ `MOMENT_AUTONOMOUS_PER_DAY`，默认 3，0 表示关闭；静止档断源，纯信息流更新、不发主对话消息）、聊天内 `moment_create`（≤ 3 次/日）与夜间规划；用户仅可评论、删除本人评论与软隐藏整条时刻。当日片刻互动（发布与评论线程）作为共享输入进入夜间规划、反思日记与日记投影。
- **内置专属工具门控**：生活空间三工具只绑定陪伴会话——工作预设与自动化任务在回合装配层即不注入 schema（`prompt_presets.LIFE_SPACE_TOOL_NAMES`，`build_turn_inputs` 与 `search_tools` 同源过滤），派发层按同一集合硬阻断（orchestrator，与 automation 同机制），工具入口不判定会话类型。
  - `room_backdrop_update`：支持聊天请求与角色自主换房，参考图选择和用户请求例外见上文。自主调用在静止档或 locked 政策下拒绝，常规档限 decorate/mood，自主档可 seasonal/rebuild；每用户每 24 小时自主换房成功 ≤ 1 次。
  - `moment_create`（args: title, body, emotion?, kind?）：角色主动记录时刻。静止档禁止调用；每日角色主动配额 ≤ 3 次。
  - `diary_write`（args: body, mood?, date?）：角色主动写日记。静止档禁止调用。

### 1.3 事件类型

| 事件 type | 触发时机 | 消费者 |
| --- | --- | --- |
| `message.start` / `message.delta` / `message.break` / `message.complete` | 聊天流开始 / 正文增量 / 连续气泡分隔 / 回合完成。`message.complete` 载荷为 `{text, reasoning?, media?, usage?, message_id?}`，不携带情绪、动作、心情或空间字段 | Client 聊天窗与语音条 |
| `companion.message` | 主动陪伴自然台词已持久化，载荷 `{text, session_id, message_id, media?}`；`session_id` 指向唯一 companion 主会话 | Client：在主会话已打开时增量追加消息，按消息 id 去重；桌面提醒受表面可见性、锁屏和打扰档位约束，消息入列不受提醒门控 |
| companion.affect | 桌面精灵自主视觉表达（载荷 `{emotion?, actions?}`，actions 最多 3 个） | Client：仅自主档、桌面精灵可见且解锁时切 EMOTIONAL 并播放动作序列 |
| companion.mood | 角色当前心情更新（载荷 `{mood}`） | Client：更新生活空间头像与名字下方的身份轨短语，不生成聊天消息 |
| avatar.regenerated | 头像重生最终结果 | Client 替换头像或展示失败 |
| model.ready / model.gen.progress / model.failed | 3D 模型就绪 / 进度 / 失败；载荷契约与产物映射见 [docs/PIPELINE.md](PIPELINE.md) | Client 加载与状态展示 |
| companion.2d.ready / .failed | 2D 完整资产包就绪 / 失败（含单侧扶边姿态重生成完成，复用本事件刷新资产与签名地址）；客户端刷新当前资产及签名地址，分层立绘与扶边姿态的完整性约束见 [能力链说明](PIPELINE.md#62-扶边姿态包) | Client 水合 puppet 渲染路径 |
| companion.render_mode.changed | 用户在设置中或多端同步切换渲染模式（`2d` / `3d`） | Client 切换展示画布 |
| companion.outfit.updated / .failed | 换装外观状态变化（重绘为草稿 / 切分就绪 / 穿着翻转 / 删除，载荷含 outfit_id 与 worn 标记）/ 切分失败（含原因），单侧扶边姿态重生成失败也走本事件（外观保持 ready，载荷附 `side`） | Client 重拉衣柜列表；worn 为 true 时重水合 2D 渲染层（与 2d.ready 双触发幂等，重绘只刷新衣柜，事件只当刷新触发、列表端点是真相源）；单侧姿态重生成以此驱动在飞提示与失败原因展示 |
| companion.room.progress | 房间图生图三段进度（brief / imagine / store），载荷 `{backdrop_id, stage}` | Client 在生活空间显示生成进度条 |
| companion.room.ready | 房间图就绪，载荷 `{backdrop_id, url, brief, origin, outfit_fingerprint}` | Client 把生活空间背景切到新图 |
| companion.room.failed | 房间图生成失败（无供应商名），载荷 `{backdrop_id, utterance}` | Client 停止等待并显示失败通知；已有房间仍保留，不自动朗读该通知 |
| companion.room.invalidated | 房间联动需重新水合，载荷 `{reason, active_backdrop_id?}` | Client 进入等待并轮询，保留当前房间至新图就绪 |
| companion.moment.created | 生活空间新增一条时刻（精灵发布） | Client 增量 push 到时间线 |
| companion.moment.comment | 一条片刻下新增评论（用户评论或精灵回复），载荷 `{moment_id, comment}` | Client 增量合并到对应片刻的评论区 |
| companion.diary.upserted | 一篇日记被写入或更新 | Client 在打开日记页时才消费（不发主动气泡） |
| video_gen.completed / .failed | 视频生成结果；completed 载荷含 task_id / url / session_id / media，兼作后台视频的异步送达通道（见下方「对话内生成媒体」） | Client 对话窗媒体卡与提示跳转 |
| channel.status | IM 通道绑定状态变化（connected / login_required / error 等，载荷 {channel, status, account_name?, error?}） | Client 通知/toast；Hub 状态以 REST 读为准（Hub 窗口无 WS） |
| channel.peer_request | 陌生对端首次来信触发配对审批（载荷 {channel, peer_id, peer_name, preview}） | Client 通知引导主人到通道设置审批 |
| `system.notification` | 普通后台自动化完成或失败，载荷含结果类别、标题、说明与可选 `session_id`；完整产物保存在对应任务会话 | Client 系统通知；有 session_id 时提供打开任务会话的入口 |
| `command.result` | Slash 命令执行结果（载荷 `{command, result:{status, message, payload?, hydrate?}}`）；必带 `session_id`（从 command.dispatch 调用中隐式继承）。客户端用 `hydrate=true` 替换本地消息列表（payload.messages），用 `hydrate=false` 仅插一条 status pill。**幂等**：同一调用会同时下发 RPC result + 此事件，前端接住任意一路即可触发渲染 | Client 聊天窗：hydrate 替换消息列表、push status pill（`status_cleared` / `compress_summary` 等） |
| `compress.completed` | 自动上下文压缩（orchestrator 命中阈值）完成后下发；载荷 `{subtype:'compress_summary', text, message_id}`，`text` 与持久化 `Message.content` 同源；必带 `session_id`。手动 `/压缩` 仍走 `command.result`+`hydrate=true` 替换整列，二者语义互补（自动 = 单行插入不打断流；手动 = 替换列表强一致） | Client 聊天窗：`pushStatusPill('compress_summary', text)` 单行插入，渲染端走 `compress_summary` 分界线式可折叠卡片分支（详见 `chat-dock-message-bubble.tsx` 的 `COMPRESS_CARD_SUBTYPES`） |
| `message.persisted` | 用户消息落库后、流式开始前下发；载荷 `{role:'user', message_ids}`，`message_ids` 为本轮提交的全部用户行 id（含 batch 前导，按插入序）；必带 `session_id`。终端助手行 id 挂在 `message.complete.message_id`（中间工具调用助手行不回写）。活路径气泡据此绑定，无需等 hydrate | Client 聊天窗：把 id 绑到当前会话末尾尚未绑定的对应用户/助手气泡 |
| `message.reasoning.delta` | 助手推理过程流式增量；载荷 `{text}`；必带 `session_id`。与 `message.delta` 并行，不进入正文、不进下一轮 LLM 输入 | Client 工作台：累加到当前助手气泡的推理区；生活空间不展示 |
| `message.deleted` | `session.undo_to_message` 的多窗口广播：载荷 `{session_id, deleted_count, messages}`，`messages` 是截断后的完整消息列表；发起窗口已通过 RPC 路径 hydrate，其它窗口经此事件用 `payload.messages` 替换本地列表；必带 `session_id`，由 `events.ts` 在会话闸门内消费 | Client 聊天窗：`hydrateChatMessages(messages)` 替换本地消息列表 |
| `message.edited` | 编辑提交后、新回合开始前广播 `{session_id, messages}`；包含修订用户行的完整历史，所有窗口按所属会话全量水合并更新快照，同时保留本窗口尚未提交或正在等待提交确认的消息气泡与附件；若待确认提交因编辑回合在途被拒绝，将其退回待发队列，待新回合结束后继续提交。修订行使用新 id，使离线窗口的旧 `after_id` 失效；RPC 响应仅确认入队，不再次水合，以免覆盖已开始的流式回复 | Client 聊天窗、编辑草稿与历史缓存 |

**事件投递范围（session_id 语义）**：session_id 就是 conversation_id 的字符串形式（见 §6）。聊天会话事件（`message.*` / `tool.start` / `tool.complete` / `error`）带信封级 `session_id`，渲染端按会话过滤；业务 outbox 事件投递到用户的 desktop，不以当前打开的会话作为路由闸门。上表同时包含这两类事件，不能统一按 outbox 处理。`companion.message`、`system.notification` 与 `video_gen.completed` 可在载荷内部携带 session_id，渲染端据此决定增量落卡或提供跳转，不将它用作 outbox 路由闸门。

**`tool.call` 是用户级设备指令，不是会话事件**（改此处需同步：backend services/application/chat/tool_dispatch.py 与 services/adapters/desktop/emitter.py、client app/runtime/gateway-event-router.ts、本文档）：它不渲染进任何气泡、不参与会话状态机，只按 `call_id`（§6 定义的唯一 Future Key）与 `tool.result` 配对，因此**不带信封级 session_id**、由后端直接推给该用户的 desktop 派发器。载荷含 `{name, args, call_id, session_id, headless?}`，其中 `session_id` 是**信息字段而非路由闸门**；交互式回合下客户端用它判断可见会话并自持精灵工作态，`headless=true` 时照常执行设备指令但不展示工作态、工具流或打字态。IM、主动 Cron、普通自动化与子 agent 的无头回合统一使用该标志，不依赖会话类型猜测可见性。

**设备指令的重复投递**：`tool.call` 与其它事件一样进重放缓冲，WS 断连重连会重发。本机副作用不可撤销（删文件、跑命令），因此**客户端必须按 `call_id` 去重**，重复帧直接丢弃——后端的 `resolve_future` 只会丢弃迟到的结果，拦不住已经发生的副作用。客户端执行时把 `call_id` 原样透传给 `execute_tool`（§2.5），让 Runner 侧调用日志成为第二道幂等防线并支撑中断后的结果查询。

**对话内生成媒体**（改此处需同步：backend 工具与聊天持久化、[对话编排 README](../backend/services/application/chat/README.md)、client 渲染层与 client/renderer/README.md、DESIGN §6）：

- 聊天回合经图像/视频生成工具产出的媒体，随对话完成事件以 media 数组（元素为 image / video 类型 + 本服务媒体 URL）下发，并持久化在对应助手消息行；后台完成的视频另以 status_media 送达行落库，实时事件与历史水合看到同一形状。
- 渲染端在**对话窗**以媒体卡内联预览、点击放大播放；精灵气泡只承载轻量文本，收到媒体时仅提示「点击查看」并支持点击打开对话窗（必要时切到目标会话）——富媒体统一在对话窗展示，不进气泡。
- 精灵画/拍自己（生成工具 subject='self'）：后端自动注入角色参考（图像参考或缺省视频首帧），参考规则见 [PIPELINE §1](PIPELINE.md#1-3d-链拓扑)。

**用户侧聊天附件**（改此处需同步：backend 网关校验与附件生命周期模块、[对话编排 README](../backend/services/application/chat/README.md)、client 附件 UX 与 client/renderer/README.md、DESIGN §6.1）：

- 图片附件以 `data:image/*` data URL 随 `prompt.submit` 的 attachments 直发（不落盘）；视频附件因 base64 远超 WS 单帧上限，客户端必须先经 `POST /api/media/videos`（multipart：file + session_id，容器白名单 mp4/mov）换取附件 URL，再以 `{"type": "video", "file_url": ...}` 提交——附件 URL 只认本会话（跨会话引用直接拒绝），绝对形态仅认 `public_base_url` 前缀（第三方绝对 URL 会被拒绝，防止借供应商发任意请求）。
- 服务端点 `GET /api/media/videos/{session_id}/{file_id}` 公开（file_id 为不可猜测 token；公网模式下供应商需直接拉取）。
- 供应商消费分双模式，由后端 `public_base_url` 配置决定：留空时构造请求前把最近 2 个内联为 data URL（单文件 50MB 上限）；配置可公网访问地址后以绝对 URL 直发供应商自拉（单文件上限=会话配额 512MB，且该地址必须真能被供应商服务器访问）。
- 附件文件按 512MB/会话滚动配额：超限从最旧剔除；压缩/夜间摘要检查点之前与历史截断删除之前的视频确定性清理。两类清理都把所属消息行的 `input_video` part 改写为 `[视频已清理]`，渲染与 LLM 上下文不残留死链。
- 持久化 part 形状为 `input_video`（扁平 `video_url`，与 `input_image` 同构）；每请求内联超限或文件缺失时降级 `[video]` 文本占位（与旧图 `[screenshot]` 同构）；视频回合的链筛选由供应商能力位（`supports_video`）决定——Responses 网关不支持 `input_video` 的供应商（mimo）不参与视频回退链。

### 1.4 聊天、心情、视觉表达与空间契约

四类输出严格分立：聊天正文只承载台词；当前心情只走 `companion.mood`；桌面视觉表达只走 `companion.affect`；空间行为只走 `companion.should_act` RPC 结果。任何控制语义都不得编码进聊天文本。

#### 聊天正文与语音演绎

用户发起的生活空间陪伴回复由同次 LLM 输出台词和独立语音描述。后端在正文下发前移除隐藏头，绑定供应商和模型的演绎描述（含该供应商支持的整体控制、导演描述或停顿，以及以正文唯一短语定位的句内语气词）作为独立元数据随增量、完成事件与历史消息交付；只有终端回复交付并保存语音描述；工具中间轮不交付正文或语音描述。描述不进入消息正文、复制文本、转写或后续 LLM 上下文，也不驱动具身状态。客户端自动语音与历史点播透传该元数据到媒体合成入口；内存、磁盘、请求合并与时长缓存均区分语音描述。缺少或无效的模型描述降级普通朗读，截断隐藏头不得作为正文泄漏。变更描述契约时须同步后端媒体模型、流式解析与持久化、供应商适配，以及客户端消息水合、媒体 IPC 和缓存。工作台、IM 与无头主动回合不生成此描述。

#### 当前心情与具身表达

**当前心情（mood）**：生活空间陪伴回合完成后，Backend 用独立 JSON 推理生成一句第一人称短语，持久化到 `persona.current_mood` 并发出 `companion.mood {mood}`。Client 无条件刷新身份轨；该状态不写 `messages` 表、不渲染气泡。直接互动也可更新同一状态；工作预设、主动 Cron、普通自动化与 IM 回合不触发这条更新。

情绪枚举以 [emotions.py](../backend/services/domains/companion/emotions.py) 为准，客户端表情映射须覆盖它；未知情绪按 neutral 处理。

**自主视觉表达**：Client 仅在生效档位为 autonomous、桌面精灵可见、屏幕解锁且空闲达到阈值时调用 `companion.check_affect`。Backend 另以档位闸门限制该 RPC，并从角色定义、记忆、时间、允许情绪与当前模型动作能力独立推理 `emotion/actions`；成功时发出 `companion.affect`。Client 收到事件时再次按同一可见性条件消费。该链不生成正文、气泡或 TTS，也不写对话历史。

**视觉动作选择**：可用动作以当前模型能力为准，排除状态机和用户交互专用键；模型未提供清单时使用 [actions.py](../backend/services/domains/companion/actions.py) 的默认集减去 `NON_LLM_ACTIONS`。一次最多按序返回三个动作。2D 与 3D 的同名动作语义须一致，具体兑现分别见 [2D 渲染说明](../client/renderer/modules/character/rendering/2d/puppet/README.md) 和 [PIPELINE §5](PIPELINE.md#5-3d-客户端消费)。视觉动作不负责移动坐标，移动走空间决策或仪式行走。

**`companion.should_act` 空间动作枚举**（权威源在 Backend `ALLOWED_ACTIONS`）：roam / perch / approach / stay。Client 仅在 autonomous、桌面精灵可见且智能驱动开启时调用，Backend 同样做档位闸门。`approach` 附带 `params.text`（10–30 字开场白，Backend 截断至 80 字并设 30 分钟冷却，冷却内或无有效文本整体降级 stay）；开场白经纯文本 `companion.message` 独立投递，RPC 响应承载走位动作。Client 根据焦点窗口计算 perch/approach 坐标，Backend 不产出像素坐标。

**Client 内部场所与表面状态**：home / perch / roam / target / workbench 均为 Client 状态，不是聊天或 WS 文本协议。`target` 只由本地工具触发的仪式性行走使用；生活空间或工作台打开时桌面透明精灵舞台收起，暂停视觉表达与自主空间推理，关闭表面后恢复。

#### 工具循环与终端答复

所有会话每个用户回合至多一条终端 assistant 行含可见正文。中间 assistant 行的 `content` 和语音描述为 NULL，保留 tool_calls 与后续 tool 行的 call_id 配对；当轮内存上下文同样只追加工具结构。终端轮继续负责完成事件、媒体和后置任务；ephemeral 仍不落库。

正文交付按会话身份分流，不依赖是否启用 TTS：

- 生活空间用户陪伴回合（`kind=special`、`system_preset_id=companion`、非自动化、非 headless/ephemeral 且无预设覆写）使用非流式请求（`stream=false`）。每次补全都提供当前已解锁的工具集，由模型选择调用工具或直接回复，全部补全受同一迭代预算约束。拿到完整响应后，先确认 `status=completed` 并检查全部输出项：包含工具调用时丢弃本次正文及语音描述，配对保存并执行工具，再继续补全；无工具调用时才解析并交付正文，不额外生成另一份答复。日常闲聊可一次补全完成。
- IM、headless、ephemeral 及其它缓冲回合沿用流式请求，但正文缓冲至供应商流正常结束，确认无工具调用的终端补全才交付。
- 上述两类回合均仅发送一次 `message.start`。终端正文按气泡顺序发送并以空行拼接落库；完成事件仅收尾和补充元数据，不能再次追加全文或重复触发 TTS。用量保留终端补全的实际值，供上下文窗口估算使用，不合并多次调用的 token 数。
- 工作台的专业特殊会话、普通会话与前台自动化会话实时下发正文增量，每次 LLM 补全前发 `message.start`，收尾上一段过程并重置分气泡状态，避免终端回复覆盖过程文字。中间文字只属于实时呈现，历史恢复仅有终端正文与工具记录。

缓冲回复在供应商异常或取消时不交付半截正文；确认终端后分段交付期间若取消或发送失败，已经交付的气泡保留且不回退重放，失败回合不写终端正文行。

流式路径收到首个供应商事件、非流式路径拿到完整响应后，均禁止切换供应商；请求阶段失败仍按供应商回退链处理。供应商返回不完整终态时，仅在尚未交付任何正文且原因不是内容过滤的情况下，允许同一供应商、同一参数自动重试一次；清除失败尝试加入内存上下文的推理项，半截正文、语音描述与工具草稿不复用、不派发、不落库。重试不新增开始事件、不重跑此前已完成的工具批次；已交付正文、内容过滤或重试仍不完整则按失败收尾。失败尝试已发送的推理增量可留在实时视图，不回灌后续模型输入。客户端气泡收尾须对 TTS 幂等：若尾部分隔事件已提交最后一个气泡，随后完成事件仅补媒体、消息 id 等元数据，保留播放状态，不再次合成。

#### 分段、历史与推理过程

**连续气泡分隔**：LLM 需要在一回合内连发多条短回复时，用单独一行 `---` 分隔；Backend 解析为 `message.break` 事件（带 session_id）并**自行控制 0.5–1.5s 的分段节流**——停顿在后端交付时完成，Client 按帧到达顺序收尾当前气泡再渲染下一气泡，双端无需各自计时。 陪伴预设同时将正文空行视为气泡边界，兼容模型未输出专用分隔行的回合；工作预设保留普通段落。陪伴回复持久化时以空行连接各段，Client 历史水合按空行恢复气泡，沿用同一个消息 id，媒体仅挂末段。

用户连发批次在提交前以空行合并为一条用户消息，生活空间的实时独立气泡只是呈现差异。陪伴历史按空行恢复气泡，沿用同一消息标识；工作台保留单条消息内的段落。批次等待与发送体验见 [DESIGN §6.6](DESIGN.md#66-对话节奏与状态分离)。

**推理过程**：供应商若产出独立推理过程，后端以 `message.reasoning.delta` 流式下发，并在 `message.complete` 可选附带本轮推理全文（多段工具循环已拼接）。会话水合消息列表用 `reasoning` 带回已落库的推理过程。该内容只给工作台展示，不进入下一轮 LLM 输入。多气泡回合里增量落在到达时的当前气泡；完成帧与正文一样，不把整轮推理覆盖到最后一格。

#### 命中区域与直接互动

- `companion.interact` RPC payload 的 `kind` 字段支持 `poke`（戳击）、`pet`（摸头抚摸）、`dizzy`（激怒/眩晕）。
- `companion.interact` RPC payload 的 `region` 字段允许传下列白名单之一（不传 = 整精灵矩形命中）：

| region | 含义 |
| --- | --- |
| `head` | 头部（含 face） |
| `face` | 脸部（head 子区域） |
| `arm_L` / `arm_R` | 左 / 右手臂 |
| `body` | 躯干 |
| `back_hair` / `front_hair` | 后发 / 前发 |
| `skirt` | 下装 / 裙子 |

命中区域与手势影响：（1）前端手势/物理反馈——摸头享受、怒气、眩晕与发区抖动的触发阈值与粒子反馈见 [DESIGN.md §6.3](DESIGN.md)；（2）LLM 反应上下文——`kind` 与 `region` 字段透传到 LLM，让回应可针对"摸头" vs "戳脸" vs "拍手" vs "眩晕"做不同文案。两条渲染路径都做可见像素级命中——3D 走 silhouette hit（离屏 alpha 回读）；2D 走 [PuppetStage](../client/renderer/modules/character/rendering/2d/puppet/PuppetStage.tsx)（当前帧部件网格精确点测，区域 = 最上层命中部件的映射，CPU 轻量，经命中区域总线 `$mesh2dHitmap` 下发）。

**扩展协议**：emotion 扩展须同步更新 **Backend 白名单 + Client 表情映射 + 本文档**；视觉 action 扩展须同步更新 **Backend [actions.py](../backend/services/domains/companion/actions.py)（DEFAULT_ACTIONS / NON_LLM_ACTIONS）+ Client [PuppetStage 包络表](../client/renderer/modules/character/rendering/2d/puppet/PuppetStage.tsx) + 本文档**；空间动作扩展须同步 `ALLOWED_ACTIONS`、Client autonomy 执行器与本文档。未覆盖 emotion 一律按 neutral 处理。

语言选择与生效时机见 [§7](#7-跨模块语言规则)。

### 1.5 资产 URL 签名与传输缓存

| 资产 | TTL |
| --- | --- |
| portrait 头像 | 5 分钟 |
| 3D 模型 GLB | 5 分钟 |
| 2D 部件 PNG / manifest.json | 5 分钟 |
| 2D 分层 PSD（分层切分产物，puppet 链消费） | 5 分钟 |
| 换装外观全身立绘（草稿期为 temp-media 免鉴权路径，确认后转正式签名） | 5 分钟 |
| 生活空间房间背景图（room_backdrop，含角色的 16:9 生成背景图） | 5 分钟 |
| 对话内生成图片 / 视频（`image_generate` / `video_generate`） | 文件永久；库内存 `companion-assets/{user_id}/` 裸路径，下发与水合改写为 `/api/companion/asset/...`，已登录 Client 可直读 |

**契约要点**：资产端点支持双通道鉴权——已登录 Client 携带有效 Bearer JWT 时可直接访问归属资产；未携带令牌时按 URL HMAC 签名校验（每次签名 5 分钟 TTL，换设备/过期需重新签名）。服务端模型/资产端点支持 HTTP Range 断点续传 + ETag + 不可变缓存头；Client 按内容哈希（SHA-256）在本地磁盘缓存，命中即跳过网络，未命中/中断走断点续传。对话生成图片与视频不进 temp-media TTL，历史会话重载不会因临时文件过期 404；改生成落盘、消息 `media_json`、`video_gen.completed` 事件、history 重建与 IM 出站媒体需同步 backend 生图/视频任务、会话持久化与水合改写。

衣橱列表元数据可在客户端持久化，打开时先读本地再刷新服务端真源；签名轮换不视为资产变化。立绘按移除签名参数后的不可变资产路径、描述符和 PSD 按整包版本且区分资源、扶边纹理按各自内容哈希复用本机缓存；新包版本必须重新取回。登出清除列表和字节缓存，在途旧会话结果不得回写。

### 1.6 错误信封

REST 端点异常路径返回统一结构：error（短码）+ reason（分类，可空）+ status（HTTP 状态）。WS JSON-RPC 错误使用标准错误码（-32700 到 -32603）。**关键契约**：内部错误抛至前端前必须脱敏，严禁包含数据库账号、服务器本地路径等栈帧细节；统一错误分类决定恢复策略，见 [backend/README.md](../backend/README.md)；流式调用（chat TTS 流）一旦首 chunk 已发，任何供应商失败都不切换 fallback。

### 1.7 IM 通道桥接（/api/channels）

外部 IM（微信 iLink）经后端进程内的通道桥与同一伙伴对话：入站消息驱动**无头 chat 回合**（不依赖用户 WS——桌面离线也能回），回复经格式化（去 markdown、按 `weixin_reply_max_chars` 分片）从原渠道送出。产品语义与渠道路线见 [DESIGN.md](DESIGN.md)；实现与已知限制见 [backend/README.md](../backend/README.md)。

**会话契约**：所有渠道共用 `im` 这一种 conversation kind；**每用户每渠道一条专属 im 会话**，由 `channel_bindings.conversation_id` 唯一外键锚定（渠道间不混流）。im 会话对桌面端**只读**：出现在会话列表与历史中，但 `prompt.submit` 拒写（后端守卫 + 客户端输入禁用）；人设/情感与桌面陪伴共享；长期记忆固定按 `(user_id, companion)` 加载。

**遥控契约（IM 驱动本机工具）**：IM 回合与桌面回合共用同一编排器与同一工具注册表，因此桌面在线时伙伴在 IM 上**能调用本机 runner 工具**——手机是遥控器，能力叠加在陪伴之上而非替代它。三条边界：

- **本机工具依赖桌面 WS，回合本身不依赖**：回合无头执行（桌面离线也能回），但其中的 runner 工具经上述 `tool.call` 设备指令通道兑现，桌面不在线即整体不可用。
- **在线与否作为环境事实进系统提示词**：判定同时要求「WS 可用」与「注册表已有该用户的 runner 工具」——只看前者会在 tools.sync 未完成或 Runner 崩溃时让伙伴声称工具可用却无 schema 可调。离线时伙伴须如实说明电脑未连接，不得含糊搪塞或假装做过。
- **授权边界就是对端白名单**：已审批对端等同本人，无额外的逐次授权层。审批一个对端 = 允许它操作本机，通道设置页的审批文案须体现这一分量。

**回合节奏**：单飞行锁保证每绑定同时只有一轮；进行中回合到达的消息先以 queued 行持久化（按绑定、对端及渠道消息标识去重渠道重投，缺失消息标识时不按内容去重）再确认接收并入队，容量不足在落库前明确拒收（对端收到可重发提示），已接收的消息不静默丢弃。回合只消费本批及更早遗留的 queued 行，后续批次保持排队；接收顺序即落库 id 序，模型上下文按消费批排序，将排队输入放在上一轮工具结果与终端回复之后；被中止清出的排队行由下一回合收编，历史水合以 `queued: true` 标注已接收未消费的行。只投递终端回复；工具中间轮的文本不发送到 IM，等待期间使用渠道支持的 typing 指示。发起回合的对端可用停止指令（全词白名单、非包含匹配，判定先于入站频控——用户连发几条后正好把窗口打满时，刹车不能跟着一起丢）中止该回合；中止须同时清空排队并 resolve 其 future。旧回合只可释放自己持有的状态；适配器守卫、登录、入站分发、无头回合与 typing 都归绑定实例所有，登出、删除、重建及进程关闭必须取消并等待整棵任务树后才能启动新实例，避免旧绑定继续派发本机工具。**中止只终止后端等待，不撤销已下发到本机的工具**——已经在跑的命令会执行完，其结果落到无人认领的 call_id，核对依据见 §2.5。

**投递与补发**：终端回复中未送达的文字分片与媒体，或后台任务结果（如视频成败通知），持久化为待补发行（`channel_deliveries`，投递状态独立于执行状态），对端下一条消息提供新鲜回复上下文时按行串行补发，仅发送剩余内容并遵守渠道分片上限；补发任务归绑定实例管理，退出时取消并等待。补发不重新执行任何任务，重复失败超限弃置并记日志。reply-only 渠道不能主动推送，结果送达天然等待对端来信——这是渠道规则而非缺陷。

**REST**（Bearer JWT，前缀 `/api/channels`）：

| 端点 | 用途 |
| --- | --- |
| `GET /api/channels` | 渠道注册表能力位 + 当前用户绑定状态（凭据字段永不出现） |
| `PUT /api/channels/{channel}` | 创建/更新绑定（config 落 config_json）并重启适配器 |
| `DELETE /api/channels/{channel}` | 停用并删除绑定（peers 级联；im 会话行沉淀为历史） |
| `GET /api/channels/{channel}/peers` | 对端白名单/待审批列表 |
| `POST /api/channels/{channel}/peers/{peer_id}` | 对端审批（approve / block / delete） |
| `POST /api/channels/weixin/login` + `GET /api/channels/weixin/login` | 微信二维码登录启动与轮询；返回等待扫码、已扫码、已确认、已过期、错误或需登录状态。等待帧附 `qr_image`；适配器未运行时返回需登录状态 |
| `POST /api/channels/{channel}/logout` | 渠道登出：清凭据转 login_required，绑定与 im 会话保留 |

**访问控制**：默认拒绝——未知对端首条消息收到一次性固定配对回复并落 pending 行（`channel.peer_request` 事件），仅主人审批放行；blocked 静默丢弃；每 peer 进程内限速（`channels_inbound_rate_per_minute`）。

**渠道能力差异**（产品语义级）：微信 iLink 为 reply-only（伙伴**不能**主动发起微信消息，回复须回显入站 context_token，过期后等用户下一条消息刷新；登录凭据失效与否以轮询回路的 -14 为准，发送路径的过期只按回复上下文失效处理），当前不支持群聊；能力位以 `GET /api/channels` 注册表为准，实际支持范围随渠道演进维护。

**改此处需同步**：backend services/adapters/channels 与 modules/channels、backend/README.md、client 通道设置页与只读守卫（client/renderer/README.md）、DESIGN.md、ARCHITECTURE.md §5.4。

### 1.8 系统预设对话（5 套并列的特殊会话）

系统确保每用户具有五条固定的 `kind='special'` 对话；预设目录与推理默认值由 [presets.py](../backend/services/domains/conversation/presets.py) 定义，产品目标见 [DESIGN §8](DESIGN.md#8-多预设并列的系统对话)。预设归属和会话类型是两个维度，不能用是否存在预设标识推断 `kind`。

| 概念 | 契约 |
|---|---|
| `system_preset_id` | 每条会话持久化非空目标标识。用户交互会话使用有效预设；自动化使用内部 `automation`。普通会话与 IM 会话也有预设归属 |
| `kind` | `special` 为固定系统对话，`standard` 为用户自建或自动化任务会话，`im` 为渠道会话 |
| `is_automation` | 与内部 `automation` 目标一致；不装配长期记忆或参与陪伴反思、活跃度和主动触达 |
| 固定会话唯一性 | 每用户每预设最多一条 `special`，由部分唯一索引约束；同预设可有多条普通会话 |
| 名称与列表 | 固定系统对话不可改名、删除；工作台置顶四条专业系统对话，生活空间使用唯一陪伴主会话。普通会话按自身权限支持改名、删除与派生 |

字段与约束见 [会话模型](../backend/modules/conversation/models.py) 和[数据库基线](../backend/alembic/versions/0001_baseline.py)；传输结构见 [会话 schema](../backend/modules/conversation/schemas.py)。

#### 创建、恢复与派生

- `system.list_presets` 只返回名称、说明、图标等元数据，不下发提示词正文。工作台新建选择器要求选择专业预设；`session.create` 校验后端目录中的有效标识，省略或空串时默认 `developer`。新建结果为普通会话，不额外创建固定系统对话，也不回退到陪伴域。
- `session.get_main` 返回陪伴预设的固定主会话。`session.resume` 优先重放可恢复帧；否则以可选 `after_id` 增量水合，锚点已删除或未提供时回退完整历史。客户端必须区分重放、增量合并和全量替换，分页与截断见 §0。IM 会话中已接收未消费的入站消息以 `queued: true` 字段标注（消费后消失），客户端显示已接收、等待处理；IM 恢复始终全量水合，避免按 `after_id` 增量拉取遗漏旧行的排队状态变化。
- `PATCH /api/sessions/{id}` 管理标题、置顶和归档，固定系统对话或不可改名会话拒绝修改；`DELETE` 对固定或不可删除会话返回 403。非默认标题不再由自动命名覆盖。改变预设目标不能借改名完成，已有历史必须保留原归属。
- `session.fork` 与 REST 镜像只允许从普通会话的指定历史节点派生，继承源预设与自动化归属，设置父会话关联。复制普通消息、工具链、媒体、推理、语音描述、摘要日期和创建时间，排除界面状态行，清零用量与耗时；复制内容是已发送历史，不同时放进输入框。系统预设与 IM 会话拒绝派生。

消息操作同时核对会话类型与回合状态：最后一条正常用户消息可编辑，固定系统对话也支持；撤回与派生仅限普通会话，IM 只读。编辑按持久化 id 定位，陪伴多气泡回填同一用户行的完整文本；本地待发批次或在途回合存在时不开放编辑。服务端守卫分别见[编辑](../backend/services/domains/conversation/edit.py)、[撤回](../backend/services/domains/conversation/undo.py)与派生服务，客户端入口见[消息气泡](../client/renderer/modules/conversation/chat-dock-message-bubble.tsx)。

创建与恢复由 [桌面 handlers](../backend/services/adapters/desktop/handlers.py) 和[会话端点](../backend/api/v1/sessions.py)交付，派生范围由 [fork.py](../backend/services/domains/conversation/fork.py) 维护；变更时同步[客户端会话列表](../client/renderer/modules/conversation/session-list-store.ts)、[历史缓存](../client/renderer/modules/conversation/session-history-cache.ts)与消息操作。参数继承和恢复默认见 §2.4。

### 预设记忆与学习作用域

`memory.list/update/delete` 是人工管理入口。会话入口提交 `session_id`；人工独立管理页提交 `system_preset_id`，两者互斥且必须有一个。用户归属取自认证，服务端拒绝未知预设与 automation。list 返回 `system_preset_id`、`session_id`（人工预设选择时为 null）、`memories`、同域 `counts`；update/delete 的 ID 跨域与不存在均返回未找到。客户端切换预设后丢弃旧请求结果。

列表接受 `status=active|candidate|invalidated|expired`（默认 active）和 `kind` 命名空间过滤。记录返回 `content_version`、`basis`、`status`、`usage`、`reason`、`expires_at`、`evidence`；证据含消息 ID、原文、支持或反对方向和发送时间。counts 返回四个可见状态的学习记忆数量及 user_profile 数量；已遗忘正文与指纹不经管理列表返回。编辑作为用户明确陈述生效，删除执行不可召回的遗忘。

用户资料条目固定在陪伴作用域，经 `kind=user_profile` 在管理页同域维护：已知 `user_*` 字段的新增与修改走 `onboarding.submit`（persona 定稿后仍开放 `user_*` 与 `voice`，行被删后可重建），其余 `user_profile:` 条目走 `memory.update`；删除与其他记忆一致执行遗忘，counts 的 user_profile 数量随之同步。

模型工具为 `memory_recall`（有效记忆检索）、`memory_inspect`（读取原始证据及版本）、`memory_retain`（提交 decisions 原子批次供独立 LLM 审核）。决策字段与约束的唯一 schema 见 [memory_policy.py](../backend/services/domains/memory/memory_policy.py)。模型可以选择已提供的证据 ID，但不能指定来源归属；服务端重新核对原始消息和全文片段。候选和失效记录只进入独立维护器，不作为对话事实返回。更新必须带当前 ID 和版本；发生并发变更整批拒绝，重新检查后再判断。

模型记忆工具不接受 user_id、system_preset_id、scope 或 source_refs；服务端捕获的作用域与来源不可由参数覆盖。`cronjob` 模型入口依源会话限定任务创建、列表及 ID 管理，任务 `system_preset_id` 表示创建目标；standard 执行会话仍显式绑定 automation，special 只能属于 companion。

客户端 `tools.sync` 声明 `skill_scope_version=1` 才开放 skills_list/skill_view/skill_manage。Backend 的 runner 工具请求以顶层 `skill_scope={user_id, system_preset_id}` 传给 Client，Client 经 `runnerInvoke` 转发 `execute_scoped_tool`；该字段不在模型工具 schema。Runner 在请求执行期间固定作用域，学习产物只写所属目录。旧全局 client_context.skills 不再用于提示词注入，技能目录由模型通过同域工具读取。自动化不开放学习技能和 cronjob 管理。

### 1.8.1 Cron 双轨契约

| `CronJob` 字段 | 契约 |
| --- | --- |
| `kind` | `{special, standard}`，新建任务默认 `standard`；夜间陪伴规划显式创建 `special` |
| `conversation_id` | `standard` 任务关联独立的 `Conversation(kind='standard')`；`special` 为 NULL，运行时始终解析 `system_preset_id='companion'` 的主会话 |
| `expires_at` | 可选绝对过期时间；夜间一次性 `special` 联系用它界定目标本地日的等待窗口，过期后不再派发 |

- **`special`**：调度 CAS 与 `companion_intents` 写入同事务，同来源未完成的触发合并。认领意图时原子写入内部 `companion.turn.request {intent_id, lease_token}`；执行端校验所属用户、状态、租约和有效期后才开始无头回合。动态意图与 Hint 只加入内存上下文，禁用 `send_message_tool` 和回合委派。空文本或精确 `<silent>` 不写消息；正文以 `Message.status_proactive` 写入主会话，并与下一次等待和 `companion.message` outbox 同事务提交。桌面或 IM 用户发言优先取消尚未提交的主动回合，晚到结果不能覆盖取消或更新后的意图。
- **`standard`**：调度 CAS 胜出者在关联任务会话中使用内部自动化预设执行；输入、工具过程与完整结果只属于该任务会话，完成或失败后创建 `system.notification`。它不装配伙伴人设、画像与长期记忆，禁用主动消息、时刻、日记与房间等陪伴专属工具，不写 companion 主会话，也不受伙伴打扰档位约束。
- **触发 Hint**：`special` 的意图与打扰档位作为请求尾部数据参与推理，不写入 `messages`；主动回合的行为与沉默协议由系统提示词装配。因此稳定的人设、记忆与真实历史保持在动态数据之前，可继续复用前缀缓存。`standard` 的任务输入属于其独立任务历史，按普通用户消息持久化以便审计与复跑理解。

#### 陪伴等待与情境唤醒

`companion_wait` 只向陪伴预设开放，受 `scheduled_tasks` 工具集开关约束。它支持保存、查询、更新和取消意图；时间条件与事件条件可任选其一或并用，并用时任一条件满足即可候选唤醒。有效期是硬边界，主动回合内的续等不得延长原有效期；回合准备与推理共用预算，有效期先到即取消回合。主动回合只能为当前意图暂存后续等待，成功终态才提交；用户回合的工具操作直接持久化。容量限制阻止新增或重新激活，已有有效等待仍可修改。结构与范围见 [schemas_loop.py](../backend/modules/companion/schemas_loop.py)。

修改源定时任务的内容、时间、轨别或有效期，以及主动删除任务时，源任务和未完成意图在同一事务中变更，旧意图不再被认领或交付；运行中的意图保留结果待核对提示。调度交接在同一用户锁下核对任务快照，不能用修改前的指令重建意图；触发后自动删除一次性任务不撤销已经交接的意图。

Client 宿主通过 `companion.signal {available, event?}` 上报短期可用性，`event` 仅允许 `desktop_available` / `context_changed`，不上传窗口标题、应用名称或屏幕内容。Backend 以接收时刻判断新鲜度，超过 90 秒、断连或收到不可用信号即停止认领；不可用信号同时取消在途主动回合。可用性不能覆盖已落库的静止档，也不证明用户愿意被打扰。只有匹配已有等待的事件才形成候选唤醒，重复事件合并；事件在节流窗口内到达也保留至下一次检查。

意图在用户行锁下按用户串行认领，租约令牌隔离重放和迟到结果。尚未开始的过期认领可重新排队；没有执行潜在副作用的失败按有界退避重试，用户插话则退回等待。运行中崩溃或工具结果不明时标记失败并保留核对提示，不自动重放；超过唤醒有效期也不丢失核对提示，失败记录在最后更新后的 30 天内仍可在用户对话中查看和重新安排。

备份包含等待意图，恢复时清除租约与旧事件标记，并重映射同批恢复的源定时任务；源任务已删除或未恢复时，意图独立保存。运行中状态按结果不明处理，核对提示的保留期从恢复时起算。覆盖源定时任务时，若目标仍保留未随本批恢复的关联意图，则保留源任务并报告该类别未覆盖，避免留下失去撤销关联的旧意图。

### 1.9 Slash 命令（对话内元动作）

普通输入模式以 `/` 开头的文本触发清空、压缩等会话级元动作，不经 `prompt.submit` 而经独立的 WS RPC 派发；编辑已发送消息时，斜杠开头的内容按普通文本处理。命令语义与 system prompt 模板（preset）、LLM 工具调用都正交——`/压缩` 不是「让 LLM 帮我压缩」，而是「我现在就要压缩」。这避免了把回合外副作用塞进 prompt 路径产生的 in-flight / 审计 / 跨窗口同步问题。

**命令语义**（名称、别名和确认标记以[注册入口](../backend/services/adapters/desktop/handlers.py)为准）：

| 命令 | 别名 | 影响历史 | 需确认 | 备注 |
| --- | --- | --- | --- | --- |
| `clear` | 清空 / reset | 是 | 是 | 清空消息保留会话行，写 `status_cleared` marker；system_preset 也允许执行 |
| `compress` | 压缩 / ctx | 是 | 否 | 复用 `session.compress_context` 强制压缩路径 |
| `remember` | 记住 / 记忆 / remind / memo / memory | 否 | 否 | 按当前会话的认证记忆域写入人工明确记忆（`recall:manual`）并生成向量；自动化无记忆域 |

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

- 命令声明 → [桌面 handlers](../backend/services/adapters/desktop/handlers.py)，注册与分发协议 → [slash_commands.py](../backend/services/application/chat/slash_commands.py)；同步 [client/renderer/shared/lib/slash-commands.ts](../client/renderer/shared/lib/slash-commands.ts) 镜像
- 拦截逻辑 / 弹层 → [client/renderer/modules/conversation/chat-slash.ts](../client/renderer/modules/conversation/chat-slash.ts) + [client/renderer/modules/conversation/slash-command-popover.tsx](../client/renderer/modules/conversation/slash-command-popover.tsx)
- 错误码 → [backend/components/constants.py](../backend/components/constants.py) + [backend/components/__init__.py](../backend/components/__init__.py) + 客户端 `slashErrorToMessage`（chat-dock.tsx）
- 状态 pill 渲染 → `status_command_result` 加入 `chat-dock-message-bubble.tsx` 的 status 渲染分支
- 自动压缩事件 → [backend/services/application/chat/orchestrator.py](../backend/services/application/chat/orchestrator.py)（orchestrator 命中阈值后 push）+ [backend/services/adapters/desktop/emitter.py](../backend/services/adapters/desktop/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/app/runtime/gateway-event-router.ts](../client/renderer/app/runtime/gateway-event-router.ts)（`compress.completed` switch）+ `chat-dock-message-bubble.tsx` 的 `COMPRESS_CARD_SUBTYPES` 折叠卡片分支 + 本文档 §1.3
- 活路径消息 id 回写 → [backend/services/application/chat/persistence.py](../backend/services/application/chat/persistence.py) + [backend/services/application/chat/orchestrator.py](../backend/services/application/chat/orchestrator.py)（`message.persisted`）+ [backend/services/adapters/desktop/emitter.py](../backend/services/adapters/desktop/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/app/runtime/gateway-event-router.ts](../client/renderer/app/runtime/gateway-event-router.ts) + `chat-store.ts` 绑定 + 本文档 §1.3
- 推理过程事件 → [backend/services/application/chat/streaming.py](../backend/services/application/chat/streaming.py) + [backend/services/application/chat/persistence.py](../backend/services/application/chat/persistence.py) + [backend/services/adapters/desktop/emitter.py](../backend/services/adapters/desktop/emitter.py)（`_TRANSLATED` 表 + `_translate`）+ [client/renderer/app/runtime/gateway-event-router.ts](../client/renderer/app/runtime/gateway-event-router.ts) + `chat-store.ts` + 工作台气泡 + 本文档 §1.3

**手动撤回不走 slash 命令**：消息级粒度的「撤回」由用户在历史用户气泡旁点击撤回图标触发，直接走 `session.undo_to_message` RPC（slash 命令无法承载消息级粒度 + 需要服务端精确路由到具体 source_message_id）。详见 §1.2 与 §1.3 的 `message.deleted` 事件。

## 2. Client ↔ Runner 契约

### 2.1 链路与鉴权

- Runner **主动**连客户端提供的 IPC 端点（Windows 命名管道 / macOS UDS，权限 0600）。
- 端点路径与 token 由客户端**单向下发**（启动时环境变量 `SPIRITAGENT_DESKTOP_TOKEN` + 落盘文件）；Runner 重连间重读文件以在客户端重启后拾取新端点与新 token。endpoint 文件在 POSIX 上 chmod 0600。
- 启动后发 runner_ready 握手通知；鉴权走 upgrade 头，校验失败客户端回 401、不完成握手；Runner 收到 401 后丢弃内存缓存端点与 token、等待重读文件。token 为每次启动新生成的 256-bit 随机值，**不是 Backend 凭据**。
- 安全模型：Windows 命名管道命名空间对本机进程可枚举、且无自定义 DACL 接口——token 是实际闸门；macOS 侧 0600 socket 为主闸门、token 为纵深防御。OS IPC 不经网络栈，无端口监听面。

### 2.2 RPC 方法清单

| 方法 | 方向 | 用途 | 改动需同步的模块 |
| --- | --- | --- | --- |
| runner_ready | Runner → Client | 启动握手，携带 version + run_generation + pid + capabilities + capabilities_health + reconnect_streak | Runner 探测 + Client 功能门控 + 重连降级展示 |
| runner_capabilities_changed | Runner → Client | 运行期能力重探测快照变化通知（载荷含 run_generation + capabilities + probe_failed）；客户端暂未消费该通知，能力门控仍以 runner_ready 为准 | Runner 监视 + Client 能力门控 |
| tools_changed | Runner → Client | 工具 schema 变更通知，Client 重拉并同步到 Backend | Runner + Client + Backend 工具表 |
| get_tools | Client → Runner | 获取工具 schema（已过滤禁用项） | Runner 过滤 + Backend 过滤 + Client |
| spiritagent.info | Client → Runner | 完整运行快照（含 run_generation + pid） | Runner 上报 + Client 诊断 |
| execute_tool | Client → Runner | 执行工具调用；params 可选 `call_id` 启用调用日志（见 §2.5） | Backend 路由 + Client 中转 + Runner 执行 |
| execute_scoped_tool | Client → Runner | 同 execute_tool，额外绑定 `skill_scope`（§预设记忆与学习作用域）；`call_id` 语义相同 | Backend 工具请求 + Client runnerInvoke + Runner 执行 |
| spiritagent.call_result | Client → Runner | 按 `call_id` 查询调用日志记录（status ∈ claimed / completed / failed / unknown / not_found），供中断后恢复决策 | Runner 调用日志 + Client 查询入口 + Backend 中断恢复 |
| spiritagent.cancel | Client → Runner | params.req_id 可选；指定则取消该 RPC，缺省取消当前进行中工具；并对目标 req_id 设置中断标记 | Client 中断 + Runner 任务取消 + 请求级隔离 |
| spiritagent.config.update | Client → Runner | 推送完整配置（云端为真源，Client 是镜像持有者与唯一推送方，见 §2.4） | Client 设置 + Runner 内存配置 |
| request_llm | Runner → Client | 反向 RPC 借大脑 | §3 |

**工具集标识与归属**：客户端公开开关以 [toolset-catalog.ts](../client/renderer/shared/lib/toolset-catalog.ts) 为入口，主进程工具数统计见 [toolset-index.ts](../client/main/shared/lib/toolset-index.ts)；实际过滤由 [Backend 目录](../backend/services/infrastructure/tool_runtime/toolsets.py) 和 [Runner 目录](../runner/tools/toolsets/catalog.py) 各自维护。Runner 另有 `system_awareness` 分组，当前未列入客户端公开开关；不能把界面清单当作完整运行时目录。新增公共标识须同步显示、统计、过滤与设置恢复。

禁用语义：UserSettings 点键 `toolsets.disabled` 持有被禁用的 id 集合。Runner 侧在 `get_tools` 源头过滤自有工具；Backend 侧在工具注册表读取时过滤 backend/memory 桶（各自的 id → 工具名映射见模块代码）。无工具集归属的工具（如 `search_tools`、`video_generate`）不受开关影响。

### 2.3 runner_ready capabilities 与 health 状态

capabilities 与 capabilities_health 来源于 Runner 的运行时探测（探测设计见 [runner/README.md §2](../runner/README.md)）：前者是向后兼容的布尔映射，后者按子能力给出可用性与失败原因。致命探测异常置 probe_failed；客户端按能力缺失做局部降级或给出可操作提示。

`reconnect_streak` 是自上次成功握手以来的连续重连次数（握手成功后重置为 0）。客户端可据此感知连接状态但保持 Runner 存活。生命周期累计重连计数通过 `spiritagent.info.reconnect_count` 上报，不重置。

**运行代次（run_generation）**：Runner 每次进程启动生成新标识，进程内重连不轮换；`runner_ready`、`runner_capabilities_changed` 与 `spiritagent.info` 同源携带。周期重探测仅在快照变化时通知，异常携 `probe_failed`。

当前客户端尚未消费运行期能力通知，也未将 `run_generation` 纳入设备就绪聚合，能力门控仍依赖握手快照。接入时须共同检查连接、握手、运行代次与工具同步；代次变化后丢弃旧运行关联，不能只因传输重连成功就沿用旧就绪结论。

### 2.4 配置所有权与云端同步

**推理配置作用域**：`agent.*` / `chat.*` 用户设置仅为 `kind=standard` 普通会话默认值，即使普通会话选择了系统提示词模板也仍继承。`kind=special` 的每条系统预设会话使用预设目录中的 `inference_defaults`，不继承工作台推理、压缩或后台记忆整理设置；IM 同样隔离并使用陪伴场景默认。所有会话最后叠加自身持久化覆盖，主动回合与手动压缩使用同一合并规则。`session.set_settings` 只写当前会话覆盖；`reasoning_effort` 与 `reasoning` 映射到 `agent.reasoning_effort`。

创建、恢复、派生与保存响应的 `info.settings` 返回生效参数。`session.set_settings` 仅接受温度、压缩阈值及推理强度三个字段；`null` 删除对应覆盖（含同义存储键），空 patch 只刷新生效参数。窗口内“恢复默认”删除三项覆盖，随后由后端重新计算默认值，避免普通会话固化旧默认。保存先在行锁下合并落库，再更新运行时；失败不得只改变运行时。场景值由 [预设目录](../backend/services/domains/conversation/presets.py) 单源维护。语言、设备与工具能力遵循各自共享契约。

**Backend 的 user_settings 是用户配置真源**（REST 为 `GET/PUT /api/config`，按点键 upsert、永不删除键）；Client 是同步代理与 Runner 的唯一推送方。本地 `desktop-settings.json` 同时保存允许同步的云端镜像和仅本机配置，供离线使用及推送 Runner。同步节、节内排除键与顶层原始值键由 [config-sync.ts](../client/main/shared/lib/config-sync.ts)维护；白名单外、未知节和机密键永不上传，边界见 §5.3。镜像带用户归属戳，换号残留按不信任处理：水合前清空同步节、不上传。

主题由配置镜像播种窗口首帧，跨进程时序见 [Client](../client/README.md#窗口与主题)；语言使用顶层原始值同步，跨模块语义见 [§7](#7-跨模块语言规则)。

同步语义：设置变更 → 镜像原子写 → spiritagent.config.update 推 Runner → 防抖后 PUT 云端；启动恢复会话、登录、换号时 GET 水合（云端值逐键覆盖镜像同名键；本地有而云端无的键回传上云，覆盖首跑播种与离线补传）。离线时镜像照常读写，恢复后自动补传；多端为按保存 last-write-wins、无合并，另一端的改动在下次水合时收敛。

`language` 是顶层原始值同步键——与 `SYNCED_SECTIONS` 的对象节不同，按 last-write-wins 与云端直接互盖；通过 `prefs:set('language', ...)` 通道写入，与 `disturbance_preference` 同族（用户偏好语义，非设备环境事实）。

生效打扰档位落 `companion.disturbance_tier` 点键经本管道上云，是后端主动闸门（主动消息、cron 自主回合、情绪/空间推理入口）的唯一档位来源（权威边界见 [ARCHITECTURE.md §5.1](ARCHITECTURE.md)）；用户偏好另存 `companion.disturbance_preference` 供跨端恢复，水合只回写偏好、不回写生效值（生效值是设备派生的）。玻璃降级手动开关 `companion.reduce_transparency` 同为用户偏好键（跨端恢复；OS 减透明偏好与帧预算自动降级是设备派生信号，不上云）。

Runner 侧不变：仅内存持有配置、每次工具调用读取，不读写磁盘配置文件。时序：Runner 就绪握手后、首个 execute_tool 前推一次 full config；此后每次设置保存再推一次；Runner 重启后内存配置清空，客户端在下次 runner_ready 时重新推送。配置键与默认值见 [Runner 配置](../runner/utils/config.py)，同步范围见上文客户端白名单；本文维护所有权与同步契约。

### 2.5 本机调用日志

`execute_tool` / `execute_scoped_tool` 的 params 携带可选 `call_id`（与 §6 的 Future Key 同一标识，由 Backend 生成、Client 原样透传、不经模型工具 schema）时，Runner 在 `$SPIRITAGENT_HOME/call-journal/` 持久化调用记录：

- **先查询后认领**：执行前按 `call_id` 查询已有记录——命中 completed 幂等重放落盘结果，命中 failed / unknown / conflict / claimed_elsewhere 一律返回携带 `data.disposition` 的错误帧（unknown 用 -32011，其余 -32000），绝不执行；无记录则以 `O_CREAT|O_EXCL` 原子认领，并发同标识只有一个进程获得执行权。
- **同标识不同参数拒绝**：认领冲突时比对参数指纹（工具名 + 参数 + 学习作用域的规范 JSON 哈希），不一致返回错误，绝不执行或跨作用域重放结果。
- **终态与中断**：认领记录刷盘后才开始执行；completed / failed 终态与完整结果保存后才回复调用方，重放不另行截断。只有持有该次认领凭据的执行者能写入终态；取消后无已保存终态的记录标 unknown（工作线程和外部副作用可能仍在继续），终态与 unknown 不被迟到写入覆盖。查询和启动清理均核对持有进程；进程已死且无终态时标 unknown，不自动重放。
- **查询入口**：`spiritagent.call_result {call_id}` 返回 `{call_id, status, result?, error?, claimed_at?, finished_at?}`，仅无记录返回 `status="not_found"`；损坏或不可读的记录返回 unknown，非法调用标识返回错误。Backend 中断恢复时先查询，据 status 决定续跑、重放结果还是保留待核对提示，不得盲目重跑本机副作用。
- **边界**：调用日志只降低重复执行风险并提供查询依据，不保证任意外部副作用恰好发生一次；终态自完成或裁决为 unknown 起保留 7 天后由 Runner 启动清理。日志不可写时允许执行和返回，但恢复能力不可保证；未获得认领凭据时不得补写或覆盖其他执行者的记录。无 `call_id` 的直调路径不记日志、行为不变。

### 2.6 Skills 平台声明与过滤

技能 frontmatter 使用 `platforms` 声明适用系统，支持字符串或列表；未声明或空列表表示不限定平台。规范值使用 `macos` / `windows`，两端也接受对应的 `darwin` / `win32` 别名并忽略大小写。该声明用于按宿主筛选，不扩大 [ARCHITECTURE 的平台支持范围](ARCHITECTURE.md#平台支持策略)。

Installer 保留完整技能文件；Client 主进程计算兼容状态，界面据此过滤，并拒绝启用不适配本机的技能；Runner 在技能列表与读取入口再次过滤。解析入口分别为 [skill-index.ts](../client/main/shared/lib/skill-index.ts)与 [skills_tool.py](../runner/tools/skills/skills_tool.py)。新增平台或调整声明语义须同时核对两端与实际 payload，不能只改界面标签。

Runner 还兼容旧式单数 `platform` 字段，客户端索引只读 `platforms`；需要两端一致识别的技能必须使用规范字段。学习技能的用户与预设隔离见[预设记忆与学习作用域](#预设记忆与学习作用域)，与平台过滤分别生效。

## 3. 反向 RPC 桥接（Runner 借大脑）

Runner 不持有后端登录凭据或模型供应商密钥。`request_llm` 经本地 IPC 到 Client，由 Client 以登录身份请求 `POST /api/llm/completion`，Backend 再调用模型。本地握手 token 仅用于 Client ↔ Runner 准入，不能用于 Backend 鉴权。

Client 在转发前检查消息数量与载荷大小。预算按反向 RPC 桥实例累计，只有重新建立该实例才重置，WebSocket 重连不清零；文字与带图请求采用不同字节上限。计数方式、当前限额及请求格式转换见 [reverse-rpc.ts](../client/main/runner/reverse-rpc.ts)，变更时同步 Runner 调用方与后端补全端点，不能将累计预算描述成单次帧大小。

## 4. IPC Future 桥接（Backend 侧契约）

Backend 按 `(user_id, call_id)` 保存一次工具调用的等待对象，用户归属来自认证上下文。结果仅能兑现同用户、同调用的未完成等待；重复或迟到结果不得重新启动回合。

派发前检查桌面连接与工具可用性，发送异常快速返回错误；已发调用受独立超时约束。连接清理判定用户离线时，未决等待以断连错误收尾，不统一取消等待任务，以便 IM 等无头回合继续说明失败。逐调用取消与等待表释放由 [ipc.py](../backend/services/infrastructure/desktop/ipc.py) 维护；丢弃后端等待不代表本机副作用已撤销，结果核对见 §2.5。

登录撤销、停用和正常刷新按 §0 的 ticket / 登录记录生命周期处理，不能以 Bearer 字符串的到期时刻推断既有 WS 的有效性。连接重放、调用日志和 Future 各有生命周期，禁止跨层复用其标识。

## 5. 跨模块安全契约

架构原则（物理隔离、防御纵深）见 [ARCHITECTURE.md §7](ARCHITECTURE.md)；本节锁定跨模块**契约**。主动消息的 outbox 下发机制见 [ARCHITECTURE.md §5](ARCHITECTURE.md)，此处不重复。

### 5.1 Reserved Keys（防 LLM 入参注入）

用户身份、配置、记忆与技能作用域、来源证据等运行参数由服务端注入。工具入口先丢弃模型提供的同名参数，再使用认证回合捕获的值；完整保留集合以 [registry.py](../backend/services/infrastructure/tool_runtime/registry.py) 的 `RESERVED_KEYS` 为准。新增运行参数时同时核对注册、装配、异步传递与消费者，不能只在提示词中禁止覆盖。

角色定义的写入权限由对应服务入口控制。将角色定义放入系统提示词不能替代身份锁定或防止所有提示词注入。

### 5.2 不可信工具结果包裹

被标记为不可信的外部工具结果在进入模型上下文前附加资料边界，说明其中内容不取得指令权限。适用工具、结构与短文本处理见 [tool_dispatch_helpers.py](../backend/services/infrastructure/tool_runtime/tool_dispatch_helpers.py)。该包装是语义提示，不证明内容安全，也不能替代来源、用户作用域及工具权限校验。

### 5.3 凭据落盘

激活码（base64 编码的 {baseUrl, token}）经 Electron safeStorage 加密落盘：Windows DPAPI / macOS Keychain（Linux 仅原理说明，Runner/Desktop 不支持）。session JWT **仅内存持有**——每次启动用激活码换新 session JWT；激活码是持久凭证，session JWT 用于日常 API 调用与 ws-ticket 签发。渲染层与预加载桥不暴露 safeStorage 凭据读取接口；这限制直接读取凭据的通路，桥接 API 仍须单独控制可调用能力。**IM 通道凭据**（微信 bot_token 等）在后端数据库保存，当前不是应用层加密存储；REST 不回显原始值，数据库与备份访问属于凭据边界。

**设置同步红线**（§2.4）：本机明文机密（`terminal.sudo_password`、`terminal.ssh.password`、`terminal.credential_files` 等 terminal/spiritagent 节内容）永不进入 user_settings / 云端；客户端按同步节白名单上云，白名单外与节内本机键只留本机文件。

### 5.4 AI 配置与密钥脱敏

用户的人工智能配置仅经管理端点维护，客户端不提供自助入口。系统与用户配置都由供应商信息库和五项核心能力的有序配置组成；供应商名称是唯一关联标识，不设置额外的卡片标识或标题。用户某项能力存在配置时完整覆盖系统调用顺序，没有配置时继承系统对应能力；用户能力中的空字段依次继承用户和系统的同名供应商信息。原始密钥永不离开后端，管理列表只返回已配置状态；留空保存会保留同名供应商已存的密钥，显式清除后恢复上级继承。

### 5.5 自更新签名（Client ↔ Backend / Installer ↔ Backend）

| 通道 | 校验 |
| --- | --- |
| Electron 二进制自更新 | 由 electron-updater 管理下载与安装；平台签名和发布配置见 [客户端更新入口](../client/main/lifecycle/auto-updater.ts) 与 [client/package.json](../client/package.json)，不等同于 Runner 清单验签 |
| Runner wheel 自更新 | SHA-512 + 公钥签名（ECDSA P-256）双重校验（签名不匹配在 Staging 阶段直接拦截） |
| Skills | 由 installer 首装 seed，client 自更新不下载 |

两阶段更新将下载校验与安装切换分开：

1. 预取 Electron 更新与 Runner 载荷；Runner 清单签名覆盖 `path|sha512`，wheel 校验 SHA-512，`server.py` 按清单记录校验 SHA-256。通过后写待安装标记，校验失败不能进入安装。
2. 安装时停止 Runner，检查现有 venv，安装新 wheel 并替换 `server.py`，随后启动新 Runner；失败写入标记供有限重试，不自动回滚旧包。

Runner venv 路径保持不变，避免移动目录破坏入口脚本和解释器引用；这不保证安装中断后旧依赖树仍完整。已有 venv 损坏时需要安装器修复，不能把更新重试当作重建环境。实现见 [updater.ts](../client/main/runner/updater.ts)，wheel 与入口的构建期一致性检查见 [scripts README](../scripts/README.md)。签名私钥的本地与 CI 配置见 [release-keys README](../scripts/release-keys/README.md)。

Installer 的启动快路径与客户端更新前检查须使用一致的 Runner venv 健康判定，不能仅凭完成标记或 Python 文件存在认定可用。修改核心导入探针时同步 [bootstrap.rs](../installer/src-tauri/src/bootstrap.rs)和 [updater.ts](../client/main/runner/updater.ts)；具体导入集合由两端实现维护。

### 5.6 备份校验与覆盖恢复

备份不维护独立格式版本号。ZIP 路径、manifest 清单、文件库存与校验和属于包级硬门槛，任一失败都不写目标数据；通过后按当前数据模型逐类预检必需字段、来源引用与向量维度。旧包未声明的当前数据保持不变，覆盖恢复也只清理通过预检且将要写入的数据类；清理某类会破坏未恢复关联数据时保留该类目标数据并列为失败。未知或不兼容的数据类不阻断其他内容恢复。导入响应列出每个失败类别、数量与原因，管理页保留部分成功结果供管理员核对。会话与消息必须成对出现；无法映射目标会话的附件只跳过该附件并计入失败结果。

恢复时只有特殊系统会话按预设去重，选择同一预设的普通会话仍独立映射。上传上限由 [Settings](../backend/components/config.py)维护，解压总量与路径检查见[管理端导入入口](../backend/api/v1/admin.py)；部署状态的排除范围见 [Backend](../backend/README.md#数据与运行可靠性)，记忆证据与遗忘指纹的重映射约束见[记忆模块](../backend/services/domains/memory/README.md#恢复与读取)。

**覆盖恢复维护边界**：管理员执行用户备份 `overwrite` 或 `merge` 恢复时，后端先把该用户标记为维护中；新 REST/WS 操作返回稍后重试，已进入的 REST 操作须退出，网关会话、IM 绑定、Cron 回合及可中断的整理任务须取消并等待，已经提交的付费生成任务须等待自然落地，之后才允许清表与写入。导入成功或回滚后都要清除会话、主动状态、交互统计与调度节流等旧内存镜像，再从数据库真源恢复 IM 绑定；客户端后续重连必须重新挂载会话，不得沿用已删除的 conversation ID。

## 6. ID 语义

| ID 类型 | 格式 | 生命周期 | 唯一性范围 |
| --- | --- | --- | --- |
| conversation_id | 整型 | 单次会话 | 全局唯一，DB 主键 |
| session_id | 字符串 | 与会话同生命周期（= conversation_id 的字符串形式，跨 WS 重连不变） | 全局唯一 |
| call_id | 字符串 | 单次 RPC 调用 | 整张表唯一（用作 Future Key） |
| task_id（视频生成） | 字符串 | 异步任务周期 | 单 (user_id, provider) 内唯一 |

**职责分立**：`session_id` 是 `conversation_id` 的字符串形式，跨 WS 重连不变；`call_id` 标识一次调用，等待表按用户隔离，见 §4。会话类型、目标归属与 IM 唯一性分别按 §1.8、§1.7 定义。

## 7. 跨模块语言规则

用户语言由 Backend 的 `language` 设置维护，Client 持镜像，经 §2.4 的同步管道保存与水合。它是用户偏好；时区是客户端连接时上报的环境配置，两者不能共用一次性信号语义。

客户端切换语言后立即刷新界面、托盘和默认媒体语言，无需重启。后端从下一次装配读取新值，已经锁定输入的回合不在中途更换语言；未知值回落默认中文。支持语言分别由 [Backend 常量](../backend/components/constants.py) 的 `SUPPORTED_LANGUAGES` 和 [Client locales](../client/renderer/shared/strings/locales.ts) 的 `SUPPORTED_LOCALES` 定义，双方必须一致。

新增语言须核对客户端字典、后端提示词及独立结构化推理、语音目录和媒体调用，不假定所有场景共用一个固定提示词块数。陪伴正文、心情、视觉与空间输出的边界统一按 [§1.4](#14-聊天心情视觉表达与空间契约) 维护。

## 8. 维护规约

- 契约变化先识别生产方、消费者、持久化及恢复路径，在同一变更同步相关实现、本文定义和必要引用；内部实现未改变契约时不必修改本文。
- 根据实际消费者判断兼容性。新增可选字段通常可兼容，但新增枚举、默认值变化也可能影响旧客户端；删除、改名或收紧约束须说明受影响版本及升级方式。需要新旧版本并存时提供明确迁移边界，不为可一起更新的内部调用保留无效兼容层。
- 枚举、字段和注册表链接源码定义，本文维护语义及联动要求。共享值变更须检查后端校验、客户端映射、Runner 能力和持久化，不能仅更新一张文档清单。
- 新设置按所有权决定是否进入同步白名单，设备派生状态与机密字段不得因方便同步而上云。验证相应的保存、水合、换号与恢复路径。
- 验证深度与变更风险相称；文档调整核对事实、路径、锚点和章节号，协议实现变化覆盖相关消费与失败恢复。仓库级验证遵循 [RULES](../RULES.md)，子模块 README 链接本文对应主题。
