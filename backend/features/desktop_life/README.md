# 桌面生活

拥有桌面视频组合、生活动作、提案、制作、播放意图与回执。模式与资源隔离归 [PROTOCOL](../../../docs/PROTOCOL.md#桌面生活视频与模式隔离)，构图、双参考和原始视频上传归 PIPELINE。

| 入口 | 职责 |
|---|---|
| [service.py](service.py) | 组合复用、提案、偏好、播放与回执 |
| [generation.py](generation.py)、[prompts.py](prompts.py) | 冻结资料、起始画面、运动、评分、外部提示词与上传 |
| [queries.py](queries.py)、[use_cases.py](use_cases.py)、[tool_use_cases.py](tool_use_cases.py) | 公开快照、HTTP/工具业务操作 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[backup_assets.py](backup_assets.py)、[asset_references.py](asset_references.py) | 恢复、备份及引用贡献 |

状态写入共用账户行锁，进程内单飞只协调任务；模型等待在事务外。当前头像与穿着图用于制作参考，组合另冻结全身身份用于版本校验。只通过外观公开快照读取，不直接操作外观 ORM。

同名动作保持冻结设计，可用成品复用，中断沿原进度继续且不重复计额。当前尝试与已采纳版本分开，失败保留旧画面。外部提示词和原始视频上传不占系统制作额度、不进入人工复核；验证容器、编码和播放约束后原样发布，失败保留原可用版本。

窗口与桌面工具由新鲜呈现状态选择。工具入口负责权限与解析，播放操作与事务归本模块。验证同时检查身份变化、恢复、上传原字节、迟到回执、旧版本可用性，以及真实客户端播放。
