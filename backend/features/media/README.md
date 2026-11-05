# 公共媒体

提供图像/视频制作、质量链、候选、供应商句柄及账户声音方案。消费模块决定结果采纳、发布与气泡绑定。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 图片链与候选检查 | [image_generation.py](image_generation.py)、[chain.py](chain.py) |
| 公共视频任务、结果接收与快照 | [video_jobs.py](video_jobs.py)、[video_results.py](video_results.py)、[queries.py](queries.py) |
| 持久队列与远端收尾 | [video_queue/service.py](video_queue/service.py)、[video_queue/contracts.py](video_queue/contracts.py) |
| 结果交付与身份端口 | [delivery.py](delivery.py)、[reference_reader.py](reference_reader.py) |
| 声音方案与合成 | [voices.py](voices.py)、[speech.py](speech.py) |
| 恢复与引用贡献 | [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) |

## 检查与恢复

单槽候选检查视图由 [review_views.py](review_views.py) 的 `single_slot_review_view` 统一装配；场景、衣柜、全身与影像只提供各自的候选、证据与操作资格。

[review_contracts.py](review_contracts.py) 定义定性判断与不可变证据；[review_operations.py](review_operations.py) 受理独立计额的显式续审。[manual_reviews.py](manual_reviews.py) 受理用户选取候选或重新生成，保存原判断及人工依据，原授权下的交付由业务所有者继续，无需用户再点交付按钮。[image_jobs.py](image_jobs.py) 保存聊天图片及逐槽位结果，消息绑定、删除和换回答由会话公共操作协同。[continuations.py](continuations.py) 保存业务等待与原继续操作身份，通过组合根装配业务所有者，检查通知不直接授予业务动作。

待复核任务不自动重新调用模型，恢复只核对原证据与原操作；续审账本只保存请求、候选额度依据及执行状态，候选的身份、目标和画质判断由 `VisualReview` 持有。检查中断时保留已确定的维度和阶段证据，人工排除是独立的选择事实。人工操作的持久启动交接与业务依赖等待分别恢复，不能将检查完成视为交付成功。已发布版本持有必要检查附件，执行检查点回收不清除最终依据。

## 任务与消费边界

调用方传入冻结参考和要求，供应商等待在事务外，写回核对任务和身份。图片链保留检查与修复所需参考，由业务登记必要引用；参考输入由调用方拥有，链只删除本次制作的落选候选与处理产物。没有恢复进度的同步制作只重试下载，不重新生成。

公共队列拥有远端提交、轮询与取消，业务通过 bootstrap 装配有效性检查和交付端口。尝试以生成身份和供应商游标唯一登记；业务检查点尚未提交时先找回已有尝试，再考虑当前配置和新提交。业务暂停时未提交尝试停止领取，已提交尝试继续核对远端；恢复时未提交尝试重新入队，已提交尝试沿原句柄接收结果。业务仅保留尝试关联与制作游标，不另存供应商名称、模型或尝试序号。`video_results.py` 统一消费原尝试终态、确定失败后的链尾资格及结果接收；先保存业务接续点再确认消费，下载失败或未知提交不派生新制作。队列进度在查询事务内批量投影，变化只发送刷新通知，不写业务检查点。图像尝试共用提交账本但不占视频名额。

聊天通过会话模块的事务内接收器绑定气泡；动态按 `PostPublication.video_job_id` 读取原任务并决定发布；组合影像由影像领域拥有。媒体层不反查动态 JSON、不修改消息。采纳资产与制作尝试分开，重做失败保留旧资产。

身份读取和结果接收端口未装配时明确失败。声音设计由 workflow 读取角色快照，适配器只消费描述或音频；所有朗读经 `speech.py` 解析音色，保留方案版本与样本引用保护。

分别核对请求装配、候选选择、原句柄恢复、取消与交付事务；真实供应商产物、音频格式及客户端呈现单独验收。
