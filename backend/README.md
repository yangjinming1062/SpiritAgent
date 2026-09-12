# Backend

## 1. 职责与边界

云端持有角色定义、记忆与资产，编排对话、云端工具、调度和事件；本机执行委托 Runner，窗口与渲染交给客户端。物理边界见 [ARCHITECTURE.md §1](../docs/ARCHITECTURE.md)，生成链见 [PIPELINE.md](../docs/PIPELINE.md)。本文件是 backend 的唯一 README：先读它理解分层与设计思路，再动代码。

## 2. 设计意图

- 对话入口统一编排上下文、模型与工具，避免不同入口（桌面、IM、Cron、子 Agent）形成互不兼容的回合语义。
- 消息持久化保留角色语义，只在模型调用边界转换为 Responses 指令区与输入项，避免供应商协议侵入数据库设计。
- 高成本生成采用异步编排、用户级互斥与频控；付费任务优先恢复已有产物，避免重启或重试重复计费。
- 数据库是持久状态与异步交付的衔接点；投递架构见 [ARCHITECTURE.md §5](../docs/ARCHITECTURE.md)。
- 服务端分层的目标不是消灭跨域协作，而是让依赖方向可检查：业务能力自洽、跨域流程集中、技术实现可替换、装配显式唯一。

## 3. 架构设计

### 3.1 物理布局与依赖总览

backend 是一个单进程（web 进程）asyncio 应用，uvicorn 入口 `main:app`（[Dockerfile](Dockerfile)）。代码按职责分成四个地带，依赖只允许自上而下：

```text
main.py ──→ bootstrap（唯一装配入口）
bootstrap ──→ api / services / components
api（HTTP·WS 入口，薄适配）──→ services
services：contracts ← domains ← application ← adapters，domains/application 可用 infrastructure
modules、components、common ＝ 共享底层，任何层可用、禁止反向导入业务
alembic/ 迁移独立于应用代码，只被启动流程调用
```

共享底层三件套的分工是刻意的，改代码前先分清归属：

