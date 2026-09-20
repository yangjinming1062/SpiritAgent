# Agent 阅读入口

修改前先读 [RULES.md](RULES.md)，再按任务从下表定位相关章节；进入具体模块时读对应 README 的边界与相关约束。同一任务中已读且未变化的内容无需重复加载。根 [README.md](README.md) 面向人类介绍产品，工程文档的事实归属及写法由 RULES 定义。

| 任务 | 阅读入口 |
|---|---|
| 模块边界、平台、信任域与不变量 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 形象、生命周期、交互、语音与故障体验 | [docs/DESIGN.md](docs/DESIGN.md) |
| 跨模块方法、事件、配置、凭据与安全契约 | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| 模型 / 视频生成链、供应商能力、产物与兑现 | [docs/PIPELINE.md](docs/PIPELINE.md) |
| 后端 / 客户端 / 本地工具 / 安装 | [backend](backend/README.md)、[client](client/README.md)、[runner](runner/README.md)、[installer](installer/README.md) |
| 提示词文本查找与调整 | [backend/prompts/README.md](backend/prompts/README.md) |
| 构建、发布与仓库级检查 | [scripts/README.md](scripts/README.md) |
| 凭据、准入与更新安全 | [docs/ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-安全与准入控制架构原则)、[docs/PROTOCOL.md §5](docs/PROTOCOL.md#5-跨模块安全契约) |

提交前按 RULES 执行相关验证，同步受影响的文档与链接；发布推送 `v<semver>` tag，由 GitHub Actions 完成双平台构建与 release 草稿（操作见 [scripts/README.md](scripts/README.md)）。
