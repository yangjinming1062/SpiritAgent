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
| [slash_commands.py](slash_commands.py) | 斜杠命令静态注册表（与预设正交，不走 LLM tool_call）；clear、compress、remember 的实现在 [desktop handlers](../../adapters/desktop/handlers.py)；客户端 [slash-commands.ts](../../../../client/renderer/shared/lib/slash-commands.ts) 仅有自动补全/确认弹窗用的元数据镜像，`command.dispatch` 是唯一权威 |
| [native_memory.py](native_memory.py) / [background_review.py](background_review.py) | 模型记忆工具执行、回合后记忆审阅 |

`run_chat_turn` 的调用方：桌面 `prompt.submit`（[handlers](../../adapters/desktop/handlers.py)）；IM（[bridge](../../adapters/channels/bridge.py)，复用已落库的入站消息，`headless`）；定时任务（[standard_turns](../automation/standard_turns.py)，`headless`）；主动陪伴（[companion_turns](../automation/companion_turns.py)，`ephemeral` 与 `headless`，排除发消息与委派工具，轮数上限为 `companion_max_loop_turns`）；子 Agent 委派（[delegation](delegation.py)，传入 `HeadlessEmitter`，沿用父回合的 `headless`）。

提示词主体文本集中在 [prompts](../../../prompts/README.md)。

## 提示词与运行时数据

