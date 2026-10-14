# 窗口动作

拥有窗口动作提案、制作、冻结外观包、采纳、目录与播放账本。制作归 [PIPELINE](../../../docs/PIPELINE.md#动作资产制作)，额度、目录和回执归 [PROTOCOL](../../../docs/PROTOCOL.md#动作目录与播放)。桌面生活视频另由独立模块管理。

## 模块入口

| 入口 | 职责 |
|---|---|
| [design.py](design.py)、[review.py](review.py)、[pipeline.py](pipeline.py) | 受理、去重、独立评审、批准与恢复 |
| [generation/commands.py](generation/commands.py) | 上传/参考创建、重试、启用与删除 |
| [generation/planning.py](generation/planning.py)、[generation/pipeline.py](generation/pipeline.py) | 静态/运动规划与逐阶段制作 |
| [generation/transactions.py](generation/transactions.py)、[generation/tasks.py](generation/tasks.py) | 事务内状态推进、单飞、恢复与关闭 |
| [generation/references.py](generation/references.py)、[generation/catalog.py](generation/catalog.py) | 冻结身份、素材继承与响应装配 |
| [repository.py](repository.py)、[materials.py](materials.py) | 包、动作和当前尝试/已采纳素材 |
| [publishing.py](publishing.py)、[asset_retirement.py](asset_retirement.py) | 目录 CAS 发布、引用退役与补偿 |
| [policy.py](policy.py)、[playback.py](playback.py)、[usage.py](usage.py) | 额度与开关、点播、回执和延迟意图 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) | 任务、目录重建与资产引用贡献 |

## 提案处理

受理与评审使用 `get_action_accept_lock(user_id)`，受理后的提交与任务启动分开；等待锁或模型时不持事务。规范化名称以 NFKC + casefold 确定身份，冻结设计不因同名重提改变。已有可播素材复用，在制不重复评审或占额，待复核返回复核项。独立重做先重查身份、开关、抠像模型并占额。制作额度账本独立于提案和包，删除不返额。

## 评审输入与额度

评审只读所属包冻结参考与实际提供的动作候选，模型复用 ID 必须来自候选集合。重启恢复 pending，deferred 仅在创建窗口内恢复，避免旧提案每次启动付费重审。新付费步骤重查抠像模型与维护状态，已有句柄继续查询下载；未知结果不重新提交。

制作文件按用途拆分，所有阶段仍使用原业务状态机。系统 drag 和左右探身是静态图像流程，不进入视频链；上传必须符合真实 alpha 规格。继承素材时复制并改写嵌套路径，待复核候选留在原动作，不能复制悬空复核状态。

## 采纳与发布

`accepted_asset_json` 保存已采纳版本，制作列仅代表当前尝试；重做失败或待复核期间旧版本继续可播。目录与播放只消费已采纳版本。`metadata_revision` 同时进入目录和播放指令，必需槽位不齐不发布。

每次 CAS 发布使用独立文件，冲突删除落败文件、刷新状态后有限重试，失败不重新制作。`emit_catalog_changed` 是目录事件的唯一发送入口，调用方在发布/激活事务内发送。备份由本模块重建 manifest，不走正常 CAS 制作路径。队列收尾处理新动作唤醒和推迟的旧包退役，空队列停止。

验证归 [Scripts](../../../scripts/README.md)。核对同名并发、幂等来源、重试占额、旧资产保留、冻结评审、CAS、播放回执和引用退役；制作与呈现分别验收。
