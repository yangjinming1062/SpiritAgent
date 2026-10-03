# 动作领域

维护按冻结外观包隔离的动作资产、策略、目录与播放事实。跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)，素材链见 [PIPELINE](../../../../docs/PIPELINE.md#动作资产制作)。

## 职责与依赖

| 层 | 职责 |
|---|---|
| `domains/actions` | 资产读写、策略门禁、播放事实、目录发布 |
| `application/actions` | 提案受理、异步评审、播放协调 |
| `generation/video` | 脚本/姿态/视频素材；收尾经 `domains/actions/publishing` 发目录 |

`generation` 不调用 `application/actions`，避免环。目录发布放在 domains，供生成收尾、人工复核采纳（`generation/media_review`）与动作管理 API（启停、删除后重发）共用；备份恢复只调用 `build_catalog_manifest` 重建 manifest，不经 CAS 发布。

## 模块入口

| 模块 | 职责 |
|---|---|
| [repository.py](repository.py) | pack、action、目录版本与播放意图读写；`create_action` 只新建动作行（同包同 key 由唯一约束拒绝），`clear_action_attempt` 作废当前生成尝试，供原位重做与用户拒绝复核共用 |
| [materials.py](materials.py) | 已采纳素材快照与当前制作尝试隔离，目录与播放在重做、失败及待复核时继续读取已采纳版本 |
| [asset_retirement.py](asset_retirement.py) | 引用释放同事务登记，宽限期后复核包、动作与复核项引用；清理失败保留登记供重试 |
| [policy.py](policy.py) | 受理与评审共用的用户级受理锁，受理门禁（时长、整秒、拒绝后 7 天抑制、自主创建开关），approve 时的制作额度与模型可点播判定 |
| [usage.py](usage.py) | 播放事实：播放指令写出、回执终态与延迟表达意图兑现；`action_to_dict` 给出动作元信息，`ACTION_PROMPT_KEYS` 与 `action_prompt_entry` 取面向模型的动作条目，动作快照与伙伴提示词上下文共用 |
| [publishing.py](publishing.py) | manifest 结构（`ActionClipSpec` / `ActionCatalogManifest`）、构建、校验、写入用户资产目录与 CAS 版本推进；`emit_catalog_changed` 是 `companion.action.catalog_changed` 事件的唯一发送入口，目录发布或包激活后由调用方在同一事务调用 |

## 资产、发布与表演

资产归属冻结 pack，生成完成不自动表演；目录由 `publishing` 以 CAS 发布，冲突只重试发布，不重新付费。每次尝试使用独立 manifest，失败文件清理且不会覆盖已发布版本；冲突时刷新数据库状态并有限次重试，仍冲突才抛 `StaleCatalogError`。动作的 `metadata_revision` 同时作为 manifest 和播放指令中的素材版本。跨端执行、回执与未知结果见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)，历史包处理见 [PIPELINE](../../../../docs/PIPELINE.md#恢复与历史包)。

## 策略约束

模型提案与点播由服务端强制权限、额度和版本。评审不设日限额，批准新制作或受理同名独立重做时在事务内占用制作额度；独立账本按来源（`user_requested` / `autonomous`）和滚动 24 小时结算，删除动作或包不返额。额度及无手动播放入口见 [动作契约](../../../../docs/PROTOCOL.md#动作目录与播放)；本领域不设使用冷却，策略值与拒绝抑制由 [policy.py](policy.py)维护。

## 验证入口

检查命令见 [Scripts](../../../../scripts/README.md#按改动选择验证)，发布、并发与恢复边界见 [PIPELINE](../../../../docs/PIPELINE.md#目录发布与并发)。
