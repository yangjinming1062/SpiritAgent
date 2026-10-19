# 外观

拥有头像、全身身份、角色卡、衣柜及其生成、上传、采纳和穿着状态。参考与产物归 [PIPELINE](../../../docs/PIPELINE.md#身份造型与参考输入)，跨端状态归 PROTOCOL；公共媒体制作、场景和动作分别归所属模块。

## 关键入口

| 入口 | 职责 |
|---|---|
| [avatar_service.py](avatar_service.py) | 头像、全身草稿/候选、身份确认与默认外观 |
| [character_card.py](character_card.py)、[character_extraction.py](character_extraction.py) | 角色卡读写、修订守卫、分部特征提取与恢复 |
| [outfit_service.py](outfit_service.py) | 外观草稿、生成、自备图、确认、穿着、删除与描述补全 |
| [prompt_assembly.py](prompt_assembly.py)、[visual_identity.py](visual_identity.py) | 外观指令和聊天/动态出镜身份与参考装配 |
| [queries.py](queries.py)、[views.py](views.py) | 跨模块冻结身份与外观快照、对外响应 |
| [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) | 任务登记、模块备份与资产引用贡献 |
| [初始资产流程](../../workflows/onboarding/initial_appearance.py) | 角色卡就绪后协调首动作包与初始场景 |

## 制作与采纳

形象、衣柜、角色卡和窗口动作共用 `get_avatar_job_lock(user_id)`；角色卡编辑另用行锁和预期修订。冻结资料后在事务外调用供应商，写回重核身份、源路径和状态，更新与事件同事务提交。正式路径存裸路径，响应出口签名。

确认身份前直接更新草稿，`confirm_fullbody_seed` 原子登记身份、角色卡与默认外观；确认后的生成结果保存为候选，分析和采纳分离。采纳重核原图和修订，失败保留可重试源图，未采纳候选与历史已采纳参考分别回收。全身与换装共用透明图片处理，不能仅凭文件后缀接受透明产物。

`OutfitSource` 是衣柜来源 JSON 的唯一解析结构，读取与备份恢复均校验字段和类型。媒体按[全身来源与参考职责](../../../docs/PIPELINE.md#固定身份与可变造型)冻结身份、身体和造型分工，不读取本次未使用的默认着装。命名与描述按账户/外观单飞，以实际采纳图片为依据，写回核对路径；失败或重启中断保留原名、已完成描述和诊断。动作包只在首次模型调用前等待描述结论，资料冻结后保持不变。

衣柜内建制作与外部提示词共用设计准备，外部交付补充画幅与透明背景说明。草稿换图在各自分支核对原图与身份后，共用提交与回收登记；AI 写回使用生成前冻结的来源，上传替换在锁内重读来源。

## 后台任务与初始资产

确认入柜和首次引导共用 [基础动作流程](../../workflows/outfit_actions.py)。启动标记与首包同事务写入，删除首包不撤销；重启与用户维护结束只补接未受理且无明确失败的着装，已有任务不重置。启动失败保留诊断，备份恢复不自动补建，均由衣柜显式处理。

维护期间收敛已提交结果并落盘，暂停后续付费步骤；上传导入没有供应商句柄，中断按失败处理。就绪通知和衣柜动作清理通过 bootstrap 装配的明确端口完成，不反向导入跨业务流程。

验证入口归 [Scripts](../../../scripts/README.md)。核对候选采纳、身份变化、并发修订、描述单飞、维护和重启；真实透明度、模型输入与客户端呈现分别验收。
