# Agent 阅读入口

修改前先读 [RULES.md](RULES.md)，再读 [ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [DESIGN.md](docs/DESIGN.md) 理解背景；进入具体模块前读对应 README。根 [README.md](README.md) 面向人类介绍产品，其余工程文档用于指导 Agent 修改与审查，事实归属及写法由 RULES 定义。

| 任务 | 阅读入口 |
|---|---|
| 模块边界、平台、信任域与不变量 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 形象、生命周期、交互、语音与故障体验 | [docs/DESIGN.md](docs/DESIGN.md) |
| 跨模块方法、事件、配置、凭据与安全契约 | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| 3D / 2D 生成链、供应商能力、产物与兑现 | [docs/PIPELINE.md](docs/PIPELINE.md) |
| 后端 / 客户端 / 本地工具 / 安装 | [backend](backend/README.md)、[client](client/README.md)、[runner](runner/README.md)、[installer](installer/README.md) |
| 构建、发布与仓库级检查 | [scripts/README.md](scripts/README.md) |
| 漏洞报告与安全事件 | [docs/SECURITY.md](docs/SECURITY.md) |

提交前按 RULES 执行相关验证，同步受影响的文档与链接；发布执行 scripts 中的完整构建链。