- 身份、用户资料、记忆、附件和工具结果都是资料，不扩大权限；陪伴工具续轮继续使用同一完整预设。专业预设只装配职业目标与本域资料，automation 保持独立任务边界。
- 主动回合的沉默规则属于系统指令，意图、档位和未兑现等待属于尾部资料；陪伴预设每回合附加未兑现等待意图，并标明其不是用户发言或已完成操作。普通用户回合不注入沉默选项。每次模型调用（含工具续轮）由 `build_companion_environment_prompt` 追加环境与动作快照：有场景时此刻着装以场景成品描述中的可见造型为准，无场景才注入当前着装，两种来源都附着装与表现相称的说明；动作快照另列动作素材中的着装。常规档主动回合不提供语音与视觉表达工具。
- 桌面客户端标识与 IM 渠道键换成对应渠道说明，无客户端资料时使用桌面说明，其他自由文本原样作为资料；设备环境带标题单独装配，环境资料和工具指令分别装配，关闭工具不等于没有环境资料。search_tools 的业务域清单在预设与调用方排除后重算。
- 任务通过 `instructions` 定义，JSON 输入承载资料，输出由代码校验；生活空间回复格式按音色和模型动态装配，只在有可引用产物时说明媒体气泡，格式恢复不附加调用工具的说明，主动回合的恢复仍允许 `[]`。基础提示词预览不能代表最终请求，修改遵循 [RULES](../../../../RULES.md#提示词设计与修改规范)。
- 推理预算与正文预算共同计算；只解析完成响应的 `output_text`，推理单独保存且不回灌后续回合，失败重试前删除追加的推理项。格式恢复会剔除推理项，历史装配不包含推理项；供应商 JSON 模式须先验证顶层数组能力，MiniMax Responses 不以偶然合法 JSON 证明约束生效。

## 上下文与记忆

### 摘要边界与原始证据

- 运行时压缩与每日摘要共用[上下文读入口](../../domains/conversation/context_window.py)，原历史保留。摘要用 `Message.summary_through_message_id` 指向实际覆盖边界，`context_order` 只用于 IM 消费排序；缺少有效边界时明确失败，不按插入时间推断。
- 分叉须重映射覆盖边界，压缩不能拆开同一消息展开项或并行工具调用与结果；备份恢复见 [PROTOCOL](../../../../docs/PROTOCOL.md#备份校验与覆盖恢复)。
- 压缩检查点只由 [context_compressor.py](context_compressor.py) 生成，正文与持久化行逐字一致；来源逐项记录，当前时间、待办快照和临时请求留在本轮尾部，不进入摘要。
- 摘要只保留媒体引用和内容未提供标记，不把图片、视频 base64 当成文字资料；部分压缩清理已覆盖内容时，保留原文或待消费的 IM 消息不能再引用被清理视频。

### 每日摘要

每日摘要按截止本地日和消息时间归属事件，覆盖截止日前已有终端回复，只合并最新摘要及未覆盖原文；保留取消、纠正、授权范围和结果不明的操作。它记录用户 / 伙伴事实而非第一人称日记，回灌只作为历史资料，不产生新发言或授权；跨日未完成回合、等待期间新消息和覆盖边界失效时都保留原文或放弃快照。

### 窗口预算与截断

Token 估算以最近一条带用量的助手行及其后新增内容为基线；无基线、偏差超过 20% 且大于 200 token，或本轮带主动请求 / 等待意图等尾部资料时改用全量估算。历史始终保留原始工具调用与结果帧，发送前再做窗口和单项截断；陪伴的日期分界与用户发言时刻按消息时间重建，不写入消息正文，系统提示只保留发送前按本地日刷新的日期行，不逐轮改写。

- 确定性窗口截断保留最近输入尾部，按 `call_id` 向前扩展到对应调用和并行批次的全部结果。
- 删除提示和窗口前最近一条非时间元数据的用户输入只作前缀，不能替换尾部；日期和系统时间不能代替用户请求。

### 工具披露与记忆访问

工具渐进披露：先提供搜索元工具及已启用的陪伴等待能力，再按域解锁，未压缩历史保留解锁结果。完整记忆维护策略随 `memory_inspect` 解锁，并与独立审核共用来源；不能省略原始证据和版本检查。

召回与维护只经[记忆公共入口](../../domains/memory/README.md)，会话种类决定推理设置继承，不以是否有预设标识代替种类判断。

## 回合执行与交付

### 派发、委派与资源关闭

[orchestrator.py](orchestrator.py)按轮数上限循环，委派由执行层接管 `DelegateAction`，工具处理器不反向调用聊天入口。整批调用只有在全为只读白名单或目标路径互不重叠的文件操作时才并发；工具开关每回合重读。

预设、工具排除、记忆域和回合后整理由会话决定（`is_automation` 与 `system_preset_id='automation'` 等价），调用方只追加本轮排除项。`ephemeral` 只用于主动陪伴：请求作为尾部资料、不落库、允许沉默；`ephemeral` 与自动化回合都不计用户接触。

交付模式由会话和 `headless` 决定：`special + companion` 会话非流式校验完整回复；无头或 IM 回合缓冲正文，其余流式推送。

[delegation.py](delegation.py)为每次委派在发起会话下新建子会话（`parent_id`），继承预设与自动化归属；子回合事件由 `HeadlessEmitter` 捕获、不推送到会话流，最终结果作为工具结果返回。子会话的列表与删除语义见 [PROTOCOL](../../../../docs/PROTOCOL.md#会话种类与历史修改)。

供应商回退锁定独立于正文缓冲，不能因用户尚未看到文字就认为请求尚未开始；成功、异常和取消都关闭响应流，缓冲正文在连接释放后交付。终端正文、重试、分气泡和 TTS 幂等见 [PROTOCOL](../../../../docs/PROTOCOL.md#结构化回复与终端交付)。

格式校验失败的常规日志记录供应商、模型、响应 ID、完成状态、用量、错误类型、脱敏路径与是否为恢复尝试；不记录可能含原文的错误消息和未知字段名。原始请求和响应预览仅走显式启用的 [LLM 调试日志](../../../README.md#llm-调试日志)。

### 消息与媒体

编辑入口在会话锁内原子替换历史并先发修订事件，编排复用已持久化的新用户行，不能重复插入。桌面陪伴回复以数组对象为气泡边界，正文与交付态分别保存，见[回复契约](../../../../docs/PROTOCOL.md#结构化回复与终端交付)。文本流只按 Markdown `---` 分隔线拆泡，空行保留在正文中；跨 chunk 保留候选前缀。

- [reply_media.py](../../domains/conversation/reply_media.py)负责可信媒体恢复、引用校验和视频绑定；用户上传视频的配额清理同步替换输入消息引用，见 [chat_videos.py](../../domains/media/chat_videos.py)。
- [reply_delivery.py](reply_delivery.py)校验同次响应的气泡类型和逐泡演绎；句内声音只接受本气泡台词中的唯一短语锚点。语音重试不重新生成台词或演绎，交付语义见[媒体协议](../../../../docs/PROTOCOL.md#媒体引用验图与原位交付)。
- 推理过程独立保存，不回灌后续回合；只有符合条件的桌面陪伴回合在终端落库并完成事件送出后调度心情更新。

### 语音资产

- [reply_audio.py](../../domains/conversation/reply_audio.py)校验消息归属并按消息串行合成，固定已保存的供应商、模型和音色；配置不可用时保留失败，不替换演绎。
- 用户回合的陪伴回复先落库，再有界合成语音，之后才发送 `message.complete`；合成期间取消时回复已保存，不再实时交付。主动回合先合成语音，再由 `finish_companion_intent` 落库并以 `companion.message` 投递，未投递时删除音频。
- 合成不占用数据库事务，写回锁定消息并核对原文与对应语音语义，合并最新媒体状态，避免删除、恢复及视频并发完成后的迟到覆盖。
- `mutagen` 读取音频格式和真实时长，拒绝无法识别、时长无效或不支持的音频；资产与更新事件提交前后分别处理取消和清理。

## 验证入口

命令与预览见 [Scripts](../../../../scripts/README.md#提示词调试)。检查实际请求及动态语音协议，覆盖工具续轮、终态失败、取消、回退锁定与资源关闭。
