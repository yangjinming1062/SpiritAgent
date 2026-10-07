# 生成服务

编排形象、衣柜、场景、动作素材及聊天/动态媒体，管理任务、提交、采纳与激活。参考优先级、供应商能力、产物及恢复的完整定义归 [PIPELINE](../../../../docs/PIPELINE.md)，跨端状态归 PROTOCOL；本文列代码入口和本模块内部约束。

## 关键入口

| 入口 | 职责 |
|---|---|
| [appearance_prompts.py](appearance_prompts.py)、[fullbody_reference_prompt.py](fullbody_reference_prompt.py) | 头像、身体结构、服装参考和全身提示词装配 |
| [avatar_service.py](avatar_service.py) | 头像、全身草稿/候选、确认身份和默认外观；角色卡特征提取及审核改写 |
| [character_card.py](character_card.py)、[initial_appearance.py](initial_appearance.py) | 分部角色卡提取与初始动作包/场景衔接、重启恢复 |
| [outfit_service.py](outfit_service.py) | 外观草稿、编辑、自备图、确认、穿着、删除、命名与描述补全 |
| [scene_service.py](scene_service.py)、[scene_prompt.py](scene_prompt.py)、[scene_image_review.py](scene_image_review.py) | 场景任务、环境提示词、重复伙伴检查、描述和原位重生成 |
| [visual_identity.py](visual_identity.py)、[image_generation.py](image_generation.py) | 出镜身份与造型的共享装配、图像能力筛选与生成出口 |
| [chat_images.py](chat_images.py) | 聊天图片批次、验图、版本与一次重做预算 |
| [video_jobs.py](video_jobs.py) | 聊天与动态视频任务，句柄轮询、交付和原任务核查 |
| [video/service.py](video/service.py)、[state.py](video/state.py)、[script.py](video/script.py) | 动作包与单动作制作、冻结上下文、静态描述/视频脚本、探身定位 |
| [media_chain.py](media_chain.py)、[character_images.py](character_images.py)、[identity_review.py](identity_review.py) | 无凭据链快照、质量进度、候选与身份评分 |
| [media_review.py](media_review.py) | 成品复核、采纳和拒绝；采纳动作时事务内发布目录 |
| [response_builders.py](response_builders.py) | 头像和外观响应装配 |

## 事务与任务所有权

形象、衣柜、角色卡和动作包共用 `avatar_service.get_avatar_job_lock` 用户锁；场景使用独立场景锁。角色卡编辑另用数据库行锁与预期修订。头像生成、重绘、自备图采纳及提示词入口在服务锁内重读 persona，API 锁外检查只作快速拒绝。

任务冻结资料后在事务外调用供应商，写回重核任务、身份、源路径和状态，业务更新与事件同事务提交。正式路径存裸路径，响应出口签名；落盘前取得外观、包或场景 ID，不能在等待供应商期间保留空草稿事务。随机临时草稿和任务固定路径有各自取消清理语义，不混用。

### 全身候选与草稿

确认身份前直接更新草稿；`confirm_fullbody_seed` 同事务锁定身份、登记角色卡和默认外观。确认后结果保存为 `FullbodyCandidate`，分析与采纳分开，采纳重核原图和角色卡修订；未采纳候选可回收，历史任务仍持有已采纳参考。

- 全身与换装共用 `prepare_transparent_image`，确认或采纳前再次校验；候选转存失败可重试，只清理全部图片均过期的草稿行，不连带正式参考。
- 全身候选和角色卡共用 `extract_card_features`；头像、全身和换装共用 `generate_with_moderation_retry`，不在不同入口另写审核策略。
- `CompanionOutfit.source_json` 只经 [OutfitSource](../../../modules/companion/models.py) 读写，保存生成来源、接受反馈及身份守卫；异常字段按缺省处理，未知旧键保留。动作包从其身份参考路径判断是否需校准。

### 后台任务与初始资产

维护期间场景、角色卡和动作任务收敛已提交制作，保存源结果，暂停后继付费调用；上传导入没有付费句柄，维护、重启或停机中断后按失败处理。任务恢复由 `resume_user_dynamic_actions`、`resume_user_scene_jobs`、`resume_user_character_extraction` 重读状态，删除用户后不恢复。

视觉业务守卫传至 `vision_chat`，每次实际调用及供应商回退前重查。维护、撤权和身份失效不能降级成评分不可用；IM 视频沿原对端授权守卫收敛。

