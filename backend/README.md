# Backend

负责云端对话编排、角色与记忆、资产、调度和持久化。本机执行委托 Runner，窗口与渲染归 Client。

## 任务入口

| 要修改的功能 | 代码入口与联动文档 |
|---|---|
| 对话、语音、上下文与工具循环 | [对话编排](services/application/chat/README.md#关键入口) |
| 记忆证据、召回与遗忘 | [记忆模块](services/domains/memory/README.md) |
| 头像、角色卡、衣柜、场景与媒体 | [生成服务](services/application/generation/README.md#关键入口)；跨端场景改动见其任务链 |
| 动作提案、生成与播放 | [动作编排](services/application/actions/README.md)、[动作领域](services/domains/actions/README.md) |
| Cron 与在线陪伴 | 调度 [cron.py](services/adapters/scheduler/cron.py)；回合 [companion_turns.py](services/application/automation/companion_turns.py) / [standard_turns.py](services/application/automation/standard_turns.py)；任务与等待分别见 [cron_jobs.py](services/domains/automation/cron_jobs.py) / [intents.py](services/domains/companion/intents.py) |
| 夜间计划与执行 | [nightly_activity.py](services/application/nightly/nightly_activity.py) 的 `run_nightly_pipeline` → [nightly_planning.py](services/application/nightly/nightly_planning.py)；检查 [阶段与恢复](#夜间批处理) |
| 动态 | [动态应用](services/application/posts/README.md)、[动态领域](services/domains/posts/store.py)、[REST](api/v1/companion_posts.py) |
| 日记 | [日记领域](services/domains/journal/journal_service.py)、[REST](api/v1/companion_journal.py) |
| IM 生命周期与投递 | 生命周期 [channels/manager.py](services/adapters/channels/manager.py)；入站、回合与补发 [bridge.py](services/adapters/channels/bridge.py)；iLink [weixin_ilink.py](services/adapters/channels/adapters/weixin_ilink.py)；REST [channels.py](api/v1/channels.py)；[IM 约束](#im-渠道) |
| 激活、登录与 WS 票据 | [user.py](api/v1/user.py)（激活、ws-ticket、刷新、登出）、管理员登录 [page.py](api/v1/page.py)；令牌与鉴权依赖 [modules/auth](modules/auth/) |
| 管理后台与用户管理 | 页面 [static/admin.html](static/admin.html)，API [admin.py](api/v1/admin.py)（用户、按用户模型配置、系统设置、夜间日志、备份导出导入、删除用户） |
| 模型与媒体供应商 | 类型与注册 [providers/base.py](services/infrastructure/llm/providers/base.py) / [registrations.py](bootstrap/registrations.py)；配置 [ai_config.py](components/ai_config.py) / [ai_config.py](services/domains/configuration/ai_config.py)；链解析 [llm_client.py](services/infrastructure/llm/llm_client.py)；回退见[供应商与网络错误](#供应商与网络错误) |
| 桌面配置同步 | [config.py](api/v1/config.py)（`user_settings` 点键读写，嵌套配置与点键互转见 [desktop_config.py](services/domains/configuration/desktop_config.py)）；契约见 [配置所有权与云同步](../docs/PROTOCOL.md#配置所有权与云同步) |
| 附件、上传视频与语音 REST | REST [media.py](api/v1/media.py) / [sessions.py](api/v1/sessions.py)；临时与附件存储 [temp_files.py](components/temp_files.py) / [attachments.py](components/attachments.py)；视频 [chat_videos.py](services/domains/media/chat_videos.py) |
| 资产存储与签名 | [asset_store.py](services/infrastructure/assets/asset_store.py)；访问契约见 [资产访问与缓存](../docs/PROTOCOL.md#资产访问与缓存) |
| 桌面与 Runner 更新分发 | [update.py](api/v1/update.py)：管理端“版本管理”上传更新 ZIP，按最新启用版本生成 `latest.yml` / `latest-mac.yml`，原样提供构建时已签名的 `latest-runner.yml`；客户端流程见 [自更新签名](../docs/PROTOCOL.md#自更新签名) |
| 备份校验与覆盖恢复 | API [admin.py](api/v1/admin.py)；包布局与解压校验 [archive.py](services/domains/backup/archive.py)，清单、序列化与恢复 [manifest.py](services/domains/backup/manifest.py) / [serializers.py](services/domains/backup/serializers.py) / [restoration.py](services/domains/backup/restoration.py)；维护边界 [maintenance.py](services/adapters/maintenance.py)；核对 [恢复契约](../docs/PROTOCOL.md#备份校验与覆盖恢复) |
| 提示词、启动与事件恢复 | [提示词索引](prompts/README.md)、[bootstrap/lifecycle.py](bootstrap/lifecycle.py)、[event_store/loop.py](services/infrastructure/event_store/loop.py) |

## 设计意图

不同入口复用回合编排，领域管理业务，应用层组织跨域流程，基础设施隔离传输。持久事实归数据库；单 web 进程限制见 [架构](../docs/ARCHITECTURE.md#部署与运行时边界)。

## 代码与依赖

### 目录职责

| 目录或入口 | 职责与边界 |
|---|---|
| `main.py` / `bootstrap` | 启动应用、显式注册和管理启停 |
| `api` | 鉴权、限流、DTO 与服务入口 |
| `services` | 协议适配、应用流程、领域、基础设施与契约 |
| `modules` | ORM、跨边界 schema，以及令牌与鉴权依赖、`user_settings` 读写、`emit_ws_event` 等贴近数据的入口 |
| `components` | 配置、数据库、后台任务与日志，以及出站网络与 SSRF 守卫、临时媒体与附件存储、用户维护态等横切工具 |
| `common` | 路由、模型基类等少量框架工具 |
| `prompts` | 零项目内依赖的提示词文本常量；渲染与装配留在服务层 |
| `alembic` | 独立于应用实现的迁移 |

`modules`、`components`、`common`、`prompts` 不反向依赖服务实现。跨包使用公共入口；能力包通过 `__init__.py` 暴露符号，`services` 根包不汇总导出。检查见 [Scripts](../scripts/README.md#导入检查)。

### services 依赖边界

主要依赖方向为 `adapters → application → domains`；application / domains 可调用 infrastructure，各服务层可使用 contracts。

| 特殊依赖 | 业务理由 |
|---|---|
| 各领域 → conversation | 会话底座 |
| companion / journal → memory | 陪伴读取记忆，日记发布时同步派生召回索引 |
| companion → actions | 只读当前包动作目录快照 |
| backup → actions | 恢复动作资产后重建目录并复用发布校验 |
| backup → memory | 从恢复的日记原文重建召回索引 |
| automation → chat | 复用对话回合编排（`run_chat_turn` / `HeadlessEmitter`） |
| nightly → generation / posts | 夜间资产与独立动态发布 |
| posts → generation | 复用图片、视频与身份生成能力 |
| application/actions → generation | 制作动作素材 |
| chat / nightly → application/actions | 读取动作上下文 |

- `contracts` 不导入服务实现；`domains` 不依赖 application / adapters；application 不依赖 adapters；infrastructure 不反向依赖业务。
- 新增跨域或应用包依赖须有业务理由，并核对[分层检查器](../scripts/check_services_architecture.py)。不使用延迟导入掩盖环。

### API 入口

`api/v1/*.py` 通过 `router = get_router()` 自动发现，负责鉴权、限流和 DTO 组装。路由前缀固定为 `/api/<文件名>`；`/api` 同时用于资产、备份、限流及客户端，不能配置修改。请求体在解析前按 [入口策略](services/adapters/http/body_limit.py) 硬限，实际流超过上限同样返回 413；大载荷入口先验证当前登录，避免未认证上传占用磁盘。WS 从 `api/v1/chat.py` 进入，RPC 注册在 [desktop handlers](services/adapters/desktop/handlers.py)，斜杠命令注册表在同目录 [slash_commands.py](services/adapters/desktop/slash_commands.py)；客户端 [slash-commands.ts](../client/renderer/shared/lib/slash-commands.ts) 只镜像自动补全与确认弹窗用的元数据，`command.dispatch` 是唯一权威。管理页面位于 `static/admin.html`；页面可加载不代表管理 API 免鉴权。

## 配置与运行生命周期

### 配置与迁移

复制 [config.toml.example](config.toml.example) 为 `config.toml` 后填写数据库、JWT、资产签名密钥和管理员凭据等冷启动依赖；JWT、资产签名密钥或管理员密码为空或仍是示例值时，web 进程拒绝启动。配置来源优先级为环境变量 → `backend/.env` → `backend/config.toml` → `backend/config.toml.example`，文件均按 Backend 目录定位，与进程工作目录无关；业务型参数进入 `system_settings`，由 [Settings](components/config.py) 声明，技术常量不承载可运营配置。

热更新入口为 [system_settings.py](services/application/configuration/system_settings.py)：串行合并候选值 → 整批校验 → 事务提交 → 原位更新 `SETTINGS` → 刷新连接池等副作用。管理后台的系统设置写入数据库并立即更新当前进程；重启时数据库中的动态值会在环境变量和 TOML 水合后再次覆盖它们。启动专用参数不能从管理后台修改。

校验或提交失败不修改运行时，避免数据库与内存分叉。持久值统一 JSON 编码，启动水合时解析或校验失败即中止启动。数值范围由 `Settings` 字段声明，拒绝非有限浮点数；管理页 `min/max` 只作输入提示，保存接口对越界值返回 422。升级前须检查数据库与配置来源中的存量值，越界值会阻止启动。资产签名密钥至少 32 字符。

用户偏好 `user_settings` 同样按点键逐值 JSON 编码（桌面配置同步与服务端写入如时区共用同一格式），只经 [modules/settings](modules/settings/values.py) 读写，读取即得解码后的原值，消费方不自行解析。

启动执行 Alembic 升级，结构由 [0001 基线](alembic/versions/0001_baseline.py)定义。迁移须可降级，回填须幂等，破坏性变更说明风险。类型与默认值需比对，部分唯一、向量和全文索引均在 ORM 模型声明；autogenerate 比对名称、唯一性与列表达式，`WHERE`、索引方法与操作符类仍须人工核对。生成迁移中被写成 `%%` 的百分号须还原为 `%`。数据库版本要求见 [Docker Compose 部署](#docker-compose-部署)。

### 装配与启停

`bootstrap` 是唯一装配入口。供应商、工具、渠道、内部事件和域钩子显式注册；注册可重复，未登记能力明确失败。

| 阶段 | 顺序与归属 |
|---|---|
| 启动 | 配置检查 → 迁移 → 配置水合与目录准备 → 调度器 → 事件回路 → 渠道桥 → 任务恢复 |
| 恢复 | 动态发布与评论、聊天与动态视频任务、动作包生成/导入、动作提案评审、角色卡提取、场景、初始外观 |
| 停止 | 关闭清理任务与调度入口 → 停渠道桥 → 停事件回路 → 收敛模块任务 → 释放数据库、Web 供应商及 LLM 连接池 |

`MANAGER`、`REGISTRY`、`SETTINGS` 与用户锁遵守单进程边界。bootstrap 管装配，不另建通用依赖注入容器。

### 事件与交付

- `emit_ws_event` 与业务状态在同一事务写入 outbox；数据库 `NOTIFY` 只负责唤醒，事件行负责恢复。
- 事件回路只认领本进程有桌面 dispatcher 的用户（含断线宽限期）。内部处理器派生任务即记送达；用户投递失败按预算退避，达到 `MAX_OUTBOX_RETRIES` 后转死信。具体清理由 [outbox_gc.py](services/infrastructure/event_store/outbox_gc.py) 负责，普通离线待投递行不过期，过期的主动回合请求例外清理。LISTEN 专线每 15 秒以 `SELECT 1` 探活，5 秒超时，失败后 5 秒重连；周期扫描继续承担兜底。
- 聊天流走会话 emitter，后台任务由各自所有者启停和恢复；事件存储不直接调用业务处理器。

## 数据与运行可靠性

数据库会话采用短读 → 无会话模型等待 → 短写，关系显式预加载，时间戳带时区。请求级 `DbSession` 与鉴权依赖共用同一会话，响应发送完毕才关闭：鉴权依赖在通过后提交只读事务（可选鉴权失败时回滚），路由与服务在模型调用、等待用户锁、文件打包及文件或流式下发之前须先提交请求会话，不让只读事务占着连接跨过这些等待。会话 `expire_on_commit=False`，提交后已加载对象仍可读写，这里不能用回滚代替提交。

图片与视频等大字节处理与落盘卸载到工作线程。正式资产写入有两种取消语义：随机命名资产（`save_companion_asset_async`）取消时删除未交接文件；任务预登记固定路径的生成资产（视频任务、动作素材与图片链候选）取消时等待原子写完并保留，供恢复复用。

资产引用列保存 `companion-assets/{user_id}/...` 裸路径；响应出口改写为 Bearer 鉴权的 `/api/companion/asset/...`（`client_asset_url`）或短时签名 URL（`signed_companion_asset_url`），签名 URL 不入库。

本机派发先注册等待对象，再发送并检查入队结果；直接持对象等待，避免极速返回后查表丢失。桌面离线以业务错误结束等待，不能一律抛取消异常而使 IM 回合静默退出。

备份不迁移登录、激活、IM 授权、事件队列和执行账本；已清理历史媒体可保留失效引用，必需资产缺失按类失败；用户级模型配置（`user_model_configs`，含供应商密钥明文）随包导出与恢复，系统信息库与本机配置另行准备。导出固定全量，导入按分组（`BACKUP_SECTIONS`）勾选恢复，只写勾选类别；旧版片刻表静默跳过。恢复时的文件复制在工作线程执行：被取消时通知线程停止并等其退出，再回滚已复制的文件，不留下未入库的资产。包级校验、部分恢复和维护态见 [PROTOCOL](../docs/PROTOCOL.md#备份校验与覆盖恢复)。

用户文件只落三处：`companion-assets/{user_id}/`（正式资产）、各会话的 `desktop-attachments/{session_id}/`，以及带 `user_id` 元数据的 `temp-media/`。删除用户（被遗忘权，[admin.py](api/v1/admin.py) 的 `delete_user`）先进入维护态并停稳运行时，再删除这三处文件和用户行；资产目录或会话附件删除失败时保留用户行，可重试，临时文件由逐文件清理和定期过期清理兜底。新增用户文件必须落在这三处之一，否则删除与备份都会遗漏。

## 业务调度

### 陪伴叙事

检索记忆的证据与维护规则归 [记忆模块](services/domains/memory/README.md)；动态与日记的隔离、发布及互动归集见 [PROTOCOL](../docs/PROTOCOL.md#动态与日记)，动态编排见 [动态应用](services/application/posts/README.md)。

### 夜间批处理

- 夜间生活（规划、日记、反思）由伙伴人格发起，只属于陪伴域：扫描为活跃账户的陪伴域启动单飞任务，不等待本轮流水线全部结束，关闭调度入口时取消并等待这些任务。专业预设不接入夜间流水线，其记忆维护由调度器定期审阅独立覆盖；夜间日志按用户与本地日记账，不区分预设。
- 夜间窗口支持跨午夜，开始与结束小时相同则配置校验失败；首次启动、中断恢复及新阶段、新动作均须在用户本地窗口内，实际启动前复核时间。已提交的制作可跨窗口完成，保留结果并收尾记账；窗口结束后不再发起后续创作或叙事。只处理刚结束的本地日，缺时区跳过，同日完成不重跑；更早的未完成日志记为部分失败且不再重放，同一用户、本地日复用一条日志。已创建的次日联系由独立调度执行。
- 规划按实际本地时间判断活动时效，回顾日期不当作当前日期；计划与动作账本先持久化再执行。每项执行前重读政策并核对依赖，终态统一落库，失败互相隔离。场景阶段不依赖外观或动作阶段。
- 规划动作 ID 保持原样并校验唯一性；过滤前置能力后依赖仍保留，只有前置整项成功才解锁后续。动态创作随意图冻结账本中伙伴的已确认活动，日记、反思及次日联系复用这些事实；部分成功只提供已完成部分，动作受理不代表制作完成。
- 有任务句柄时先核对原任务；结果未知的在途动作保留中断事实，不重发副作用。文本与媒体参数在执行前校验长度，不以截断改变计划。
- 日记和反思共享目标日同域对话（含 IM）、动态互动与已完成活动；没有可读素材时跳过叙事阶段。反思结构不符时修复一次，仍失败不覆盖旧理解。阶段已完成决定与业务写入同事务保存，恢复复用已有结果；发布、召回与未读契约见 [PROTOCOL](../docs/PROTOCOL.md#动态与日记)。

### 陪伴调度与恢复

[等待域](services/domains/companion/intents.py)管理条件、有效期、认领和原子终态；[陪伴回合](services/application/automation/companion_turns.py)复用工具循环并限制轮数、时长和委派，要求用户桌面在线。取消或失败不提交暂存续等。

未开始的认领可以重试，只调用[查询类工具](services/application/automation/companion_turns.py)的回合视同未开始；已执行而结果不明时保留待核对提示，不重跑副作用。有效期结束不抹去核对信息；创建、恢复及解除暂停统一在用户锁下检查活跃任务配额，重启不补算未互动时长。未知结果语义见[调用日志与未知结果](../docs/PROTOCOL.md#调用日志与未知结果)。

### IM 渠道

适配器由装配层注册。接收锁保证落库与入队顺序，投递锁避免并发补发；登录、入站、回合、typing 和补发均归绑定实例，退出或重建前取消并等待整棵任务树。绑定启停由 REST 直驱、守卫循环自愈，无周期对账（单 web 进程，无端口单例锁 / failover）。可恢复的长轮询错误在同一实例中退避重试，保留游标及在途回合；达到连续次数与持续时间阈值时显示重连中，成功轮询后恢复连接状态；适配器子任务失败由绑定所有者读取并上报。持久化 iLink 凭据读回时整体严格校验，无法解析或字段类型不符按无凭据处理，须重新扫码；日志不记录令牌。

iLink 长轮询 `getupdates` 返回 `-14` 即清除登录凭据并将绑定置为 `login_required`（用户重新发起扫码后为 `login_pending`）；发送或 typing 返回同码只代表回复上下文失效，等待下一次来信，不触发重新扫码。超出每分钟入站限流的消息在落库前丢弃且不通知对端。媒体经渠道加解密转换；配对、排队、只读与本机授权见 [PROTOCOL](../docs/PROTOCOL.md#im-通道)。

## 供应商与网络错误

- 供应商身份由注册与配置决定，不从 URL 推断。
- 幂等方法、显式幂等键或确认未发送的连接失败才可自动重试；请求体须可重放。
- 非幂等请求在写入或读取响应阶段断线按结果未知处理，不能直接换供应商再提交。
- 后台单次文本调用、标题和网页摘要使用完整配置链，每家重算模型、推理档位、温度、结构输出与窗口预算；嵌入仍保持模型一致性，不套用文本回退。
- 通用链 [execute_with_fallback](services/infrastructure/llm/llm_fallback.py)（LLM、STT、TTS、立绘与非本人聊天图片）换家只看 [错误分类](services/infrastructure/llm/error_classifier.py) 的 `should_fallback`：确定性失败，以及本家传输层重试耗尽后的超时 / 过载；结果未知或流已开始时不换家。媒体质量链（视频任务、动作素材、角色 / 场景 / 衣柜图片）逐家单独调用，由 [media_failure_reason](services/application/generation/media_chain.py) 判定：结果未知优先且不换家，其余接受产物校验声明的可回退错误或 `should_fallback`。

- 出站 SSRF 守卫默认关闭（`SSRF_GUARD_ENABLED`，管理后台可热切换）：关闭时不做保留网段与黑名单校验，避免 DNS 污染 / fake-ip 代理环境误拦正常出站，内网访问风险由部署者自担。实现见 [components/network.py](components/network.py)。
- 开启后默认拒绝保留网段；显式 fake-IP 豁免（`SSRF_ALLOWED_CIDRS`）不取消域名、协议、HTTPS 降级、云元数据与 CGNAT 检查。
- 下载层的大小、协议白名单与 HTTPS 降级检查不受开关影响。
- 对外错误脱敏，内部诊断保留原因。

## 数据结构定义

跨边界或需校验的结构使用 Pydantic；纯进程内值对象使用 dataclass，只读对象可冻结。不使用 `TypedDict`，也不在调用方重复声明已有 schema。

## 部署与排障

### Docker Compose 部署

在 `backend` 目录按 [配置模板](config.toml.example)准备数据库、JWT、资产签名密钥和管理员配置，然后执行：

```bash
docker compose up -d
# 同时启动监控
docker compose --profile monitoring up -d
```

数据库须为 PostgreSQL 16 及以上并安装 pgvector，compose 使用 `pgvector/pgvector:pg16`，外部数据库同样适用。会话搜索与记忆证据检索使用 `IS JSON` 谓词与 `pg_input_is_valid`，没有启动期版本检查，低版本只会让这两处查询报错。

容器与卷见 [docker-compose.yml](docker-compose.yml)，指标抓取见 [Prometheus 配置](monitoring/prometheus.yml)。`/metrics` 默认无需鉴权；配置 `metrics_auth_token` 后须以 `Authorization: Bearer <令牌>` 或 `X-Metrics-Token` 访问。

使用随附 Prometheus 时在 `backend/.env` 设置 `METRICS_AUTH_TOKEN`，Backend 经 `env_file` 读取，Prometheus 经 compose 注入同一令牌文件；未设置时后端不校验。compose 不会因令牌变化自动重建容器，修改后执行 `docker compose --profile monitoring up -d --force-recreate backend prometheus`。管理后台保存或清除令牌会即时更新 Backend 的 `system_settings`；数据库中的动态值在启动时优先于 `.env`，只改文件不能覆盖已保存值；启用 Prometheus 时须同步管理后台与 `.env`。Backend 不参与桌面安装包构建。

后端镜像安装 FFmpeg（含 `ffprobe`），用于视频探测、抠像和转码；构建时检查两个命令可执行。后端代码和依赖均打入镜像，修改后在 `backend` 目录执行 `docker compose up -d --build backend` 重建并替换容器。构建上下文为仓库根，仅根 `.dockerignore` 生效。

动作素材生成还须在数据卷 `models/<matting_model>.onnx` 放置 ISNet 模型（默认 `isnet-general-use.onnx`），运行路径由 [matting.py](services/infrastructure/video_processing/matting.py) 决定；镜像不自动下载。生成动作保留原生 alpha，必要时本地抠像；缺失模型时在新付费制作前拒绝并保留进度。上传透明图片或视频不调用生成模型；静态图片处理见 [image.py](services/infrastructure/video_processing/image.py)。

### 本地供应商

`local` 默认对接 LM Studio（LLM / embedding）和 ComfyUI（图像），默认地址见各适配器的 `DEFAULT_BASE_URL`（[local](services/infrastructure/llm/providers/local/)）。信息库卡片只保存 API Key 与 Base URL，各能力卡片可覆盖地址并单独填写模型名称；无鉴权服务可留空 API Key。地址须从 Backend（含容器）可达；启用 SSRF 守卫时，私网地址须加入 `SSRF_ALLOWED_CIDRS`；CGNAT（100.64.0.0/10，含 Tailscale 默认地址）与云元数据地址不可豁免（见[供应商与网络错误](#供应商与网络错误)），这类地址只能在守卫关闭时使用，或改用可豁免的局域网地址。

- ComfyUI 图像能力使用固定 Qwen 工作流，模型文件自动匹配，能力卡片不支持独立 API Key 或自选权重名称。
- LLM 须支持 [Responses API](https://lmstudio.ai/docs/developer/openai-compat/responses)，显式填写已部署模型 ID，加载窗口须覆盖[适配器预算](services/infrastructure/llm/providers/local/chat.py)。
- 陪伴请求限时读取 `/props` 并在供应商实例内缓存识别结果。确认是 llama.cpp 后，无工具请求通过 `response_format.json_schema` 约束本轮输出；该接口不使用标准 `text.format`。工具阶段不混用自定义输出语法，其他本地服务或探测失败时沿用提示词与应用层校验。
- 能力链是各能力唯一调用信息源，信息库卡片只提供共享密钥与地址。用户未设 embedding 卡片时继承系统链；系统也未设卡片时该能力视为未配置。记忆只使用首个有效配置；链为空、显式链无效或调用失败时降级为[关键词召回](services/domains/memory/README.md#读取召回与恢复)，不自动切换模型。

本地向量校验与维度适配见 [embedding.py](services/infrastructure/llm/providers/local/embedding.py)：仅默认模型允许 [MRL 截断](https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF)，短向量归一化后补零，其他超宽向量拒绝。千问默认模型与 Gemini 按记忆库列宽请求输出；宽度不符的向量不入库，记忆按关键词召回，调用失败记录告警。当前记忆未记录模型标识，更换模型不能复用旧向量。

### 运营参数

登录 `/admin/` 管理供应商、能力链与运营参数。保存按[配置与迁移](#配置与迁移)生效，不要求重启容器；密钥继承与脱敏见协议文档。

自主动态任务按 [动态模块](services/application/posts/README.md) 保留快照和精简审计，未知、在途、永久幂等及仍有引用职责的记录不清理；启动和小时清理归 bootstrap 所有。

### LLM 调试日志

仅排障时临时开启 LLM 调试并将日志级别设为 DEBUG。日志写入实际执行调用的容器 stdout，可能包含原始对话内容，不能当作默认生产日志或未经脱敏的验证材料。

## 契约与验证

跨端契约见 [PROTOCOL](../docs/PROTOCOL.md)，仓库命令见 [Scripts](../scripts/README.md#按改动选择验证)。修改后按影响范围同步：

- API、WS、IM、资产或备份：核对生产方、消费方、持久化和恢复语义，并更新 [PROTOCOL](../docs/PROTOCOL.md) 的归属章节。
- `services` 依赖：更新本页[依赖理由](#services-依赖边界)，运行[分层检查器](../scripts/check_services_architecture.py)。
- 配置或迁移：核对 [Settings](components/config.py)、`system_settings` 的启动专用键和 Alembic 迁移；动态参数要验证提交失败时运行时不变。
- 提示词：沿 [提示词索引](prompts/README.md) 检查完整请求、解析和实际消费，按 [调试入口](../scripts/README.md#提示词调试) 验证。

文档改动至少核对事实、相对路径和锚点，并运行 `git diff --check`；代码改动按 [Scripts](../scripts/README.md) 选择导入、分层、构建或专项验证。
