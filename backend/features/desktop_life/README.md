# 桌面生活

拥有独立桌景套装库、分步制作草稿、当前成品、动作提案及播放事实。体验归 [DESIGN](../../../docs/DESIGN.md#桌面生活视频)，跨模块契约归 [PROTOCOL](../../../docs/PROTOCOL.md#桌面生活视频与模式隔离)，参考、画幅、媒体及恢复要求归 [PIPELINE](../../../docs/PIPELINE.md#桌面生活视频)。

| 入口 | 职责 |
|---|---|
| [service.py](service.py)、[repository.py](repository.py) | 套装、草稿确认、采用、基准失效与播放事务 |
| [generation.py](generation.py)、[prompts.py](prompts.py) | 冻结参考、图片生成与调整、运动设计、视频制作、检查及上传 |
| [queries.py](queries.py)、[use_cases.py](use_cases.py)、[tool_use_cases.py](tool_use_cases.py) | 冻结桌景上下文、HTTP 与工具公开操作 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[backup_assets.py](backup_assets.py)、[asset_references.py](asset_references.py) | 任务恢复、备份和资产引用贡献 |

所有状态写入共用账户行锁，进程内单飞只协调任务，模型等待在事务外。来源库通过公开快照读取；套装冻结参考的资产引用独立于来源条目生命周期。内容哈希用于校验，不代表套装身份，当前衣柜和场景指针不约束制作。

当前成品与唯一草稿分离，图片确认绑定实际输入图；修改图片使旧确认失效。采用基准同事务作废旧草稿、指令与派生资格，并记录后续更新待办；跨任务调度通过 bootstrap 注入回调，不能在事务内触发模型。创建事务先保存画面想法，前置检查失败仍可恢复。暂停先等待本地任务落盘，再重锁复核草稿；继续不能把上传来源改成新 AI 制作，恢复边界归 PROTOCOL。

上传视频保留原字节，技术检查与首帧提取在媒体层完成。已有成品的替换先等待采用，失败保留当前成品。备份恢复保留图片确认和待采用结果，清除在途供应商状态及自动付费待办。验证重点包括来源库删除后的继续制作、图片确认、基准更新、迟到结果、原始字节、旋转与 SAR、备份和客户端真实播放。
