# 动作编排

负责提案受理、异步评审与播放协调，调用 `generation/video` 制作素材，经 `domains/actions/publishing` 发布目录。播放指令与账本同事务写 outbox，跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

## 提案处理

受理事务先做门禁与语义去重，不等待后台评审或视频；评审失败落 `deferred` 可重试，不默认批准。拒绝抑制窗口见领域 [policy.py](../../domains/actions/policy.py)。完整制作链见 [PIPELINE](../../../../docs/PIPELINE.md#评审与制作)。

## 模块入口

| 模块 | 职责 |
|---|---|
| [design.py](design.py) | 受理：门禁 → 语义去重 → 落库（reused / pending_review / rejected），结论附所属包 |
| [pipeline.py](pipeline.py) | 调用方提交受理事务后经 `schedule_accepted_proposal` 启动评审或同 key 动作重做；评审重启恢复；approve 后 kick 生成 |
| [review.py](review.py) | 独立 LLM 评审；approve 才占制作额度并冻结设计规格 |
| [playback.py](playback.py) | 播放指令、TTL、回执；queued ≠ completed |
| [context.py](context.py) | 运行时动作快照（`{{ACTION_CONTEXT}}`）；含外观包 ID 与就绪动作时长 |

## 评审输入与额度

- 制作额度只在 approve 时强制，不向模型展示，避免额度影响创建意图。
- 评审只用提案所属包冻结的参考图、角色快照与生成上下文中的人设，不读取当前角色卡或实时人设。
- 现有动作（`existing_actions`）是同包全部可点播表达动作，保留内容、适用/避免条件、时长与播放方式，按词面相近度排序，超过 10 个才只取最相近的 10 个；词面相近度对中文近义表达不敏感，不能据此剔除零分动作。列表为空即该形象尚无表达动作，评审不以此暂缓；复用 ID 须属于该列表。

## 运行时快照与播放意图

- 动态资料用 JSON 保留内容边界，名称不代替运动描述；在途与拒绝提案保留原设计，列表截断须明示。
- 已批准提案同时提供实际制作状态，失败或结果未知不能标记为制作完成。
- 尚未就绪时保存有有效期的表达意图并记录外观代次；过期不补播，外观已切换（含换装后再穿回同一包）时记 rejected。代次规则与表演事实按[播放契约](../../../../docs/PROTOCOL.md#动作目录与播放)判定。

## 依赖与验证

可读 companion / memory 公共入口并调用 `generation/video`，不得反向调用 chat / nightly。制作与恢复按 [PIPELINE](../../../../docs/PIPELINE.md#验证)验收；播放与回执按 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)核对，静态检查见 [Backend](../../../README.md#契约与验证)。
