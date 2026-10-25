# 伙伴动态

编排主动创作、线程评论回复和后台任务。动态与日记的正文、附件和评论统一由 [内容领域](../entries/README.md) 持久化，发布任务使用共享 jobs 控制状态与本模块的专有详情。共享背景、时间窗口、发布额度、未读及结果未知的完整语义归 [PROTOCOL](../../../docs/PROTOCOL.md#动态与日记)，出镜媒体参考归 PIPELINE。

## 入口与所有权

| 入口 | 职责 |
|---|---|
| [publication.py](publication.py) | 额度预留、创作前资料快照和媒体制作，原视频核查/采纳/放弃及记录回收 |
| [context.py](context.py) | 创作与评论共用背景、动态制作资料和事件时间 |
| [replies.py](replies.py) | 每动态串行生成评论回复，写回核对触发评论及尝试版本 |
| [autonomous.py](autonomous.py) | 已初始化活跃账户的低频决策，按用户单飞 |
| [prompt_contract.py](prompt_contract.py) | 共用 schema 生成模型可读约束 |
| [store.py](store.py) | 发布事务、额度及内容领域操作的业务编排 |

任务启停经本模块生命周期贡献在 bootstrap 登记，周期恢复随 Cron 扫描。视频制作与文件归 [features/media/video_jobs.py](../media/video_jobs.py)，发布通过显式 `video_job_id` 关联并调用媒体操作，不能直接删除视频产物。

## 制作与恢复

[PostPublication](publication_models.py) 受理时预留额度，首次创作调用前装配并保存完整资料快照，有计划不重新规划。计划中不适合内容类型或实际能力的字段保存前丢弃；视频规格在提交前校验。受理和执行均重新核对政策与能力。

- 视频持续等待原任务的确定终态，重启后沿已保存任务继续。没有结果记录的 `*_submitting` 按未知提交停止；`narration_submitting` 已有主媒体时可按旁白失败收尾。
- 明确失败或阻止后，任务没有关联动态才释放独占图片、语音和旁白；已发布、未知及取消状态保留必要产物。发布状态和媒体引用须一起提交。
- `result_unknown` 的核对、采纳及放弃由 `publication.py` 协调原视频任务，放行条件遵循上述动态契约；放弃仍协同视频所有者回收引用。
- 评论线程在写回前核对触发评论和尝试版本，删除与旧尝试不能产生迟到回复；失败沿原任务重试。

发布结果使用 [PostPublicationResult](schemas.py)，共享任务状态与发布结果分别保存；列表与评论读取由内容领域提供。

## 记录保留与验证

启动和小时维护调用 `gc_autonomous_publications`，按 [必要历史](../../../docs/DATABASE.md#必要历史)清理已知终态资料；在途视频和待完成资产清理仍受保护。

命令归 [Scripts](../../../scripts/README.md)。重点核对主对话隔离、线程顺序、删除与重试、并发额度、未知提交、原视频恢复及长媒体评论；模型创作质量与静态检查分开报告。
