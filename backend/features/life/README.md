# 生活状态与执行

负责账户生活状态、持续事项、执行步骤与事件。事实、权限、额度和完成条件归 [生活契约](../../../docs/PROTOCOL.md#生活状态与生活事件)，实体关系见 [字段字典](../../../docs/DATABASE_FIELDS.md#生活夜间与调度)。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 判断与调度 | [life_decisions.py](../../workflows/life_decisions.py)、[scheduling.py](../../workflows/scheduling.py) |
| 行动结构、认领、额度与执行 | [actions.py](actions.py)、[execution.py](execution.py)、[life_execution.py](../../workflows/life_execution.py) |
| 事务提交、事项转换与结算 | [use_cases.py](use_cases.py)、[lifecycle.py](lifecycle.py)、[service.py](service.py)、[settlement.py](settlement.py) |
| 阅读与记忆投影 | [life_reading.py](../../workflows/life_reading.py)、[life_events.py](../../workflows/life_events.py) |
| 上下文与只读接口 | [context.py](context.py)、[queries.py](queries.py)、[prompts.py](prompts.py)、[HTTP](../../entrypoints/http/routes/companion_life.py) |
| 聊天调整与呈现 | [life_tool.py](../../entrypoints/tools/builtin/life_tool.py)、[life_presentation.py](../../workflows/life_presentation.py) |

## 内部约束

共同上下文保证聚焦事项不被分页挤出；完成判断另读未解决步骤及可引用结果证据。`prompt_payload` 只投影内容、时间、叙事和结构化失败类别，不携带原始执行诊断、执行参数或存储元数据。用户生活接口使用公开叙事投影，执行层使用内部步骤视图；两者不能互相替代。事项、事件分别按更新时间、发生时间加 ID 稳定分页，总数显式请求才查询，模型上下文不走带总数的管理列表。

生活判断保存 `activity_type` 和当前环境、物品的 `scene_tags`，环境变化未提供标签时清空旧条件。语义、心情或场景变化推进呈现修订；真实步骤认领按能力设置活动类别，结束、暂停或退回等待时撤销正在进行的活动语义并通知投影。视觉选择只读取这些资料，不改变执行或结果事实。

`commit_decision_in_session` 在调用方事务内保存状态、事项、结构化下一步、伙伴叙事、事件与 outbox。叙事由模型在对应操作中提供，程序不通过关键词过滤或静态模板补写伙伴表达。步骤创建、认领、结算共用账户生活状态锁；业务受理再经 `require_step_submission` 检查，采纳事务批量结算关联步骤。模型和能力调用在事务外。

有意义事件经装配端口形成带 `source_event_id` 的系统记忆。投影先批量使用嵌套事务，失败逐条隔离，索引失败不回滚已接受的生活事实。跨模块通过公开操作、DTO 和端口协作。执行失败写入结构化 `failure_code`，原始诊断只留在任务和日志边界；已取得成品地址但下载失败的视觉步骤保留为 `unknown`，沿原任务恢复，不创建第二个步骤。

[backup.py](backup.py) 重映射动作输入、成果和证据；额度记录只合并，已提交事实保留，只有未受理步骤可沿原身份续行。阅读正文与实际范围保存在永久成果中，公开详情只返回叙事、笔记、来源和摘要。`0003_life_narrative_reset` 清理旧生活历史、失效这些事件派生的记忆索引并建立新的叙事字段，同时终止旧夜间计划，保留独立的额度与提交账本；备份仅接受 0003，不迁移旧工程日志。

## 来源与阅读上下文

聊天模型判断本次操作是否来自用户的明确指令。`life_adjust.user_instruction` 必填：用户要求时逐字引用，伙伴自行调整时为 `null`；待确认的要求不能按自主决定执行。入口核对引用来自服务端注入的本轮非主动用户消息，非空引用不匹配则拒绝调整；有效引用才标记 `source=user`。引用留在工具调用记录，不进入领域决策字段。原文匹配只核对出处，操作与目标的语义判断仍由聊天模型负责；领域层继续检查用户暂停、修订和请求幂等。

阅读接收已认领的步骤，复用伙伴人设、记忆和生活状态，并按步骤关联定点补充所属事项及结果证据，避免近期列表挤出该事项。无关联事项时使用当前背景和阅读目的。输入经内容投影去除内部引用；笔记保留有依据的动机、所得、与已有认识的关系及未解问题，不强求新发现。笔记沿现有成果、事件和记忆链进入后续选择与表达，是否分享由各消费入口结合情境判断。

## 验证边界

验证按 [持续生活验收](../../../scripts/README.md#持续生活验收) 覆盖事务、幂等、来源、暂停和恢复。原文匹配与保存笔记不能单独证明意图识别或阅读连续性；否定、引文、目标歧义，以及阅读对后续选择、聊天和日记的影响尚待真实模型验证，跨日质量另用长期使用核查。
