# 提示词索引

全仓 LLM 提示词文本集中登记处：只放面向模型的指令文本与无副作用纯常量（双语 dict、模板骨架、种子词表）。渲染器、装配器、ORM 实例与调用逻辑归各服务层；零项目内依赖（至多标准库），与 `common`/`components`/`modules` 同属最底层。

## 修改路径

从下表定位文本及装配方，再沿完整请求、原始返回、解析与实际呈现核对；要求归 [RULES](../../RULES.md#提示词设计与修改规范)。文本变更须重启 Backend，运行时不做热更新；调试见 [Scripts](../../scripts/README.md#提示词调试)。

## 模块索引

| 模块 | 内容 | 渲染与装配 |
|---|---|---|
| `chat.py` | 预设体骨架（陪伴 / 工作 / 自动化）、四职业双语头部、系统提示词块、陪伴回复格式与修复及媒体气泡规则、标题生成、上下文压缩 | `services/application/chat/`（prompt_blocks、prompt_presets、system_prompt、streaming、title_generator、context_compressor） |
| `companion.py` | 心情、空闲表达、空间行为、动态性格标签、角色设定与着装块标题、初次见面主动意图 | `services/domains/companion/` |
| `generation.py` | 角色、外观、场景与出镜媒体的图像及视频提示词，以及身份评审与评分、角色卡提取、服装与场景描述等视觉理解指令，和造型命名描述、审核拒绝后改写等文本指令；共享风格机制见 [PIPELINE](../../docs/PIPELINE.md#提示词与供应商输入) | `services/infrastructure/llm/prompt_engineer.py`、`services/application/generation/`、`services/application/nightly/`、`services/adapters/tools/builtin/`、`services/domains/companion/character_card.py` |
| `memory.py` | 记忆维护政策（`MEMORY_POLICY`）、审查指令、用户资料上下文标签与块标题 | `services/domains/memory/`（memory_review、memory_bootstrap）、`services/adapters/tools/memory.py` |
| `actions.py` | 动作检索、设计提案、状态检查与播放工具描述及独立评审指令 | `services/adapters/tools/builtin/action_tool.py`、`services/application/actions/`（review、context） |
| `nightly.py` | 夜间规划、每日检查点、用户可见日记、内部夜间反思、片刻回复、片刻冲动决策 | `services/application/nightly/`、`services/application/moments/` |
| `tools.py` | 工具 schema 的主描述与参数描述，以及验图（`MEDIA_INSPECTION_INSTRUCTIONS`）与网页摘要（`WEB_SUMMARY_INSTRUCTIONS`）指令；schema 结构（name/enum/类型/required）留在各工具文件 | `services/adapters/tools/`（builtin/ 与同级 `*.py`）、`services/application/generation/chat_images.py` |

## 场景与动作装配

**场景**

- `chat.py::SCENE_TOOL_GUIDANCES` / `tools.py::SCENE_TOOL_DESCRIPTIONS`：自主创建与启用的决策、输入及结果语义，由 `scene_tool.py` 执行；客户端手动操作与工具路径独立，契约见 [场景启用与授权](../../docs/PROTOCOL.md#场景启用与授权)。
- `generation.py::SELF_IMAGE_*` / `SELF_VIDEO_*`：只表达本次造型；图片由 `visual_identity.py::build_self_image_prompt` 共用于聊天与夜间。
- `generation.py::SCENE_TEMPLATE` / `SCENE_REFERENCE` / `SCENE_IMAGE_RULES`：创建与图片重绘共用 `scene_prompt.py`，按实际参考数量与可选造型装配；输入选择见 [身份、造型与参考输入](../../docs/PIPELINE.md#身份造型与参考输入)。
- `generation.py::SCENE_DESCRIBE_SYSTEM`：成品描述双用途，JSON 契约与 `modules/companion` 的 `SceneDescriptionRequest` 对齐。
- 初始场景默认文案在 `scene_service.py::_INITIAL_SCENE_DEFAULT_NOTES`。

**动作**

- 对话：`actions.py` 工具说明与字段 schema → `application/actions/context.py` 动态资料 → 工具结果续轮；气泡正文与语音格式只约束台词交付，工具关闭时资料仍可保留，不能据此假设有操作能力。
- 夜间：`nightly.py::PLANNING_SYSTEM_PROMPT` 与夜间能力目录 → 提案受理 → 后台独立评审；受理成功只证明已申请，后续叙事不能据此推断已制作或已表演。
- 制作：`actions.py::ACTION_REVIEW_INSTRUCTIONS` 与冻结参考图、候选动作 → `generation.py::VIDEO_ACTION_SCRIPT_INSTRUCTIONS` → 起始姿态图与运动描述 → 视频。内置和动态动作共用描述字段；loop 连续循环，once 自然收束。反馈与交付见 [评审与制作](../../docs/PIPELINE.md#评审与制作)。
- 探身：`generation.py` 的 `VIDEO_PEEK_ACTION_DESCRIPTION` 与 `VIDEO_PEEK_GEOMETRY_INSTRUCTIONS` 分别供[动作脚本与定位校准](../services/application/generation/video/script.py)消费；产物要求见 [PIPELINE](../../docs/PIPELINE.md#系统动作与动态动作)。
- 空闲：`companion.py::IDLE_EXPRESSION_INSTRUCTIONS` 与当前可用动作的内容、适用/避免条件 → 单个 `action_id` → 统一播放。中英文保持相同的选择与空值语义。

完整外观链路及各分支的审查导航见 [完整提示词链检查入口](../../docs/PIPELINE.md#完整提示词链检查入口)。

头像由装配器统一追加 `AVATAR_IMAGE_RULES` 和画风；参考分工、资料优先级及审核改写要求见 [身份、造型与参考输入](../../docs/PIPELINE.md#身份造型与参考输入)。

## 命名与消费

- 双语 dict 键为 `zh`/`en`，消费方用 `components.resolve_prompt_text` 取文本。
- `JOURNAL_DIARY_TEXTS`（用户可见日记）与 `NIGHTLY_REFLECTION_TEXTS`（内部夜间反思）是内容不同的两套文本，不可混用。
- 本包不设转发别名：消费方按模块文件直接导入常量（如 `from prompts.chat import COMPANION_CHAT_GUIDANCES`）；重命名时用常量名全仓搜索同步所有导入点。
- Runner 是独立物理模块（独立 pyproject、物理解耦），其提示词不在本包；例外见下文 Runner 一节。

## 服务层动态提示词

- [夜间能力目录](../services/application/nightly/nightly_planning.py)——描述与参数选项随能力可用性装配，字段说明包含类型、用途及必要的可选性、长度和取值约束；说明供规划模型阅读，实际校验由参数模型与执行器负责。动作预算从执行端常量传入 `plan_limits`；检查规划提示词时同时核对目录、互斥组和参考图能力。
- [语音气泡演绎能力](../services/infrastructure/llm/providers/speech_style.py)——按实际供应商与模型装配能力和 JSON 示例，附加到陪伴终端请求；检查正文规则时一并核对。
- [媒体气泡](../services/application/chat/streaming.py)——`chat.py::COMPANION_MEDIA_REPLY_GUIDANCES` 与可信产物清单装配到正常和恢复请求；`tools.py` 的批次生成、验图与重做说明对应 [chat_images.py](../services/application/generation/chat_images.py)。
- [时间与共享块装配](../services/application/chat/prompt_blocks.py)——工具开关与实际解锁集合决定能力描述，时间资料只表达经过时间，不推断用户经历。
- [初次见面意图](../services/domains/companion/first_greeting.py)——`companion.py::FIRST_MEETING_INTENT_TEXTS` 按用户语言写入意图记录，由[主动回合](../services/application/automation/companion_turns.py)作为资料消息与 `chat.py::COMPANION_PROACTIVE_GUIDANCES` 一同装配；两者对开口与沉默（`[]`）的约定须一致。
- [陪伴小推理资料](../services/domains/companion/prompt_runtime.py)——心情、空闲表达、空间行为与片刻使用人设和相关记忆，不附加完整视觉形象资料；近期对话保留原始角色、时间和截断标记，旧请求不自动成为当前触发条件。
- 音色设计说明：[MiniMax](../services/infrastructure/llm/providers/minimax/tts.py)、[MiMo](../services/infrastructure/llm/providers/mimo/tts.py) 等各供应商 TTS 模块的 `VOICE_DESIGN_GUIDE`——供应商支持的描述维度，供用户创建音色；不能混入逐条语音的正文。
- 数据库 `AvatarAsset.prompt_json` 等审计字段是生成时快照，不是定义源。

## 独立运行端

### Runner

Runner 当前不内嵌模型提示词；各工具目录的 schema、描述与终端平台说明直接提供给模型，须连同开关、权限、参数及返回语义检查。Runner 与 Backend 无共享代码包（独立 pyproject、物理解耦），不为提示词引入跨模块共享机制；改动按 [本机工具契约](../../docs/PROTOCOL.md#本机工具)验证。

### 发布脚本

[gen_release_notes.py](../../scripts/gen_release_notes.py) 独立运行，维护提交摘要的系统提示词；提交记录是资料，验证范围和功能影响范围须分别保留，不从提交推断发布或测试已经完成。

## 验证

通用提示词验证遵循 [RULES](../../RULES.md#提示词设计与修改规范)。本包重点核对共享块在各预设、工具开关和双语下的装配；图像链核对[参考顺序与身份约束](../../docs/PIPELINE.md#验证)。静态预览只检查装配，不能证明模型质量。
