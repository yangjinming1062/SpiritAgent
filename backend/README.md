# Backend

## 1. 职责与边界

负责云端对话编排、角色与记忆、资产、调度和持久化；本机执行委托 Runner，窗口与渲染归 Client。

按任务进入：[对话编排](services/application/chat/README.md)、[记忆](services/domains/memory/README.md)、[生成服务](services/application/generation/README.md)。本文只维护后端共用的依赖、配置、生命周期和运行约束。

## 2. 设计意图

桌面、IM、Cron 与子 Agent 复用回合编排；领域能力管理业务，应用层组织跨域流程，基础设施隔离供应商与传输。数据库保存业务事实，运行时状态不替代持久化；当前按单 web 进程部署，不因使用数据库认领而具备完整水平扩展能力。

## 3. 架构设计

### 3.1 物理布局与依赖总览

```text
main.py → bootstrap → api / services / components
api → services
adapters → application → domains
application / domains → infrastructure
各服务层 → contracts
modules / components / common / prompts 不反向依赖服务实现
```

`modules` 定义 ORM 与跨边界 schema；`components` 管配置、数据库、任务和日志；`common` 只放路由、模型基类等少量框架工具；`prompts` 集中存放面向 LLM 的提示词文本常量，零项目内依赖，渲染与装配留在各服务层（见 [prompts/README.md](prompts/README.md)）。迁移独立于应用实现。

