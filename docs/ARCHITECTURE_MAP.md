# 项目架构图谱

本页用图示展示系统拓扑、模块分层、构建装配与部署形态；各图只呈现结构关系，语义、约束与契约以链接的目标文档为准。进程职责与状态权威见 [ARCHITECTURE](ARCHITECTURE.md)，数据关系见 [DATABASE](DATABASE.md)，生成链见 [PIPELINE](PIPELINE.md)。

## 系统运行拓扑

```mermaid
flowchart LR
    subgraph Desktop[用户设备]
        Client[Client 主进程与渲染窗口]
        Runner[Runner 本机工具]
        Client <-->|OS IPC 与启动 token| Runner
    end
    subgraph Cloud[Linux Docker]
        Backend[Backend web 单实例]
        DB[(PostgreSQL 与资产存储)]
        Provider[模型与媒体供应商]
        Backend <--> DB
        Backend <--> Provider
    end
    Client <-->|业务 HTTP、事件与设备 WS| Backend
    Mobile[手机浏览器 Remote] <-->|设备 Cookie、HTTP 与 WS| Backend
```

进程职责、状态权威与信任边界见 [ARCHITECTURE](ARCHITECTURE.md)。

## 后端分层与模块

```mermaid
flowchart TD
    subgraph Entry[entrypoints]
        HTTP[HTTP 路由]
        WSH[WS 连接控制]
        Sched[调度入口]
        ToolE[内置工具入口]
    end
    subgraph WF[workflows]
        Chat[chat 对话编排]
        LifeW[life_* 生活判断与执行]
        Nightly[nightly 夜间流水线]
        Auto[automation 主动回合与任务]
        Onboard[onboarding 引导]
        Maint[maintenance 账户维护]
    end
    subgraph FE[features 数据与规则]
        F1["conversation · memory · life · entries · posts · journal"]
        F2["appearance · scenes · action_library · visuals · media"]
        F3["companion · topics · automation"]
        F4["accounts · configuration · jobs · assets · updates"]
    end
    RT[runtime 运行态、连接、任务、工具注册]
    INF[infrastructure 数据库、网络、供应商、媒体处理]
    BOOT[bootstrap 显式装配与生命周期]
    Entry --> WF
    Entry --> FE
    WF --> FE
    WF --> RT
    WF --> INF
    FE --> RT
    FE --> INF
    RT --> INF
    BOOT -.装配.-> Entry
    BOOT -.装配.-> WF
    BOOT -.装配.-> FE
    BOOT -.装配.-> RT
```

依赖方向与边界检查见 [ARCHITECTURE](ARCHITECTURE.md#依赖方向) 与 [Backend](../backend/README.md#模块依赖边界)；各业务模块入口见 [Backend 任务入口](../backend/README.md#任务入口)。

## 客户端、Remote 与 Runner

```mermaid
flowchart LR
    subgraph Client[Electron 桌面 Client]
        Main[主进程 main<br/>凭据、连接、窗口、Runner、缓存]
        Preload[preload 受控桥]
        Renderer[渲染层 renderer<br/>页面、会话投影、呈现]
        Audio[隐藏音频窗口]
        Win[Windows Rust helper<br/>Explorer 挂载、托盘、工作区]
        Mac[macOS Node 模块<br/>表面、AX 窗口]
        Main <--> Preload
        Preload <--> Renderer
        Main --> Audio
        Main --> Win
        Main --> Mac
    end
    Runner[Runner Python 包<br/>终端、文件、浏览器等本机工具]
    Main <-->|命名管道 / UDS + 启动 token| Runner
    subgraph Shared["根 shared/"]
        Contract[生成契约、HTTP 与连接恢复核心]
    end
    Remote[Remote 手机浏览器应用]
    Client --- Shared
    Remote --- Shared
```

主进程准入与资源生命周期见 [client/main](../client/main/README.md)，渲染层分层见 [client/renderer](../client/renderer/README.md)，工具装配见 [Runner](../runner/README.md)，跨端身份与恢复见 [本机工具](PROTOCOL.md#本机工具)。

## 契约生成与交付构建

```mermaid
flowchart LR
    OpenAPI[HTTP 路由与 OpenAPI] --> Gen[scripts/generate_protocol.py]
    RPC[控制 RPC 与事件契约] --> Gen
    RunnerC[Runner 工具、预设与学习域目录] --> Gen
    Gen --> TS[shared/api.generated.ts]
    Gen --> Gate["--check 漂移门禁"]
    TS --> Builds[client 与 remote 构建]
    Payload[installer 载荷<br/>桌面包、Runner wheel、技能、引导音频] --> Setup[Tauri 安装器]
    Builds --> Payload
```

跨端映射与调用语义见 [通信契约](PROTOCOL.md#通道分工)，构建与发布命令见 [Scripts](../scripts/README.md)。

## 数据与状态存储

```mermaid
flowchart LR
    Backend[Backend] --> PG[(PostgreSQL<br/>业务数据、向量、outbox)]
    Backend --> Files[正式资产文件<br/>companion-assets]
    Client[Client] --> Cache[账户本地缓存<br/>资产字节、收听记录、历史快照]
    Runner[Runner] --> Journal[调用日志与技能文件]
    Remote[Remote] --> Session[会话内草稿与上传内存]
```

实体关系与删除语义见 [DATABASE](DATABASE.md)，字段归属见 [DATABASE_FIELDS](DATABASE_FIELDS.md)，缓存边界见 [资产访问与缓存](PROTOCOL.md#资产访问与缓存)。

## 调度与事件交付

```mermaid
flowchart LR
    Cron[cron 双轨] -->|special| Companion[陪伴主会话无头回合]
    Cron -->|standard| Task[automation 任务会话]
    Night[夜间流水线] --> Ledger[夜间账本与日记]
    Intent[companion_wait 意图] --> Turn[有界主动回合]
    Companion --> Outbox[(outbox)]
    Task --> Outbox
    Turn --> Outbox
    Emitter[会话 emitter 聊天流] --> WS[网关 WS 交付]
    Outbox --> WS
```

档位闸门与交接规则见 [调度](PROTOCOL.md#调度)，事件路由与恢复见 [事件路由](PROTOCOL.md#事件路由)与[序号与恢复](PROTOCOL.md#序号与恢复)。

## 部署形态

```mermaid
flowchart TD
    subgraph Host[Linux Docker 宿主]
        Backend[backend web 单实例 :10620]
        PG[(PostgreSQL :15432)]
        Updater[updater 服务更新器<br/>共享 Unix socket]
        XHS[xiaohongshu-mcp 采集服务<br/>内部网络]
        Backend --- PG
        Backend --- XHS
        Updater --> Backend
    end
    Desktop[Windows / macOS 桌面与安装器] -->|HTTP / WS| Backend
    Phone[手机浏览器 /remote/] -->|HTTP / WS| Backend
    Admin[管理端 /admin/] --> Backend
```

单副本边界、端口与运维见 [部署与运行时边界](ARCHITECTURE.md#部署与运行时边界) 与 [Backend 部署](../backend/README.md#部署与排障)。
