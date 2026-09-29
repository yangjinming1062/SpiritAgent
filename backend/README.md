# Backend

负责云端对话编排、角色与记忆、资产、调度和持久化。本机执行委托 Runner，窗口与渲染归 Client。

## 任务入口

| 要修改的功能 | 代码入口与联动文档 |
|---|---|
| 对话、语音、上下文与工具循环 | [对话编排](services/application/chat/README.md#关键入口) |
| 记忆证据、召回与遗忘 | [记忆模块](services/domains/memory/README.md) |
| 头像、角色卡、衣柜、场景与媒体 | [生成服务](services/application/generation/README.md#关键入口)；跨端场景改动见其任务链 |
| 动作提案、生成与播放 | [动作编排](services/application/actions/README.md)、[动作领域](services/domains/actions/README.md) |
| Cron 与在线陪伴 | [scheduler/cron.py](services/adapters/scheduler/cron.py) → [companion_turns.py](services/application/automation/companion_turns.py) / [standard_turns.py](services/application/automation/standard_turns.py)；任务与 standard 执行会话归 [cron_jobs.py](services/domains/automation/cron_jobs.py)，等待状态与后台认领归 [intents.py](services/domains/companion/intents.py) |
| 夜间计划与执行 | [nightly_activity.py](services/application/nightly/nightly_activity.py) 的 `run_nightly_pipeline` → [nightly_planning.py](services/application/nightly/nightly_planning.py)；检查[阶段与恢复](#夜间批处理) |
| 片刻与日记 | [journal_service.py](services/domains/journal/journal_service.py)、[autonomous.py](services/application/moments/autonomous.py)、[replies.py](services/application/moments/replies.py)；桌面端点 [companion_journal.py](api/v1/companion_journal.py) |
| IM 生命周期与投递 | [channels/manager.py](services/adapters/channels/manager.py)；[IM 约束](#im-渠道) |
| 备份校验与覆盖恢复 | [manifest.py](services/domains/backup/manifest.py)、[restoration.py](services/domains/backup/restoration.py)；核对 [恢复契约](../docs/PROTOCOL.md#备份校验与覆盖恢复)的维护态、引用映射与部分恢复 |
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
| `modules` | ORM 与跨边界 schema |
| `components` | 配置、数据库、任务与日志 |
| `common` | 路由、模型基类等少量框架工具 |
| `prompts` | 零项目内依赖的提示词文本常量；渲染与装配留在服务层 |
| `alembic` | 独立于应用实现的迁移 |

`modules`、`components`、`common`、`prompts` 不反向依赖服务实现。跨包使用公共入口；能力包通过 `__init__.py` 暴露符号，`services` 根包不汇总导出。检查见 [Scripts](../scripts/README.md#导入检查)。

### services 依赖边界

主要依赖方向为 `adapters → application → domains`；application / domains 可调用 infrastructure，各服务层可使用 contracts。

| 特殊依赖 | 业务理由 |
|---|---|
| 各领域 → conversation | 会话底座 |
| companion / journal → memory | 陪伴与叙事读取记忆 |
| companion → actions | 只读当前包动作目录快照 |
| backup → actions | 恢复动作资产后重建目录并复用发布校验 |
| automation → chat / nightly | 复用自动化执行流程 |
| chat → nightly | 回合后整理 |
| nightly → generation | 制作夜间资产 |
| application/actions → generation | 制作动作素材 |
| chat / nightly → application/actions | 读取动作上下文 |

- `contracts` 不导入服务实现；`domains` 不依赖 application / adapters；application 不依赖 adapters；infrastructure 不反向依赖业务。
- 新增跨域或应用包依赖须有业务理由，并核对[分层检查器](../scripts/check_services_architecture.py)。不使用延迟导入掩盖环。

### API 入口

`api/v1/*.py` 通过 `router = get_router()` 自动发现，负责鉴权、限流和 DTO 组装。WS 从 `api/v1/chat.py` 进入，RPC 注册在 [desktop handlers](services/adapters/desktop/handlers.py)。管理页面位于 `static/admin.html`；页面可加载不代表管理 API 免鉴权。

## 配置与运行生命周期

### 配置与迁移

`config.toml` 或环境变量提供启动依赖；可运营参数进入 `system_settings`，由 `Settings` 声明并在调用时读取。技术常量不承载可运营配置。

热更新入口为 [system_settings.py](services/application/configuration/system_settings.py)：串行合并候选值 → 整批校验 → 事务提交 → 原位更新 SETTINGS → 刷新连接池等副作用。

校验或提交失败不修改运行时，避免数据库与内存分叉。持久值统一 JSON 编码，启动水合时解析或校验失败即中止启动。

用户偏好 `user_settings` 同样按点键逐值 JSON 编码（桌面配置同步与服务端写入如时区共用同一格式），只经 [modules/settings](modules/settings/values.py) 读写，读取即得解码后的原值，消费方不自行解析。

启动执行 Alembic 升级。未部署时可调整 baseline，部署后追加迁移；迁移须可降级，回填须幂等，破坏性变更说明风险。类型与默认值需比对，迁移中维护的 PostgreSQL 部分、向量和全文索引不能误删。

### 装配与启停

`bootstrap` 是唯一装配入口。供应商、工具、渠道、内部事件和域钩子显式注册；注册可重复，未登记能力明确失败。

| 阶段 | 顺序与归属 |
|---|---|
| 启动 | 配置检查 → 迁移 → 配置水合与目录准备 → 调度器 → 事件回路 → 渠道桥 → 任务恢复 |
| 恢复 | 聊天视频、视频包生成/导入、动作提案评审、角色卡提取、场景、初始外观 |
| 停止 | 关闭清理任务与调度入口 → 收敛模块任务 → 停渠道桥与事件回路 → 释放数据库、Web 供应商及 LLM 连接池 |

`MANAGER`、`REGISTRY`、`SETTINGS` 与用户锁遵守单进程边界。bootstrap 管装配，不另建通用依赖注入容器。

### 事件与交付

`emit_ws_event` 随业务状态同事务写 outbox，经 NOTIFY 唤醒、原子认领后分派到内部处理器或用户 dispatcher；失败按预算退避，超限进入死信。发送记账与清理由所属回路管理。聊天流另走会话 emitter，存储层不认识业务处理器；后台任务纳入所有者的启停与恢复。

## 数据与运行可靠性

数据库会话采用短读 → 无会话模型等待 → 短写，关系显式预加载，时间戳带时区。图片与视频等大字节处理与落盘卸载到工作线程；正式资产使用统一异步写入入口处理取消清理。

本机派发先注册等待对象，再发送并检查入队结果；直接持对象等待，避免极速返回后查表丢失。桌面离线以业务错误结束等待，不能一律抛取消异常而使 IM 回合静默退出。

备份不迁移登录、激活、IM 授权、事件队列和执行账本，也不恢复已清理媒体；供应商与本机配置另行准备。包级校验、部分恢复和维护态见 [PROTOCOL](../docs/PROTOCOL.md#备份校验与覆盖恢复)。

用户文件只落三处：`companion-assets/{user_id}/`（立绘与全部正式资产）、各会话的 `desktop-attachments/{session_id}/`，以及元数据记录 `user_id` 的 `temp-media/`。删除用户（被遗忘权）复用覆盖恢复的维护边界停稳运行时，先删除这三处文件、再删除用户行由外键级联清理其余数据；任一步失败都保留用户行，可重试。新增用户文件存储须落在这三处之一，否则删除与备份都会遗漏。

## 业务调度

### 陪伴叙事

检索记忆与片刻、日记分开维护。白天自主片刻只更新信息流，不写主对话或产生桌面打扰；互动统计按用户本地日聚合。片刻媒体由生成方先存为正式资产，写入入口拒绝外部或临时地址。证据和维护规则归 [记忆模块](services/domains/memory/README.md)。

### 夜间批处理

- 调度传入刚结束的本地日，缺时区跳过；同日完成不重跑，最近未完成日按恢复窗口接续。
- 规划入口必须绑定夜间日志，计划与动作账本持久化后执行；每项先核对依赖与当前政策，终态统一落库，失败互相隔离。
- 执行按外观、场景、片刻 / 媒体与联系的阶段推进，场景不依赖外观动作。
- 无当日消息仍可依据长期记忆规划，但没有新互动时不虚构日记。
- 片刻发布和评论纳入当天经历；只有成功结果进入叙事。
- 有任务句柄时核对原任务，结果未知的在途动作保留中断事实，不盲目重发。
- 规划的动作 ID 保持原样并校验唯一性；能力或预算过滤掉前置动作后，后续依赖仍保留并按未完成跳过。
- 依赖要求前置整项成功，部分成功只提供已完成事实，不解锁依赖整项完成的后续动作。
- 文本与媒体参数在执行前校验长度，不以截断改变已规划的表达。
- 内部反思按实际存储字符预算生成，结构或长度不符时有一次修复机会，仍不符则不保存；用户可见日记与内部反思的用途、正文和存储入口保持分离。

### 陪伴调度与恢复

[等待域](services/domains/companion/intents.py)管理条件、有效期、认领和原子终态；[陪伴回合](services/application/automation/companion_turns.py)复用工具循环并限制轮数、时长和委派。取消或失败不提交暂存续等。

未开始的认领可以重试；已执行而结果不明时先查询 Runner 调用日志，不重跑副作用。有效期结束不抹去核对信息。创建、恢复及解除暂停统一在用户锁下检查活跃任务配额；重启不凭空补算未互动时长。

### IM 渠道

适配器由装配层注册。接收锁保证落库与入队顺序，投递锁避免并发补发；登录、入站、回合、typing 和补发均归绑定实例，退出或重建前取消并等待整棵任务树。

iLink 轮询持续返回 `-14` 才按登录失效处理；发送时的同码仅代表回复上下文失效，等待下一次来信，不直接触发重新扫码。媒体经渠道加解密转换；配对、排队、只读与本机授权见 [PROTOCOL](../docs/PROTOCOL.md#im-通道)。

## 供应商与网络错误

- 供应商身份由注册与配置决定，不从 URL 推断。
- 幂等方法、显式幂等键或确认未发送的连接失败才可自动重试；请求体须可重放。
- 非幂等请求在写入或读取响应阶段断线按结果未知处理，不能直接换供应商再提交。
- 能力链换家只看 [错误分类](services/infrastructure/llm/error_classifier.py) 的 `should_fallback`：确定性失败，以及本家传输层重试耗尽后的超时 / 过载；结果未知或流已开始时不换家。

- 出站 SSRF 守卫默认关闭（`SSRF_GUARD_ENABLED`，管理后台可热切换）：关闭时不做保留网段与黑名单校验，DNS 污染 / fake-ip 代理环境不再误拦正常出站，内网访问风险由部署者自担。
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

容器与卷见 [docker-compose.yml](docker-compose.yml)，指标抓取见 [Prometheus 配置](monitoring/prometheus.yml)。Backend 不参与桌面安装包构建。

后端镜像安装 FFmpeg（含 `ffprobe`），用于视频探测、抠像和转码；构建时检查两个命令可执行。更新 Dockerfile 后，在 `backend` 目录执行 `docker compose up -d --build backend` 重建并替换容器。

### 本地供应商

`local` 默认对接 LM Studio（LLM / embedding）和 ComfyUI（图像），默认地址见各适配器的 `DEFAULT_BASE_URL`（[local](services/infrastructure/llm/providers/local/)）。各能力卡片可覆盖信息库中的地址与模型；无鉴权服务可留空 API Key。地址须从 Backend（含容器）可达；启用 SSRF 守卫时，私网地址须加入 `SSRF_ALLOWED_CIDRS`。

- LLM 须支持 [Responses API](https://lmstudio.ai/docs/developer/openai-compat/responses)，显式填写已部署模型 ID，加载窗口须覆盖[适配器预算](services/infrastructure/llm/providers/local/chat.py)。
- 用户未设 embedding 卡片时继承系统链；系统也未设卡片时，按系统信息库顺序选用支持向量的供应商及其默认模型。记忆只使用首个有效配置；显式链无效或调用失败时降级为[关键词召回](services/domains/memory/README.md#读取召回与恢复)，不自动切换模型。

本地向量校验与维度适配见 [embedding.py](services/infrastructure/llm/providers/local/embedding.py)：仅默认模型允许 [MRL 截断](https://huggingface.co/Qwen/Qwen3-Embedding-4B-GGUF)，短向量归一化后补零，其他超宽向量拒绝。当前记忆未记录模型标识，更换模型不能复用旧向量。

### 运营参数

登录 `/admin/` 管理供应商、能力链与运营参数。保存按[配置与迁移](#配置与迁移)生效，不要求重启容器；密钥继承与脱敏见协议文档。

### LLM 调试日志

仅排障时临时开启 LLM 调试并将日志级别设为 DEBUG。日志写入实际执行调用的容器 stdout，可能包含原始对话内容，不能当作默认生产日志或未经脱敏的验证材料。

## 契约与验证

跨端契约见 [PROTOCOL](../docs/PROTOCOL.md)，命令见 [Scripts](../scripts/README.md#按改动选择验证)。依赖变化检查分层；数据库变化做迁移比对；对话、记忆与生成按专项 README 验证。
