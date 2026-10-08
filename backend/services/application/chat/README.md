# 对话编排

桌面、远程网页、Cron、主动陪伴与子 Agent 共用的回合执行层：装配上下文、调用模型、派发工具、保存和交付回复。会话及交付语义归 [PROTOCOL](../../../../docs/PROTOCOL.md#会话与消息)，媒体等待与恢复归 PIPELINE；本页维护内部装配、历史边界和任务所有权。

## 关键入口

| 入口 | 职责 |
|---|---|
| [orchestrator.py](orchestrator.py) | `run_chat_turn`：会话互斥、轮数与时限、委派、回退锁定及终态 |
| [turn_inputs.py](turn_inputs.py) | 会话设置、历史转换、工具集合、资料域和 Token 估算 |
| [prompt_presets.py](prompt_presets.py)、[prompt_blocks.py](prompt_blocks.py)、[system_prompt.py](system_prompt.py) | 预设体、排除集合、共享块与每次调用前的环境/动作快照 |
| [streaming.py](streaming.py) | 实际请求、日期与语音/媒体能力、流式和缓冲调用、回复恢复 |
| [reply_delivery.py](reply_delivery.py)、[bubble.py](bubble.py)、[reply_links.py](reply_links.py) | 回复 schema、语音分句与校验、文本分泡、媒体地址来源 |
| [tool_dispatch.py](tool_dispatch.py)、[delegation.py](delegation.py) | 工具批执行与 `DelegateAction` 接管 |
| [persistence.py](persistence.py) | 调用与结果、终端回复、音频、压缩检查点及回合后任务 |
| [submissions.py](submissions.py) | 用户提交幂等账本、输入原子保存与重启中断标记 |
| [context_compressor.py](context_compressor.py)、[message_sanitization.py](message_sanitization.py) | 模型摘要、确定性窗口截断、工具参数 JSON 修复 |
| [title_generator.py](title_generator.py) | 首条用户文字与助手回复生成标题，不发送附件地址 |
| [native_memory.py](native_memory.py)、[background_review.py](background_review.py) | 即时记忆提案与回合后异步审阅 |
| [chat_emitter.py](chat_emitter.py)、[turn_errors.py](turn_errors.py) | 交付接口、`HeadlessEmitter` 与脱敏错误帧 |

调用方分别为桌面与远程网页 `prompt.submit`、`standard_turns`、`companion_turns` 和委派。传入 `persisted_message_id` 时，由调用方保存并发布用户输入，本层不重复插入或通知。

## 提示词与运行时数据

预设和记忆域由会话决定，工具排除由预设、用户开关和本轮限制共同形成；装配、搜索解锁和派发共用同一集合。陪伴、自动化和委派有独立预设体，专业预设共用工作体并追加预设目录对应的双语头部，缺少登记或头部明确失败。

- `build_companion_environment_prompt` 在每次模型调用前读取当前场景、着装和动作；子 Agent 不装配陪伴人格、等待意图或实时环境。专业与自动化的工具边界由 `prompt_presets.py` 统一声明。
- 日期行发送前按用户本地日刷新；陪伴历史按原消息时间重建跨日标记和发言时刻，时刻资料位于对应发言之前，不写入正文。实时环境、等待及本轮资料留在尾部，不进入历史摘要。
- 正常请求按当前音色与可信产物装配回复结构；`TurnInputs.reply_persona` 单独保存修复所需人设，避免压缩或截断丢失。修复与手动重试的资料范围和预算由 PROTOCOL 统一定义。
- 推理与最终输出分开保存，推理不回灌历史；只解析完成响应的 `output_text`，失败重试前移除追加的推理项。

文本定义见 [prompts](../../../prompts/README.md)，检查实际请求还须核对供应商原生参数。

## 上下文与记忆

### 摘要边界与原始证据

上下文压缩通过 [context_window.py](../../domains/conversation/context_window.py) 读取历史。`summary_through_message_id` 指定真实覆盖边界，历史按 `Message.id` 升序读取；边界无效时失败，不能按插入时间猜测。

- 压缩不拆开同一消息的展开项、并行调用和结果。按模型窗口预留输出与余量，只摘要能容纳的完整前缀；超预算单条保留原文并失败。
- 检查点只由 `context_compressor.py` 生成，交付正文与保存内容一致；分叉和恢复重映射覆盖边界。
- 检查点数量只统计本次覆盖的用户发言和助手最终回复，不计工具调用、结果、推理、时间资料或既有摘要。陪伴摘要只接收实际对话、相关时间和既有摘要；日期分界独立保留，不随工具记录一起过滤。工作及自动化摘要保留工具执行证据。预算仍按完整调用/结果批次确定覆盖边界。
- 摘要只保存媒体引用与内容未提供标记，不把 base64 当文字；清理已覆盖历史时，未覆盖原文仍须保持媒体可用。

### 窗口预算与截断

Token 估算以最近带用量的助手行和其后增量为基线；无基线、与全量差异超过 `max(200, 20%)` 或本轮有额外尾部资料时改用全量估算。

- 确定性截断优先保留摘要和检查点，再保留最近输入，按 `call_id` 向前扩展到整批调用及结果。历史单项上限不约束末尾连续用户输入，后者以窗口四分之一作为较大预算，截断带原长度标记；手动重试沿原输入使用同一预算。
- 正常请求在截断和视频内联后按实际保留媒体选择能力链；格式恢复移除媒体后选链，摘要只用文字链。不能发送保留了媒体却无对应能力的请求。
- 多模态工具结果以 `multimodal_v1` 存储并还原为数组，仅近期条目保留媒体，旧历史留占位。调用已保存而结果缺失时补记结果未知，避免孤立调用破坏整条请求。
- 删除提示与最近用户锚点只能作补充前缀，日期和时间元数据不能代替当前请求。

### 工具披露与记忆访问

首轮提供搜索元工具和已启用的等待能力；非委派陪伴回合默认披露 `COMPANION_MEDIA_TOOL_NAMES` 中本轮允许的基础媒体工具，其余按域解锁，未压缩历史保留解锁结果。验图只在有就绪图片时披露，重做只在本轮当前版本有真实问题且仍有额度时披露，参数限定为可用图片及验图标识；每次调用前重算，历史解锁和搜索结果不能绕过状态边界。完整维护政策随 `memory_inspect` 解锁，与独立审核共用文本。召回和提交只经[记忆公共入口](../../domains/memory/README.md)，不直接写记录。

## 回合执行与交付

`run_chat_turn` 的会话锁覆盖整个回合和取消收尾。默认轮数、执行预算运行时读取 Settings，调用方可覆盖；[wait_budget.py](../../infrastructure/llm/wait_budget.py) 只暂停已接单本地生图等待期间的执行计时，取消、设备撤权和外层有效期仍生效。无进展守卫追加换策略提示，不自行终止循环。

- 全批只读白名单调用或互不重叠的文件操作才并发。工具参数先修复并校验为 JSON 对象，失败不派发，仍成对发出工具开始/完成并返回参数错误；日志只记长度，不记录参数中的用户数据。
- 工具调用行与结果的写入任务必须在取消时等待落完，再释放会话锁，不能把写库留在后台。资产写入保护覆盖工具返回到父回合提交的间隔。
- `ephemeral` 仅用于主动陪伴，请求留作尾部资料，压缩检查点仍保存；`headless` 决定本机执行的桌面工作态，`has_viewer` 决定缓冲与气泡停顿，两者分别处理。
- `special + companion` 使用完整结构校验；其他有观看者的回合可流式交付，无头和无观看者回合缓冲正文。文本流只按代码围栏外的 Markdown `---` 分泡，跨 chunk 保留候选前缀和原空行。
- 委派在父会话下建子会话，继承预设、用户设置与限制；`HeadlessEmitter` 捕获事件，最终结果作为工具结果返回。生活空间和进一步委派工具由执行层排除；子会话消息不作为用户事实证据。
- 成功、异常及取消均关闭响应流，缓冲正文在连接释放后交付；回退锁定独立于是否已向用户展示文字。

`turn_errors.py` 的 `message` 是用户文案，英文 `detail` 只供无头结果和诊断日志消费。格式错误另由编排入口构造。常规日志保留模型、响应 ID、状态、用量、错误类型和脱敏定位，原始请求/响应只走显式 LLM 调试。

### 消息与媒体

可信媒体引用与视频绑定由 [reply_media.py](../../domains/conversation/reply_media.py) 管理，上传视频配额和清理由 [chat_videos.py](../../domains/media/chat_videos.py) 管理。终端持久化合并最新媒体状态；只有符合条件的桌面陪伴回合在终端提交、完成事件送出后调度心情更新。

### 语音资产

[reply_audio.py](../../domains/conversation/reply_audio.py) 按消息加锁，固定已保存的供应商、模型和音色，同消息并发合成并按气泡序提交。合成期间不持事务，写回重核原文、语音语义与消息存在性，合并最新媒体状态，避免迟到音频覆盖删除或视频更新。

用户回合先保存回复再合成；主动回合先合成，再由意图终态事务保存与投递。取消保留已保存正文并清理未交接音频。`mutagen` 核查真实格式和时长，拒绝无法识别或无效产物。

## 验证入口

命令和请求预览归 [Scripts](../../../../scripts/README.md#提示词调试)。修改本模块重点核对上下文完整批次、工具续轮、禁用工具、格式恢复、取消写库、回退锁定与连接关闭；语音和媒体须检查实际交付，不能只验证解析成功。
