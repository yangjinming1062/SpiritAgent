# 预设记忆

负责召回、证据维护、人工编辑与恢复有效性。调用方只使用公共入口，不直接改记录或复制策略；作用域由认证会话固定，隔离协议见 [PROTOCOL](../../../../docs/PROTOCOL.md#预设记忆与学习作用域)。

## 入口与事实来源

| 入口 | 职责 |
|---|---|
| [memory_review.py](memory_review.py) | 审阅编排：同域串行的后台审阅循环（`review_memories`）及其失败退避（`memory_review_backed_off`）、独立模型评估变更（`assess_memory_changes`） |
| [memory_learning.py](memory_learning.py) | 审阅资料装配（原始消息、待维护记忆与批次上限），决策的证据校验与原子提交 |
| [memory_policy.py](memory_policy.py) | 决策结构与硬校验；政策文本是 [prompts/memory.py](../../../prompts/memory.py) 的 `MEMORY_POLICY` |
| [memory_retrieval.py](memory_retrieval.py) | 向量与关键词双路召回、融合排序 |
| [memory_store.py](memory_store.py) | 存储、作用域过滤、写锁与遗忘清理 |
| [memory_admin.py](memory_admin.py) | 人工列表、编辑与计数 |
| [memory_narratives.py](memory_narratives.py) | 日记原文索引、恢复重建及最新相处理解快照 |
| [memory_bootstrap.py](memory_bootstrap.py) | 用户资料读写；用户时区归 [settings](../../../modules/settings/timezone.py) |
| [memory_format.py](memory_format.py) / [memory_namespaces.py](memory_namespaces.py) | 提示词记忆块渲染、记录上下文命名空间 |

审阅入口包括回合后审阅（[persistence.py](../../application/chat/persistence.py)）、夜间整理（[nightly_activity.py](../../application/nightly/nightly_activity.py)）和调度器定期审阅（[cron.py](../../adapters/scheduler/cron.py)）；调度候选与审阅装配共用 `list_memory_review_scopes` 的用户、会话及学习范围规则，停用账户不入选；模型记忆工具经 [native_memory.py](../../application/chat/native_memory.py) 提交即时提案。

`/remember` 经 `create_memory` 写入无证据的显式记录，属于 `recall:` 命名空间，写入时不经审阅但会进入后续维护轮转。学习记录保存原子事实，长期背景只是明确陈述的视图；onboarding、统计、系统事件、日记和助手表达各自不能单独证明用户偏好。

## 审核与原子提交

- 证据逐字来自同域、上下文水位以上的原始用户消息。发送时间与原文指纹识别分叉副本，重复处理不增加独立证据。
- 审阅只读用户本人对话（[user_authored_conversation](../conversation/memory_scope.py)）：子 Agent 会话的“用户”消息由父 Agent 撰写，不作证据，调度扫描、夜间摘要与主动意图的用户发言判定同样排除。`source_kind` 为 `manual` 的记录只有更新的用户消息作证时才可改动；`basis` 为 `system` 的伙伴自身记录（如夜间自主活动）不进入审阅与版本比对，但仍参与召回。
- 后台同域审核串行，即时提案经独立模型审核；提交重核快照、版本和原文并整批原子生效。快照失效丢弃，格式或证据错误最多反馈重试一次。
- 推理不持数据库事务，聊天不等待回合后审核。

### 完整审阅

- 审阅输出预算含推理与决策，见 [memory_review.py](memory_review.py)；未完成响应不应用决策，失败保留水位并退避。
- 后台审阅按消息 ID 升序从各会话水位取连续批次，排空到本次触发的截止消息；只有完整批次成功后才推进水位，零变更也算成功检查。
- 任一批次失败即停止本次审阅，该批及后续消息从原位置续审；持续失败会阻塞后续批次。原始消息不截断后跳过，单条超预算时单独审阅，超过供应商上下文能力则保留待处理。
- 审阅失败后按作用域和审阅范围在进程内指数退避：调度器与夜间整理的整域审阅算一个范围，回合后审阅按单个会话各算一个。退避从 10 分钟起，退避期结束后再失败则翻倍，上限为 [`memory_review_interval_seconds`](../../../components/config.py)，成功后清除，用户维护边界内随审阅锁一并丢弃；审阅期间记忆被并发修改的冲突（`MemoryConflictError`）不算失败。调度器和回合后审阅在退避期内跳过该范围，失败前已排队的回合后审阅仍各执行一次但不延长退避；夜间整理按自己的每日节奏执行、不受退避限制，其成败仍按同一规则更新退避状态。调度器遇到没有可用 LLM 配置的作用域只记录并跳过，不计入失败。
- 单批消息、记忆和关键词条数上限见 [memory_learning.py](memory_learning.py) 顶部常量，决策与证据条数上限见 [memory_policy.py](memory_policy.py) 字段约束。

即时检查优先保留最新消息并按时间呈现；后台批次同时轮转维护记忆，并按批次内用户发言各分句轮流取出的关键词补充相关记录，命中词数多的优先。关键词与相关记录同受上述常量限制，批次很大时靠后的发言可能取不到词。

## 修订与遗忘

| 操作 | 保存与清理语义 |
|---|---|
| 人工编辑 | 移除旧正文证据，保留遗忘所需指纹；重新保存资料重置旧有效期和维护属性 |
| 删除与遗忘 | 清除正文、修订与引用，仅保留事件指纹，防止旧消息与分叉重新学习；后续新表达可成为新证据 |
| 遗忘抑制 | 以整条原始消息为单位，其中其他内容也不再自动提取 |
| 失效 | 撤销错误判断，不能替代用户明确遗忘 |

## 读取、召回与恢复

聊天、伙伴状态与夜间规划只读有效且未到期记录，推断标明依据类型。恢复时重映射证据和修订引用、重置审核水位，并保留遗忘指纹、来源类型与更新时间，手写记录恢复后仍只接受更新的用户消息改动；缺少原始消息的导入判断须失效，包级规则见 [PROTOCOL](../../../../docs/PROTOCOL.md#备份校验与覆盖恢复)。来源类型已记为 `import` 的既有记录无法还原原类型；备份中无法识别的来源类型同样记为 `import`。

`memory_narratives.py` 管理 `diary:<日期>` 派生索引与 `reflection:current` 理解快照；二者与夜间活动记录同为伙伴自身记录，参与召回但不进入事实审阅。发布、遗忘及日期召回契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动态与日记)。用户资料由专门的块注入、统计不参与召回。画像入口只接收已登记的 `user_*` 引导字段，引导、人设提交和记忆编辑共用 `USER_PROFILE_MAX_CONTENT_CHARS` 上限。

夜间规划读取同域全部有效事实记忆，不套用管理列表分页上限；外部列表仍使用有界查询。提交后补向量，失败不影响已保存内容及关键词召回；召回同时走向量和关键词，经 RRF、重要性和时间衰减融合，嵌入不可用或维度不匹配时仅用同域关键词。关键词取拉丁词和各汉字分句轮流产生的 2/3-gram（上限见 [memory_retrieval.py](memory_retrieval.py) 的 `SPARSE_QUERY_TERM_MAX`），长句不独占配额；关键词候选在数据库端先按命中词数（正文命中重于仅上下文命中）、再按更新时间取前若干条，然后才进入融合。融合只使用候选名次而非命中分，较旧但命中更多的记录进入候选后，最终排名仍可能因时间衰减低于近期记录。

## 验证入口

覆盖作用域、原文与版本、原子提交、完整水位、失败退避（成功清零、夜间整理不受限）、关键词候选排序与跨分句取词、遗忘后重复导入和恢复引用映射。静态入口见 [Backend](../../../README.md#契约与验证)，样例检索通过不代表真实模型审核质量。
