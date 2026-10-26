# 动作素材

负责独立的动作语义定义与评审，不拥有可播放影像。播放语义见 [动作契约](../../../docs/PROTOCOL.md#动作素材与影像播放)，评审输入与产物要求见 [PIPELINE](../../../docs/PIPELINE.md#动作素材定义与评审)。

## 入口与边界

[service.py](service.py) 管理受理去重、编辑和评审结算；[review.py](review.py) 调用模型，[prompts.py](prompts.py) 定义任务。[queries.py](queries.py) 与 [views.py](views.py) 返回查询投影，[use_cases.py](use_cases.py) 转换请求错误。

定义保存冻结的身份与评审资料，供本次评审和后续制作核对；字段以 [models.py](models.py)、[schemas.py](schemas.py) 为准。列表按 ID 游标读取摘要，筛选在服务端执行，总数按需查询，不为列表加载完整参考资料。

## 评审与恢复

账户动作锁合并同名且内容相同的提交；评审任务按账户串行、同动作单飞。模型调用前核对定义修订并持久化评审中状态，每次调用再次检查执行资格，供应商等待不持事务。

评审结论在同一事务更新动作、关联生活步骤和事件。通过才能产生成功步骤；拒绝或暂缓保留原因并结算失败。已进入评审中的异常、取消和重启中断转为暂缓，等待显式重试，不自动重复付费。

[lifecycle.py](lifecycle.py) 登记恢复与收敛，[backup.py](backup.py) 和 [asset_references.py](asset_references.py) 维护冻结资料及文件引用。修改时按 [Scripts](../../../scripts/README.md) 核对重复受理、并发编辑、缺失参考、模型错误、取消和重启，并用真实输入检查语义评审质量。
