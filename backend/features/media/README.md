# 公共媒体

拥有可复用图像/视频制作、质量链、候选、供应商句柄和复核记录。业务如何采纳、发布或绑定气泡归消费模块。供应商等待、参考和产物规格归 [PIPELINE](../../../docs/PIPELINE.md)。

| 入口 | 职责 |
|---|---|
| [image_generation.py](image_generation.py)、[chain.py](chain.py) | 能力筛选、提交、候选进度、评分与下载 |
| [video_jobs.py](video_jobs.py)、[contracts.py](contracts.py)、[queries.py](queries.py) | 视频请求、冻结句柄、恢复和只读任务快照 |
| [delivery.py](delivery.py)、[reference_reader.py](reference_reader.py) | 事务内结果交付与 bootstrap 装配的冻结身份读取端口 |
| [review_service.py](review_service.py)、[review_contracts.py](review_contracts.py) | 复核状态与公开响应；跨业务采纳由 [media_review](../../workflows/media_review.py) 协调 |
| [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) | 任务登记与候选/可恢复资源引用 |

调用方传入冻结参考与约束，制作在事务外等待供应商，写回重核任务和身份。未知结果保留原句柄，不重新提交；已提交工作在维护期间继续查询并落盘，后续付费步骤暂停。没有恢复进度的同步制作只重试下载，不重复生成。

聊天使用会话模块登记的事务内结果处理器绑定媒体气泡；动态通过 `PostPublication.video_job_id` 显式关联并决定发布；动作和桌面生活自行管理采纳与目录。公共视频模块不反查动态 JSON 或修改消息。已采纳资产与制作尝试分别保存，重做失败保持旧资产可用。

身份结构是纯 DTO，外观读取通过明确端口装配，未装配时失败。模型输入、候选选择、确定性门禁、真实供应商产物和客户端呈现是独立验证范围。
