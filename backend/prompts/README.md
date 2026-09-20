# prompts/

全仓 LLM 提示词文本的集中登记处。只存面向模型的指令文本与无副作用的纯常量；渲染器、装配器、ORM 实例与调用逻辑在各服务层。包章程与导入约定见 [`__init__.py`](__init__.py)。

改动提示词时先读 [RULES 提示词设计与修改规范](../../RULES.md#提示词设计与修改规范)；文本变更需要 backend 重启，运行时不做热更新。调试入口见 [scripts/debug_prompt.py](../../scripts/README.md#7-提示词调试与检查--debug_promptpy)。

## 验证

Python 修改走仓库标准入口（`pre-commit`、`check_imports.py --strict-imports`、依赖变化时 `check_services_architecture.py`，见 [scripts/README §8](../../scripts/README.md#8-按改动选择验证)）。措辞变更须连同消费方核对实际请求，覆盖正常输入、资料缺失、失败或未完成结果、相互冲突的要求及双语输出；共享块检查不同预设与工具开关下的装配。图像链另按 [PIPELINE 验证要求](../../docs/PIPELINE.md#8-按改动选择验证)检查参考顺序与身份约束。静态或模拟验证只证明装配和消费契约，不证明真实模型的表达、判断或视觉质量改善。

## 模块索引

| 模块 | 内容 | 渲染与装配的所在层 |
|---|---|---|
| `chat.py` | 5 套预设体骨架、四职业双语头部、约 20 个系统提示词块、标题生成、上下文压缩 | `services/application/chat/`（prompt_blocks、prompt_presets、system_prompt、title_generator、context_compressor） |
| `companion.py` | 心情、互动、空闲表达、空间行为、骨骼分类、性格标签（含种子词表）、角色设定与着装块标题 | `services/domains/companion/` |
| `generation.py` | 头像改写、画风词典（外观参考专用 `anime_illustration` 与模型种子路由 `refined_anime_cg`/`realistic` 分槽，见 [PIPELINE §1.1](../../docs/PIPELINE.md#11-共用参考与种子图派生)）、姿态短语、全身画幅不可裁切部位、头像参考、全身扩展/视角派生、换装、编辑保持条款、拼图参考分工、出镜媒体、服装转写、房间模板与光线词典、审核合规改写、衣柜描述、房间陈设 | `services/infrastructure/llm/prompt_engineer.py`、`services/application/generation/`、`services/application/nightly/`、`services/adapters/tools/builtin/` |
| `memory.py` | 记忆维护政策（MEMORY_POLICY）、审查指令、用户资料上下文标签与块标题 | `services/domains/memory/`（memory_policy、memory_review、memory_bootstrap） |
| `nightly.py` | 夜间规划、每日检查点、用户可见日记、内部夜间反思、片刻回复、片刻冲动决策 | `services/application/nightly/`、`services/application/moments/` |
| `tools.py` | 16 个工具 schema 的主描述与参数描述 | `services/adapters/tools/`（builtin/ 与同级 *.py） |

### 命名约定

- 双语 dict 键为 `zh`/`en`，消费方用 `components.resolve_prompt_text` 取文本。
- `JOURNAL_DIARY_TEXTS`（用户可见日记）与 `NIGHTLY_REFLECTION_TEXTS`（内部夜间反思）是内容不同的两套文本，不可混用。
- 本包不设转发别名：消费方直接从 `prompts.chat`、`prompts.memory` 等模块导入常量；重命名时用常量名全仓搜索同步所有导入点。

### 保留在服务层的提示词数据（不在本包，调整时从所在文件入手）

- `FullbodyTemplate` 物种/骨骼模板 dict（`prompt_engineer.py`）——dataclass 载体，与类型路由强耦合。
- `_SPECIES_STYLE` / `_PRESET_SPECIES`（`prompt_engineer.py`）——模型种子画风路由数据而非文本。
- [夜间能力目录](../services/application/nightly/nightly_planning.py)——描述与参数选项随能力可用性装配，动作预算从执行端常量传入 `plan_limits`；检查规划提示词时同时核对目录、互斥组和参考图能力。
- [语音气泡演绎能力](../services/infrastructure/llm/providers/speech_style.py)——按实际供应商、模型与音色装配能力和 JSON 示例，附加到陪伴终端请求；检查正文规则时一并核对。
- 数据库 `AvatarAsset.prompt_json` 等审计字段是生成时快照，不是定义源。

## Runner 例外

`runner/tools/browser/helpers.py` 内嵌浏览器内容抽取提示词。Runner 与 Backend 无共享代码包（独立 pyproject、物理解耦），不为单条提示词引入跨模块共享机制；该提示词调整时同步在本 README 登记，并按 [PROTOCOL](../../docs/PROTOCOL.md) 的 runner 契约验证。
