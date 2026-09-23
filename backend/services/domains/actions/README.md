# 动作库（actions）

外观快照隔离的可增长动作库。跨模块契约见 [PROTOCOL §1.10](../../../../docs/PROTOCOL.md#110-动作库与播放契约)、[PIPELINE §2](../../../../docs/PIPELINE.md#2-动作资产链)。

## 分工

| 层 | 职责 |
|---|---|
| `domains/actions` | 资产读写、策略门禁、播放事实、目录发布 |
| `application/actions` | 提案受理、异步评审、播放协调 |
| `generation/video` | 脚本/姿态/视频素材；收尾经 `domains/actions/publishing` 发目录 |

`generation` 不调用 `application/actions`，避免环。目录发布放在 domains，供生成收尾与提案编排共用。

| 模块 | 职责 |
|---|---|
| `repository.py` | pack、action、目录版本与播放意图读写 |
| `policy.py` | 权限、制作额度、近 7 天拒绝抑制与硬门禁 |
| `usage.py` | 播放事实：回执终态与延迟表达意图兑现 |
| `publishing.py` | manifest 构建、校验与 CAS 版本推进 |

## 独有约束

- 一个动作只属于一个冻结 pack，不跨外观复用素材。
- 生成完成只入库，不自动表演；表演事实仅来自可见播放器回执。
- 模型只提案与点播；权限、额度、版本、执行由服务端强制。制作额度按用户本地日、approve 时占用；不设评审限额与使用冷却。
- 未知结果不自动重发；发布失败只重试发布，不重新付费生成。
- 无手动播放入口；用户在对话中提出的需求由模型调用已有动作。

验证入口见 [scripts/README.md](../../../../scripts/README.md)。
