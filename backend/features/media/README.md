# 公共媒体

拥有可复用图像/视频制作、质量链、候选、供应商句柄及账户声音方案。业务如何采纳、发布或绑定气泡归消费模块。图像和视频的供应商等待、参考和产物规格归 [PIPELINE](../../../docs/PIPELINE.md)，声音契约归 [音色设计与克隆](../../../docs/PROTOCOL.md#音色设计与克隆)。

| 入口 | 职责 |
|---|---|
| [image_generation.py](image_generation.py)、[chain.py](chain.py) | 能力筛选、提交、候选进度、评分与下载 |
| [video_jobs.py](video_jobs.py)、[contracts.py](contracts.py)、[queries.py](queries.py) | 视频请求、冻结句柄、恢复和只读任务快照 |
| [video_queue/service.py](video_queue/service.py)、[video_queue/contracts.py](video_queue/contracts.py) | 公共持久视频队列、限流冷却与远端收尾；分组机制见 [全局视频队列](../../../docs/PIPELINE.md#全局视频队列)，用户状态和管理接口契约见 [PROTOCOL](../../../docs/PROTOCOL.md#视频排队与结果核对) |
| [delivery.py](delivery.py)、[reference_reader.py](reference_reader.py) | 事务内结果交付与 bootstrap 装配的冻结身份读取端口 |
| [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) | 任务登记与候选/可恢复资源引用 |
| [voices.py](voices.py)、[speech.py](speech.py) | 声音方案制作与确认、公共音色解析和合成 |

调用方传入冻结参考与约束，制作在事务外等待供应商，写回重核任务和身份。图片链保留供画面检查与修复使用的参考，由业务保留必要引用，落选候选由链清理；没有恢复进度的同步制作只重试下载，不重复生成。

视频专有消息关系与共享执行控制分开，表关系见 [字段字典](../../../docs/DATABASE_FIELDS.md#影像播放与媒体制作)。展示阶段由快照投影，不能作为第二份执行状态。队列拥有远端提交、轮询和取消，业务通过 bootstrap 装配的有效性及交付端口协作；占用、未知结果和恢复规则集中在 [全局视频队列](../../../docs/PIPELINE.md#全局视频队列)。图像尝试共用提交账本，但不占视频名额。

聊天使用会话模块登记的事务内结果处理器绑定媒体气泡；动态通过 `PostPublication.video_job_id` 显式关联并决定发布；影像模块管理组合成品与制作工作项。公共视频模块不反查动态 JSON 或修改消息。已采纳资产与制作尝试分别保存，重做失败保持旧资产可用。

身份结构是纯 DTO，外观读取通过明确端口装配，未装配时失败。模型输入、候选选择、确定性门禁、真实供应商产物和客户端呈现是独立验证范围。

声音设计的角色资料由 workflow 通过公开快照读取；供应商适配器只消费已解析的描述或音频，不读取业务表。所有语音消费方经 `speech.py` 解析音色，避免绕过方案版本与样本引用保护。
