# Agent 阅读入口

修改前读 [RULES.md](RULES.md)。初次了解项目读 [ARCHITECTURE](docs/ARCHITECTURE.md)；具体任务按下表进入相关章节和模块 README，再沿入口核对代码、消费方与验证要求，无需通读所有主题文档。已读且未变化的内容不重复加载。

| 要修改的功能 | 先读的语义 | 代码与联动入口 |
|---|---|---|
| 对话、气泡、语音、历史恢复 | [会话与消息](docs/PROTOCOL.md#会话与消息) | [对话编排](backend/services/application/chat/README.md#关键入口)、[Client](client/README.md#任务入口) |
| 初始化、角色卡、换装 | [认识伙伴](docs/DESIGN.md#认识伙伴)、[身份与参考](docs/PIPELINE.md#身份造型与参考输入) | [生成服务](backend/services/application/generation/README.md#关键入口) |
| 场景创建、重生成与切换 | [场景任务](docs/PROTOCOL.md#场景任务与原位换图)、[启用与授权](docs/PROTOCOL.md#场景启用与授权) | [场景改动链](backend/services/application/generation/README.md#场景改动链) |
| 动作制作、目录与播放 | [动作资产](docs/PIPELINE.md#动作资产制作)、[播放契约](docs/PROTOCOL.md#动作目录与播放) | [动作编排](backend/services/application/actions/README.md)、[渲染层](client/renderer/README.md#角色呈现契约) |
| 记忆学习、编辑与遗忘，学习技能 | [记忆作用域](docs/PROTOCOL.md#预设记忆与学习作用域) | [记忆模块](backend/services/domains/memory/README.md)、[Runner 学习技能](runner/README.md#按预设学习技能) |
| 主动陪伴、Cron 与夜间 | [在线与夜间体验](docs/DESIGN.md#主动陪伴)、[调度契约](docs/PROTOCOL.md#调度与渠道) | [Backend 任务入口](backend/README.md#任务入口) |
| 动态与日记 | [动态体验](docs/DESIGN.md#生活动态)、[日记体验](docs/DESIGN.md#第一人称日记)、[契约](docs/PROTOCOL.md#动态与日记) | [Backend 任务入口](backend/README.md#任务入口) |
| IM 配对、消息与本机能力 | [IM 契约](docs/PROTOCOL.md#im-通道) | [Backend IM](backend/README.md#im-渠道)、[Runner](runner/README.md#任务入口) |
| 窗口、拖拽、主题、多屏 | [窗口与会话](docs/DESIGN.md#窗口与会话)、[主题](docs/DESIGN.md#主题与图片查看)、[桌面表现](docs/DESIGN.md#桌面表现与移动) | [Client 任务入口](client/README.md#任务入口)、[窗口与主题约束](client/README.md#窗口与主题) |
| 工具、能力同步、执行取消 | [本机工具契约](docs/PROTOCOL.md#本机工具) | [Runner 任务入口](runner/README.md#任务入口)、[Client 主进程](client/main/README.md#runner-生命周期) |
| 激活、账户切换、凭据、配置与更新 | [信任边界](docs/ARCHITECTURE.md#信任边界与安全)、[账户体验](docs/DESIGN.md#激活与账户切换)、[安全契约](docs/PROTOCOL.md#安全更新与备份) | [Client](client/README.md#任务入口)、[Backend 任务入口](backend/README.md#任务入口) |
| 首次安装、修复与内置技能 | [安装与修复](installer/README.md#安装与修复) | [Installer 任务入口](installer/README.md#任务入口)、[内置技能文档](installer/README.md#内置技能文档) |
| 后端配置、部署、供应商与管理后台 | [部署边界](docs/ARCHITECTURE.md#部署与运行时边界)、[AI 配置与密钥](docs/PROTOCOL.md#ai-配置与密钥) | [Backend 任务入口](backend/README.md#任务入口)、[配置与迁移](backend/README.md#配置与迁移)、[部署与排障](backend/README.md#部署与排障) |
| 备份与覆盖恢复 | [恢复契约](docs/PROTOCOL.md#备份校验与覆盖恢复) | [Backend 任务入口](backend/README.md#任务入口) |
| 提示词与供应商输入 | [提示词规范](RULES.md#提示词设计与修改规范) | [文本与装配索引](backend/prompts/README.md)、[生成链验证](docs/PIPELINE.md#完整提示词链检查入口) |
| 构建、检查与发布 | [Scripts](scripts/README.md) | 文档改动核对事实和链接；发布流程见该文档 |

根 [README](README.md) 面向使用者；工程事实归属见 [RULES](RULES.md#文档职责与事实归属)。
