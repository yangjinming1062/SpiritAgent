# 伙伴动态

编排独立动态的发布、评论回复和后台任务；跨端契约见 [动态与日记](../../../../docs/PROTOCOL.md#动态与日记)。

| 入口 | 职责与内部约束 |
|---|---|
| [publication.py](publication.py) | 受理发布意图、分类额度、冻结输入并编排创作与媒体制作；受理和执行阶段均核对开关与能力 |
| [replies.py](replies.py) | 按动态串行处理评论；写回前核对触发评论及尝试版本 |
| [autonomous.py](autonomous.py) | 扫描已完成伙伴初始化的活跃账户，发起低频决策并恢复待处理发布与回复 |
| [领域存储](../../domains/posts/store.py) | 发布与额度事务（含剩余额度查询）、评论操作和夜间线程归集 |

[发布任务](../../../modules/companion/posts.py)在创作前预留额度并冻结输入，已有计划时不重新规划；模型计划里不适用于所选类型或当前能力的字段（如图片动态的文稿、无语音能力时的旁白）在保存前丢弃，不拒绝整条计划。视频按供应商链能力选择分辨率（偏好见 [publication.py](publication.py)），付费生成首帧前先核对，放不下记为阻止。恢复时续查已知视频句柄；视频等待以任务创建时间加 [video_generation_wait_seconds](../generation/video_jobs.py) 为限，重启后截止时间不变，超时与没有结果记录的 `*_submitting` 阶段一样按结果未知停止、不重新提交；旁白阶段可保留已完成主媒体发布。

以阻止或失败收尾的任务不产生动态，终态提交后删除其保存的图片、语音与旁白；动态已存在时不改写任务也不删除文件，结果未知和取消的任务保留已有产物。视频成品归[视频任务](../generation/video_jobs.py)所有，发布任务不删除；成片后才被阻止的视频动态会留下该成品，暂无回收入口。

发布受理与状态查询共用 [PostPublicationResult](../../../modules/companion/schemas_posts.py)，工具出口序列化为 JSON。夜间账本将自主不发布记为跳过、政策或能力拦截记为阻止，成功和部分成功关联实际动态。

启停由 [lifecycle.py](../../../bootstrap/lifecycle.py) 管理，周期恢复随 [Cron 扫描](../../adapters/scheduler/cron.py) 执行；后台任务须登记用户归属，以纳入账户维护。

验证入口见 [Scripts](../../../../scripts/README.md)：核对主对话隔离、同线程顺序、删除与重试、并发额度、未知提交、重启，以及长媒体下的实际评论入口。
