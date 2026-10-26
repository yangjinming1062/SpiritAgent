# 公共媒体

提供图像/视频制作、质量链、候选、供应商句柄及账户声音方案。消费模块决定结果采纳、发布与气泡绑定；制作规则归 [PIPELINE](../../../docs/PIPELINE.md)，声音规则归 [音色契约](../../../docs/PROTOCOL.md#音色设计与克隆)。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 图片链与候选检查 | [image_generation.py](image_generation.py)、[chain.py](chain.py) |
| 公共视频任务与快照 | [video_jobs.py](video_jobs.py)、[contracts.py](contracts.py)、[queries.py](queries.py) |
| 持久队列与远端收尾 | [video_queue/service.py](video_queue/service.py)、[video_queue/contracts.py](video_queue/contracts.py) |
| 结果交付与身份端口 | [delivery.py](delivery.py)、[reference_reader.py](reference_reader.py) |
| 声音方案与合成 | [voices.py](voices.py)、[speech.py](speech.py) |
| 恢复与引用贡献 | [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) |

## 任务与消费边界

调用方传入冻结参考和要求，供应商等待在事务外，写回核对任务和身份。图片链保留检查与修复所需参考，由业务登记必要引用；落选候选交链清理。没有恢复进度的同步制作只重试下载，不重新生成。

公共队列拥有远端提交、轮询与取消，业务通过 bootstrap 装配有效性检查和交付端口。图像尝试共用提交账本但不占视频名额；占用、未知结果及恢复统一见 [全局视频队列](../../../docs/PIPELINE.md#全局视频队列)。展示阶段从任务快照投影，不另存执行真源。

聊天通过会话模块的事务内接收器绑定气泡；动态按 `PostPublication.video_job_id` 读取原任务并决定发布；组合影像由影像领域拥有。媒体层不反查动态 JSON、不修改消息。采纳资产与制作尝试分开，重做失败保留旧资产。

身份读取和结果接收端口未装配时明确失败。声音设计由 workflow 读取角色快照，适配器只消费描述或音频；所有朗读经 `speech.py` 解析音色，保留方案版本与样本引用保护。

修改时按 [Scripts](../../../scripts/README.md) 分别核对请求装配、候选选择、原句柄恢复、取消与交付事务；真实供应商产物、音频格式及客户端呈现单独验收。
