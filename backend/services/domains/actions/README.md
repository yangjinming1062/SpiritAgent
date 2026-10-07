# 动作领域

维护冻结外观包内的动作、策略、目录和播放事实。跨端定义归 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)，制作及历史包恢复归 PIPELINE。

## 职责与依赖

`application/actions` 组织提案、评审与播放，`generation/video` 制作素材，`domains/actions` 管资产和目录。generation 收尾只调用领域发布入口，不调用动作应用层。发布供生成、人工采纳、启停及删除共用；备份恢复直接调用 `build_catalog_manifest` 重建文件，不走 CAS 发布。

## 模块入口

| 入口 | 职责 |
|---|---|
| [repository.py](repository.py) | 包、动作、目录版本和意图存储；同包同 key 唯一，`clear_action_attempt` 仅作废当前尝试 |
| [materials.py](materials.py) | 图片/视频分型的已采纳快照与 `ActionResult` 解析，重做期间保留播放版本 |
| [policy.py](policy.py) | 用户受理锁、时长与拒绝抑制、开关、制作占额、可点播判定 |
| [usage.py](usage.py) | 播放指令、回执终态、延迟意图兑现及面向模型的动作条目 |
| [publishing.py](publishing.py) | `ActionCatalogManifest` 构建、校验、落盘和 CAS；`emit_catalog_changed` 是目录变更事件的唯一发送入口 |
| [asset_retirement.py](asset_retirement.py) | 引用释放的事务内登记、宽限后复核与失败重试 |

## 发布与策略约束

目录消费已采纳素材，不读正在制作的候选；动作语义供模型使用，不写入 manifest。必需槽位不齐不发布。每次 CAS 尝试使用独立文件，冲突删除落败文件、刷新包和动作后有限重试，失败不重新制作；调用方在发布或激活事务中发目录事件。

素材的 `metadata_revision` 同时进入目录和播放指令，不能用本次失败尝试替换已采纳版本。制作额度在批准或独立重做受理时事务内占用，账本按来源独立于提案、动作与包，删除不返额；领域不另设播放冷却。

## 验证入口

命令归 [Scripts](../../../../scripts/README.md)。核对已采纳版本、唯一约束、CAS 冲突、事件事务、播放回执和引用退役；成品制作按 PIPELINE 验收。
