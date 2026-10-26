# 对话编排

桌面、手机、Cron、主动陪伴和子 Agent 共用的回合执行层，负责上下文、模型调用、工具派发及回复交付。会话与跨端恢复归 [PROTOCOL](../../../docs/PROTOCOL.md#会话与消息)，本页维护内部装配、历史边界与任务所有权。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 业务操作、受理与历史修改 | [operations.py](operations.py)、[submissions.py](submissions.py)、[history_commands.py](history_commands.py) |
| 回合互斥、工具循环和取消 | [orchestrator.py](orchestrator.py)、[tool_dispatch.py](tool_dispatch.py)、[delegation.py](delegation.py) |
| 参数、历史与模型资料 | [turn_inputs.py](turn_inputs.py)、[system_prompt.py](system_prompt.py)、[prompt_presets.py](prompt_presets.py)、[prompt_blocks.py](prompt_blocks.py) |
| 模型请求、回退与结构恢复 | [streaming.py](streaming.py)、[reply_delivery.py](reply_delivery.py) |
| 压缩与确定性截断 | [context_compressor.py](context_compressor.py)、[message_sanitization.py](message_sanitization.py) |
| 保存、气泡和来源绑定 | [persistence.py](persistence.py)、[bubble.py](bubble.py)、[reply_links.py](reply_links.py) |
| 会话列表、参数与标题 | [session_resources.py](session_resources.py)、[title_generation.py](../../features/conversation/title_generation.py) |
| 即时记忆与异步审阅 | [native_memory.py](native_memory.py)、[background_review.py](background_review.py) |
| 交付、错误与媒体协调 | [chat_emitter.py](chat_emitter.py)、[turn_errors.py](turn_errors.py)、[images.py](images.py) |

## 受理与所有权

HTTP 受理由账户任务持有，调用方取消不能中断已持久受理。结构与输入互斥由 [http_contracts.py](../../features/conversation/http_contracts.py) 校验，工作流核对附件、额度和消息资格；[会话提交账本](../../features/conversation/submissions.py) 在锁内原子保存完整消息序列、附件和请求，重复提交复用原结果。传入 `persisted_message_id` 时输入已由调用方保存并发布，编排不再插入或通知。

`run_chat_turn` 的会话锁覆盖回合及取消收尾。工具调用/结果写入必须完成后才能释放锁，资产保护覆盖工具返回到父回合提交的间隔。终端保存归 [turn_store.py](../../features/conversation/turn_store.py)，不得把尚未落库的结果留给无所属的后台任务。

`ephemeral` 用于主动陪伴，请求作为本轮尾部资料，压缩检查点仍保存；`headless` 控制本机桌面工作态，`has_viewer` 控制缓冲和气泡停顿。委派建立父会话下的子会话，继承预设、设置和限制，以 `HeadlessEmitter` 捕获结果；生活空间及再次委派在执行层排除，子会话输入不能成为用户事实证据。

## 提示词与运行时数据

预设决定人格与记忆域，预设、用户开关和本轮状态共同决定工具集合；装配、搜索解锁、披露与派发使用同一资格。工作预设按登记加入双语角色头部，缺少定义明确失败。文本导航见 [Backend](../../README.md#提示词定位)。

陪伴每回合装配统一生活背景，每次模型调用刷新实际呈现资料，窗口/桌面的可见事实由 [companion_actions.py](../companion_actions.py) 投影。普通自动化和子 Agent 不装配陪伴人格及生活空间工具；子 Agent 也不继承实时环境或等待意图。素材查询、动作设计与制作可在空库使用，点播须有当前合格成品，执行时再次核对目标与版本。

日期按用户本地日刷新，历史时刻置于对应发言前；环境、等待和本轮来源资料留在尾部，不进入历史摘要。来源候选及引用绑定由 [话题模块](../../features/topics/README.md) 管理。结构化回复根据音色和可信产物装配，`reply_persona` 独立保存修复所需人设，避免压缩丢失。

推理与交付正文分别保存，只接受完成响应的正文。失败尝试的正文、推理及工具草稿不能进入下一供应商的请求；工具开始和参数增量不代表实际执行。

### 工具披露与记忆访问

首轮披露搜索元工具和已启用等待能力；陪伴默认媒体集合由 `prompt_presets.py` 定义，其余按域解锁。每次调用前依据真实产物重算可用集合：验图须有就绪图，重做须有本轮问题及剩余额度，已受理图片请求或结果未知时不再次披露新图片生成，没有视频任务时不披露查询。

历史解锁不能绕过当前资格。记忆维护政策随 `memory_inspect` 解锁，与独立审阅共用文本；召回与修改只经 [记忆入口](../../features/memory/README.md)。

## 上下文与记忆

### 摘要边界与原始证据

历史通过 [context_window.py](../../features/conversation/context_window.py) 按消息 ID 读取，`summary_through_message_id` 是真实覆盖边界；无效边界直接失败，分叉与恢复重映射引用，不能按时间猜测。

压缩只处理能完整容纳的历史前缀，不拆消息展开项、并行调用及结果。陪伴摘要保留真实交流和时间，工作/自动化摘要保留工具证据；计数只包含用户发言和助手最终回复。媒体只传引用与未提供内容标记，不把 base64 当文字。检查点交付与保存一致，清理已覆盖历史时仍保护未覆盖媒体。

### 窗口预算与截断

有可靠用量基线时估算增量，否则全量估算，判定阈值由 `turn_inputs.py` 维护。确定性截断优先保留摘要/检查点和最近输入，按调用身份扩展到完整工具批次；连续用户输入使用独立较大预算，删减带原长度标记，重试沿原输入应用同一规则。

实际请求在截断后重新筛选媒体能力，音频原生或转写路径须得到结果或明确错误后继续。多模态工具结果按 `multimodal_v1` 还原，旧媒体留占位；缺失工具结果记为未知，不能发送孤立调用。删除提示、日期和时间只能补充上下文，不能替代当前用户请求。

## 回合执行与交付

工具批次仅对白名单只读调用或互不重叠文件操作并发。参数先修复和校验，再派发；错误也成对交付开始/结束事件，不泄露原始异常或用户参数。

循环按 [tool_guardrails.py](../../runtime/tools/tool_guardrails.py) 检查失败重复、无进展和动作后的核对额度，媒体另按 [turn_state.py](../../features/media/turn_state.py) 收束；达到条件后切为无工具最终回复，跨模型保留这一状态。主动回合由意图有效期约束。阈值与工具名单留在源码，模型等待和重试预算归 [失败契约](../../../docs/PROTOCOL.md#模型失败与重试预算)。

非子 Agent 的陪伴会话执行完整结构校验；其他有观看者的回合可流式交付，无头/无观看者回合缓冲正文。文本只按代码围栏外的 Markdown 分隔线分泡，跨 chunk 保留候选前缀。成功、异常及取消都关闭响应流，缓冲正文在释放连接后交付；实际正文交付才锁定供应商回退，恢复资格依链的 `has_next_attempt` 判断。

调用用量只统计模型生成与响应消费，包含续轮、格式修复和回退；工具、委派及回合后任务不继承父回合统计。用户错误文案来自 `turn_errors.py`，英文 detail 仅用于无头结果及诊断；原始请求响应只走显式调试日志。

### 消息与媒体

[reply_media.py](../../features/conversation/reply_media.py) 管理可信媒体及视频绑定，[attachments.py](../../features/conversation/attachments.py) 管理附件额度和清理。终端持久化合并最新媒体状态，符合资格的用户陪伴回合在提交及完成事件之后才调度心情更新。

[reply_audio.py](../../features/conversation/reply_audio.py) 按消息加锁、固定保存的音色配置，事务外合成后核对原文、语音语义及消息存在性，按气泡序提交并合并最新媒体状态。用户回合先保存回复再合成，主动回合先合成再由意图终态事务保存与投递；取消清理未交接音频。真实格式和时长用媒体检查读取，不能只信扩展名。

## 验证入口

按 [提示词调试](../../../scripts/README.md#提示词调试) 核对实际请求，覆盖完整工具批次、权限、压缩/重试、格式恢复、取消写库、回退锁定和连接关闭；语音与媒体须检查实际交付，解析成功只证明结构可读。
