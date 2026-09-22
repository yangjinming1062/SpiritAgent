# 应用层动作编排（application/actions）

提案受理 → 异步独立评审 → 生成编排（`generation/video`）→ 目录发布（`domains/actions/publishing`）。播放指令与账本同事务写 outbox。

| 模块 | 职责 |
|---|---|
| `design.py` | 受理：门禁 → 语义去重 → 落库（reused / pending_review / rejected） |
| `pipeline.py` | 后台评审调度与重启恢复；approve 后 kick 生成 |
| `review.py` | 独立 LLM 评审；approve 才占制作额度并冻结设计规格 |
| `playback.py` | 播放指令、TTL、回执；queued ≠ completed |
| `context.py` | 运行时动作快照（`{{ACTION_CONTEXT}}`）；含外观包 ID 与就绪动作时长 |

## 代码外约束

- 提案事务不等待评审与视频；评审失败落 `deferred` 可重试，不默认批准。
- 不向模型展示制作额度，避免限额影响创建意图；额度只在 approve 强制。
- 动作尚未就绪时保存带 TTL 的表达意图；过期不补播。
- 评审使用提案所属形象的冻结参考图；候选仅含同包可点播动作，并保留内容、适用/避免条件、时长与播放方式。复用 ID 必须属于本次候选。
- 动态资料使用 JSON 保留内容边界，名称不代替运动描述；列表截断须明示，已批准提案同时提供真实制作状态，不能把失败或结果未知标为制作完成。
- 可读 companion/memory 公共入口并调用 `generation/video`；不得反向调用 chat/nightly。
