# Backend

云端业务入口：对话、角色与记忆、资产制作、调度、IM 和持久化。模块分工、状态权威与部署限制归 [ARCHITECTURE](../docs/ARCHITECTURE.md)，跨端行为归 [PROTOCOL](../docs/PROTOCOL.md)，媒体制作归 [PIPELINE](../docs/PIPELINE.md)。本文维护代码导航、后端内部约束与部署操作。

## 任务入口

| 任务 | 入口 |
|---|---|
| 对话、语音、上下文与工具循环 | [对话编排](services/application/chat/README.md) |
| 记忆召回、证据、编辑与遗忘 | [记忆领域](services/domains/memory/README.md) |
| 头像、角色卡、衣柜、场景与媒体 | [生成服务](services/application/generation/README.md) |
| 动作提案、评审、目录与播放 | [动作编排](services/application/actions/README.md)、[动作领域](services/domains/actions/README.md) |
| Cron、主动陪伴与夜间 | [cron.py](services/adapters/scheduler/cron.py)、[业务调度](#业务调度) |
| 动态与日记 | [动态编排](services/application/posts/README.md)、[journal_service.py](services/domains/journal/journal_service.py)；REST 在 `api/v1/companion_posts.py`、`companion_journal.py` |
| IM 配对、消息与投递 | [IM 渠道](#im-渠道)、[channels.py](api/v1/channels.py) |
| 激活、登录、WS 票据与凭据 | [user.py](api/v1/user.py)、[page.py](api/v1/page.py)、[modules/auth](modules/auth/) |
| 管理后台、配置与用户维护 | [admin.py](api/v1/admin.py)、[static/admin.html](static/admin.html)、[配置与迁移](#配置与迁移) |
| 供应商与能力链 | [registrations.py](bootstrap/registrations.py)、[providers/base.py](services/infrastructure/llm/providers/base.py)、[llm_client.py](services/infrastructure/llm/llm_client.py) |
| 桌面配置同步 | [config.py](api/v1/config.py)、[desktop_config.py](services/domains/configuration/desktop_config.py) |
| 附件、上传视频与语音 | [media.py](api/v1/media.py)、[sessions.py](api/v1/sessions.py)、[chat_videos.py](services/domains/media/chat_videos.py) |
| 正式资产写入、签名与回收 | [asset_store.py](services/infrastructure/assets/asset_store.py)、[资产领域](services/domains/assets/README.md) |
| 桌面与 Runner 更新分发 | [update.py](api/v1/update.py)、[update_releases.py](services/domains/update_releases.py) |
| 备份与覆盖恢复 | [admin.py](api/v1/admin.py)、[backup](services/domains/backup/)、[maintenance.py](services/adapters/maintenance.py) |
| 提示词、装配与任务恢复 | [提示词索引](prompts/README.md)、[bootstrap/lifecycle.py](bootstrap/lifecycle.py) |

## 代码与依赖

| 目录 | 职责 |
|---|---|
| `main.py` / `bootstrap` | 薄入口、显式注册、启停与恢复 |
| `api` | 鉴权、限流、DTO 与服务调用 |
| `services/adapters` | HTTP、桌面 WS、IM、调度器和工具协议适配 |
| `services/application` | 回合、生成、动态、夜间等跨域流程 |
| `services/domains` | 业务状态、策略与持久化 |
| `services/infrastructure` / `contracts` | 供应商、传输、文件等基础能力 / 跨层值对象 |
| `modules` | ORM、跨边界 schema、鉴权、设置和事务内事件入口 |
| `components` / `common` | 配置、数据库、任务、日志、网络及少量框架工具 |
| `prompts` / `alembic` | 指令文本与纯常量 / 独立迁移 |

### services 依赖边界

主要方向为 `adapters → application → domains`；application 和 domains 可调用 infrastructure，各层可用 contracts。`contracts`、`modules`、`components`、`common`、`prompts` 不导入服务实现；application 不依赖 adapters，domains 不依赖 application，infrastructure 不反向依赖业务。能力包经 `__init__.py` 公开入口，`services` 根包不汇总导出。跨边界校验结构用 Pydantic，进程内值对象用 dataclass，不另设 `TypedDict` 或重复 schema。

以下单向依赖与 [分层检查器](../scripts/check_services_architecture.py) 对应，新增依赖同时说明业务理由：

| 依赖 | 原因 |
|---|---|
| 各领域 → conversation | 共用会话底座 |
| conversation / actions / backup → assets | 引用核查与事务性释放 |
| companion / journal → memory | 陪伴召回与日记派生索引 |
| companion → actions | 读取当前包目录快照 |
| backup → actions / memory | 重建动作目录与日记索引 |
| automation → chat | 复用 `run_chat_turn` 与 `HeadlessEmitter` |
| nightly → generation / posts / actions | 夜间制作、发布与动作上下文 |
| posts / application/actions → generation | 制作动态媒体与动作素材 |
| chat → application/actions | 读取动作上下文 |

### API 入口

`api/v1/*.py` 通过 `get_router()` 自动发现，默认前缀为 `/api/<文件名>`；部分资源另显式声明 `/api/companion/...` 等路径，入口以路由定义为准。`/api` 同时用于资产、备份、限流及客户端，不能作为部署配置修改。

[body_limit.py](services/adapters/http/body_limit.py) 在解析前限制请求体，实际流超限也返回 413，大载荷入口先验证当前登录。WS 入口是 [chat.py](api/v1/chat.py)，RPC 和斜杠命令分别在 [handlers.py](services/adapters/desktop/handlers.py)、[slash_commands.py](services/adapters/desktop/slash_commands.py)；`command.dispatch` 是命令执行权威，Client 只镜像补全和确认元数据。管理页面可加载不代表管理 API 免鉴权。

## 配置与运行生命周期

### 配置与迁移

冷启动配置依次取环境变量 → `backend/.env` → `config.toml` → [config.toml.example](config.toml.example)，文件按 Backend 目录定位。数据库、JWT、资产签名密钥、管理员凭据与数据目录属于启动配置；密钥或管理员密码为空、仍是示例值时拒绝启动。字段校验和默认值以 [Settings](components/config.py) 为准，资产签名密钥至少 32 字符。

运营参数由 [system_settings.py](services/application/configuration/system_settings.py) 管理：串行合并候选值 → 整批校验 → 事务提交 → 原位更新 `SETTINGS` → 刷新日志或连接池。数据库动态值在启动时再次覆盖环境变量和 TOML；启动专用键不能从后台修改。校验或提交失败不改运行时，非法持久值中止启动。保存接口执行数值边界校验，管理页输入范围只作提示；升级前须核查存量配置。

`system_settings` 和点键 `user_settings` 逐值 JSON 编码。用户设置只经 [modules/settings](modules/settings/values.py) 读写，消费方取得解码值，不再自行解析。

启动自动执行 Alembic 升级，当前结构基线为 [0001_baseline.py](alembic/versions/0001_baseline.py)。迁移可降级、回填幂等；比对 ORM 类型、默认值及索引。autogenerate 不足以核对部分索引的 `WHERE`、索引方法与操作符类，迁移中转义的 `%%` 须还原为 `%`。

### 装配与启停

[registrations.py](bootstrap/registrations.py) 是供应商、工具、渠道、内部事件和域钩子的装配入口；导入业务包不注册能力，未登记能力明确失败。

[lifecycle.py](bootstrap/lifecycle.py) 的启动顺序为：安全配置检查与迁移 → 配置水合、目录和更新存储恢复 → 描述中断标记与清理 → 调度器、事件回路、渠道桥 → 视频、动作、提案、角色卡、场景、初始资产、动态及评论恢复 → 正式资产核查。

停机先关闭清理任务、调度、渠道与事件入口，再并发收敛各模块任务，最后释放数据库、Web 供应商和 LLM 连接池。后台任务须登记所有者与用户归属，纳入停机和账户维护；`MANAGER`、用户锁及等待表遵守单进程部署边界。

### 事件与交付

事务内异步通知经 `emit_ws_event` 写 outbox，聊天流另走会话 emitter。持久化与端到端恢复语义归 PROTOCOL；实现入口为 [event_store/loop.py](services/infrastructure/event_store/loop.py)。

回路只认领本进程持有桌面 dispatcher 的用户（含断线宽限）。内部处理器派生任务即标记送达，投递失败按预算退避并转死信。LISTEN 专线探活、重连与周期扫描共同唤醒；[outbox_gc.py](services/infrastructure/event_store/outbox_gc.py) 不过期清理普通离线待投递行，过期主动回合请求另行处理。

## 数据与运行可靠性

数据库读写采用短事务，模型等待在事务外；关系显式预加载，时间戳带时区。请求 `DbSession` 与鉴权共用会话，响应结束才关闭。鉴权通过后提交只读事务，路由在等待用户锁、调用模型、打包及文件或流式下发前也须提交。`expire_on_commit=False` 保留已加载对象，不能用回滚代替提交。

大字节处理、FFmpeg 和文件复制卸载到工作线程。随机命名正式资产取消时删除未交接文件；预登记固定路径的生成资产取消时等待原子写完并保留，交由任务恢复。裸路径、签名、引用和回收由资产领域统一处理。

本机派发先注册等待对象，再发送并检查入队结果；直接持对象等待，避免极速返回后查表丢失。桌面离线以业务错误结束等待，不能用统一取消异常使 IM 回合静默退出。

覆盖恢复和删除用户先经 `maintenance.py` 阻止新操作并收敛在途任务，完成后重读持久状态恢复可继续任务。用户文件只落正式资产 `companion-assets/{user_id}/`、会话附件 `desktop-attachments/{session_id}/`、带用户元数据的 `temp-media/`；新增路径须同时纳入删除和备份。删除正式资产或会话附件失败保留用户行供重试，临时文件另由逐文件和过期清理兜底。备份类别、密钥范围及覆盖规则统一见 PROTOCOL。

## 业务调度

### 夜间批处理

`cron.py` 为活跃账户的陪伴域启动单飞流水线，关闭入口时取消并等待；专业预设的记忆维护另由定期审阅覆盖。执行入口 [nightly_activity.py](services/application/nightly/nightly_activity.py) 组织 [nightly_planning.py](services/application/nightly/nightly_planning.py)、[阶段账本](services/application/nightly/stage_state.py)和日记、反思执行；阶段、窗口、账本和恢复语义见 [夜间流水线](../docs/PROTOCOL.md#夜间流水线)。

### 陪伴调度与恢复

[companion/intents.py](services/domains/companion/intents.py) 管理条件、认领与原子终态；[companion_turns.py](services/application/automation/companion_turns.py) 管理有界回合和提交；普通任务由 [standard_turns.py](services/application/automation/standard_turns.py) 串行执行。交付和副作用恢复归 [Cron 双轨](../docs/PROTOCOL.md#cron-双轨)。

### IM 渠道

[manager.py](services/adapters/channels/manager.py) 管绑定生命周期，[bridge.py](services/adapters/channels/bridge.py) 管入站、回合和补发，[weixin_ilink.py](services/adapters/channels/adapters/weixin_ilink.py) 管微信协议与媒体转换。

REST 直接驱动绑定启停，守卫循环自愈，不做周期对账。接收锁保证落库与排队顺序，投递锁避免并发补发；登录、入站、回合、typing 与补发均属于绑定实例，退出或重建前取消并等待整棵任务树。凭据整体严格解析，损坏按无凭据处理并要求重新扫码；日志不记录令牌。配对、撤权、限流和回复上下文规则见 [IM 通道](../docs/PROTOCOL.md#im-通道)。

## 供应商与网络错误

通用回退、安全重试与结果未知归 [模型失败与重试预算](../docs/PROTOCOL.md#模型失败与重试预算)。实现分别在 [llm_fallback.py](services/infrastructure/llm/llm_fallback.py)、[error_classifier.py](services/infrastructure/llm/error_classifier.py)、[providers/http.py](services/infrastructure/llm/providers/http.py)；媒体质量链由 [media_chain.py](services/application/generation/media_chain.py) 管理，选材与恢复归 PIPELINE。

出站守卫由 [components/network.py](components/network.py) 管理，`SSRF_GUARD_ENABLED` 默认关闭，可后台热切换，适用于 DNS 污染或 fake-IP 代理环境。开启后拒绝保留网段，可用 `SSRF_ALLOWED_CIDRS` 指定豁免；豁免不取消域名、协议、HTTPS 降级、云元数据及 CGNAT 校验。下载大小、协议白名单与 HTTPS 降级检查不受守卫开关影响。

## 部署与排障

### Docker Compose 部署

复制配置模板为 `config.toml`，填写密钥与管理员凭据，在 `backend` 目录运行：

```bash
docker compose up -d
```

数据库要求 PostgreSQL 16 及以上和 pgvector，Compose 使用 `pgvector/pgvector:pg16`。会话搜索与记忆证据查询依赖 `IS JSON` 和 `pg_input_is_valid`，没有启动期版本检查；低版本会在查询时失败。Compose 的数据库密码与宿主端口映射见 [docker-compose.yml](docker-compose.yml)，部署时与连接配置一并调整。

后端代码与依赖打入镜像，修改后执行 `docker compose up -d --build backend`。构建上下文为仓库根，仅根 `.dockerignore` 生效；镜像内含 FFmpeg 与 ffprobe，Backend 不参与桌面安装包构建。

### HTTPS 与公网访问

1. 域名解析到服务器，放行 80/443。
2. 复制 [Caddyfile.example](Caddyfile.example) 为 `Caddyfile` 并填写域名。
3. 配置 `public_base_url = "https://<域名>"`，用于激活码地址及供应商读取视频附件。
4. 执行 `docker compose --profile public up -d`，以 `https://<域名>` 激活，管理端位于 `/admin/`。

Caddy 自动签发续期证书并代理 HTTP 与 WS；Backend 的明文 10620 仅绑定宿主回环。本机测试可直接使用 `http://127.0.0.1:10620`。

全身、着装与动作制作需在数据卷放置 `models/<matting_model>.onnx`，默认 ISNet 文件名及解析见 [matting.py](services/infrastructure/video_processing/matting.py)，镜像不自动下载。新付费步骤前校验模型；已有源素材保留供恢复。全身和着装上传可本地抠图，动作上传须自带有效 alpha，处理入口是 [image.py](services/infrastructure/video_processing/image.py)。

### 本地供应商

`local` 对接 Responses 服务、embedding 和 ComfyUI，默认地址见 [适配器](services/infrastructure/llm/providers/local/)。Base URL 必须从 Backend（含容器）可达；信息库卡片提供共享地址与密钥，能力卡片可覆盖。无鉴权服务可留空密钥；启用 SSRF 守卫时私网地址须加入允许 CIDR，CGNAT 和云元数据地址不可豁免。

- ComfyUI 使用固定 Qwen 工作流，自动匹配权重，不支持能力卡片独立密钥或自选权重。图像能力与长等待归 PIPELINE。
- LLM 显式填写已部署模型 ID，支持 Responses API，加载窗口至少覆盖 [chat.py](services/infrastructure/llm/providers/local/chat.py) 的预算。适配器按地址缓存 `/props` 探测结果；确认 llama.cpp 后，仅无工具请求追加其 `response_format.json_schema`，其余走提示词与应用校验。
- embedding 仅使用能力链首个有效配置，不自动换模型；链为空、无效或调用失败时记忆用关键词召回。维度适配见 [embedding.py](services/infrastructure/llm/providers/local/embedding.py)：仅默认模型允许 MRL 截断，短向量归一化补零，其他超宽向量拒绝。记忆未记录向量模型标识，更换模型不能复用旧向量。

### 运营参数

`/admin/` 提供供应商、能力链、按用户配置及运营参数，动态设置保存后即时生效。自主动态快照的保留期由 [publication.py](services/application/posts/publication.py) 实现，启动及小时维护负责回收；未知、在途、永久幂等和仍有引用的记录保留。

### LLM 调试日志

排障时临时启用 LLM 调试并将日志级别设为 DEBUG，日志写调用所在容器 stdout，可能包含原始对话。默认日志只留脱敏诊断；验证材料须另行脱敏，基础提示词预览不能代表完整发送请求。

## 契约与验证

检查命令归 [Scripts](../scripts/README.md)。API、事件、资产和恢复变更核对生产者、消费方及持久化；配置变更核对 Settings、启动专用键和提交失败语义；依赖变更核对本页允许边并执行分层检查；提示词沿文本索引检查实际请求与解析。文档变更核对代码事实、路径、锚点和 `git diff --check`，无需触发付费制作。
