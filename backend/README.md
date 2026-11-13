# Backend

负责云端生活决策与执行、对话、角色与记忆、媒体制作、调度和持久化。本页提供代码入口、后端共享约束和运维操作。

## 任务入口

| 修改任务 | 入口 |
|---|---|
| 对话、语音、上下文与工具循环 | [对话编排](workflows/chat/README.md) |
| 全双工实时通话 | [实时语音](features/realtime_voice/README.md)、[realtime_voice.py](entrypoints/http/routes/realtime_voice.py) |
| 持续生活、执行与经历回流 | [生活](features/life/README.md) |
| 记忆、证据、编辑与遗忘 | [记忆](features/memory/README.md) |
| 头像、视觉身份、衣柜与环境 | [外观](features/appearance/README.md)、[场景](features/scenes/README.md) |
| 动作、影像与公共媒体 | [动作](features/action_library/README.md)、[影像](features/visuals/README.md)、[媒体](features/media/README.md) |
| 动态与日记 | [动态](features/posts/README.md)、[内容存储](features/entries/README.md)、[journal_service.py](features/journal/journal_service.py) |
| Cron、主动陪伴与夜间 | [业务调度](#业务调度)、[cron.py](entrypoints/scheduler/cron.py)、[cron_jobs.py](features/automation/cron_jobs.py) |
| 资讯采集、来源引用与反馈 | [话题](features/topics/README.md)、[social_accounts.py](features/configuration/social_accounts.py) |
| 手机访问 | [远程授权](features/accounts/remote/README.md)、[remote.py](entrypoints/http/routes/remote.py) |
| 激活、账户与 WS 准入 | [user.py](entrypoints/http/routes/user.py)、[gateway.py](entrypoints/http/routes/gateway.py)、[账户](features/accounts/) |
| 管理后台与运营配置 | [admin.py](entrypoints/http/routes/admin.py)、[admin.html](static/admin.html)、[admin.js](static/admin.js)、[admin.css](static/admin.css) |
| 供应商和能力链 | [tasks.py](infrastructure/ai/tasks.py)、[providers/base.py](infrastructure/ai/providers/base.py)、[llm_client.py](infrastructure/ai/llm_client.py) |
| 桌面配置与附件 | [desktop_config.py](features/configuration/desktop_config.py)、[attachments.py](features/conversation/attachments.py)、[media.py](entrypoints/http/routes/media.py) |
| 共享任务与正式资产 | [任务](features/jobs/README.md)、[资产](features/assets/README.md)、[asset_store.py](infrastructure/assets/asset_store.py) |
| 桌面/Runner 发布分发 | [update.py](entrypoints/http/routes/update.py)、[update_releases.py](features/updates/releases.py) |
| 后端服务更新 | [服务更新](#服务更新)、[updater](updater/README.md)、[service_updater.py](infrastructure/web/service_updater.py) |
| 数据库结构与账户维护 | [模型注册](bootstrap/models.py)、[user.py](workflows/maintenance/user.py) |
| 提示词与恢复装配 | [提示词定位](#提示词定位)、[registrations.py](bootstrap/registrations.py)、[runtime_lifecycle.py](bootstrap/runtime_lifecycle.py) |

## 代码与依赖

### 模块依赖边界

允许方向为 `entrypoints → workflows / features / runtime / infrastructure`、`workflows → features / runtime / infrastructure`、`features → runtime / infrastructure`、`runtime → infrastructure`。反向依赖禁止；跨业务协调放 workflows，反向通知经 bootstrap 装配的端口。能力从所属文件导入，不经包级 facade。

ORM 留在数据所有者内部。跨域读取返回 DTO/冻结快照，写入使用公开操作；跨域原子变更使用事务内操作共享短事务，供应商等待在事务外。Pydantic 校验外部结构，dataclass 表达进程内值对象；纯 DTO、枚举与静态词表不承担业务操作。

[JobRecord](infrastructure/job_models.py) 是共享任务基础契约，业务可继承并拥有 joined-table 详情；这一例外不允许访问别的业务详情 ORM。共享正文归 entries，任务归 jobs，文件归 assets，具体关系归数据库文档。媒体通过显式业务关联和结果端口交付，不反查消费方正文或 JSON。

边界由 [check_services_architecture.py](../scripts/check_services_architecture.py) 检查，新增依赖仍须核实数据所有者、事务与权限，不能仅以检查通过为依据。

### API 入口

[routes/__init__.py](entrypoints/http/routes/__init__.py) 显式登记资源前缀及操作；`/api` 同时被资产和客户端使用，不是可调整的部署前缀。业务层不返回 FastAPI 响应，入口负责鉴权、解析与错误转换。

业务操作由 HTTP 受理，稳定操作标识与编码/等待策略生成到共享契约；WS [handlers.py](entrypoints/desktop/handlers.py) 只管理连接控制、工具同步、回执和情境信号。[body_limit.py](entrypoints/http/body_limit.py) 在解析前执行身份及载荷限制，实际流超限同样拒绝；管理页面可加载不代表管理 API 免鉴权。

## 配置与运行生命周期

### 配置与迁移

冷启动依次取环境变量、`backend/.env`、`config.toml` 和 [示例配置](config.toml.example)，路径按 Backend 目录定位。数据库、密钥、管理员和数据目录属于启动配置，字段及安全校验以 [Settings](infrastructure/config.py) 为准；空值或示例凭据拒绝启动。

[system_settings.py](features/configuration/system_settings.py) 串行合并并校验整批运营参数，数据库提交成功后才更新运行时及相关资源；启动水合覆盖文件中的动态值，非法持久值中止启动，启动专用键不能在管理页修改。用户点键配置统一经 [settings/values.py](features/configuration/settings/values.py) 读写。

[0001 完整基线](alembic/versions/0001_baseline.py) 是空库起点。启动执行 `alembic upgrade head`，不自动清库；后续结构变化新增迁移。模型与迁移保持类型、约束、索引和触发器一致，不能只依赖 autogenerate；降级只用于隔离库验收。

### 装配与启停

[registrations.py](bootstrap/registrations.py) 统一登记供应商、工具、内部事件及业务端口，导入模块不隐式注册。启动由 [lifecycle.py](bootstrap/lifecycle.py) 完成安全检查、迁移、配置水合、模块准备、调度/事件启动和任务恢复；参与者顺序由 [runtime_lifecycle.py](bootstrap/runtime_lifecycle.py) 维护。

停机先关闭生产入口，再收敛所属任务，最后释放数据库和供应商连接池。[task_supervisor.py](runtime/task_supervisor.py) 记录所有者、账户与错误，业务负责持久状态；常驻单 loop 与按 key 去重的任务集合见 [background.py](runtime/background.py)，内部事件、媒体制作和资讯登录轮询共用任务作用域。同键补跑只保留最后一次请求，显式继续等待旧任务取消收尾，暂停和放弃清除待补跑请求；停机收尾不再接收补跑。取消与写盘收尾共用 [async_io.py](infrastructure/async_io.py)，重复取消也须等待目标释放资源。媒体、影像与导入的互斥锁按账户、任务或候选在持有期间创建，调用方以局部引用保证同一业务共用一把锁，随引用释放而不建立长期锁表；任务结束、取消与账户删除都不遗留锁对象。

HTTP、调度及清理通过 [user_maintenance.py](runtime/user_maintenance.py) 共享账户租约。启动和周期维护按账户处理遗留媒体与正式资产，维护中的账户延后；各清理项隔离失败，不能让一项异常跳过其他回收。

### 事件与交付

事务通知经 `emit_ws_event` 写 outbox，聊天流经会话 emitter。实现入口为 [events/loop.py](runtime/events/loop.py)。

事件回路按本进程连接及 [受众契约](entrypoints/desktop/event_contracts.py) 认领；内部处理器派生所属任务后确认投递，失败按预算退避/转死信。LISTEN 探活与周期扫描共同唤醒；[outbox_gc.py](runtime/events/outbox_gc.py) 不按年龄清理普通离线事件。账户受理回合由 [chat_accounts.py](runtime/chat_accounts.py) 持有，连接只拥有身份与投递恢复状态。

## 数据与运行可靠性

关系显式预加载，时间带时区。请求 DbSession 与鉴权共用会话；鉴权后及等待用户锁、模型、打包或文件下发前结束只读事务。`expire_on_commit=False` 保留已加载对象，不能用回滚代替提交。

大字节、FFmpeg 和文件复制卸载到工作线程。随机路径的未交接文件在取消时清理；预登记固定路径的生成文件须等原子写完并保留给恢复。引用与删除统一由资产领域处理。本机派发先登记等待对象，再发送并检查入队，桌面离线以业务错误结束等待。

账户删除先阻止新操作并收敛在途任务；成功后失效运行缓存并解除维护，失败或取消才恢复原账户工作。用户文件仅落正式资产、会话附件和带用户归属的临时目录；新增路径纳入删除清理。正式文件或附件删失败时保留用户行供重试。

## 业务调度

### 夜间批处理

[cron.py](entrypoints/scheduler/cron.py) 为活跃账户启动陪伴域单飞流水线；[nightly_activity.py](workflows/nightly/nightly_activity.py) 协调规划、账本、日记与反思，专业预设记忆另行定期审阅。

夜间计划项保存依赖、冻结输入和生活步骤关联，执行状态及成果从步骤读取；等待时退出流水线，由后续扫描按原计划续行。阶段日志保留摘要和诊断，不复制动作结果；已经保存的日记与反思独立防重。

### 陪伴调度与恢复

[companion/intents.py](features/companion/intents.py) 保存条件和认领，主动回合由 [companion_turns.py](workflows/automation/companion_turns.py) 执行，普通任务由 [standard_turns.py](workflows/automation/standard_turns.py) 处理。

[scheduling.py](workflows/scheduling.py) 用扫描描述登记发现内容和执行间隔，以扫描名、账户及工作键共同单飞，夜间流水线跨本地日仍按账户隔离。周期工作首次等待一个间隔，临时维护或缺席不清除有效冷却；动态决策使用 45–90 分钟随机间隔。运行状态只在内存，业务结果以各领域持久状态为准。判断、执行、呈现分别进入 [生活模块](features/life/README.md) 所列工作流，不通过页面读取触发。

### 远程网页

[remote.py](entrypoints/http/routes/remote.py) 处理扫码和设备会话，Remote 页面使用同账户业务接口与对话编排；本机操作仍要求桌面 Runner。

## 供应商与网络错误

回退、可重试错误与未知结果由 [llm_fallback.py](infrastructure/ai/llm_fallback.py)、[error_classifier.py](infrastructure/ai/error_classifier.py) 和 [providers/http.py](infrastructure/ai/providers/http.py) 处理。聊天与独立调用共用 [response_validation.py](infrastructure/ai/response_validation.py) 校验响应终态，业务输出仍由各自契约校验。

实时语音的增量能力由 [providers/base.py](infrastructure/ai/providers/base.py) 的 `supports_streaming` 与 `provider_supports_streaming` 显式声明；`stream_transcribe_audio` 和 `stream_synthesize_speech` 只选择已声明且与当前模型、协议、端点匹配的供应商。语音链在未指定固定音色时优先排列 Mimo、MiniMax；流式筛选后由实际支持增量的 MiniMax 承担 PCM 输出，Mimo 保留为整段路径或兼容回退。Qwen 适配器目前只支持 `qwen-audio-3.1-asr-flash-streaming` 与 `qwen-audio-3.0-asr-flash-streaming` 的任务式 WebSocket 协议，要求工作区专用 DashScope 端点，并把通话的 24 kHz PCM 按流转换为供应商请求的 16 kHz；`qwen3-asr-flash-realtime` 等其它实时协议不会误路由到此适配器。MiniMax TTS 通过官方 T2A WebSocket `task_start → task_continue → task_finish` 输出 24 kHz PCM 分片，并沿用已绑定的音色与演绎参数。现有整段适配器不会被伪装成流式实现，也不会在不可重放的音频输入中途切换供应商；未配置流式能力时返回明确的 `ProviderCapabilityError`。

[network.py](infrastructure/network.py) 按协议使用 `HTTP_PROXY`/`HTTPS_PROXY`，回退 `ALL_PROXY`；进程环境优先于文件。`NO_PROXY` 支持域名、IP、端口、通配与 CIDR，网段只匹配 URL 中的 IP，服务短名称须显式包含。DDGS 使用 `DDGS_PROXY`。

`SSRF_GUARD_ENABLED` 默认关闭以适配 fake-IP 等代理，可在后台调整。开启后保留网段拒绝规则受 `SSRF_ALLOWED_CIDRS` 有限豁免，域名黑名单、云元数据和 CGNAT 不可豁免。代理地址同样校验，实际代理建连由代理端限制；`NO_PROXY` 不取消守卫，协议、大小和 HTTPS 降级检查始终生效。

## 部署与排障

### Docker Compose 部署

复制 [config.toml.example](config.toml.example) 为 `config.toml`，填写密钥与管理员凭据；复制 [.env.example](.env.example) 为 `.env`，填写 PostgreSQL 账号、密码和数据库名。在 `backend` 目录执行：

```bash
docker compose up -d
```

[docker-compose.yml](docker-compose.yml) 使用 PostgreSQL 16/pgvector，Backend、更新器与小红书服务各自运行。会话搜索和证据查询依赖 PostgreSQL 16 的 JSON 校验能力；当前无启动版本检查，低版本会在查询时失败。

Compose 与后端共用数据库凭据，默认连 `postgres:5432`；显式数据库 URL 优先，未配置时自动构造。已有卷沿用初始化凭据，改 `.env` 不会修改数据库账号，须同步实际数据库。

后端代码和依赖打入镜像，源码变化运行 `docker compose up -d --build backend`；环境变化运行 `docker compose up -d --force-recreate backend`，单独 restart 不更新环境。构建上下文是仓库根，只使用根 `.dockerignore`，产物包含手机页、管理页 HTTP 资源及 FFmpeg，不构建桌面安装包。

宿主机代理地址使用 `http://host.docker.internal:<端口>`，代理需接受 Docker 网桥连接。构建镜像可在 `.env` 设置 `BACKEND_BUILD_DEBIAN_MIRROR`，地址不带 `/debian`，须同时提供 Debian 与安全更新源；留空使用默认源，管理页构建同样生效。

### 端口与对外访问

默认发布 HTTP/WS `10620` 和 PostgreSQL `15432`，端口映射见 Compose。桌面激活地址为 `http://<服务器地址>:10620`，管理端为 `/admin/`；实际访问仍取决于防火墙与网络。

管理后台的 `public_base_url` 用于激活码地址、手机入口及供应商读取视频附件，须对实际访问方可达。反向代理透传 WS 升级及 Origin。调整 Compose 后重新 `up -d` 应用；移除旧服务时核对孤立容器，保留数据卷。

### 透明素材模型

全身及衣柜需要数据目录中的 `models/<matting_model>.onnx`，默认文件名和解析见 [matting.py](infrastructure/video_processing/matting.py)，镜像不自动下载。新付费阶段前检查模型，已有源素材保留供恢复；本地抠图入口为 [image.py](infrastructure/video_processing/image.py)。

### 免费社交资讯来源

客户端「设置 → 资讯来源」管理个人登录。登录方式由平台注册表的 `auth_kind` 决定：cookie 平台接受 Cookie 请求头或浏览器导出的 Cookie JSON，仅保存需要的字段，保存成功表示结构有效，平台拒绝后需重新配置；qr 平台使用扫码登录，必要时补充短信验证码。

小红书服务随 Compose 启动，Backend 固定连接 `http://xiaohongshu-mcp:18060`，无地址或访问令牌配置，也不发布宿主机端口。修改该服务时执行 `docker compose up -d --build backend xiaohongshu-mcp`；后端启动不依赖其健康检查，健康只证明进程可访问。

平台 Cookie 只由 Backend 数据库按账户持久保存，关闭设置页面不取消登录。管理后台只维护相关夜间预算，`NO_PROXY` 须包含服务短名称。

新增平台在 [social_platforms.py](infrastructure/web/social_platforms.py) 登记标识与原文规则，在 [social_sources.py](infrastructure/web/social_sources.py) 登记凭证及采集/登录工厂，再生成共享契约。复用账户存储、预算和分享流程，仅新登录交互类型需补客户端表单。

### 服务更新

管理页经共享 Unix socket 调用独立更新器，更新期间后端进程可被替换。首次在服务器准备：

1. 将部署密钥 `deploy_key` 和 `known_hosts` 放入仓库已忽略的 `deploy/`。
2. 在 `backend/.env` 设置 `UPDATER_REPO_DIR` 为仓库宿主机绝对路径，容器使用同一路径。
3. 在 `backend` 执行 `docker compose up -d --build updater`。

更新器预检、获取 upstream、fast-forward、构建并重启 backend，最后核对健康及运行提交。页面分别显示仓库与运行版本，代码拉取成功不代表部署成功。

拉取/构建失败时旧服务继续运行，代码已推进可重试同版本；启动失败保留诊断，不自动回退镜像或数据库。范围固定为 backend，小红书、更新器及拓扑变化仍手工升级；构建使用 Docker 缓存，单独提交号变化不使依赖层失效。

### 本地供应商

`local` 提供 Responses、embedding 和 ComfyUI，默认地址以 [适配器](infrastructure/ai/providers/local/) 为准。信息库端点须从 Backend 容器可达，不同地址分别登记；无鉴权服务可空密钥，启用 SSRF 守卫时私网仍须满足允许规则。

- LLM 卡片填写真实部署模型及实际上下文/输出窗口。[chat.py](infrastructure/ai/providers/local/chat.py) 按 `/props` 缓存识别 llama.cpp/Strata，选择对应无工具结构输出与推理参数；未识别服务依提示词和应用校验。温度按 1:1 下发。
- embedding 使用记忆任务配置并保留实际维度，验证元素有限、非零及同批宽度；空间不匹配或失败时走关键词召回。Qwen3-Embedding 按模型名在查询侧补官方检索指令，入库向量不加。
- ComfyUI 端保留 MIOpen（`COMFYUI_ENABLE_MIOPEN=1`）：ComfyUI 在 RDNA3 及更新架构上默认关闭它，关闭态的 torch conv3d 在 Qwen3-VL 视觉塔上堆损坏崩服；`MIOPEN_FIND_MODE=FAST` 省掉新分辨率的算法搜索等待，否则首次请求会多花数分钟。

### 运营参数

管理后台维护 AI 配置继承、用户覆盖和运营参数。管理页复用共享 HTTP 核心，源码启动前运行 `pnpm --dir remote build`，生成手机页与 `admin-http.js`；Docker 已包含这些产物。

### LLM 调试日志

每次逻辑模型调用记录 `LLM usage`，包含任务、模型、终态、耗时及已取得的用量，内部网络重试归同一调用；缺失计数保留 null，失败和取消不丢已有用量。日志写容器 stdout。

临时启用 LLM 调试并设为 DEBUG 才记录原始请求/响应，可能包含对话，验证材料另行脱敏。基础提示词预览不能代替完整发送请求。

## 提示词定位

文本随所属业务/流程维护，数据库生成提示词仅作审计快照；双语常量经 `resolve_prompt_text` 选择，直接从所有者导入。结构化调用在供应商没有原生 JSON Schema 时由 [prompt_engineer.py](infrastructure/ai/prompt_engineer.py) 注入结构与字段说明，普通 JSON 模式不提供这些语义。

| 文本与入口 | 装配及消费 |
|---|---|
| [聊天](workflows/chat/prompts.py)、[会话](features/conversation/prompts.py)、[语音](infrastructure/ai/speech_guidance.py) | 预设、回复、压缩、标题及朗读；入口见对话模块 |
| [陪伴](features/companion/prompts.py)、[内容审阅](features/companion/narrative_prompts.py) | 人格、心情、问候、主动回合与创作审阅 |
| [生活](features/life/prompts.py)、[判断](workflows/life_decisions.py)、[阅读](workflows/life_reading.py) | 事实投影、事项选择、结构化行动与阅读笔记 |
| [外观](features/appearance/prompts.py)、[场景](features/scenes/prompts.py) | 各自 prompt_assembly、角色提取与制作服务 |
| [媒体](features/media/prompts.py)、[动作](features/action_library/prompts.py)、[影像](features/visuals/prompts.py) | 冻结参考、语义评审、组合策划和质量链 |
| [记忆](features/memory/prompts.py)、[动态](features/posts/prompts.py) | 审阅、召回、创作与评论 |
| [夜间](workflows/nightly/prompts.py)、[资讯检索](workflows/nightly/topic_prompts.py) | 规划、日记/反思、来源筛选与检查点 |
| [工具](runtime/tools/prompts.py)、[伙伴呈现视图](workflows/companion_action_prompts.py) | 工具说明、网页摘要与呈现视图；参数仍以工具 schema 为准 |

声音设计由 [voice_design.py](workflows/voice_design.py) 装配实际适配器指南，不进入朗读正文；Runner 工具及发布脚本各自维护输入。

## 契约与验证

按改动核对入口、生产/消费与恢复：数据变更验证事务和引用，配置变更验证失败及热更新，依赖变更执行分层检查，提示词核对实际请求和真实产物。文档检查事实、链接、锚点与差异，不触发付费制作。
