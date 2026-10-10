# 提示词文本与装配索引

本包集中维护 Backend 的模型指令和纯常量，只依赖标准库。渲染、动态 schema、上下文选择、模型调用与解析归消费方；数据库中的生成提示词是审计快照，不是定义源。修改要求归 [RULES](../../RULES.md#提示词设计与修改规范)，图像链的参考与验收归 [PIPELINE](../../docs/PIPELINE.md#完整提示词链检查入口)。

## 文本与消费方

| 文本 | 内容 | 装配与消费 |
|---|---|---|
| [chat.py](chat.py) | 陪伴、工作、自动化及委派预设，回复与修复、标题、压缩 | [chat](../services/application/chat/) 的 `prompt_presets`、`prompt_blocks`、`system_prompt`、`streaming`、`title_generator`、`context_compressor` |
| [speech.py](speech.py) | 朗读指导与段内控制语义 | [speech_style.py](../services/infrastructure/llm/providers/speech_style.py) 按实际供应商和模型补能力与示例 |
| [companion.py](companion.py) | 人设与外形资料标签，交互环境、心情、空闲与空间决策，初次见面及主动回合资料 | [companion](../services/domains/companion/)、[companion_turns.py](../services/application/automation/companion_turns.py)、调度器与对话环境装配 |
| [generation.py](generation.py) | 形象、服装、壁纸、出镜媒体、动作素材，视觉提取、评分、命名和审核改写 | [generation](../services/application/generation/)、动态制作、媒体工具及角色卡渲染 |
| [memory.py](memory.py) | 共享 `MEMORY_POLICY`、审阅指令、资料标签 | [memory](../services/domains/memory/)、即时记忆工具 |
| [actions.py](actions.py) | 动作检索、提案、检查、播放工具和独立评审 | [action_tool.py](../services/adapters/tools/builtin/action_tool.py)、[actions](../services/application/actions/) |
| [desktop_videos.py](desktop_videos.py)、[desktop_tools.py](desktop_tools.py) | 桌面生活的构图、起始画面、运动、成品检查和动作工具 | [desktop_video.py](../services/application/generation/desktop_video.py)、[desktop.py](../services/application/actions/desktop.py)、[desktop_action_tool.py](../services/adapters/tools/builtin/desktop_action_tool.py) |
| [nightly.py](nightly.py) | 规划、事实账本、每日检查点、日记与反思 | [nightly](../services/application/nightly/) |
| [posts.py](posts.py) | 请求分类、独立动态创作、线程回复和共享资料语义 | [posts](../services/application/posts/) 的 `prompt_contract`、`publication`、`replies`，夜间回顾 |
| [tools.py](tools.py) | 工具与字段描述、验图、网页摘要 | [工具适配器](../services/adapters/tools/)；字段类型、枚举和必填项留在各 schema 定义 |

双语常量以 `zh` / `en` 为键，经 `components.resolve_prompt_text` 取值；消费方直接从文本所属模块导入，不设转发别名。文本在进程启动时加载，改动后须重启 Backend。

## 动态装配入口

这些位置按实际能力、状态或输出结构生成模型可读资料，检查文本时同时核对：

| 入口 | 容易遗漏的约束 |
|---|---|
| [chat/prompt_blocks.py](../services/application/chat/prompt_blocks.py)、[system_prompt.py](../services/application/chat/system_prompt.py) | 工具开关与已解锁集合、时间资料、环境和动作快照；预览基础预设不能代表最终请求 |
| [tool_runtime/domains.py](../services/infrastructure/tool_runtime/domains.py) | `search_tools` 的业务域随可用工具重算，再应用预设与本轮排除 |
| [chat/streaming.py](../services/application/chat/streaming.py)、[reply_delivery.py](../services/application/chat/reply_delivery.py) | 当前音色、可信产物、回复 schema 与格式修正共用实际交付能力 |
| [nightly_planning.py](../services/application/nightly/nightly_planning.py) | 可用能力、参数约束、互斥组、参考图能力和执行预算共同组成目录，执行端仍作硬校验 |
| [posts/prompt_contract.py](../services/application/posts/prompt_contract.py) | 从公共 schema 注入长度、内容类型和选项，创作与解析保持一致 |
| [generation/scene_prompt.py](../services/application/generation/scene_prompt.py)、[scene_image_review.py](../services/application/generation/scene_image_review.py) | 环境生成、成品分析与重复伙伴检查分别调用；初始环境要求由 `scene_service._initial_scene_notes` 给出 |
| [generation/visual_identity.py](../services/application/generation/visual_identity.py) | 聊天与动态共用出镜身份、造型和参考装配；无角色出镜的图片与视频也在此补统一的写实要求，新增生图入口须经过对应装配函数 |
| [generation/video/script.py](../services/application/generation/video/script.py) | 静态图片描述与视频起始姿态/运动脚本分开，另有探身定位校准 |
| [generation/desktop_video.py](../services/application/generation/desktop_video.py)、[actions/desktop.py](../services/application/actions/desktop.py) | 身份图与穿着图分工，完整环境起始画面、独立运动描述、循环采样检查；模式、预算与播放意图由代码校验 |
| [companion/first_greeting.py](../services/domains/companion/first_greeting.py)、[prompt_runtime.py](../services/domains/companion/prompt_runtime.py) | 问候保存为意图资料；小推理只选相关人设、记忆与近期原始对话，保留时间和截断标记 |

供应商的 `VOICE_DESIGN_GUIDE` 位于各 TTS 适配器，仅指导用户设计音色，不装入每条语音正文。

## 独立运行端与验证

Runner 不共享本包，也不内嵌模型提示词；它的工具 schema、描述与平台说明直接提供给模型，按本机工具契约核对。独立发布脚本 [gen_release_notes.py](../../scripts/gen_release_notes.py) 自行维护摘要指令。

从表中定位文本和装配器，沿完整请求、原始返回、解析、存储与呈现核对，覆盖双语、工具开关及条件分支。静态预览和真实模型质量分开报告；命令见 [Scripts](../../scripts/README.md#提示词调试)。
