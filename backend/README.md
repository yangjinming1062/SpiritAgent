# Backend

云端业务入口：对话、角色与记忆、资产制作、调度、远程网页和持久化。模块分工、状态权威与部署限制归 [ARCHITECTURE](../docs/ARCHITECTURE.md)，跨端行为归 [PROTOCOL](../docs/PROTOCOL.md)，媒体制作归 [PIPELINE](../docs/PIPELINE.md)。本文维护代码导航、后端内部约束与部署操作。

## 任务入口

| 任务 | 入口 |
|---|---|
| 对话、语音、上下文与工具循环 | [对话编排](workflows/chat/README.md) |
| 记忆召回、证据、编辑与遗忘 | [记忆领域](features/memory/README.md) |
| 头像、角色卡与衣柜 | [外观](features/appearance/README.md) |
| 环境素材制作与重生成 | [场景](features/scenes/README.md) |
| 动作素材定义与评审 | [动作素材](features/action_library/README.md) |
| 影像组合、制作与呈现 | [影像](features/visuals/README.md) |
| 公共媒体制作与复核 | [媒体](features/media/README.md) |
| Cron、主动陪伴与夜间 | [cron.py](entrypoints/scheduler/cron.py)、[定时任务数据](features/automation/cron_jobs.py)、[业务调度](#业务调度) |
| 主动信息检索、来源分享与反馈 | [话题模块](features/topics/README.md)、[夜间检索](workflows/nightly/topic_discovery.py) |
| 动态与日记 | [动态](features/posts/README.md)、[journal_service.py](features/journal/journal_service.py)；REST 在 `entrypoints/http/routes/companion_posts.py`、`companion_journal.py` |
| 手机扫码与远程访问 | [远程网页](#远程网页)、[remote.py](entrypoints/http/routes/remote.py) |
| 激活、登录、WS 票据与凭据 | [user.py](entrypoints/http/routes/user.py)、[网关](entrypoints/http/routes/gateway.py)、[账户](features/accounts/)、[远程身份](features/accounts/remote/README.md) |
| 管理后台、配置与用户维护 | [admin.py](entrypoints/http/routes/admin.py)、[后台结构](static/admin.html)、[交互](static/admin.js)、[样式](static/admin.css)、[配置与迁移](#配置与迁移) |
| 供应商与任务链 | [ai_tasks.py](infrastructure/ai/tasks.py)、[registrations.py](bootstrap/registrations.py)、[providers/base.py](infrastructure/ai/providers/base.py)、[llm_client.py](infrastructure/ai/llm_client.py) |
| 桌面配置同步 | [config.py](entrypoints/http/routes/config.py)、[desktop_config.py](features/configuration/desktop_config.py) |
| 账户资讯来源与扫码登录 | [social_accounts.py](entrypoints/http/routes/social_accounts.py)、[账户来源](features/configuration/social_accounts.py)；采集入口见[话题模块](features/topics/README.md) |
| 附件、上传视频与语音 | [media.py](entrypoints/http/routes/media.py)、[sessions.py](entrypoints/http/routes/sessions.py)、[chat_attachments.py](features/conversation/attachments.py) |
| 正式资产写入、签名与回收 | [asset_store.py](infrastructure/assets/asset_store.py)、[资产领域](features/assets/README.md) |
| 桌面与 Runner 更新分发 | [update.py](entrypoints/http/routes/update.py)、[update_releases.py](features/updates/releases.py) |
| 服务更新（后端一键更新） | [updater/](updater/)、[service_updater.py](infrastructure/web/service_updater.py)、[service_operations.py](features/updates/service_operations.py)；接入步骤见[服务更新](#服务更新) |
| 数据库设计、关系与查询性能 | [数据库设计](../docs/DATABASE.md)、[ORM 注册](bootstrap/models.py)、[完整基线](alembic/versions/0001_baseline.py) |
| 备份与覆盖恢复 | [admin.py](entrypoints/http/routes/admin.py)、[backup](workflows/maintenance/backup/)、[maintenance.py](workflows/maintenance/user.py) |
| 提示词、装配与任务恢复 | [提示词索引](#提示词定位)、[bootstrap/lifecycle.py](bootstrap/lifecycle.py) |

## 代码与依赖

| 目录 | 职责 |
|---|---|
| `main.py` / `bootstrap` | 薄入口、模型/路由/供应商/业务端口的显式装配、启停与恢复 |
| `entrypoints` | HTTP、桌面与远程 WS、工具和调度入口；解析、鉴权、调用及错误转换 |
| `workflows` | 聊天、初始化、主动与定时回合、夜间、账户删除及备份恢复等跨业务编排 |
| `features` | 各业务的数据、规则、公开操作与查询、提示词、持久任务状态及恢复 |
| `runtime` | 账户回合与锁、连接和调用关联、任务监督、事件交付与公共运行时类型 |
| `infrastructure` | 数据库、配置加载、网络、供应商、文件和媒体处理 |
| `alembic` | 完整空库基线与结构版本入口 |

### 模块依赖边界

允许的依赖方向：`entrypoints → workflows / features / runtime / infrastructure`，`workflows → features / runtime / infrastructure`，`features → runtime / infrastructure`，`runtime → infrastructure`。反向一律禁止：基础设施不导入业务与运行时，业务不导入流程与协议入口，流程不导入入口与装配，入口不导入装配。双向协调放入流程，反向通知使用 bootstrap 登记的明确回调。各模块直接从能力所属文件导入，不经包级 facade。

ORM 留在数据所属模块。跨模块读取使用冻结 DTO 或只读快照，写入使用公开操作；普通用例管理短事务，需原子完成的跨模块流程通过各模块提供的事务内操作共享同一事务。供应商等待始终在事务外。Pydantic 用于校验结构，dataclass 用于进程内值对象。

[JobRecord](infrastructure/job_models.py) 是共享任务存储契约：业务持久模型可继承并拥有自己的 joined-table 详情，jobs 模块负责通用任务操作。此基础类型依赖不允许访问另一业务的详情 ORM。动态和日记正文、评论及附件统一归 entries；资产、任务、事实与投影的完整关系见 [数据库设计](../docs/DATABASE.md)。

业务实现依赖保持单向；例如动作读取外观和媒体，动态使用媒体并读取陪伴/记忆，陪伴读取记忆和会话证据。媒体不读取动态表或修改消息，分别通过显式任务关联和事务内结果处理器交付。角色就绪和影像动作等协作端口由 [registrations.py](bootstrap/registrations.py) 装配。纯 DTO、枚举与静态词表不承担业务操作。

[依赖检查器](../scripts/check_services_architecture.py) 检查目录方向、模块与业务实现环、跨业务 ORM 和私有入口、公开返回 ORM、入口 SQL、禁用目录及缺失导入。新增公开依赖须说明数据所有者和业务理由；检查器的通过不替代事务、权限和行为验证。

### API 入口

[routes/__init__.py](entrypoints/http/routes/__init__.py) 显式登记资源路由及其前缀、名称与响应结构。`/api` 同时用于资产、备份、限流及客户端，不能作为部署配置修改。文件与业务错误由协议适配层转换，业务操作不返回 FastAPI 响应对象。

[body_limit.py](entrypoints/http/body_limit.py) 在解析前读取路由策略限制请求体，实际流超限也返回 413，大载荷入口先验证当前登录；聊天正文与批量图片使用独立预算。业务操作统一由 HTTP 受理，路由登记稳定操作标识，身份、编码和等待策略生成到跨端契约。WS 的 [handlers.py](entrypoints/desktop/handlers.py) 只处理连接控制、工具同步与回执、桌面情境信号；斜杠确认和执行由会话业务操作负责，Client 只镜像补全和确认元数据。管理页面可加载不代表管理 API 免鉴权。

## 配置与运行生命周期

### 配置与迁移

管理后台由 `admin.html`、`admin.js`、`admin.css` 组成，经 `page.py` 的固定资源入口提供，无独立构建步骤。AI 配置与系统参数分别维护草稿和保存状态；切页保留草稿，切换 AI 配置对象时处理未保存修改。图形化调用链只维护已有模型引用，来源解析与总览配置检查共用继承逻辑，不代表供应商实测结果。

冷启动配置依次取环境变量 → `backend/.env` → `config.toml` → [config.toml.example](config.toml.example)，文件按 Backend 目录定位。数据库、JWT、资产签名密钥、管理员凭据与数据目录属于启动配置；密钥或管理员密码为空、仍是示例值时拒绝启动。字段校验和默认值以 [Settings](infrastructure/config.py) 为准，资产签名密钥至少 32 字符。

运营参数由 [system_settings.py](features/configuration/system_settings.py) 管理：串行合并候选值 → 整批校验 → 事务提交 → 原位更新 `SETTINGS` → 刷新日志或连接池。数据库动态值在启动时再次覆盖环境变量和 TOML；启动专用键不能从后台修改。校验或提交失败不改运行时，非法持久值中止启动。保存接口执行数值边界校验，管理页输入范围只作提示。

用户点键配置只经 [settings/values.py](features/configuration/settings/values.py) 批量读写；值编码和模型配置的存储边界见 [配置字段字典](../docs/DATABASE_FIELDS.md#账户配置与设备身份)。

[0001 完整基线](alembic/versions/0001_baseline.py) 面向空库，建立表、扩展、约束、索引、会话统计与 outbox 唤醒触发器。启动执行 `alembic upgrade head`，不自动清库或转换历史结构；备份只接受当前[恢复契约](../docs/PROTOCOL.md#备份校验与覆盖恢复)定义的格式。

模型与基线同步维护；核对内容包括类型、默认值、外键、索引谓词、操作符类和触发器，不能只依赖 autogenerate。基线降级仅用于隔离空库验收。

### 装配与启停

[registrations.py](bootstrap/registrations.py) 是供应商、工具、内部事件和域钩子的装配入口；导入业务包不注册能力，未登记能力明确失败。

[lifecycle.py](bootstrap/lifecycle.py) 依次执行安全配置检查与迁移、配置水合、模块准备、调度与事件启动、模块恢复、正式资产核查。参与者的准备、恢复、维护和关闭能力及顺序在 [runtime_lifecycle.py](bootstrap/runtime_lifecycle.py) 登记。

停机先关闭生产入口，收敛已受理回合和各模块任务，再释放数据库、Web 供应商和 LLM 连接池。[task_supervisor.py](runtime/task_supervisor.py) 统一登记所有者、账户、单飞任务和错误回报；业务拥有任务状态机。任务取消、等待与写盘线程收尾共用 [async_io.py](infrastructure/async_io.py)，重复取消等待方也须等目标释放资源后再传播。

HTTP、调度与资产清理共用 [user_maintenance.py](runtime/user_maintenance.py) 的请求租约，退出时保护计数释放。启动与分钟清理按账户依次回收遗留视频和正式资产，维护中的账户留待下轮；小时清理逐项隔离失败，避免一项失败跳过其他清理。

[chat_accounts.py](runtime/chat_accounts.py) 持有账户受理回合；连接持有身份、ACK、重放和断线宽限。dispatcher 在断线时清空发送器，未写出的事件不记为送达，重连后补发；完整恢复语义见[序号与恢复](../docs/PROTOCOL.md#序号与恢复)。

### 事件与交付

事务内异步通知经 `emit_ws_event` 写 outbox，聊天流另走会话 emitter。持久化与端到端恢复语义归 PROTOCOL；实现入口为 [events/loop.py](runtime/events/loop.py)。

回路按本进程持有的桌面或远程 dispatcher 认领事件；只有远程连接时，依据[事件契约](entrypoints/desktop/event_contracts.py)的受众筛选精确事件名称，桌面执行与主动回合指令仍要求桌面连接（含断线宽限）。内部处理器派生任务即标记送达，投递失败按预算退避并转死信。LISTEN 专线探活、重连与周期扫描共同唤醒；[outbox_gc.py](runtime/events/outbox_gc.py) 不过期清理普通离线待投递行，过期主动回合请求另行处理。

## 数据与运行可靠性

关系显式预加载，时间戳带时区。请求 `DbSession` 与鉴权共用会话，响应结束才关闭。鉴权通过后提交只读事务，路由在等待用户锁、调用模型、打包及文件或流式下发前也须提交。`expire_on_commit=False` 保留已加载对象，不能用回滚代替提交；跨模块事务约束见[模块依赖边界](#模块依赖边界)。

大字节处理、FFmpeg 和文件复制卸载到工作线程。随机命名正式资产取消时删除未交接文件；预登记固定路径的生成资产取消时等待原子写完并保留，交由任务恢复。裸路径、签名、引用和回收由资产领域统一处理。

本机派发先注册等待对象，再发送并检查入队结果；直接持对象等待，避免极速返回后查表丢失。桌面离线以业务错误结束等待，不能用统一取消异常使对话回合静默退出。

覆盖恢复和删除用户先经 `user.py` 阻止新操作并通过统一生命周期登记收敛在途任务，完成后重读持久状态恢复可继续任务。用户文件只落正式资产 `companion-assets/{user_id}/`、会话附件 `desktop-attachments/{session_id}/`、带用户元数据的 `temp-media/`；新增路径须同时纳入删除和备份。删除正式资产或会话附件失败保留用户行供重试，临时文件另由逐文件和过期清理兜底。备份类别、密钥范围及覆盖规则统一见 PROTOCOL。

[backup.py](bootstrap/backup.py) 显式登记各模块的物理表贡献者；备份按依赖排序批量写入，任务详情复用共享父表主键，循环外键在完整关系图写入后连接。所选范围先预检再原子恢复，资产库存及任务身份合并保留；影像快照和日记索引由所属模块重建。备份流程只消费贡献契约，不直接访问业务 ORM。

## 业务调度

### 夜间批处理

`cron.py` 为活跃账户的陪伴域启动单飞流水线，关闭入口时取消并等待；专业预设的记忆维护另由定期审阅覆盖。执行入口 [nightly_activity.py](workflows/nightly/nightly_activity.py) 组织 [nightly_planning.py](workflows/nightly/nightly_planning.py)、[阶段账本](workflows/nightly/stage_state.py)和日记、反思执行；阶段、窗口、账本和恢复语义见 [夜间流水线](../docs/PROTOCOL.md#夜间流水线)。

### 陪伴调度与恢复

[companion/intents.py](features/companion/intents.py) 管理条件、认领与原子终态；[companion_turns.py](workflows/automation/companion_turns.py) 管理有界回合和提交；普通任务由 [standard_turns.py](workflows/automation/standard_turns.py) 串行执行。交付和副作用恢复归 [Cron 双轨](../docs/PROTOCOL.md#cron-双轨)。

生活判断由 [life_decisions.py](workflows/life_decisions.py) 编排；[scheduling.py](workflows/scheduling.py) 按生活复查时间发现到期账户，复用逐账户单飞与维护租约派发，慢账户不阻塞其他账户。[生活执行](workflows/life_execution.py) 推进已保存步骤，与夜间共用管理端配置的每日额度；[生活呈现](workflows/life_presentation.py) 只评估已有画面及静态素材。作用范围与资格见 [生活契约](../docs/PROTOCOL.md#生活状态与生活事件)。

### 远程网页

[remote.py](entrypoints/http/routes/remote.py) 管理扫码授权与设备会话；独立 [Remote](../remote/README.md) 网页直接复用账户内容接口和对话编排。设备会话独立于桌面登录，本机工具仍由桌面 Runner 执行。身份、撤权与多端恢复契约见 [远程访问](../docs/PROTOCOL.md#远程访问)。

## 供应商与网络错误

通用回退、安全重试与结果未知归 [模型失败与重试预算](../docs/PROTOCOL.md#模型失败与重试预算)。实现分别在 [llm_fallback.py](infrastructure/ai/llm_fallback.py)、[error_classifier.py](infrastructure/ai/error_classifier.py)、[providers/http.py](infrastructure/ai/providers/http.py)；媒体质量链由 [chain.py](features/media/chain.py) 管理，选材与恢复归 PIPELINE。

出站网络层见 [network.py](infrastructure/network.py)。模型、媒体下载、Tavily、Brave HTTP 请求按协议读取 `HTTP_PROXY` / `HTTPS_PROXY`，回退 `ALL_PROXY`；进程环境优先于 `.env`。`NO_PROXY` 支持域名、IP、端口、`*` 和 IPv4 / IPv6 CIDR，网段只匹配 URL 中的 IP；模板已排除内网和 Tailscale，短名称或自定义域名需自行添加。DDGS 的显式代理使用 `DDGS_PROXY`。

`SSRF_GUARD_ENABLED` 默认关闭以适配 DNS 污染 / fake-IP 代理，可后台热切换。开启后拒绝保留网段，`SSRF_ALLOWED_CIDRS` 不能豁免域名黑名单、云元数据及 CGNAT。代理地址也受守卫检查，目标另做本地 DNS 校验；代理实际建连须由代理端限制。`NO_PROXY` 不取消守卫，下载的大小、协议和 HTTPS 降级检查始终生效。

## 部署与排障

### Docker Compose 部署

复制 [配置模板](config.toml.example) 为 `config.toml`，填写密钥与管理员凭据；复制 [.env.example](.env.example) 为 `.env`，填写数据库账号、密码和数据库名，在 `backend` 目录运行：

```bash
docker compose up -d
```

数据库要求 PostgreSQL 16 及以上和 pgvector，Compose 使用 `pgvector/pgvector:pg16`。会话搜索与记忆证据查询依赖 `IS JSON` 和 `pg_input_is_valid`，没有启动期版本检查；低版本会在查询时失败。

Compose 和后端共用 `.env` 的 PostgreSQL 凭据，后端默认连接 `postgres:5432`；显式 `DATABASE_URL` 或 `config.toml` 的 `database_url` 优先，未配置时自动构造。已有数据卷沿用现有凭据；修改 `.env` 不会修改已初始化的账号、密码或数据库，变更须先在数据库中同步。

宿主机代理使用 `http://host.docker.internal:<端口>`，须允许 Docker 网桥访问。修改 `.env` 后执行 `docker compose up -d --force-recreate backend`，`restart` 不更新容器环境。

后端代码与依赖打入镜像，修改后执行 `docker compose up -d --build backend`。构建上下文为仓库根，仅根 `.dockerignore` 生效；镜像构建包含独立手机网页并托管 `/remote/`，另含 FFmpeg 与 ffprobe；Backend 不参与桌面安装包构建。

### 免费社交资讯来源

用户在客户端「设置 → 陪伴」的资讯来源区域配置个人登录。X 支持 Cookie 请求头或浏览器导出的 JSON，须包含 `auth_token`、`ct0`，保存时只保留这两个字段；后端使用固定版本 `twscrape` 的请求定义和解析器进行有界读取。保存只表示结构有效，平台拒绝登录时提示重新配置。小红书采用扫码和按需短信验证。账户隔离、清除与分享行为统一见 [主动信息与来源分享](../docs/PROTOCOL.md#主动信息与来源分享)。

小红书采集服务随默认 Compose 一起启动，在固定官方 `v2.5.5` 源码上构建账户隔离与登录验证扩展，复用官方镜像的 Linux amd64 浏览器；补丁、内部接口及资源上限见 [采集服务](infrastructure/web/xiaohongshu_service/README.md)。Backend 默认连接 `http://xiaohongshu-mcp:18060`，`.env` 的非空 `XHS_MCP_BASE_URL` 可覆盖该地址，管理页保存的服务配置优先。服务仅暴露宿主机回环端口，健康检查不要求平台登录，Backend 不等待其健康状态启动。

管理后台「系统参数 → 社交资讯来源」仅维护服务地址、内部访问令牌和夜间预算。在 `.env` 配置非空 `XHS_MCP_AUTH_TOKEN`，Backend 与服务使用相同令牌；管理页保存的令牌只用于 Backend 访问服务。`XHS_MAX_BROWSERS` 限制全服务浏览器并发，默认四个，扫码会话也占用名额。部署或升级时在 `backend` 目录执行 `docker compose up -d --build backend xiaohongshu-mcp`。`/health` 成功只表示服务可用，不证明任何账户已登录。

平台 Cookie 只持久化在账户数据库，采集服务每次接收所属账户凭证并创建隔离浏览器，不再使用全局登录文件或登录数据卷。扫码成功后由后台任务保存凭证；关闭设置页面不取消登录，服务重启丢失的扫码会话需重新开始。登录接口与二维码不缓存，公开响应不回传 Cookie。浏览器出站代理可通过 `XHS_PROXY` 配置，Backend 访问服务的短名称须包含在 `NO_PROXY`。采集只读取来源内容，搜索的一天内筛选仍由实际发布时间再次校验。

新增平台时，在 [平台定义](infrastructure/web/social_platforms.py)登记公开标识、名称、登录方式和原文规则，在 [适配器注册](infrastructure/web/social_sources.py)登记凭证类型、可用性及采集／登录工厂，再生成共享契约。账户存储、登录结果提交、夜间预算与分享流程共用既有实现；新的登录交互类型才需要补充客户端表单。

### 服务更新

管理后台「服务更新」页完成 `backend` 的更新：检查更新、拉取代码、构建镜像、重启并核对实际运行版本。执行体是独立容器 [updater](updater/README.md)，随默认 Compose 一起启动，与后端进程解耦——更新期间后端可被替换，更新器自身不受影响；页面请求经共享 Unix socket（`updater_socket` 卷）转交给它。

首次接入需 SSH 到服务器完成一次性准备，之后常规更新都在管理页完成：

1. 准备部署密钥与 `known_hosts`，放入仓库 `deploy/` 目录（`deploy_key`、`known_hosts`）。该目录已在 `.gitignore` 中，否则未跟踪文件会让预检拒绝更新。
2. 在 `backend/.env` 填写 `UPDATER_REPO_DIR`，即仓库在宿主机的绝对路径（容器内按同一路径挂载，相对目录挂载与命名卷才指向原部署）。
3. 执行 `docker compose up -d --build updater`。

更新顺序为预检（仓库、SSH、Docker、compose）→ 获取 upstream → 仅 fast-forward 推进当前分支 → 构建 `backend` → 重建并等待健康 → 核对运行镜像。预检要求当前分支有 upstream、工作区干净且没有本地领先或分叉；页面提交的更新绑定检查时确认的提交，远端在检查后变化则要求重新检查。

镜像内写入的提交标识区分「代码已拉取」与「更新成功」，页面分别显示仓库版本与运行版本。拉取或构建失败时旧服务继续运行；代码已推进但部署未成功时可重试同一版本。启动失败保留诊断并停止，不自动回退镜像或数据库。更新范围只有 `backend`；小红书采集服务、更新器自身与部署拓扑变化仍需手工升级。

### HTTPS 与公网访问

1. 域名解析到服务器，放行 80/443。
2. 复制 [Caddyfile.example](Caddyfile.example) 为 `Caddyfile` 并填写域名。
3. 配置 `public_base_url = "https://<域名>"`，用于激活码地址、手机扫码入口及供应商读取视频附件。
4. 执行 `docker compose --profile public up -d`，以 `https://<域名>` 激活，管理端位于 `/admin/`。

Caddy 自动签发续期证书并代理 HTTP 与 WS；Backend 的明文 10620 仅绑定宿主回环。本机测试可直接使用 `http://127.0.0.1:10620`。

全身与造型素材制作需在数据卷放置 `models/<matting_model>.onnx`，默认 ISNet 文件名及解析见 [matting.py](infrastructure/video_processing/matting.py)，镜像不自动下载。新付费步骤前校验模型；已有源素材保留供恢复。全身和造型素材上传可本地抠图，处理入口是 [image.py](infrastructure/video_processing/image.py)。

### 本地供应商

`local` 对接 Responses 服务、embedding 和 ComfyUI，默认地址见 [适配器](infrastructure/ai/providers/local/)。信息库地址须从 Backend（含容器）可达，不同地址分别建条目。无鉴权服务可留空密钥；启用 SSRF 守卫时私网地址须加入允许 CIDR，CGNAT 和云元数据地址不可豁免。

- ComfyUI 自动匹配 Qwen 权重，卡片要求见 [AI 配置契约](../docs/PROTOCOL.md#ai-配置与密钥)；图像能力与长等待归 PIPELINE。
- LLM 支持 Responses API；能力卡片须填写已部署模型 ID 、模型服务实际加载的上下文窗口与输出 Token 上限，供压缩、调用预算和会话容量展示使用。适配器按地址缓存 `/props` 探测结果；无工具结构输出中，llama.cpp 使用 `response_format.json_schema`，Strata 使用 `text.format`。两者按会话或任务的推理强度下发 `reasoning.effort`（`none` 关闭思考），未识别服务不下发推理参数和原生结构约束。其余由提示词与应用校验约束。归一化温度按 1:1 下发（界面 0.7 即 0.7），本地模型的推荐值多在 0–1。
- embedding 使用记忆向量任务配置的模型；链为空、无效或调用失败时记忆用关键词召回。[适配器](infrastructure/ai/providers/local/embedding.py)保留供应商原生维度，校验向量元素有限、向量非零且批次同宽。检索匹配模型标识、维度和内容版本，不截断或补零到固定宽度；向量空间与补齐规则见 [记忆读取与恢复](features/memory/README.md#读取召回与恢复)。

### 运营参数

`/admin/` 维护三级 AI 配置、用户覆盖与运营参数；继承、凭据与预算见 [AI 配置契约](../docs/PROTOCOL.md#ai-配置与密钥)。管理页复用共享 HTTP 核心，`pnpm --dir remote build` 同时构建手机页面与 `admin-http.js`；源码运行 Backend 前须完成该构建，Docker 镜像已包含产物。

### LLM 调试日志

每次 LLM 逻辑调用以 INFO 记录 `LLM usage`，包含调用 ID、供应商与模型、任务、终态、耗时及输入、缓存命中、缓存写入、输出和推理 Token；SDK 内部网络重试归属同一调用。缺失或非法计数为 null，失败和取消保留已取得的用量，不记录对话内容；日志写调用所在容器 stdout，聊天汇总口径见 [缓存用量契约](../docs/PROTOCOL.md#事件路由)。

排障时临时启用 LLM 调试并将日志级别设为 DEBUG，可能包含原始对话。验证材料须另行脱敏，基础提示词预览不能代表完整发送请求。

## 提示词定位

文本随业务或流程维护，数据库里的生成提示词仅是审计快照。双语常量经 `resolve_prompt_text` 取值，直接从所属文件导入，不设转发别名。`infrastructure/ai/prompt_engineer.py` 的结构化调用在供应商未下发原生 JSON Schema 时，将输出结构和字段说明加入指令；普通 JSON 模式本身不提供这些语义。修改规范归 [RULES](../RULES.md#提示词设计与修改规范)，参考与验收归 PIPELINE。

| 文本 | 装配与消费 |
|---|---|
| [聊天](workflows/chat/prompts.py)、[会话](features/conversation/prompts.py)、[语音](infrastructure/ai/speech_guidance.py) | 预设、回复/压缩、标题、图片修正与朗读；`system_prompt`、`streaming`、`context_compressor`、`title_generation`、聊天图片流程和供应商 `speech_style` |
| [陪伴](features/companion/prompts.py)、[创作内容审阅](features/companion/narrative_prompts.py) | 人设、心情、环境、问候和主动回合；`prompt_runtime`、`first_greeting` 与主动流程；动态计划及日记的 `narrative_quality` 审阅与有界修订 |
| [外观](features/appearance/prompts.py)、[场景](features/scenes/prompts.py) | 头像、全身、衣柜、角色卡及环境；各模块的 `prompt_assembly`、提取和制作服务 |
| [媒体](features/media/prompts.py) | 共享视觉质感、身份参考、审核与评分；图像/视频质量链和出镜装配 |
| [动作素材](features/action_library/prompts.py)、[影像](features/visuals/prompts.py) | 动作语义定义与评审、组合制作计划、运动设计和成品检查 |
| [记忆](features/memory/prompts.py)、[动态](features/posts/prompts.py) | 维护政策、召回资料、独立创作与线程回复；记忆模块、动态 `context`/`prompt_contract`/`publication`/`replies` |
| [夜间](workflows/nightly/prompts.py)、[话题检索](workflows/nightly/topic_prompts.py) | 规划、阶段账本、检查点、日记与反思；夜间话题检索方向与筛选 |
| [工具](runtime/tools/prompts.py)、[伙伴动作](workflows/companion_action_prompts.py) | 通用工具说明、验图、网页摘要、本机操作及统一动作能力；参数类型与必填项仍在工具 schema |

[声音设计装配](workflows/voice_design.py) 使用实际 TTS 适配器的 `VOICE_DESIGN_GUIDE`，指南不进入朗读正文。Runner 的工具 schema 独立维护，发布脚本 [gen_release_notes.py](../scripts/gen_release_notes.py) 自行维护摘要指令。动态装配与消费链见[完整提示词链检查入口](../docs/PIPELINE.md#完整提示词链检查入口)，命令归 [提示词调试](../scripts/README.md#提示词调试)。

## 契约与验证

检查命令归 [Scripts](../scripts/README.md)。API、事件、资产和恢复变更核对生产者、消费方及持久化；配置变更核对 Settings、启动专用键和提交失败语义；依赖变更核对本页允许边并执行分层检查；提示词沿文本索引检查实际请求与解析。文档变更核对代码事实、路径、锚点和 `git diff --check`，无需触发付费制作。
