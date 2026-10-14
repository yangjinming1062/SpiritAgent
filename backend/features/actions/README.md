# 窗口动作

拥有窗口动作提案、制作、冻结外观包、采纳、目录与播放账本。制作归 [PIPELINE](../../../docs/PIPELINE.md#动作资产制作)，额度、目录和回执归 [PROTOCOL](../../../docs/PROTOCOL.md#动作目录与播放)。桌面生活视频另由独立模块管理。

## 模块入口

| 入口 | 职责 |
|---|---|
| [design.py](design.py)、[review.py](review.py)、[pipeline.py](pipeline.py) | 受理、去重、独立评审、批准与恢复 |
| [generation/commands.py](generation/commands.py) | 上传/参考创建、重试、启用与删除 |
| [generation/planning.py](generation/planning.py)、[generation/pipeline.py](generation/pipeline.py) | 静态/运动规划与逐阶段制作 |
| [generation/transactions.py](generation/transactions.py)、[generation/tasks.py](generation/tasks.py) | 事务内状态推进、单飞、恢复与关闭 |
| [generation/references.py](generation/references.py)、[generation/catalog.py](generation/catalog.py) | 冻结身份、目录收尾与响应装配 |
| [repository.py](repository.py)、[materials.py](materials.py) | 包、动作和当前尝试/已采纳素材 |
| [publishing.py](publishing.py)、[asset_retirement.py](asset_retirement.py) | 目录 CAS 发布、引用退役与补偿 |
| [policy.py](policy.py)、[playback.py](playback.py)、[usage.py](usage.py) | 额度与开关、点播、回执和延迟意图 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) | 任务、目录重建与资产引用贡献 |

## 提案处理

受理与评审使用 `get_action_accept_lock(user_id)`，受理后的提交与任务启动分开；等待锁或模型时不持事务。规范化名称以 NFKC + casefold 确定身份，冻结设计不因同名重提改变。已有可播素材复用，在制不重复评审或占额，待复核返回复核项。独立重做先重查身份、开关、抠像模型并占额。制作额度账本独立于提案和包，删除不返额。

## 评审输入与额度

评审只读所属包冻结参考与实际提供的动作候选，模型复用 ID 必须来自候选集合。重启恢复 pending，deferred 仅在创建窗口内恢复，避免旧提案每次启动付费重审。新付费步骤重查抠像模型与维护状态，恢复边界归 [PIPELINE](../../../docs/PIPELINE.md#恢复与资产回收)。

制作文件按用途拆分，共用逐动作状态机；图片与视频分支见 [系统动作与动态动作](../../../docs/PIPELINE.md#系统动作与动态动作)。

## 采纳与发布

`accepted_asset_json` 保存当前已采纳素材，制作列代表本次尝试；目录与播放只消费已采纳内容。采纳事务与自动穿着守卫归 [媒体复核与激活](../../../docs/PROTOCOL.md#媒体复核与激活)。

`emit_catalog_changed` 是目录事件的唯一发送入口，调用方在发布/激活事务内发送；CAS 发布规则归 [目录发布与并发](../../../docs/PIPELINE.md#目录发布与并发)。备份由本模块重建 manifest，不走正常 CAS 制作路径。生成任务按动作登记单飞，同包共享并发预算；任务计数在最后一个任务结束后释放包保护，结果未知的任务仍保留供核对。包收尾等待所有排队与制作动作取得结果，发布前再次核对素材与新入队动作。

验证归 [Scripts](../../../scripts/README.md)。核对同名并发、幂等来源、重试占额、旧资产保留、冻结评审、CAS、播放回执和引用退役；制作与呈现分别验收。
