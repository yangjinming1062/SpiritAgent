# 对话编排

组织桌面、IM、Cron、主动陪伴与子 Agent 回合的上下文、模型、工具和交付。领域层管理业务，基础设施负责供应商传输；依赖见 [Backend](../../../README.md#services-依赖边界)，输出契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#结构化回复与终端交付)。

## 回合流程

装配入口资料 → 模型调用 → 工具派发/委派与续轮 → 按场景校验、保存和交付终端回复。循环在得到无工具调用的终端回复或调用失败时结束；轮数上限默认 `agent_max_loop_turns`，调用方可覆盖，耗尽时发 error 帧。无进展守卫不终止循环，只在工具结果后追加换策略提示。上下文刷新与失败规则见下文。

## 关键入口

| 入口 | 职责 |
|---|---|
| [orchestrator.py](orchestrator.py) | `run_chat_turn`：轮数预算、交付模式、委派与供应商回退锁定 |
| [turn_inputs.py](turn_inputs.py) | 回合装配：历史转换、Token 估算、工具开关、推理与会话设置合并 |
| [prompt_presets.py](prompt_presets.py) / [prompt_blocks.py](prompt_blocks.py) | 预设体与工具排除集合、共享块渲染 |
| [system_prompt.py](system_prompt.py) / [streaming.py](streaming.py) | 系统提示词与每次模型调用前的环境、动作快照；请求装配（日期刷新、回复格式与可用媒体）及流式/非流式调用 |
| [title_generator.py](title_generator.py) / [context_compressor.py](context_compressor.py) | 标题、运行时压缩 |
| [reply_delivery.py](reply_delivery.py) / [bubble.py](bubble.py) | 陪伴气泡 schema 与校验、文本流分泡 |
| [tool_dispatch.py](tool_dispatch.py) / [delegation.py](delegation.py) | 工具派发与 `DelegateAction` 执行层接管 |
| [persistence.py](persistence.py) | 工具调用行与结果落库、同步本回合输入并按 `search_tools` 结果解锁工具（批执行在 tool_dispatch）；终端回复落库、语音合成与 `message.complete` 交付；回合后任务调度；压缩检查点落库 |
| [message_sanitization.py](message_sanitization.py) | 工具参数 JSON 修复、确定性窗口截断 |
| [chat_emitter.py](chat_emitter.py) | 事件发射接口与捕获全部帧的 `HeadlessEmitter` |
| [slash_commands.py](slash_commands.py) | 斜杠命令注册与匹配；clear、compress、remember 的实现在 [desktop handlers](../../adapters/desktop/handlers.py) |
| [native_memory.py](native_memory.py) / [background_review.py](background_review.py) | 模型记忆工具执行、回合后记忆审阅 |

`run_chat_turn` 的调用方：桌面 `prompt.submit`（[handlers](../../adapters/desktop/handlers.py)）；IM（[bridge](../../adapters/channels/bridge.py)，复用已落库的入站消息）；定时任务（[standard_turns](../automation/standard_turns.py)，`headless`）；主动陪伴（[companion_turns](../automation/companion_turns.py)，`ephemeral` 与 `headless`，排除发消息与委派工具，轮数上限为 `companion_max_loop_turns`）；子 Agent 委派（[delegation](delegation.py)，传入 `HeadlessEmitter`，不带 `headless` 等标志）。

提示词主体文本集中在 [prompts](../../../prompts/README.md)。

## 提示词与运行时数据

身份、用户资料、记忆、附件和工具结果作为资料输入，不扩大权限；同一陪伴工具循环使用完整预设，不切换到另一套准备人格。

专业预设只装配职业目标与本域资料；automation 使用独立任务边界，不带陪伴人格、记忆维护或实时用户答复假设。主动回合的沉默规则属于系统指令，意图和档位属于尾部运行资料；普通用户回合不注入沉默选项。陪伴预设的每个回合都在尾部附加未兑现的陪伴等待意图，标明是资料而非用户发言或已完成操作。

陪伴预设每次调用模型前（包括工具续轮）由 `build_companion_environment_prompt` 追加环境与动作快照：已有场景时使用成品描述，仅无当前场景时注入衣柜着装。环境资料与工具指令分别装配，关闭工具仍保留环境。

渠道说明：客户端提供的自由文本原样作为资料；IM 适配器声明的渠道键（如 `weixin`）换成对应渠道说明；没有客户端资料时使用桌面说明。

内部结构化推理通过 `instructions` 定义任务，JSON 输入承载资料，输出由代码校验。生活空间回复协议由服务端按实际音色与模型装配为文字或混合气泡格式，提示词只描述该格式与演绎字段；基础提示词预览不能代表最终请求。修改遵循 [RULES](../../../../RULES.md#提示词设计与修改规范)。

- 短输出仍允许模型推理，调用预算须同时容纳推理和正文，不能按 JSON 字段长度估算总输出预算。
- 只解析完成响应的 `output_text`。推理文本单独收集保存，不回灌后续回合；原生推理项留在本回合输入中随工具续轮回传，失败尝试追加的推理项在重试前删除；格式恢复请求剔除全部推理项，历史装配不含推理项。
- 供应商 JSON 模式只有经验证支持顶层数组才用于陪伴回复，不能使用强制对象的模式改变回复契约。
- MiniMax Responses 不声明 JSON 模式能力；请求被接受或偶然输出合法 JSON 不能证明格式约束生效。回复始终经过相同的输入与语音演绎校验。

## 上下文与记忆

### 摘要边界与原始证据

- 运行时压缩与主对话每日摘要共用[上下文读入口](../../domains/conversation/context_window.py)，原历史保留。
- 摘要必须通过 `Message.summary_through_message_id` 指向实际覆盖到的原消息；`context_order` 只用于 IM 消费排序。
- 读取时按原消息的上下文位置排除已总结部分，缺少有效覆盖边界时明确失败，不按摘要插入时间推断。
- 分叉须重映射覆盖边界；备份恢复见 [PROTOCOL](../../../../docs/PROTOCOL.md#备份校验与覆盖恢复)。
- 压缩边界不能拆开同一消息展开的输入项或并行工具调用与结果。
- 压缩检查点正文（按用户语言的标题行 + 摘要）只由 [context_compressor.py](context_compressor.py) 生成：压缩当轮的上下文占位与持久化行逐字相同，后续回合读回的即当轮所见；首行由客户端显示为摘要卡片标题。
- 装配时逐项记录来源，无持久化来源的当前时间、待办快照与临时请求留在本轮尾部，不进入持久摘要。
- 文本摘要只接收媒体引用和内容未提供的标记，不把内联图片、视频的 base64 当成文字资料。
- 部分压缩只清理已覆盖、且保留原文或待消费的 IM 消息不再引用的视频。

### 每日摘要

每日摘要按明确的截止本地日与消息时间保留事件归属；取消、纠正、授权范围和结果不明的操作须保留，避免后续恢复过期安排。

每日摘要用明确的用户 / 伙伴归属记录事实，区别于第一人称日记；回灌为历史资料，不作为新发言或新授权。工具关闭时仍保留未知结果与取消请求的区别，不能因本轮无法查询而宣称历史操作未发生或已撤销。

- 每日摘要覆盖目标本地日末之前已有终端回复的对话；跨日未完成回合及模型等待期间的新消息继续保留原文。
- 只合并最新摘要与其未覆盖原文。
- 等待期间清空或删除了覆盖边界时，不保存失效快照。

### 窗口预算与截断

Token 估算以最近一条带用量的助手行加其后新增内容为基线；无基线、基线与全量估算偏差超过 20% 且大于 200 token，或本轮带主动请求、等待意图等尾部资料时改用全量估算。历史装配对所有会话都保留原始工具调用与结果帧，不按会话种类剥离；送出前仍受下文的确定性窗口截断与单项长度截断。陪伴的日期分界与用户发言时刻作为独立输入项按消息时间重建，不写进消息正文；系统提示词只保留一行发送前按本地日刷新的日期，不逐轮改写以免破坏前缀复用。

- 确定性窗口截断保留最近输入尾部，按 `call_id` 向前扩展到全部保留结果对应的调用，包含并行批次。
- 删除提示和窗口前最近一条非时间元数据的用户输入只作为前缀，不能替换尾部；日期分界与系统时间提示不能代替用户请求。

### 工具披露与记忆访问

工具渐进披露：先提供搜索元工具及已启用的陪伴等待能力，再按域解锁，未压缩历史保留解锁结果。完整记忆维护策略随 `memory_inspect` 解锁，并与独立审核共用来源；不能省略原始证据和版本检查。

召回与维护只经[记忆公共入口](../../domains/memory/README.md)，会话种类决定推理设置继承，不以是否有预设标识代替种类判断。

## 回合执行与交付

### 派发、委派与资源关闭

[orchestrator.py](orchestrator.py)按轮数上限循环。委派由执行层接管 `DelegateAction`，工具处理器不反向调用聊天入口。整批调用全部是只读白名单工具或目标路径互不重叠的文件读写工具时才并发，否则整批串行；工具开关每回合重读。

预设、工具排除、记忆域与回合后整理都由会话决定（数据库约束 `is_automation` 与 `system_preset_id='automation'` 等价），调用方只追加本轮排除的工具。`ephemeral` 只用于主动陪伴：请求作为尾部资料，不落库、允许沉默；它与自动化回合都不计用户接触。

交付模式由会话与调用方 `headless` 决定：固定陪伴会话（special + companion）非流式取得完整回复后校验交付；否则调用方传 `headless` 或 IM 会话时缓冲正文；其余流式推送。

[delegation.py](delegation.py)为每次委派在发起会话下新建子会话（`parent_id`），继承预设与自动化归属；子回合事件由 `HeadlessEmitter` 捕获、不推送到会话流，最终结果作为工具结果返回。子会话的列表与删除语义见 [PROTOCOL](../../../../docs/PROTOCOL.md#会话种类与历史修改)。

供应商回退锁定独立于正文缓冲，不能因用户尚未看到文字就认为请求尚未开始。成功、异常和取消都关闭响应流；缓冲正文在连接释放后交付。终端正文、重试、分气泡和 TTS 幂等见 [PROTOCOL](../../../../docs/PROTOCOL.md#结构化回复与终端交付)。

格式校验失败的常规日志记录供应商、模型、响应 ID、完成状态、用量、错误类型、脱敏路径与是否为恢复尝试；不记录可能含原文的错误消息和未知字段名。原始请求和响应预览仅走显式启用的 [LLM 调试日志](../../../README.md#llm-调试日志)。

### 消息与媒体

编辑入口在会话锁内原子替换历史并先发修订事件，编排复用已持久化的新用户行，不能重复插入。桌面陪伴回复以数组对象为气泡边界，正文与交付态分别保存，见[回复契约](../../../../docs/PROTOCOL.md#结构化回复与终端交付)。文本流只按 Markdown `---` 分隔线拆泡，空行保留在正文中；跨 chunk 保留候选前缀。

- [reply_media.py](../../domains/conversation/reply_media.py)负责可信媒体恢复、引用校验与视频绑定；交付语义见[媒体协议](../../../../docs/PROTOCOL.md#媒体引用验图与原位交付)。
- 用户上传视频的配额清理同步替换输入消息引用，见 [chat_videos.py](../../domains/media/chat_videos.py)。
- [reply_delivery.py](reply_delivery.py)校验同次响应的气泡类型和逐泡演绎；句内声音只接受本气泡台词中的唯一短语锚点。
- 语音资产由 conversation 领域负责，重试不重新生成台词或演绎。
- 推理过程独立保存，不回灌后续回合。
- 仅符合条件的桌面用户陪伴回合在终端落库且完成事件送出后调度心情更新，不能泛化到 IM、工作或主动回合。

### 语音资产

- [reply_audio.py](../../domains/conversation/reply_audio.py)校验消息归属并按消息串行合成，固定已保存的供应商、模型和音色；配置不可用时保留失败，不替换演绎。
- 用户回合的陪伴回复先落库，再有界合成语音，之后才发送 `message.complete`；合成期间取消时回复已保存，不再实时交付。主动回合先合成语音，再由 `finish_companion_intent` 落库并以 `companion.message` 投递，未投递时删除音频。
- 合成不占用数据库事务，写回锁定消息并核对原文与对应语音语义，合并最新媒体状态，避免删除、恢复及视频并发完成后的迟到覆盖。
- `mutagen` 读取音频格式和真实时长，拒绝无法识别、时长无效或不支持的音频；资产与更新事件提交前后分别处理取消和清理。

## 验证入口

命令与预览见 [Scripts](../../../../scripts/README.md#提示词调试)。检查实际请求及动态语音协议，覆盖工具续轮、终态失败、取消、回退锁定与资源关闭。
