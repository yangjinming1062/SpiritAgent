# 动作素材

拥有动作语义定义与评审状态。全局语义归 [动作素材与影像播放](../../../docs/PROTOCOL.md#动作素材与影像播放)，定义、评审及恢复规格归 [PIPELINE](../../../docs/PIPELINE.md#动作素材定义与评审)。

## 入口与边界

| 入口 | 职责 |
|---|---|
| [models.py](models.py)、[schemas.py](schemas.py)、[views.py](views.py) | 稳定定义、状态、冻结评审依据与对外投影 |
| [service.py](service.py)、[review.py](review.py)、[prompts.py](prompts.py) | 受理去重、编辑与独立语义评审执行 |
| [queries.py](queries.py)、[use_cases.py](use_cases.py) | 只读查询与请求错误转换 |
| [backup.py](backup.py)、[asset_references.py](asset_references.py) | 定义备份与冻结评审参考的资产引用 |

## 评审与恢复

账户内动作锁收敛同名且内容相同的提交，复用已有定义。模型提交前持久记录评审中；恢复由 `lifecycle.py` 登记，冻结评审资料的备份和资产引用分别由表中贡献者维护。

列表按 ID 游标分页，总数按需读取；摘要查询不加载冻结评审资料。桌面、远程和模型检索共用服务端筛选及分页标志。