- **modules/**（auth、conversation、companion、memory、media、scheduler、settings、system、channels、update）持有领域模型与协议 Schema。它按数据库与契约"拥有"行结构和载荷定义，业务代码引用它而不重复定义；它不知道哪些业务在用它。加字段 / 加枚举从这里开始，迁移与之同提交。
- **components/**（config、database、logger、background、attachments、hashing、correlation、user_maintenance_runtime 等）是进程级运行时设施：`SETTINGS` 配置单例、异步引擎与会话、TaskBag/BackgroundTask 任务托管、日志与相关 ID。它无业务语义，是单副本 eager-import 设计的地基；新增横切设施放这里，而不是在各业务包里自造单例。
- **common/** 只有 api.py 与 model.py：路由声明、ORM 基类、列表响应等极少量框架工具，保证所有路由的声明方式与分页响应形状一致。

运行时周边：`static/admin.html` 是管理台单文件页，`updates/` 与 `data/` 是挂载卷（自更新产物与附件根目录），`monitoring/` 供 Prometheus 抓取配置，容器编排见 [docker-compose.yml](docker-compose.yml)。

### 3.2 services 五层

`services/` 按五层组织，每层回答一个问题：

| 层 | 回答的问题 | 红线（违反即架构 BUG） |
| --- | --- | --- |
| `contracts/` | 跨层传递的最小词汇（目前仅 `DelegateAction`） | 不导入任何服务实现 |
| `domains/` | 单一业务能力长什么样：conversation（会话底座）、memory、journal、companion、media、automation、configuration、backup | 不依赖 application / adapters；跨域只经公共入口且仅限下述登记例外 |
| `application/` | 跨域流程怎么走：chat（回合编排）、generation（形象/房间/2D/3D/媒体）、automation（Cron 两轨回合）、nightly（夜间整理与规划）、configuration（配置提交与热更）、updates | 不导入 adapters；包间只允许显式声明的单向边 |
| `infrastructure/` | 技术能力怎么实现：llm、image_to_3d、seethrough、assets、web、tool_runtime、desktop（连接/IPC/JSON-RPC）、event_store（outbox） | 不认识业务编排——不导入 domains / application / adapters，这是全系统最重要的方向不变量 |
| `adapters/` | 外部协议如何进来：desktop（WS handlers）、channels、scheduler、tools、http、maintenance | 只做适配与入口编排，不沉淀业务规则 |

依赖方向：`adapters → application → domains`，`domains`/`application` 可引用 `infrastructure`，全部层可引用 `contracts`。三条结构性规则由 [check_services_architecture.py](../scripts/check_services_architecture.py) 强制（环检测、层白名单、域隔离），改导入前先跑它：

- **application 包间只允许显式单向边**：automation → chat / nightly、chat → nightly（回合后整理）、nightly → generation。加新边必须同时改检查脚本的 `APPLICATION_FLOW_EDGES` 与本文件。
- **域间例外**（脚本例外表的权威）：各域可单向导入 conversation——它是一切回合与叙事的会话底座且零反向依赖；companion / journal 只读 memory 的时区解析；companion 经 bootstrap 注入的钩子触发 generation（onboarding 首张房间图），业务域自身不反向依赖应用流程。
- **接缝例外**：generation 内部 mesh2d 子包与 infrastructure/seethrough 双向直达（2D 拆分链一体两面），是唯一允许的跨层直达。

### 3.3 api 入口

`api/__init__.py` 约定式自动发现 `v1/*.py`（`router = get_router()` 即注册），新增路由零登记成本。api 保持薄适配：鉴权依赖、限流装饰器、DTO 组装，不写业务规则；桌面 WS 路由挂在 `api/v1/chat.py`，全部会话 RPC 方法注册在 `adapters/desktop/handlers.py`；管理台是 `static/admin.html` 单文件页，由 api 的免鉴权路由与静态挂载提供。

### 3.4 配置与迁移体系

- **冷启动与动态配置分离**：`config.toml`（模板 [config.toml.example](config.toml.example)）或环境变量只提供启动硬依赖，业务参数持久化在 `system_settings` 表并热更新。启动水合与管理端保存共用同一条路径：整批验证 → 原位应用 `SETTINGS` 单例 → 副作用刷新（日志、LLM 连接池），验证失败不落任何变更；`DynamicLimiter.enabled` 直读 SETTINGS，限流开关无需进程内赋值。
- **Alembic 启动升级**：单实例部署在启动时升级到 head，减少漏迁移步骤；未部署允许改 baseline，部署后只追加。迁移须可降级、回填幂等；PostgreSQL 部分唯一索引、向量与全文索引只在迁移里维护，不塞进模型 metadata 以免自动生成误删。

### 3.5 装配与生命周期

实例创建、注册与启停顺序全部集中在 `bootstrap/`，业务包导入不产生任何副作用：

- **显式注册**（`bootstrap/registrations.py`）：LLM 供应商、图生 3D 供应商、LLM 工具与 memory 工具 schema、渠道适配器、内部事件处理器（`cron.turn.request` → `execute_cron_turn`）、域钩子（首房间图调度）。新增供应商/工具/渠道 = 实现类 + 装配层加一行注册；遗漏会在能力链解析时显式抛 `LookupError` 而非静默缺能力。注册表覆盖式幂等，重复调用安全。
- **启动顺序**（`bootstrap/lifecycle.py`）：迁移 → 配置水合 → 注册（应用导入期）→ 调度器 → 事件回路（LISTEN 专线）→ 渠道桥 → 恢复未完成任务（视频、3D 管道）。依赖注入式解耦：事件回路不认识 cron 业务，处理器由装配层绑定。
- **停止顺序**：先停调度器再 drain（tick 会 spawn 新任务，反序留下逃逸窗口）→ 并行 drain 各模块任务集合 → 停渠道桥（适配器任务可能还在写事件）→ 停事件回路 → 释放引擎与连接池。付费生成任务的恢复语义不变：无法确认提交结果的任务保留不确定状态，不自动重发。
- **运行时单例**：`MANAGER`（桌面连接）、`REGISTRY`（工具）、`SETTINGS`、TaskBag、用户级锁表保留为模块级单例——这是单副本语义（[ARCHITECTURE §5.3](../docs/ARCHITECTURE.md)）下的刻意选择；跨副本状态一律经持久化与 outbox 路由外置，bootstrap 管"谁注册谁启动"，不做 DI 容器。

### 3.6 事件与交付回路

业务代码只往 outbox 写行（`modules.ws.emit_ws_event`，同一事务提交），事件回路负责其余：PostgreSQL NOTIFY 唤醒 → 原子认领（`FOR UPDATE SKIP LOCKED`）→ 按事件类型分派——已注册处理器的内部事件（cron 回合）就地 spawn 强引用 task，其余事件经该用户的 JSON-RPC dispatcher 投递；失败指数退避、超限死信、writer 送达确认批量落库、历史行独立 GC。存储层不知道任何业务处理器；通道桥投递与交付原子性见 [PROTOCOL §1.2 / §1.8.1](../docs/PROTOCOL.md)。

## 4. 关键设计决策

### 对话、上下文与记忆

- 演绎描述同次生成：生活空间用户回合先按已选音色与当前模型装配完整能力说明，再以有界隐藏头表达供应商专属语音意图（MiMo 隐藏头还承载角色、场景、指导三部分导演描述），随后继续流式输出台词，避免二次推理增加首音延迟。句内语气词通过唯一正文短语定位，未找到或不唯一定位不插入声音；只有保留的隐藏头被解析，普通括号、Markdown 与角色台词原样保留。交付契约见 [PROTOCOL §1.4](../docs/PROTOCOL.md)。
- 心情独立更新：仅用户发起的生活空间 companion special 回合在终端正文落库且 `message.complete` 下发完成后，才后台调度 `domains/companion/mood.py` 做一次结构化状态推理；结果写 `persona.current_mood` 并发出 `companion.mood`，不写 Message、不进正文。工作预设、主动 Cron、普通自动化与 IM 回合不触发。
- 迭代预算双层：`IterationBudget` 计数（上限 `AGENT_MAX_LOOP_TURNS`）+ `ToolCallGuardrailController` 的重复失败/无进展提前退出，任一触发即停。
- 供应商回退边界：`execute_with_fallback` 的 `on_first_chunk` 哨兵防止 mid-stream 切换供应商——一旦开始向渲染端流式输出就锁死当前供应商，避免同一回合混合两个模型的输出。
- 视频附件：HTTP 上传、WS 只引用会话 URL，避免视频突破帧限制；仅接收 mp4/mov，供应商实测拒绝 webm。未配置公网地址时内联最近视频，配置后让供应商拉 URL。会话滚动配额与压缩 / 日总结清理须同时替换消息中的视频引用，避免数据库、渲染与上下文产生死链；限额与清理入口见 [chat_videos.py](services/domains/media/chat_videos.py)。
- 双压缩检查点：运行时摘要与主对话夜间日总结同为读起点，旧摘要融入新总结；原历史留库。只有主对话允许用工具摘要替换中间帧，普通对话跳帧会丢工作上下文。
- Token 估算：以上轮真实用量为基线，只估新增中西文与图片；冷启动或主对话裁掉工具帧时全量重估。异构供应商不共享分词表，固定字符比例又会低估中文。
- 工具渐进披露：初始只挂元工具，按域解锁 schema；从未压缩历史继承已解锁工具，不改历史或逐轮重拼系统提示词，兼顾体积与前缀缓存。
- 工具集开关：后端 / 记忆工具在注册表读取口过滤，畸形禁用值按空过滤处理；Runner 工具由客户端源头过滤。每回合重读设置，保存后无需重连；枚举见 [PROTOCOL §2.2](../docs/PROTOCOL.md)。
- 工具执行安全网：文件写入黑名单前置 block、回合内重复精确失败与幂等无进展由 guardrail 合成结果并追写指导、批内并发按幂等性判定；子 Agent 委派经 `DelegateAction` 由对话执行层接管（见 §3.1），工具处理器不反向重入对话入口。
- 生成媒体：仅提取成功工具结果并随终端助手消息落库，多气泡挂末格，后台视频另建送达行；不能依赖模型在正文贴 URL。
- 推理过程：独立于正文持久化并只在工作台展示；不回灌下一轮 Responses 输入，避免供应商推理协议污染后续上下文。
- 时间感知不落库：陪伴对话的时间元数据不写进消息，跨轮按发送时刻重建以保留 prefix cache；陪伴预设的系统提示词不含当前日期，并要求模型只输出角色台词。
- 气泡边界：陪伴预设的空行与专用分隔行共用流式切分，跨 chunk 暂留可能属于较长分隔符的前缀，避免把分隔线泄漏为正文；流式 chunk 在 5–10ms 批窗口内合并，break 与 message.start 立即发出。专业预设不按空行切分。
- 推理设置隔离：回合与手动压缩共用会话设置合并入口；普通会话按种类继承工作台默认，特殊会话按预设目录取场景默认，不能以模板标识非空替代会话种类判断。作用域及窗口水合见 [PROTOCOL §2.4](../docs/PROTOCOL.md)。
- 长期记忆：向量与关键词 / CJK N-gram 双路召回、RRF 融合，叠加重要性和不归零的时间衰减；写入召回池同步补向量，主对话前自动注入相关记忆。嵌入未配置或维度不匹配时降级关键词检索。
- 时刻与日记：检索记忆与展示切片分别维护；主动时刻受每日配额约束，工作预设禁止记录。日记按用户本地日归集，用户编辑后的内容只能追加，夜间整理静默交付。
- 互动统计：戳击反应有成本上限，失败另有短冷却；戳击或对话任一达到门限即写小时汇总，日期与夜间反思共用用户本地日口径。

### 夜间批处理

- 活动规划：休息窗口内统一执行画像、记忆整理、自主规划与日记；白天阈值整理与夜间整理按用户互斥，LLM 返回后仅在源记忆快照未被用户编辑或删除时以短事务替换。规划使用高推理档和当时可用的安全能力目录，输入角色、衣橱、房间、供应商、画像、近期历史与政策，允许小组合或空计划。无当日消息仍可基于生日等长期记忆规划；无新互动的画像 / 日记步骤跳过，不能伪造经历。
- 执行相序：外观 → 房间 → 片刻 / 媒体 → 主动联系；各项独立失败、执行前重读政策。新装成功激活后才进入后续生成，角色图片使用身份与当前外观双参考，视频使用当前外观首帧，可附当前音色配音并转存正式资产。
- 日期与恢复：调度单点传入“刚结束的本地日”，缺时区跳过用户；同日完成不重跑，最近未完成日可在休息窗口外恢复，超过一日本地恢复窗口的未确认动作终止。保存计划与动作账本，有可核对任务 id 的续跑原记录，无法确认的在途副作用标记中断，不重复付费。
- 成功事实才进入自传记忆、日记与次日问候；标题、正文及配音遵循用户语言。片刻与对话交付原子性见 [PROTOCOL §1.2](../docs/PROTOCOL.md)。

### 资产与外观

- 身份锁定在服务端执行：确认全身图时草稿转正式资产；锁定后所有形象重生成入口拒绝，角色编辑保留既有物种、性别和基础外貌且不重置确认态，防止绕过 UI 破坏身份并重复付费。
- 草稿可恢复、确认才转正：持久化草稿路径与画风，转存失败可重试；未确认样图可整组丢弃，提前转正会累积孤儿资产。恢复契约见 [PROTOCOL §1.2](../docs/PROTOCOL.md)。
- 画幅稳定：骨架分类是模型推理，首次分类后随形象持久化，重绘与换装复用，避免多次分类翻转画幅。种子图画风、姿态、参考图及双供应商拆分策略统一见 [PIPELINE](../docs/PIPELINE.md)。
- 3D 能力链独立于通用 LLM 链，仍复用供应商注册机制；长任务与 web 进程同生命周期，启动扫描未完成记录接续，恢复见 [PIPELINE §3](../docs/PIPELINE.md)。
- 2D 拆分编排（mesh2d）：分层拆分的供应商传输在 `infrastructure/seethrough`，生成任务、优先级队列、资产落库、外观激活与就绪事件归 `application/generation/mesh2d`；完整包校验通过才发布。自主视觉动作白名单由 `domains/companion/actions.py` 维护（`DEFAULT_ACTIONS` / `NON_LLM_ACTIONS`），变更须同步客户端动作兑现与 [PROTOCOL §1.4](../docs/PROTOCOL.md)。
- 2D 任务排队：render_mode='3d' 的后台 2D 拆分用 low 优先级、render_mode='2d' 用 high；不同资产任务独立排队，发布时核对当前身份与穿着意图，避免较晚完成的旧任务覆盖用户选择。
- 2D 失败兜底：生成失败不激活新模型、不自动穿着新外观；已有可用模型继续服务，无可用模型时走客户端渲染级联（puppet → 3D → 程序化蛋，永不空白）。
- see-through 拆分供应商：只做传输不做拆分逻辑——调 see-through 在线 Space 把立绘拆成 22 个语义层 PSD，主用 Hugging Face Space、魔搭创空间备用（API-Inference 专用域 + Bearer token，www 代理域不可匿名）。失败统一抛 `SeeThroughError` 由编排层落失败态；参数必须传 Gradio FileData 对象，裸路径字符串被静默拒收。产物只是分层 PSD 字节，后端不解析 PSD、不依赖 psd-tools；完整资产包生成与发布由 mesh2d 负责。切换、冷却与共享预算见 [PIPELINE §6](../docs/PIPELINE.md)。
- 换装两段激活：新建外观时保留当前成功行，新包成功落库时才原子翻转；用户中途手动换装会清除自动穿着意图。校验和所有翻转共用用户级锁，避免并发确认产生重复付费任务；激活查询只认成功行。
- 衣柜预览：列表批量查询每套外观最新的成功 2D 行，复用桌面资产签名与序列化；查询按用户及外观归属隔离，无需激活即可预览，契约见 [PROTOCOL §1.2](../docs/PROTOCOL.md)。
- 衣柜归属：首次访问懒建初始外观并回填归属；初始立绘归头像行，删除外观不能删其文件。换装不受身份锁阻挡，服装 / 发型可换，五官 / 物种 / 性别仍由头像锚定；独立小时频控，不限衣柜数量。
- 着装感知：后台生成着装描述，失败不阻塞就绪；对话与触摸反应共用当前着装上下文，性格主导行为、着装只调节表达。
- 房间生成：性格须显式进入房间简述，参考像素不能表达性格；普通会话生图不得修改激活背景。在线换房受用户锁、日配额与打扰档位约束，夜间共用互斥和政策锁但不占在线配额；换装失效及保护规则见 [PROTOCOL](../docs/PROTOCOL.md)。

### 配置、数据库与运行可靠性

- 特殊会话场景默认值由会话预设目录单源维护，推理与窗口水合共用同一合并逻辑；普通会话即使选择系统模板也保持普通配置作用域，契约见 [PROTOCOL §2.4](../docs/PROTOCOL.md)。
- 备份不承载部署恢复：登录态、激活信息、IM 绑定 / 授权、事件队列及后台执行账本不迁移，不保证在途任务跨部署接续；系统级供应商配置与本机配置仍需在目标部署单独配置。源端已清理的历史媒体无法从数据库还原，备份只携带现存文件。恢复时仅 `kind='special'` 的系统会话按预设 ID 去重，带同一预设的普通会话始终独立映射。覆盖恢复先建立用户级维护边界：拒绝新 REST/WS 请求，等待已进入的请求，取消可中断的网关、IM、调度与整理任务，并等待已付费生成自然落地；成功或回滚后清除旧运行时缓存并从数据库重启渠道。上传 ZIP 上限 500 MB，解压上限 4 GB。
- 冷启动与动态配置分离：文件只提供启动硬依赖，业务参数在管理端持久化并热更新，启动从库水合。文件优先级以 [config.toml.example](config.toml.example) 为准，动态项随后由数据库覆盖。
- 供应商显式注册：由 `bootstrap/registrations.py` 集中登记（连同 LLM 工具、渠道适配器与内部事件处理器，见 §3.2）；按供应商 / 客户端 / 带回退执行三个入口选用，不从 URL host 反推供应商。
- 供应商信息与能力链分离：共享凭据不隐式启用核心能力，名称作为唯一关联标识；嵌入按供应商顺序筛选并使用对应模型。继承、覆盖与密钥规则见 [PROTOCOL §5.4](../docs/PROTOCOL.md)。
- Alembic 启动升级：单实例部署在启动时升级，减少漏迁移步骤；未部署允许改 baseline，部署后只追加。迁移须可降级，回填幂等；删除或不可逆收紧须拆分并说明风险。
- 迁移比对：类型与默认值必须零差异；迁移环境对视频任务模型的显式导入不可删。PostgreSQL 部分唯一、向量及全文索引仅在迁移维护，不塞入模型 metadata 导致自动生成误删。
- 数据访问：统一异步连接池，事件监听独占可重连直连；关系显式预加载，时间戳必须带时区。数据库会话不跨 LLM 等待，后台任务拆成短会话读 → 无会话推理 → 短会话写。
- 关联与断线：按用户和调用 id 隔离未决响应；短断线保留生成与缓冲，宽限到期回收孤儿任务。恢复及在线要求见 [PROTOCOL §0 / §4](../docs/PROTOCOL.md)。
- 登录撤销：WS ticket 绑定有效登录记录，入站帧持续复核；注销、替换登录与用户停用共用即时回收入口，凭据正常刷新则沿用同一登录生命周期。契约见 [PROTOCOL §0](../docs/PROTOCOL.md)。
- 本机派发：先注册待响应对象再发送，并直接持对象等待，防止极速结果到达后条目已弹出。检查入队结果，不能调用吞异常且无成功返回值的直推入口，否则断线会白等超时；设备指令与回合展示分离见 [PROTOCOL §1.3](../docs/PROTOCOL.md)。
- 离线不等于取消：桌面消失时以离线错误完成未决响应；取消异常会穿透普通异常处理，令 IM / 对话静默死亡。真正的任务取消保留取消语义。
- Outbox 清理独立于派发：按在线连接原子认领、成功标记投递、失败指数退避、超限入死信；调度器批量回收历史行、死信与孤儿内部事件，避免清理拖慢交付。
- Cron 双轨：上下文、在线要求与原子交付见 [PROTOCOL §1.8.1](../docs/PROTOCOL.md)；两轨均禁止主动发消息工具，由编排统一捕获终态交付。
- Cron 活跃配额：运行期创建、恢复及修改 schedule 解除暂停共用用户行事务锁下的配额检查，候选任务不重复计数；夜间后台创建走同一入口，避免并发请求突破用户上限。
- IM 适配器：进程内自注册，无头回合从原渠道回复；适配器声明渠道能力，统一处理 Markdown 清理与分片。绑定状态落库与实际变化通知共用入口；非致命故障由守卫退避重建，单 web 进程无需额外单例锁或周期对账。登录、入站分发、回合与 typing 子任务均归绑定实例所有；登出、删除、重建和进程关闭会取消并等待整棵任务树，排队 future 同步落地。
- iLink 登录与媒体：适配器管理扫码，成功后重建凭据与游标并自动授权主人；媒体经 CDN 加解密、转存临时媒体后输入回合，产出媒体加密回送，文字与附件合并单条消息。配对、只读会话及桌面在线边界见 [PROTOCOL §1.7](../docs/PROTOCOL.md)。
- 错误恢复：有限分类决定重试、轮换凭据、压缩或终止；内容风控不可重试，鉴权关闭码不靠重连恢复。生成错误使用可重试的友好提示，供应商 URL、认证头与堆栈不能透传。
- HTTP 重试：幂等方法、显式幂等键及确认未发送的连接失败才可自动重试，请求体须先缓存为可重放字节。非幂等请求在写入或读取响应阶段断线时标记结果不确定，不回退供应商；异步视频提交保留本地任务行并禁止自动重发。
- SSRF 豁免：默认拒绝保留段，fake-ip 代理部署须显式配置网段豁免；豁免仅跳过保留段检查，域名、协议、HTTPS 降级、云元数据与 CGNAT 拦截保留。
- LLM 调试：日志集中在聊天包装、回退链与嵌入入口，避免供应商重试叠加造成漏记；开启方式与内容暴露范围见 §7。

## 5. 与外部的契约

- 对客户端：生命周期、媒体与叙事交付见 [PROTOCOL §1](../docs/PROTOCOL.md)，配置与凭据见 [§5](../docs/PROTOCOL.md)。
- 对客户端 / 调度入口：IM、系统预设、Cron 双轨见 [PROTOCOL §1.7–1.8.1](../docs/PROTOCOL.md)；在线与副本边界见 [ARCHITECTURE §5](../docs/ARCHITECTURE.md)。
- 对客户端 / Runner：工具集与反向 RPC 见 [PROTOCOL §2–4](../docs/PROTOCOL.md)。
- 对供应商 / 渲染端：生成输入、能力与资产兑现见 [PIPELINE](../docs/PIPELINE.md)。

## 6. 已知限制

- 系统预设尚无隐藏 / 禁用开关，全部预设作为入口展示；列表契约见 [PROTOCOL §1.8](../docs/PROTOCOL.md)。
- web 部署边界见 [ARCHITECTURE §5.3](../docs/ARCHITECTURE.md)；启动迁移没有并发锁，限流默认进程内，生成长任务共享事件循环带宽，水平扩展需同时评估这些约束。
- MiniMax 新版视频轮询直接返下载 URL，Hailuo 另需取文件；URL 均短时效，须立即下载到临时媒体，不能原样返前端。
- 陪伴型 Cron 离线触发由孤儿事件清理丢弃，下次调度时间已前移；普通自动化不受此在线守卫约束。
- 连发排队消息在上一轮助手落库后作为新回合批量写入，刷新后顺序按回合排列；IM 也合并为下一轮前导批，队列超限丢最旧消息。
- iLink 仅回复入站消息，不支持群聊；回复 token 过期需重新扫码，长任务甚至可能无法投递失败提示。IM 不进夜间日总结，运行时压缩仍生效。
- 桌面断线宽限窗内可能仍被视为可用，工具派发需等超时或宽限结束才收到离线错误。IM 对端白名单没有逐次授权层，批准对端即授予本机操作能力。
- see-through 为社区免费算力、无 SLA：失败/超时由编排层落失败态，无 CPU 兜底链，客户端按渲染级联降级并可在设置页重试；魔搭备用创空间休眠后首调含唤醒时间，由 900s 单次超时覆盖。

## 7. 部署与监控

### Docker Compose 部署

- **基础核心启动**（仅启动 postgres + backend）：
  ```bash
  docker compose up -d
  ```
- **附带 Prometheus 观测平台启动**（一键拉起指标采集）：
  ```bash
  docker compose --profile monitoring up -d
  ```
  启动后可直接访问 `http://localhost:9090` 打开 Prometheus 查询面板，指标默认每 15s 自动抓取 `backend:10620/metrics`。

### 参数配置与后台动态热更新

- **冷启动文件配置**：首次部署仅需根据 `config.toml.example` 复制创建 `config.toml`（或直接使用环境变量），指定 `database_url`、`jwt_secret_key`、`companion_asset_signing_key` 与管理员账密即可完成启动。
- **系统设置管理面板**：服务启动后，以管理员身份登录 Web 管理端（`/admin/`）切换至「系统设置」Tab，即可增删、拖拽排序供应商信息卡片与五项能力的调用卡片，也可编辑图生 3D 与 2D 分层资产、上下文压缩与会话、联网搜索后端、接口速率限制与安全配额、IM 渠道及运行时调优等参数。卡片引用、覆盖与空链语义见 [PROTOCOL.md §5.4](../docs/PROTOCOL.md)。
- **即时生效保障**：保存操作在进程内串行合并完整候选配置并复用启动配置模型整体验证；全部通过后才在单次事务中持久化，并整体替换进程内 `SETTINGS` 单例。验证或提交失败不改变数据库、内存或运行时副作用；启动水合同样整批验证，拒绝部分应用非法持久配置。接口限流、日志级别与 LLM 客户端连接池随后即刻刷新，**全程无需重启后端容器**。

### LLM 调试日志

排查对话失败时按需开启的开关。开启后每个 LLM 调用在专用 logger 上按调用粒度输出供应商、模型、请求/响应摘要、延迟与失败原因（字段与默认截断长度见 `llm_debug.py`）。默认关闭——开启会把对话内容原样落到 stdout，仅在复现失败时临时启用。注意两点：事件以 DEBUG 级别发出，需同时设 `log_level = "DEBUG"` 才可见；日志落在实际执行调用的容器里。
