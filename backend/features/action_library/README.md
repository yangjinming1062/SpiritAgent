# 动作素材

负责动作定义、内置目录与语义评审，不拥有可播放影像。分组、编辑与恢复语义见 [动作契约](../../../docs/PROTOCOL.md#动作素材与影像播放)，评审输入与产物要求见 [PIPELINE](../../../docs/PIPELINE.md#动作素材定义与评审)。

## 入口与边界

[service.py](service.py) 管理受理去重、编辑和评审结算；[review.py](review.py) 调用模型，[prompts.py](prompts.py) 定义任务。[queries.py](queries.py) 与 [views.py](views.py) 返回查询投影，[use_cases.py](use_cases.py) 转换请求错误。

[presets.py](presets.py) 维护通用内置定义，持久字段与请求视图以 [models.py](models.py)、[schemas.py](schemas.py) 为准。列表按 ID 游标读取摘要，总数按需查询，不为列表加载完整冻结参考。

## 评审与恢复

账户动作锁合并同名且内容相同的自创提交；评审任务按账户串行、同修订单飞。先认领并提交评审中状态，再读取参考和配置；每次付费调用前核对执行资格和修订，结算只接受当前评审修订。任务按修订登记，避免新修订被正在收尾的任务挡住；供应商等待不持事务。

模型结论保存评审依据，技术异常保存可重试状态并记录诊断日志。动作、关联生活步骤和事件在同一事务结算；通过才能产生成功步骤，已失败的步骤不因后续重评成功被改写。认领提交回滚后按受理修订收敛等待状态，停机收敛同时覆盖尚未开始执行的任务。

[lifecycle.py](lifecycle.py) 登记恢复与收敛，[backup.py](backup.py) 和 [asset_references.py](asset_references.py) 维护冻结资料及文件引用。修改时按 [Scripts](../../../scripts/README.md) 核对重复受理、并发编辑、缺失参考、模型错误、取消和重启，并用真实输入检查语义评审质量。