初始默认外观的启动标记与首动作包同事务写入，删除任务不撤销已启动事实。角色卡就绪后启动初始场景，重启由 `resume_initial_appearance` 分别补查首包和场景。

外观描述按用户/外观合并在途任务，以实际采纳图片命名，写回核对路径；失败或重启中断保留原名、已完成描述和诊断，由衣柜手动补全，不隐式重复付费。无描述的新包仅在首次模型调用前有界等待已有描述任务，然后原子冻结快照；超时或失败按参考图继续，既有包不回填后来描述。

## 场景改动链

创建、描述分析、图片重生成和启用分别管理。状态、尺寸、原位替换与恢复语义归 PROTOCOL / PIPELINE，改动沿下表核对：

| 环节 | 入口 | 联动 |
|---|---|---|
| 用户操作 | [scene-page.tsx](../../../../client/renderer/app/features/living/scene-page.tsx)、[scene-detail-view.tsx](../../../../client/renderer/app/features/living/scene-detail-view.tsx) | 保存描述、重生成、取消和旧图呈现 |
| 状态与背景 | [scene-store.ts](../../../../client/renderer/modules/scene/scene-store.ts)、[scene-backdrop.tsx](../../../../client/renderer/app/features/living/scene-backdrop.tsx) | POST 后水合、版本与账户守卫 |
| API 与数据 | [companion_scenes.py](../../../api/v1/companion_scenes.py)、[schemas_scene.py](../../../modules/companion/schemas_scene.py)、[scene.py](../../../modules/companion/scene.py) | 创建/重生成任务区分与响应状态 |
| 执行 | `scene_service.regenerate_scene`、`_run_scene_regeneration`、`resume_scene_jobs` | 单任务互斥、冻结尺寸和原位提交 |
| 模型链 | `scene_prompt`、`character_images`、`scene_image_review` | 环境参考、尺寸适配、独立重复伙伴检查 |

## 图像输入与装配

`resolve_image_gen_chain` 按编辑、多参考及提示词长度过滤，不在公共筛选器按透明能力过滤；透明优先由具体质量链处理。`generate_images` 按 `persist_user_assets` 返回正式资产、原生 URL 或 data URI，能力位由供应商基类声明。

聊天与动态共用 `SelfVisualPlan` 和出镜装配。图片进度的 `storage_directory`、视频任务目录在受理时冻结，恢复不按当前日期重选。无 `save_progress` 的同步调用只对产物下载作有界重试，不重新提交生成，最终失败回收已落盘文件；调用方须转换公开错误，避免泄露异常原文或误报结果未知。

## 动作包与媒体质量链

聊天工具共享 `MediaTurnState`，视频终态由 conversation 领域原位更新结构化气泡；其他会话追加媒体状态消息。无目标会话的视频只交给动态发布，不执行聊天交付。视频固定文件名包含冻结生成 ID，避免恢复与旧文件重名。

- `create_pack_from_clips` 和 `create_pack_from_reference` 按图片/视频分型处理，共用目录发布；任务保存 `status × stage`，图片处理和 FFmpeg 在线程中执行。上传包没有可重做的冻结参考。
- 动作已采纳版本存于 `accepted_asset_json`，制作列只代表当前尝试。重做、失败或待复核不撤销旧已采纳素材；目录与播放只消费已采纳版本。反馈每次替换本动作字段，空串清除。
- 跨包继承复制素材、冻结参考和恢复资源并重写嵌套路径；待复核候选仍属于原动作，不复制悬空复核状态。没有可继承素材时明确失败。确定性门禁拒绝使用 `invalid_asset`，不能对同一坏素材反复继续。
- 系统 drag 和左右探身独立整理静态描述，不选择视频链，结果不填视频时长、帧数或循环字段。探身定位由 `script.py` 执行；图像与视频交付规格统一归 PIPELINE。
- 新付费步骤重查 `require_action_matting_model`，已有句柄继续查询下载，源素材保留供恢复。队列收尾须兑现新动作唤醒和推迟的旧包退役；空队列停止，不循环恢复未知结果。

正式文件引用与回收使用 [assets 公共入口](../../domains/assets/README.md)。`media_chain` 保存候选、游标与选择，`identity_review` 做评分，`media_review` 做人工采纳；失败文案统一经 `video_failure_message`，供应商原文不进入用户提示。

## 验证入口

命令归 [Scripts](../../../../scripts/README.md)，参考、真实 alpha、身份、候选采纳、并发与恢复按 PIPELINE 的完整链验收。文档变更只核查事实与链接，不重新付费生成。
