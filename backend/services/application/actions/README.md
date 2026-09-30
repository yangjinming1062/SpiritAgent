# 动作编排

负责提案受理、异步评审与播放协调，调用 `generation/video` 制作素材（生成收尾经 `domains/actions/publishing` 发布目录）。播放指令与账本同事务写 outbox，跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

## 提案处理

去重、门禁与 flush 在用户级受理锁（`policy.get_action_accept_lock`）内完成，调用方在锁外提交。受理不等待本提案的评审或视频，但评审持同一把锁，因此受理会排在同用户在途评审之后：

1. 同 key 动作已就绪则复用，制作中则报告在制，成品仍有待确认的复核项则直接返回；失败、取消或复核已结束仍停在 `review` 的同 key 动作原位重做，不新建提案。
2. 同创意提案在审时直接返回该提案。
3. 同创意最近一次评审结论为复用、且所指动作仍可播放时直接复用该动作。
4. 经 [policy.py](../../domains/actions/policy.py) 的 `check_can_accept` 门禁（时长上限、整秒、拒绝后 7 天抑制、自主创建开关）。
5. 同创意的 deferred / rejected 提案复用原行重新评审；已批准或已复用而动作已不可用时保留历史行，新建提案重新评审，批准照常计入额度。

同 key 重做与在制动作不经门禁、评审与额度，由 `schedule_accepted_proposal` 按受理结论的 `existing_action` 唤醒生成（同包已有在途任务时只记唤醒）；待确认的动作只等用户复核。用户在复核中拒绝成品时即作废该次生成尝试（`clear_action_attempt`），之后的重做是独立的新尝试，不会再复核同一素材；复核已结束却仍停在 `review` 的动作在重做前同样作废。同 key 重做、复用提案与新建提案在受理时都经 `generation/video` 的 `require_action_matting_model` 检查抠像模型，缺失即拒绝，不进入评审或付费生成。拒绝抑制从拒绝时刻（拒绝行的 `updated_at`）起算，复用旧行再被拒时重新计时。

幂等键在 (user, source, pack, fingerprint) 下取首个未占用的序号：首行沿用 `pack|source|fingerprint` 的散列，历史行占用时追加序号；跨来源复用 deferred / rejected 行时同时换成新来源的空闲键。提案写入在保存点内进行，并发受理同一创意时由唯一约束去重，落败方返回已受理的提案，受理不因幂等约束失败。评审失败落 `deferred` 可重试，不默认批准；重启时 pending 与 deferred 提案重新排队评审。完整制作链见 [PIPELINE](../../../../docs/PIPELINE.md#评审与制作)。

## 模块入口

| 模块 | 职责 |
|---|---|
| [design.py](design.py) | 受理，结论为 reused / pending_review / rejected 并附所属包；落在同 key 已有动作上时附其状态（在制、待确认、重做） |
| [pipeline.py](pipeline.py) | 调用方提交受理事务后经 `schedule_accepted_proposal` 启动评审或同 key 重做；`_run_proposal_review` 持受理锁覆盖评审、额度校验与提交；评审重启恢复；approve 后启动生成 |
| [review.py](review.py) | 独立 LLM 评审；同 key 动作已存在时不调用模型，按其状态复用或暂缓；approve 时经 `consume_create_slot` 校验制作额度，只新建动作行并冻结设计规格 |
| [playback.py](playback.py) | 播放请求：校验当前包与动作，账本与 outbox 指令同事务写入，动作制作中（queued / processing / result_unknown）时保存带有效期与外观代次的表达意图，失败、取消或待复核直接 rejected |
| [context.py](context.py) | 动作快照（外观包 ID、就绪动作与时长、在途与拒绝提案），陪伴预设会话由对话编排的 `build_companion_environment_prompt` 在每次模型调用前追加；夜间规划直接读取其字段 |

调用方：聊天工具 [action_tool.py](../../adapters/tools/builtin/action_tool.py)、动作 REST（[companion_actions.py](../../../api/v1/companion_actions.py)）与夜间规划受理提案；`action_tool` 与桌面 RPC `companion.idle_expression` 发起播放。回执经 REST 写入 `domains/actions/usage`。

## 评审输入与额度

- 制作额度只在 approve 时强制，额度数值不进入模型上下文，避免影响创建意图；超限转 deferred 时，理由经动作快照与 `action_inspect` 可见。
- 同名即同一动作身份（key 由名称派生，非 ASCII 名称由语义指纹派生）。评审前与批准前都核对同包同 key 动作：已就绪按复用结案，其余状态（制作中、待确认、失败等）暂缓并写明原因，不覆盖、不重排其他提案的动作；两个同名提案先后获批时，后者按先者动作的状态收敛。批准经 `create_action` 只新建动作行，核对之后才并发出现的同 key 动作由唯一约束挡住，不会被覆盖。
- 评审只用提案所属包冻结的参考图、角色快照与生成上下文中的人设，不读取当前角色卡或实时人设。
- 现有动作（`existing_actions`）是同包全部可点播表达动作，保留内容、适用/避免条件、时长与播放方式，按词面相近度排序，超过 10 个才只取最相近的 10 个；词面相近度对中文近义表达不敏感，不能据此剔除零分动作。列表为空即该形象尚无表达动作，评审不以此暂缓；模型给出的复用 ID 须属于该列表。

## 运行时快照与播放意图

- 动态资料用 JSON 保留内容边界，名称不代替运动描述；在途与拒绝提案保留原设计，列表截断须明示。
- 已批准提案同时提供实际制作状态，失败或结果未知不能标记为制作完成。
- 动作制作中时保存有有效期的表达意图并记录外观代次；兑现规则与表演事实按[播放契约](../../../../docs/PROTOCOL.md#动作目录与播放)判定。

## 依赖与验证

可读 companion / memory 公共入口并调用 `generation/video`，不得反向调用 chat / nightly。制作与恢复按 [PIPELINE](../../../../docs/PIPELINE.md#验证)验收；播放与回执按 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)核对，静态检查见 [Backend](../../../README.md#契约与验证)。
