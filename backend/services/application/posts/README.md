# 伙伴动态

编排独立发布、线程评论回复和后台任务。内容隔离、来源额度、未读及结果未知的完整语义归 [PROTOCOL](../../../../docs/PROTOCOL.md#动态与日记)，出镜媒体参考归 PIPELINE。

## 入口与所有权

| 入口 | 职责 |
|---|---|
| [publication.py](publication.py) | 请求分类、额度预留、冻结输入、创作和媒体制作，原视频核查/采纳/放弃及记录回收 |
| [replies.py](replies.py) | 每动态串行生成评论回复，写回核对触发评论及尝试版本 |
| [autonomous.py](autonomous.py) | 已初始化活跃账户的低频决策，按用户单飞 |
| [prompt_contract.py](prompt_contract.py) | 共用 schema 生成模型可读约束 |
| [domains/posts/store.py](../../domains/posts/store.py) | 发布事务、额度、评论和夜间线程归集 |

任务启停归 bootstrap，周期恢复随 Cron 扫描；所有后台任务登记用户归属，纳入账户维护。视频制作与文件归 `generation/video_jobs` 所有，发布不能直接删除视频产物。

## 制作与恢复

[PostPublication](../../../modules/companion/posts.py) 在创作前预留额度并冻结输入，有计划不重新规划。计划中不适合内容类型或实际能力的字段保存前丢弃；视频规格在提交前校验。受理和执行均重新核对政策与能力。

- 原视频等待截止由任务创建时间和 `video_generation_wait_seconds` 决定，重启不延长。没有结果记录的 `*_submitting` 按未知提交停止；`narration_submitting` 已有主媒体时可按旁白失败收尾。
- 明确失败或阻止后，任务没有关联动态才释放独占图片、语音和旁白；已发布、未知及取消状态保留必要产物。发布状态和媒体引用须一起提交。
- 视频未知恢复使用原计划和任务句柄，查询只下载及本地核查，不评分或换链；用户采纳和放弃经发布服务与视频所有者协作，重查权限、身份、额度和引用。
- 评论线程在写回前核对触发评论和尝试版本，删除与旧尝试不能产生迟到回复；失败沿原任务重试。

受理和状态共用 [PostPublicationResult](../../../modules/companion/schemas_posts.py)，工具出口序列化为 JSON；夜间账本关联实际动态，不以受理当成发布。

## 记录保留与验证

`gc_autonomous_publications` 仅回收自主来源、随机 UUID 请求键的已知终态任务：完整输入/计划七天，精简审计九十天。未知、在途、用户请求键及夜间账本引用保留，已发布内容的媒体归属字段继续保留。启动和小时维护调用回收。

命令归 [Scripts](../../../../scripts/README.md)。重点核对主对话隔离、线程顺序、删除与重试、并发额度、未知提交、原视频恢复及长媒体评论；模型创作质量与静态检查分开报告。