跨包使用公共入口。各能力包通过 `__init__.py` 暴露符号，`services` 根包不汇总导出；导入与 facade 检查见 [Scripts](../scripts/README.md#4-import-检查--check_importspy)。

### 3.2 services 五层

`contracts` 不导入服务实现；`domains` 不依赖 application / adapters；`application` 不依赖 adapters；`infrastructure` 不反向依赖业务；`adapters` 只做协议适配。

跨域和应用包间依赖仅允许已登记的单向关系：conversation 是会话底座；companion / journal 按需使用 memory；自动化复用 chat / nightly，chat 复用回合后整理，nightly 调用生成服务。新增依赖同时核对业务理由与 [分层检查器](../scripts/check_services_architecture.py)，不使用延迟导入掩盖依赖环。

### 3.3 api 入口

`api/v1/*.py` 通过 `router = get_router()` 自动发现，负责鉴权、限流和 DTO 组装。WS 从 `api/v1/chat.py` 进入，RPC 注册在 [desktop handlers](services/adapters/desktop/handlers.py)。管理页面位于 `static/admin.html`；页面可加载不代表管理 API 免鉴权。

### 3.4 配置与迁移体系

`config.toml` 或环境变量提供启动依赖；可运营参数进入 `system_settings`，由 `Settings` 声明并在调用时读取。技术常量不承载可运营配置。

热更新顺序为：串行合并候选值 → 整批校验 → 事务提交 → 原位更新 `SETTINGS` → 刷新连接池等副作用。失败不修改运行时，避免数据库与内存分叉。

启动执行 Alembic 升级。未部署时可调整 baseline，部署后追加迁移；迁移须可降级，回填须幂等，破坏性变更说明风险。类型与默认值需比对，视频任务模型的显式导入及迁移中维护的 PostgreSQL 部分、向量和全文索引不能误删。

### 3.5 装配与生命周期

`bootstrap` 是唯一装配入口。供应商、工具、渠道、内部事件及域钩子显式注册；注册可重复，未登记能力显式失败。

启动依次完成配置检查、迁移、配置水合与目录准备、调度器、事件回路、渠道桥和任务恢复。停止先关闭清理任务与调度入口，再收敛模块任务，停止渠道桥与事件回路，最后释放数据库和连接池。

`MANAGER`、`REGISTRY`、`SETTINGS` 和用户锁等单例遵守单进程边界；bootstrap 管装配，不另建通用依赖注入容器。

### 3.6 事件与交付回路

需与状态共同生效的通知通过 `emit_ws_event` 同事务写入 outbox，随后由 NOTIFY 唤醒、原子认领和分派；内部事件进入已注册处理器，桌面事件进入用户 dispatcher。失败按预算退避，超限进入死信；发送记账与历史清理由所属回路完成。

聊天流另走会话 emitter。存储层不认识业务处理器，任务须有明确所有者，不能用无托管后台任务绕开退出与恢复。

## 4. 关键设计决策

### 陪伴叙事

检索记忆与片刻、日记分开维护。白天自主片刻只更新信息流，不写主对话或产生桌面打扰；互动统计按用户本地日聚合。证据和维护规则归 [记忆模块](services/domains/memory/README.md)。

### 夜间批处理

调度传入刚结束的本地日，缺时区跳过；同日完成不重跑，最近未完成日按恢复窗口接续。计划与动作账本持久化，执行按外观 → 房间 → 片刻 / 媒体 → 联系推进，每项前重读政策，失败互相隔离。

无当日消息仍可依据长期记忆规划，但没有新互动时不虚构日记。片刻发布和评论纳入当天经历；只有成功结果进入叙事。有任务句柄时核对原任务，结果未知的在途动作保留中断事实，不盲目重发。

### 数据与运行可靠性

数据库会话采用短读 → 无会话模型等待 → 短写，关系显式预加载，时间戳带时区。图片、视频、GLB 等大字节处理与落盘卸载到工作线程；正式资产使用统一异步写入入口处理取消清理。

本机派发先注册等待对象，再发送并检查入队结果；直接持对象等待，避免极速返回后查表丢失。桌面离线以业务错误结束等待，不能一律抛取消异常而使 IM 回合静默退出。

备份不迁移登录、激活、IM 授权、事件队列和执行账本，也不恢复已清理媒体；供应商与本机配置另行准备。包级校验、部分恢复和维护态见 [PROTOCOL](../docs/PROTOCOL.md#56-备份校验与覆盖恢复)。

### 陪伴调度与恢复

[等待域](services/domains/companion/intents.py)管理条件、有效期、认领和原子终态；[陪伴回合](services/application/automation/companion_turns.py)复用工具循环并限制轮数、时长和委派。取消或失败不提交暂存续等。

未开始的认领可以重试；已执行而结果不明时先查询 Runner 调用日志，不重跑副作用。有效期结束不抹去核对信息。创建、恢复及解除暂停统一在用户锁下检查活跃任务配额；重启不凭空补算未互动时长。

### IM 渠道

适配器由装配层注册。接收锁保证落库与入队顺序，投递锁避免并发补发；登录、入站、回合、typing 和补发均归绑定实例，退出或重建前取消并等待整棵任务树。

iLink 轮询持续返回 `-14` 才按登录失效处理；发送时的同码仅代表回复上下文失效，等待下一次来信，不直接触发重新扫码。媒体经渠道加解密转换，配对、排队、只读与本机授权见 [PROTOCOL](../docs/PROTOCOL.md#17-im-通道桥接apichannels)。

### 供应商与网络错误

供应商身份由注册与配置决定，不从 URL 推断。幂等方法、显式幂等键或确认未发送的连接失败才可自动重试；请求体须可重放。非幂等请求在写入或读取响应阶段断线按结果未知处理，不能直接换供应商再提交。

SSRF 默认拒绝保留网段；显式 fake-IP 豁免不取消域名、协议、HTTPS 降级、云元数据与 CGNAT 检查。对外错误脱敏，内部诊断保留原因。

### 数据结构定义

跨边界或需校验的结构使用 Pydantic；纯进程内值对象使用 dataclass，只读对象可冻结。不使用 `TypedDict`，也不在调用方重复声明已有 schema。

## 5. 与外部的契约

通信、配置、安全和恢复统一见 [PROTOCOL](../docs/PROTOCOL.md)，形象输入与产物见 [PIPELINE](../docs/PIPELINE.md)。内部实现调整未改变契约时，不重复修改外部文档。

## 6. 验证入口

使用 [Scripts 检查入口](../scripts/README.md#8-按改动选择验证)。Python 修改核对 lint、导入与类型，依赖变化加跑分层检查；数据库变化另做迁移比对。对话、记忆和生成按专项 README 验证，静态检查不能代替真实供应商与恢复验证。

## 7. 部署与监控

### Docker Compose 部署

在 `backend` 目录按 [配置模板](config.toml.example)准备数据库、JWT、资产签名密钥和管理员配置，然后执行：

```bash
docker compose up -d
# 同时启动监控
docker compose --profile monitoring up -d
```

容器与卷见 [docker-compose.yml](docker-compose.yml)，指标抓取见 [Prometheus 配置](monitoring/prometheus.yml)。Backend 不参与桌面安装包构建。

### 参数配置与后台动态热更新

登录 `/admin/` 管理供应商、能力链与运营参数。保存按 §3.4 生效，不要求重启容器；密钥继承与脱敏见协议文档。

### LLM 调试日志

仅排障时临时开启 LLM 调试并将日志级别设为 DEBUG。日志写入实际执行调用的容器 stdout，可能包含原始对话内容，不能当作默认生产日志或未经脱敏的验证材料。
