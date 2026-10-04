# 动作编排

负责提案受理、异步评审与播放协调，调用 `generation/video` 按动作规格制作图片或视频（生成收尾经 `domains/actions/publishing` 发布目录）。动态表达当前仅制作和点播视频，系统槽位图片随客户端基础状态呈现。播放指令与账本同事务写 outbox，跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

## 提案处理

去重、门禁与 flush 在用户级受理锁（`policy.get_action_accept_lock`）内完成，调用方在锁外提交。受理在取得该锁前不访问数据库，传入的会话须尚未开启事务（REST 入口先结束鉴权事务），排队等待时不占用连接。受理不等待评审或视频，评审仍持同一把锁，因此同用户的在途评审会先完成：

- 同包规范化名称已有采纳素材则复用，制作中报告在制，待确认只返回复核项；失败或复核结束仍停在 `review` 的动作原位重做，不新建提案。审中的同创意提案直接返回，最近一次结论为复用且动作仍可播放时也直接复用。
- 新建提案须通过 [policy.py](../../domains/actions/policy.py) 的 `check_can_accept`（时长、整秒、拒绝后 7 天抑制、自主创建开关）。`deferred` / `rejected` 原行可重新评审；已批准或已复用但动作不可用时保留历史行并新建提案。
- 在制动作不重复评审或计额，独立同名重做须在清尝试前重新核对身份、自主开关、抠像模型并预留制作额度，由 `schedule_accepted_proposal` 按 `existing_action` 唤醒生成；同包已有在途任务时只记唤醒，待确认动作只等用户复核。复核拒绝或已结束复核仍停在 `review` 时，先用 `clear_action_attempt` 作废本次素材，再独立重做；受理阶段统一检查 `require_action_matting_model`，缺失即拒绝，不进入评审或付费生成。
- 提案幂等键按 `(user, source, pack, fingerprint)` 取未占用序号，跨来源复用 `deferred` / `rejected` 行需换新来源键；保存点内以唯一约束去重，并发落败方返回已受理提案。评审失败落 `deferred` 可重试；重启时中断的 `pending` 重新排队，`deferred` 只在创建后 24 小时内随重启重试；展示给模型的暂缓提案自最近一次暂缓（`updated_at`）起算 24 小时（`DEFERRED_PROPOSAL_WINDOW`），在途提案按最近一次状态变更倒序。超期后重提同一创意才复用原行重新评审，复用后再次暂缓的提案不随重启重审，避免每次启动付费重审；拒绝抑制从拒绝行的 `updated_at` 起算，复用后再次拒绝会重新计时。

完整制作链见 [PIPELINE](../../../../docs/PIPELINE.md#评审与制作)。

## 模块入口

| 模块 | 职责 |
|---|---|
| [design.py](design.py) | 受理，结论为 reused / pending_review / rejected 并附所属包；落在同 key 已有动作上时附其状态（在制、待确认、重做） |
| [pipeline.py](pipeline.py) | 调用方提交受理事务后经 `schedule_accepted_proposal` 启动评审或同 key 重做；`_run_proposal_review` 持受理锁覆盖评审、额度校验与提交；评审重启恢复；approve 后启动生成 |
| [review.py](review.py) | 独立 LLM 评审；同 key 动作已存在时不调用模型，按其状态复用或暂缓；approve 时经 `consume_create_slot` 校验制作额度，只新建动作行并冻结设计规格 |
| [playback.py](playback.py) | 播放请求：校验当前包与动作（系统槽位动作不对模型开放，点播与 `action_inspect` 均按不存在处理），账本与 outbox 指令同事务写入，动作制作中（queued / processing / result_unknown）时保存带有效期与外观代次的表达意图，无已采纳素材且失败或待复核时直接 rejected |
| [context.py](context.py) | 动作快照（外观包 ID、就绪动作与时长、在途与拒绝提案），陪伴预设会话由对话编排的 `build_companion_environment_prompt` 在每次模型调用前追加；夜间规划直接读取其字段 |

调用方：聊天工具 [action_tool.py](../../adapters/tools/builtin/action_tool.py)、动作 REST（[companion_actions.py](../../../api/v1/companion_actions.py)）与夜间规划受理提案；`action_tool` 与桌面 RPC `companion.idle_expression` 发起播放。回执经 REST 写入 `domains/actions/usage`。

## 评审输入与额度

- 制作额度在 approve 或独立重做受理时强制，额度数值不进入模型上下文，避免影响创建意图；超限转 deferred 时，理由经动作快照与 `action_inspect` 可见。
- 同包以完整名称的 NFKC 和 casefold 结果确定动作身份，新 key 用名称哈希，不受运动描述或截断影响；查重也按规范化名称读取旧 key。历史多个同名动作明确报告冲突，须重命名或删除，不静默覆盖。同名失败或未采纳动作沿冻结设计重做，改内容须换名称。评审前及批准前复核同名状态，已采纳复用，其他状态暂缓；批准只新建动作行，唯一约束保护并发。
- 额度账本独立于提案、动作和包，按滚动24小时结算；批准键幂等，删除不返额。制作来源及窗口契约归 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。
- 评审只用提案所属包冻结的参考图、角色快照与生成上下文中的人设，不读取当前角色卡或实时人设。
- 现有动作（`existing_actions`）是同包全部可点播表达动作，保留内容、适用/避免条件、时长与播放方式，按词面相近度排序，超过 10 个才只取最相近的 10 个；词面相近度对中文近义表达不敏感，不能据此剔除零分动作。列表为空即该形象尚无表达动作，评审不以此暂缓；模型给出的复用 ID 须属于该列表。

## 运行时快照与播放意图

- 动态资料用 JSON 保留内容边界，名称不代替运动描述；在途与拒绝提案保留原设计，列表截断须明示。
- 已批准提案同时提供实际制作状态，失败或结果未知不能标记为制作完成。
- 动作制作中时保存有有效期的表达意图并记录外观代次；兑现规则与表演事实按[播放契约](../../../../docs/PROTOCOL.md#动作目录与播放)判定。

## 依赖与验证

可读 companion / memory 公共入口并调用 `generation/video`，不得反向调用 chat / nightly。制作与恢复按 [PIPELINE](../../../../docs/PIPELINE.md#验证)验收；播放与回执按 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)核对，静态检查见 [Backend](../../../README.md#契约与验证)。
