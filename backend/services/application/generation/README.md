# 生成服务

编排形象、外观、场景和媒体生成，管理任务互斥、提交、落库与激活；供应商传输归 infrastructure。参考来源、产物和跨端恢复见 [PIPELINE](../../../../docs/PIPELINE.md)。

入口：[角色卡分析](character_card.py)、[出镜身份装配](visual_identity.py)、[初始外观](initial_appearance.py)、[形象](avatar_service.py)、[外观](outfit_service.py)、[场景](scene_service.py)、[视频包](video/)、[聊天视频](video_jobs.py)。

## 事务与任务

头像生成、全身生成、选择和确认共用用户级锁。供应商等待期间结束读事务或关闭会话，写入前核对当前身份、状态和源路径，避免迟到结果覆盖其他操作。状态与刷新事件同事务提交；未采纳候选被新候选替换时清理其图片，已采纳的旧全身图保留给冻结的历史任务。

已确认角色的全身重绘与自备图先写入 `FullbodyCandidate`，候选身体分析失败可重试；用户采纳时校验基准全身路径和角色卡修订，同事务更新当前全身图与角色卡身体字段，头像特征保持。旧外观与视频可继续展示，新生成只以新全身图为身体身份依据。

草稿转存失败须可重试；只有所有图片均为过期草稿的头像行才可清理，正式头像或全身参考不能连带删除。读取后脱离 ORM 再签名，避免把临时 URL 写回资产列。

场景后台任务在用户维护期间等待自然收敛，保留已提交的付费结果并完成状态落库；用户显式取消与进程停止仍可取消任务。

角色卡提取任务纳入用户维护及进程启停管理；发布、重试与生成快照约束见 [PIPELINE](../../../../docs/PIPELINE.md#113-角色卡与固定外形)。

默认外观、视频启动标记与启动错误使用 ORM 显式字段，启动标记与首个视频包同事务写入。外观描述任务按用户及外观合并在途请求，停机时取消并等待退出。

衣橱命名读取实际采纳图，先视觉转写再生成名称与描述。失败保留原名且不阻塞资产就绪；回写核对图片路径，避免迟到命名覆盖新图。

## 图像输入与装配

单图与双图参考由 [image_generation.py](image_generation.py) 按供应商原生能力装配；身份图优先于派生文字，双图需要供应商明确支持。参考与角色卡的职责见 [PIPELINE §1.1](../../../../docs/PIPELINE.md#11-共用参考与种子图派生)。外部制作提示词的宽高比与请求尺寸同源。编辑保持条款集中在 prompts，按点位选择；允许修改头像外貌的条款不能用于换装。生成与采纳共用安装入口，统一落库和文件清理。

聊天与夜间出镜图片共用 `visual_identity.py::build_self_image_prompt`。新出镜视频先按视频要求生成起始画面；显式首帧通过身份图与原画面分工校准。首帧生成和校准均走图片质量链，视频恢复沿用已保存的首帧，完整语义及能力限制见 [PIPELINE §1.1](../../../../docs/PIPELINE.md#11-共用参考与种子图派生)。

[scene_service.py](scene_service.py)管理场景任务互斥、切换版本与 outbox 事务；用户参考图与完整造型的 `outfit_description` 在任务创建前校验并冻结。参考装配、描述分析与中断恢复见 [PIPELINE](../../../../docs/PIPELINE.md#11-共用参考与种子图派生)，启用与额度见 [PROTOCOL](../../../../docs/PROTOCOL.md#12-伙伴生命周期方法方法级契约)。[visual_identity.py](visual_identity.py) 统一出镜媒体的造型选择，经 `SelfVisualPlan` 冻结本次最终造型；规则见 [PIPELINE](../../../../docs/PIPELINE.md#11-共用参考与种子图派生)。

## 视频包与验证

上传导入与按参考生成共用交付链；生成上下文与动作结果由 [state.py](video/state.py) 校验。上传包不拥有可重做的冻结参考。素材制作与恢复见 [PIPELINE](../../../../docs/PIPELINE.md#2-动作资产链)。

[media_chain.py](media_chain.py)定义无凭据的供应商快照、游标与候选选择，[character_images.py](character_images.py)编排身份保持图片，[identity_review.py](identity_review.py)只负责评分和严格复核。场景、聊天视频及动作任务持久化进度，姿态图和视频分别推进各自的能力链；原始参考、已选首帧及成功动作复用。动作包独立冻结全身身份图，避免用低分派生图作为评分依据。[media_review.py](media_review.py)管理最终动作疑点复核。选择、保底、未知提交与清理规则统一见 [PIPELINE](../../../../docs/PIPELINE.md#11-共用参考与种子图派生)。

检查命令见 [Backend](../../../README.md#6-验证入口)，生成与恢复验收见 [PIPELINE](../../../../docs/PIPELINE.md#4-按改动选择验证)。
