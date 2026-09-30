# 动作编排

负责提案受理、异步评审与播放协调，调用 `generation/video` 制作素材（生成收尾经 `domains/actions/publishing` 发布目录）。播放指令与账本同事务写 outbox，跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

## 提案处理

去重、门禁与 flush 在用户级受理锁（`policy.get_action_accept_lock`）内完成，调用方在锁外提交。受理不等待本提案的评审或视频，但评审持同一把锁，因此受理会排在同用户在途评审之后：

1. 同 key 动作已就绪则复用，制作中则报告在制；失败或取消的同 key 动作原位重做，不新建提案。
2. 同创意提案在审时直接返回该提案。
3. 经 [policy.py](../../domains/actions/policy.py) 的 `check_can_accept` 门禁（时长上限、整秒、近 7 天拒绝抑制、自主创建开关）。
4. 同创意的 deferred / rejected 提案复用原行重新评审，否则新建提案。

同 key 重做与在制动作不经门禁、评审与额度，由 `schedule_accepted_proposal` 直接唤醒生成（同包已有在途任务时只记唤醒）。评审失败落 `deferred` 可重试，不默认批准；重启时 pending 与 deferred 提案重新排队评审。完整制作链见 [PIPELINE](../../../../docs/PIPELINE.md#评审与制作)。

## 模块入口

| 模块 | 职责 |
|---|---|
| [design.py](design.py) | 受理，结论为 reused / pending_review / rejected 并附所属包 |
| [pipeline.py](pipeline.py) | 调用方提交受理事务后经 `schedule_accepted_proposal` 启动评审或同 key 重做；`_run_proposal_review` 持受理锁覆盖评审、额度校验与提交；评审重启恢复；approve 后启动生成 |
| [review.py](review.py) | 独立 LLM 评审；approve 时经 `consume_create_slot` 校验制作额度并冻结设计规格 |
| [playback.py](playback.py) | 播放请求：校验当前包与动作，账本与 outbox 指令同事务写入，动作制作中（queued / processing / result_unknown）时保存带有效期与外观代次的表达意图，失败、取消或待复核直接 rejected |
| [context.py](context.py) | 动作快照（外观包 ID、就绪动作与时长、在途与拒绝提案），陪伴预设会话由对话编排的 `build_companion_environment_prompt` 在每次模型调用前追加；夜间规划直接读取其字段 |

调用方：聊天工具 [action_tool.py](../../adapters/tools/builtin/action_tool.py)、动作 REST（[companion_actions.py](../../../api/v1/companion_actions.py)）与夜间规划受理提案；`action_tool` 与桌面 RPC `companion.idle_expression` 发起播放。回执经 REST 写入 `domains/actions/usage`。

## 评审输入与额度

- 制作额度只在 approve 时强制，额度数值不进入模型上下文，避免影响创建意图；超限转 deferred 时，理由经动作快照与 `action_inspect` 可见。
- 评审只用提案所属包冻结的参考图、角色快照与生成上下文中的人设，不读取当前角色卡或实时人设。
- 现有动作（`existing_actions`）是同包全部可点播表达动作，保留内容、适用/避免条件、时长与播放方式，按词面相近度排序，超过 10 个才只取最相近的 10 个；词面相近度对中文近义表达不敏感，不能据此剔除零分动作。列表为空即该形象尚无表达动作，评审不以此暂缓；复用 ID 须属于该列表。

## 运行时快照与播放意图

- 动态资料用 JSON 保留内容边界，名称不代替运动描述；在途与拒绝提案保留原设计，列表截断须明示。
- 已批准提案同时提供实际制作状态，失败或结果未知不能标记为制作完成。
- 动作制作中时保存有有效期的表达意图并记录外观代次；兑现规则与表演事实按[播放契约](../../../../docs/PROTOCOL.md#动作目录与播放)判定。

## 依赖与验证

可读 companion / memory 公共入口并调用 `generation/video`，不得反向调用 chat / nightly。制作与恢复按 [PIPELINE](../../../../docs/PIPELINE.md#验证)验收；播放与回执按 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)核对，静态检查见 [Backend](../../../README.md#契约与验证)。
