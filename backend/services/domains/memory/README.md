# 预设记忆

维护召回、证据、人工编辑、遗忘及恢复有效性。认证会话确定作用域，调用方只用公共入口；跨预设、异步继承和学习隔离归 [PROTOCOL](../../../../docs/PROTOCOL.md#预设记忆与学习作用域)。

## 入口与事实来源

| 入口 | 职责 |
|---|---|
| [memory_review.py](memory_review.py) | 同域串行后台审阅、独立模型判断和失败退避 |
| [memory_learning.py](memory_learning.py) | 原始消息与记忆批次、版本/证据复核、原子提交和水位 |
| [memory_policy.py](memory_policy.py) | 决策结构与硬校验，共用 [MEMORY_POLICY](../../../prompts/memory.py) |
| [memory_retrieval.py](memory_retrieval.py) | 向量、关键词及 RRF 融合 |
| [memory_store.py](memory_store.py)、[memory_admin.py](memory_admin.py) | 存储、范围过滤、写锁、编辑和遗忘 |
| [memory_narratives.py](memory_narratives.py) | 日记派生索引、恢复重建和相处理解快照 |
| [memory_bootstrap.py](memory_bootstrap.py) | 登记的 `user_*` 引导资料；时区由 `modules/settings/timezone.py` 管理 |
| [memory_format.py](memory_format.py)、[memory_namespaces.py](memory_namespaces.py) | 模型资料块与记录命名空间 |

后台入口是回合后、夜间整理和调度器；候选及装配共用 `list_memory_review_scopes`，停用账户不入选。即时模型提案经 `chat/native_memory.py` 提交。`/remember` 用 `create_memory` 保存显式无证据记录，进入 `recall:` 后续维护轮转。

## 证据与原子提交

证据必须逐字来自同域、上下文水位以上的原始用户消息。`user_authored_conversation` 排除子 Agent 的伪用户输入；时间与原文指纹识别分叉副本，重复不增加独立证据。onboarding、统计、日记、系统和助手表达不能单独证明新的用户偏好。

- 提交重新核对整域版本、记录版本、原文及来源，整批原子生效。并发修改丢弃快照，格式或证据错误最多反馈重试一次；模型等待不持事务，聊天不等待回合后审核。
- `source_kind=manual` 的记录只能依据比编辑时间更新的用户消息修改；`basis=system` 的伙伴自身记录不进入用户事实审阅或版本比对，仍可召回。
- `user_profile:` 引导记录在维护决策中只能 `invalidated` 或 `forgotten`；有原始证据的新判断另存记录，不能直接改写 onboarding。违例使整批回滚，审核水位不前进。
- 审阅输出未完成时不应用决策。后台按消息 ID 连续分批审到触发截止，完整批次成功才推进水位，零变更也是成功；失败保留该批及后续位置，持续失败阻塞后续批次。单条超批次预算仍完整送审，超供应商窗口则保留待处理。

### 失败退避与批次

退避按 `(MemoryScope, session_id)` 在进程内管理：整域审阅与各会话各算一个范围，从十分钟起、退避期后再失败翻倍，上限由 `memory_review_interval_seconds` 控制，成功清零。并发版本冲突不计失败；用户维护清除锁与退避。调度器和回合后审阅查询退避后跳过，已排队审阅仍执行但不延长未到期退避；夜间按每日节奏执行、不受跳过规则限制，其成败仍更新同一范围。无 LLM 配置的调度候选跳过，不算失败。

单批消息、记忆、关键词和决策上限以 learning / policy 定义为准。即时检查优先最新消息；后台同时轮转维护记录，按用户发言各分句轮流取关键词补充相关记忆，优先词面命中多者，大批次后部可能取不到词。

## 修订与遗忘

| 操作 | 约束 |
|---|---|
| 人工编辑 | 清旧正文证据、保留指纹，重置有效期和维护属性；伙伴自身记录仍为 `basis=system` |
| 遗忘 | 清正文、修订与引用，保留事件指纹阻止从旧消息或分叉重学；后续新表达可成为新证据 |
| 抑制 | 按整条原始消息生效，同消息其他内容也不自动提取 |
| 失效 | 撤销错误判断，不能替代用户明确遗忘 |

## 读取、召回与恢复

只读有效且未到期的记录，推断携带依据。画像由专用块注入，统计不召回；画像字段只允许已登记入口，和人工记忆编辑共用 `USER_PROFILE_MAX_CONTENT_CHARS`。日记 `diary:<日期>` 和 `reflection:current` 均是伙伴自身记录，发布、索引遗忘与日期召回归动态/日记协议。

召回以 RRF 相关性排序，同分再比较重要性、更新时间和 ID；向量失败或宽度不匹配用同域关键词。写入后补向量失败不撤销正文。关键词取拉丁词及汉字 2/3-gram，按分句轮流分配 `SPARSE_QUERY_TERM_MAX`；数据库候选先按命中词数（正文重于上下文）、再按更新时间限量，融合用名次而非原分数。夜间读取全部有效事实，不套管理列表分页。

恢复重映射原文证据和修订引用、重置审阅水位，保留遗忘指纹、来源和更新时间；缺原始消息的导入判断失效，手写记录仍受新证据时间限制。未知来源记为 `import`，已丢失来源无法复原；包级规则归备份协议。

## 验证入口

命令归 [Scripts](../../../../scripts/README.md)。重点覆盖原文/版本、整批回滚与水位、onboarding 限制、退避、分句取词和候选排序、遗忘后分叉/重复导入及恢复引用；样例检索不能证明真实模型审阅质量。
