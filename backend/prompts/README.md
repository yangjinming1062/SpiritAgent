# 提示词索引

全仓 LLM 提示词文本集中登记处：只放面向模型的指令文本与无副作用纯常量（双语 dict、模板骨架、种子词表）。渲染器、装配器、ORM 实例与调用逻辑归各服务层；本包只依赖标准库，处于最底层。

## 修改路径

从下表定位文本及装配方，再沿完整请求、原始返回、解析与实际呈现核对；要求归 [RULES](../../RULES.md#提示词设计与修改规范)。文本变更须重启 Backend，运行时不做热更新；调试见 [Scripts](../../scripts/README.md#提示词调试)。

## 模块索引

| 模块 | 内容 | 渲染与装配 |
|---|---|---|
| `chat.py` | 预设体骨架（陪伴 / 工作 / 自动化）、四职业双语头部、系统提示词块、陪伴回复格式与修复及媒体气泡规则、标题生成、上下文压缩 | `services/application/chat/`（prompt_blocks、prompt_presets、system_prompt、streaming、title_generator、context_compressor） |
| `companion.py` | 心情、空闲表达、空间行为、动态性格标签、角色设定字段与外形资料标签、当前着装标题、初次见面与低频问候意图、陪伴回合尾部资料标注 | `services/domains/companion/`、`services/application/automation/companion_turns.py`、`services/adapters/scheduler/cron.py`、`services/application/chat/orchestrator.py` |
| `generation.py` | 角色、外观、场景、动作与出镜媒体的图像及视频提示词，以及身份评审与评分、角色卡提取、服装与场景描述等视觉理解指令，和造型命名描述、审核拒绝后改写等文本指令；共享风格机制见 [PIPELINE](../../docs/PIPELINE.md#提示词与供应商输入) | `services/application/generation/`（`appearance_prompts.py` 与各功能装配器）、`services/application/posts/`、`services/adapters/tools/builtin/`、`services/domains/companion/character_card.py` |
| `memory.py` | 记忆维护政策（`MEMORY_POLICY`）、审查指令、用户资料上下文标签、记忆块标题与依据标签 | `services/domains/memory/`（memory_review、memory_bootstrap、memory_format）、`services/adapters/tools/memory.py` |
| `actions.py` | 动作检索、设计提案、状态检查与播放工具描述及独立评审指令 | `services/adapters/tools/builtin/action_tool.py`、`services/application/actions/`（review、context） |
| `nightly.py` | 夜间规划、次日联系事项模板、夜间叙事事实、每日检查点及其标题、自主日记发布、关系与相处方式的夜间反思 | `services/application/nightly/` |
| `posts.py` | 发布请求判断、独立动态创作与线程回复，以及回复和夜间回顾共用的创作资料语义 | `services/application/posts/`（含由公共 schema 装配长度与选项的 `prompt_contract.py`）、`nightly.py` |
| `tools.py` | 工具 schema 的主描述与参数描述，以及验图（`MEDIA_INSPECTION_INSTRUCTIONS`）与网页摘要（`WEB_SUMMARY_INSTRUCTIONS`）指令；schema 结构（name/enum/类型/required）留在各工具文件 | `services/adapters/tools/`（builtin/ 与同级 `*.py`）、`services/application/generation/chat_images.py` |

## 场景与动作装配

**场景与出镜媒体**

- `chat.py::SCENE_TOOL_GUIDANCES` / `tools.py::SCENE_TOOL_DESCRIPTIONS` 负责自主创建与启用的决策和结果语义，由 `scene_tool.py` 执行；手动操作走独立客户端路径，契约见 [场景启用与授权](../../docs/PROTOCOL.md#场景启用与授权)。
- `generation.py::SELF_IMAGE_*` / `SELF_VIDEO_*` 装配本次出镜要求，图片由 `visual_identity.py::build_self_image_prompt` 共用于聊天和动态。
- `generation.py` 的壁纸模板与环境参考规则由 `scene_prompt.py` 装配；`SCENE_IMAGE_REVIEW_SYSTEM` 由独立 `scene_image_review.py` 对照伙伴与候选，`SCENE_DESCRIBE_SYSTEM` 由场景服务分析成品。各输入用途与描述归属见 [场景输入](../../docs/PIPELINE.md#场景创建与描述)。初始场景默认文案在 `scene_service.py::_INITIAL_SCENE_DEFAULT_NOTES`。

**动作**

- 对话工具说明 → `application/actions/context.py` 动态资料 → 工具结果续轮；工具关闭不等于没有动作资料或能力。
- 夜间规划先受理提案，再由后台独立评审；受理成功只表示申请，不表示已制作或已表演。
- 图片动作由 `ACTION_IMAGE_DESCRIPTION_INSTRUCTIONS` 描述单张静态姿态，视频动作由 `VIDEO_ACTION_SCRIPT_INSTRUCTIONS` 生成起始姿态和运动描述；动态动作先经独立评审。描述装配与探身定位见 [video/script.py](../services/application/generation/video/script.py)，制作见 [PIPELINE](../../docs/PIPELINE.md#评审与制作)，播放见 [PROTOCOL](../../docs/PROTOCOL.md#动作目录与播放)。

完整外观链路及各分支的审查导航见 [完整提示词链检查入口](../../docs/PIPELINE.md#完整提示词链检查入口)。

头像由装配器统一追加 `AVATAR_IMAGE_RULES` 和画风；参考分工、资料优先级及审核改写要求见 [身份、造型与参考输入](../../docs/PIPELINE.md#身份造型与参考输入)。

## 命名与消费

- 双语 dict 键为 `zh`/`en`，消费方用 `components.resolve_prompt_text` 取文本。
- 日记与反思共用资料说明，输出分别由发布与理解快照消费；契约见 [PROTOCOL](../../docs/PROTOCOL.md#动态与日记)。通用记忆指导只说明检索用途，具体参数语义归工具说明，来源与时效归返回资料标签。
- 本包不设转发别名：消费方按模块文件直接导入常量（如 `from prompts.chat import COMPANION_CHAT_GUIDANCES`）；重命名时用常量名全仓搜索同步所有导入点。
- Runner 是独立物理模块（独立 pyproject、物理解耦），其提示词不在本包；例外见下文 Runner 一节。

## 服务层动态提示词

- [夜间能力目录](../services/application/nightly/nightly_planning.py)——描述与参数选项随能力可用性装配，字段说明包含类型、用途及必要的可选性、长度和取值约束；说明供规划模型阅读，实际校验由参数模型与执行器负责。动作预算从执行端常量传入 `plan_limits`；检查规划提示词时同时核对目录、互斥组和参考图能力。
- [语音气泡演绎能力](../services/infrastructure/llm/providers/speech_style.py)——按实际供应商与模型装配能力和 JSON 示例，附加到陪伴终端请求；检查正文规则时一并核对。
- [媒体气泡](../services/application/chat/streaming.py)——本轮有可引用产物时，`chat.py::COMPANION_MEDIA_REPLY_GUIDANCES` 与可信产物清单装配到正常和恢复请求；`tools.py` 的批次生成、验图与重做说明对应 [chat_images.py](../services/application/generation/chat_images.py)。
- [工具域目录](../services/infrastructure/tool_runtime/domains.py)——根据当前实际可用工具重写 `search_tools` 描述，预设与调用方排除后在对话装配中再次计算。
- [时间与共享块装配](../services/application/chat/prompt_blocks.py)——工具开关与实际解锁集合决定能力描述，时间资料只表达经过时间，不推断用户经历。
- [初次见面意图](../services/domains/companion/first_greeting.py)——`companion.py::FIRST_MEETING_INTENT_TEXTS` 按用户语言写入意图记录，由[主动回合](../services/application/automation/companion_turns.py)作为资料消息与 `chat.py::COMPANION_PROACTIVE_GUIDANCES` 一同装配；开口与沉默遵循[回复契约](../../docs/PROTOCOL.md#结构化回复与终端交付)。
- [陪伴小推理资料](../services/domains/companion/prompt_runtime.py)——心情、空闲表达、空间行为与动态使用人设和相关记忆，不附加完整视觉形象资料；当前时间是带时区偏移的用户本地时间，近期对话保留原始角色、时间和截断标记，旧请求不自动成为当前触发条件。
- 音色设计说明：[MiniMax](../services/infrastructure/llm/providers/minimax/tts.py)、[MiMo](../services/infrastructure/llm/providers/mimo/tts.py) 等各供应商 TTS 模块的 `VOICE_DESIGN_GUIDE`——供应商支持的描述维度，供用户创建音色；不能混入逐条语音的正文。
- 数据库 `AvatarAsset.prompt_json` 等审计字段是生成时快照，不是定义源。

## 独立运行端

### Runner

Runner 当前不内嵌模型提示词；各工具目录的 schema、描述与终端平台说明直接提供给模型，须连同开关、权限、参数及返回语义检查。Runner 与 Backend 无共享代码包（独立 pyproject、物理解耦），不为提示词引入跨模块共享机制；改动按 [本机工具契约](../../docs/PROTOCOL.md#本机工具)验证。

### 发布脚本

[gen_release_notes.py](../../scripts/gen_release_notes.py) 独立运行，维护提交摘要的系统提示词；提交记录是资料，验证范围和功能影响范围须分别保留，不从提交推断发布或测试已经完成。

## 验证

通用提示词验证遵循 [RULES](../../RULES.md#提示词设计与修改规范)。本包重点核对共享块在各预设、工具开关和双语下的装配；图像链核对[参考顺序与身份约束](../../docs/PIPELINE.md#验证)。静态预览只检查装配，不能证明模型质量。
