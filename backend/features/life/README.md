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

共同上下文保证聚焦事项不被分页挤出；完成判断另读未解决步骤及可引用结果证据。`prompt_payload` 只投影内容、时间与证据身份，不携带执行参数和存储元数据。事项、事件分别按更新时间、发生时间加 ID 稳定分页，总数显式请求才查询，模型上下文不走带总数的管理列表。

`commit_decision_in_session` 在调用方事务内保存状态、事项、结构化下一步、事件与 outbox。步骤创建、认领、结算共用账户生活状态锁；业务受理再经 `require_step_submission` 检查，采纳事务批量结算关联步骤。模型和能力调用在事务外。

有意义事件经装配端口形成带 `source_event_id` 的系统记忆。投影先批量使用嵌套事务，失败逐条隔离，索引失败不回滚已接受的生活事实。跨模块通过公开操作、DTO 和端口协作。

[backup.py](backup.py) 重映射动作输入、成果和证据；额度记录只合并，已提交事实保留，只有未受理步骤可沿原身份续行。阅读正文与实际范围保存在永久成果中，公开详情只返回笔记、来源和摘要。

## 当前限制

- 聊天调整按“存在用户消息且非主动回合”标记 `source=user`，尚未核实对本次操作的明确指令。普通闲聊中的自主调整可能误归用户，也可能绕过用户暂停的恢复限制；领域层对来源的检查不能修正入口误标。
- 阅读指令与字段说明仍面向“为用户回应阅读目的”，只提供目的和资料。个人动机、经历如何进入阅读及后续分享尚需改进与验证，取得资料和保存笔记不能单独证明这种连续性。

验证按 [持续生活验收](../../../scripts/README.md#持续生活验收) 覆盖事务、幂等、来源、暂停和恢复；跨日选择与表达质量另用真实模型和长期使用核查。
