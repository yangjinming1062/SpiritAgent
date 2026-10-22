# 窗口动作

拥有窗口动作制作、冻结外观包、采纳、目录与播放账本。每套着装对应唯一动作组，约束定义在 [models.py](models.py)，重做在原动作上保存新尝试与候选。自定义动作语义及评审归[共享目录](../action_library/README.md)，本模块独立制作透明角色版本；桌景版本由桌面生活模块管理。制作归 [PIPELINE](../../../docs/PIPELINE.md#动作资产制作)，目录和回执归 [PROTOCOL](../../../docs/PROTOCOL.md#动作目录与播放)。

## 模块入口

| 入口 | 职责 |
|---|---|
| [design.py](design.py)、[pipeline.py](pipeline.py) | 共享动作设计适配与窗口制作调度 |
| [generation/commands.py](generation/commands.py) | 上传/参考创建、重试、启用与删除 |
| [generation/planning.py](generation/planning.py)、[generation/pipeline.py](generation/pipeline.py) | 静态/运动规划与逐阶段制作 |
| [generation/transactions.py](generation/transactions.py)、[generation/tasks.py](generation/tasks.py) | 事务内状态推进、单飞、恢复与关闭 |
| [generation/references.py](generation/references.py)、[generation/catalog.py](generation/catalog.py)、[video_queue.py](video_queue.py) | 冻结身份、目录收尾、响应装配与视频提交前有效性检查 |
| [repository.py](repository.py)、[materials.py](materials.py) | 包、动作和当前尝试/已采纳素材 |
| [publishing.py](publishing.py)、[asset_retirement.py](asset_retirement.py) | 目录 CAS 发布、引用退役与补偿 |
| [policy.py](policy.py)、[playback.py](playback.py)、[usage.py](usage.py)、[expression_catalog.py](expression_catalog.py) | 创建开关、点播、回执、延迟意图及伙伴动作视图的公开快照 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) | 任务、目录重建与资产引用贡献 |

## 提案处理

自定义动作由共享目录统一受理与评审，受理后的提交与任务启动分开。窗口版本使用稳定 `definition_id`，同组同定义唯一，已有可播素材和在途任务均不重复提交。目标删除记录排除状态，重做保留旧素材；手动待制作目标由显式生成启动。制作前核对明确目标的冻结身份、着装、抠像模型及自主创建开关，无需切换当前穿着。

## 制作与恢复

共享目录保存原始参考来源，本模块继续使用动作组对齐后的冻结参考和规格制作。新付费步骤重查抠像模型与维护状态，恢复边界归 [PIPELINE](../../../docs/PIPELINE.md#恢复与资产回收)。

制作文件按用途拆分，共用逐动作状态机；图片与视频分支见 [系统动作与动态动作](../../../docs/PIPELINE.md#系统动作与动态动作)。姿态图准备完成后，视频提交与轮询交给公共视频队列；动作保存队列尝试引用及可公开的排队进度，重启按同次尝试续跑。实际提交前由本模块复核动作、冻结身份与抠像模型，远端结果保存后确认消费，再进入下载、抠像与评分。

## 采纳与发布

`accepted_asset_json` 保存当前已采纳素材，制作列代表本次尝试；目录与播放只消费已采纳内容。采纳事务与自动穿着守卫归 [媒体检查与激活](../../../docs/PROTOCOL.md#媒体检查与激活)。

备份恢复保留已保存的源片、候选和已采纳资产，清除视频队列引用及远端提交句柄，不启动付费制作；覆盖前的未结算远端占用由独立队列继续收尾。

`emit_catalog_changed` 是目录事件的唯一发送入口，调用方在发布/激活事务内发送；CAS 发布规则归 [目录发布与并发](../../../docs/PIPELINE.md#目录发布与并发)。备份由本模块重建 manifest，不走正常 CAS 制作路径。生成任务按动作登记单飞，同包共享并发预算；任务计数在最后一个任务结束后释放包保护，结果未知的任务仍保留供核对。包收尾等待所有排队与制作动作取得结果，发布前再次核对素材与新入队动作。

验证归 [Scripts](../../../scripts/README.md)。核对同名并发、共享定义绑定、手动首次生成、旧资产保留、非当前目标制作、CAS、播放回执和引用退役；制作与呈现分别验收。
