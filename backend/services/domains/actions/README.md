# 动作领域

维护按冻结外观包隔离的动作资产、策略、目录与播放事实。跨端契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)，素材链见 [PIPELINE](../../../../docs/PIPELINE.md#动作资产制作)。

## 职责与依赖

| 层 | 职责 |
|---|---|
| `domains/actions` | 资产读写、策略门禁、播放事实、目录发布 |
| `application/actions` | 提案受理、异步评审、播放协调 |
| `generation/video` | 脚本/姿态/视频素材；收尾经 `domains/actions/publishing` 发目录 |

`generation` 不调用 `application/actions`，避免环。目录发布放在 domains，供生成收尾、人工复核采纳（`generation/media_review`）与动作管理 API（启停、删除后重发）共用。

## 模块入口

| 模块 | 职责 |
|---|---|
| [repository.py](repository.py) | pack、action、目录版本与播放意图读写 |
| [policy.py](policy.py) | 受理与评审共用的用户级受理锁，受理门禁（时长、整秒、拒绝后 7 天抑制、自主创建开关），approve 时的制作额度与模型可点播判定 |
| [usage.py](usage.py) | 播放事实：播放指令写出、回执终态与延迟表达意图兑现 |
| [publishing.py](publishing.py) | manifest 结构（`ActionClipSpec` / `ActionCatalogManifest`）、构建、校验、写入用户资产目录与 CAS 版本推进 |

## 资产、发布与表演

资产归属冻结 pack，生成完成不自动表演；目录由 publishing 用 CAS 发布，失败只重试发布，不重新付费。每次发布尝试先写路径唯一的 manifest 文件再 CAS，落败方不会覆盖已发布版本；CAS 落败时删除本次文件，flush 本事务改动后按数据库最新版本与动作行（`populate_existing`）重建重试，有限次后仍冲突才抛 `StaleCatalogError`。动作的 `metadata_revision` 同时作为 manifest 与播放指令中的素材版本。跨端执行、回执与未知结果见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)，历史包处理见 [PIPELINE](../../../../docs/PIPELINE.md#恢复与历史包)。

## 策略约束

模型提案与点播，服务端强制权限、额度和版本。制作额度按来源（user_requested / autonomous）分别计数，以用户本地日（缺时区按 UTC）内的 `approved_at` 结算；额度及无手动播放入口见[动作契约](../../../../docs/PROTOCOL.md#动作目录与播放)。本领域不设使用冷却，策略值与拒绝抑制由 [policy.py](policy.py)维护。

## 验证入口

检查命令见 [Scripts](../../../../scripts/README.md#按改动选择验证)，发布、并发与恢复边界见 [PIPELINE](../../../../docs/PIPELINE.md#目录发布与并发)。
