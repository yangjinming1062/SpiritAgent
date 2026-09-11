# `services/chat/`

Backend 单次对话回合的编排核心：把“系统指令 + 用户输入 + 历史 + 工具 schema”组装成 Responses 上下文喂给 LLM，对流式正文分段与批处理，把工具调用下发给本地 Runner，等回灌后再次送入模型直到 budget 用尽或 LLM 自决终止，最后把 assistant 回复持久化并下发对话完成事件。

架构上下文与跨模块契约见 [ARCHITECTURE.md §4.2.I / §6.3 / §6.4](../../../docs/ARCHITECTURE.md)；本文件只记 chat 包内独有的设计决策与边界。

## 模块地图

对话编排统一连接输入装配、流式处理、工具派发和持久化；外部只经包公共入口调用。子 Agent 复用完整回合，以无头输出收集终态；迭代预算与流式回退边界见下文。

## 关键不变量（chat 包内独有）

- **台词与语音描述同次生成**：生活空间用户回合先按已选音色与当前模型装配完整能力说明，再以有界隐藏头表达供应商专属语音意图，随后继续流式输出台词，避免二次推理增加首音延迟；MiMo 隐藏头还承载角色、场景、指导三部分导演描述。句内语气词通过唯一正文短语定位，省去跨增量字符偏移计算；未找到或不唯一的定位不插入声音。只有保留的隐藏头被解析，普通括号、Markdown 与角色台词原样保留。语音交付契约见 [PROTOCOL.md §1.4](../../../docs/PROTOCOL.md)。桌面视觉表达与空间行为仍由独立自主推理负责。
- **当前心情独立更新**：仅用户发起的生活空间 companion special 回合在终端正文落库并完成下发后，后台调度 `services/companion/mood.py` 做一次结构化状态推理；结果写 `persona.current_mood` 并发出 `companion.mood`，不写 Message、不进入正文。工作预设、主动 Cron、普通自动化与 IM 回合不触发。
- **iteration budget 双层**：`IterationBudget(max_total=AGENT_MAX_LOOP_TURNS=150)` 计数 + `ToolCallGuardrailController.halt_decision` 语义提前退出。任一触发即停。
- **provider fallback 边界**：`execute_with_fallback` 的 `on_first_chunk` 哨兵防止 mid-stream 切换 provider——一旦已开始向 renderer 流式输出，下一 call 就锁死在当前 provider，避免同一回合混合两个模型的输出。
- **Responses 边界**：持久化层保留按角色建模的消息行；仅在读历史、工具回灌与后台 LLM 调用时转换为指令区 + 输入项。同一工具回合内的推理输出项保留到函数调用与输出闭合，近期图片载荷按二进制附件预算处理，不参与长文本截断。
- **image part 单一来源**：`message_sanitization._IMAGE_PART_TYPES = {"input_image"}` 是 Responses API 输入图片 part 唯一类型；`persistence._build_persisted_content_from_parts` 写入与 `_input_part` 读取两侧一致。
- **生成媒体在回合收口提取、随终端助手行落库**：`persistence.extract_turn_media` 只认图像/视频生成工具的成功结果（pending 与失败跳过），媒体列与正文正交且不进 LLM 上下文（URL 已在工具结果/摘要行内）；多气泡回合媒体挂最后一格。为什么不信任正文贴 URL：模型会漏贴或夹带 markdown，结构化提取是渲染端唯一可靠通道。跨模块契约见 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)。
- **陪伴对话的时间感知不写进消息正文**：时间元数据不落库，跨轮按发送时刻重建以保留 prefix cache。陪伴预设的系统提示词不放当前日期，并明确要求模型只输出角色台词。
- **陪伴气泡边界**：陪伴预设的空行与专用分隔行共用流式切分和停顿；跨 chunk 暂留可能属于较长分隔符的前缀，避免把分隔线泄漏为正文。专业预设不按空行切分，交付与历史恢复见 [PROTOCOL §1.4](../../../docs/PROTOCOL.md)。
- **流式 chunk 批处理**：在 5–10 ms 批窗口内合并连续 chunk 事件为单个 chunk 载荷；break 与 message.start 立即发出。
- **推理设置隔离**：回合与手动压缩共用会话设置合并入口；普通会话按种类继承工作台默认，特殊会话按预设目录取场景默认，不能以模板标识非空替代会话种类判断。无运行时覆盖的无头回合读取持久化覆盖。作用域及窗口水合见 [PROTOCOL §2.4](../../../docs/PROTOCOL.md)。

## 已知限制

无
