# 生成服务

编排形象、外观、场景和媒体生成，管理任务互斥、提交、落库与激活；供应商传输归 infrastructure。输入语义、产物和跨端恢复见 [PIPELINE](../../../../docs/PIPELINE.md)。

## 关键入口

| 入口 | 职责 |
|---|---|
| [avatar_service.py](avatar_service.py) / [fullbody_reference_prompt.py](fullbody_reference_prompt.py) | 头像、全身草稿与候选、全身确认（锁定身份并建默认外观）、立绘裸路径读写与响应签名；全身参考提示词与自备图画幅 |
| [character_card.py](character_card.py) | 角色卡分析任务 |
| [initial_appearance.py](initial_appearance.py) | 首个视频包启动（含补排默认外观描述）；首个视频包与初始场景的重启恢复（初始场景由角色卡就绪后经 `scene_service.schedule_initial_scene` 启动） |
| [visual_identity.py](visual_identity.py) | 出镜身份与本次造型（`SelfVisualPlan`），共用 `build_self_image_prompt` |
| [outfit_service.py](outfit_service.py) | 衣柜外观草稿、重绘、自备图、确认、穿着、删除、替换策略与后台命名 |
| [image_generation.py](image_generation.py) / [scene_prompt.py](scene_prompt.py) | 图像参考装配与场景提示词装配 |
| [chat_images.py](chat_images.py) | 聊天图片批次登记、实际验图、版本与一次重做预算；工具入口见 [image_generation_tool.py](../../adapters/tools/builtin/image_generation_tool.py) |
| [scene_service.py](scene_service.py) | 场景创建、描述分析、图片重生成与切换版本 |
| [video/](video/) / [video_jobs.py](video_jobs.py) | 视频包（script、state、service）与聊天、动态视频任务；供应商轮询 `poll_video_task` 两者共用，聊天工具入口见 [video_generation_tool.py](../../adapters/tools/builtin/video_generation_tool.py) |
| [media_chain.py](media_chain.py) / [character_images.py](character_images.py) / [identity_review.py](identity_review.py) | 供应商链择优、身份保持图片、评分与严格复核 |
| [media_review.py](media_review.py) | 用户复核项的创建、查询、采纳与拒绝；采纳动作且所属包仍激活时同事务发布目录，拒绝时作废该次生成尝试；同一素材只复用仍待确认的复核项 |
| [response_builders.py](response_builders.py) | 头像/外观响应装配 |

## 事务与任务所有权

所有形象、衣柜、角色卡和视频包任务共用 `avatar_service.get_avatar_job_lock` 用户级锁；角色卡编辑使用数据库行锁与预期修订，场景另用 `scene_service` 的场景锁。任务按“冻结资料 → 事务外等待供应商 → 校验身份、状态和源路径 → 状态和事件同事务提交”执行，迟到结果不得覆盖新任务，候选与正式资产分开清理。

已保存场景和已就绪视频包启用时只核对身份图：场景比对头像记录的全身图路径，视频包比对冻结全身身份图与当前全身图字节；在途任务的自动启用还要核对角色卡修订。角色卡文字修订不阻止已保存资产启用，完整规则见 [PIPELINE](../../../../docs/PIPELINE.md#角色卡与并发写入)。

### 全身候选与草稿

- 身份确认前，全身生成和自备图直接替换草稿；`confirm_fullbody_seed` 锁定身份、登记角色卡并保存默认外观快照。确认后先写 `FullbodyCandidate`，分析可重试，采纳时校验原图与角色卡修订并同事务更新；未采纳候选可清理，已采纳旧图留给历史任务。完整身份语义见 [PIPELINE](../../../../docs/PIPELINE.md#全身候选采纳)。
- 草稿转存失败可重试，只有全部图片过期的头像行才清理，不连带正式参考。服务内部和 ORM 使用裸路径（草稿为 `temp-media/`，正式资产为 `companion-assets/{user_id}/`），URL 只在响应出口签名，客户端地址只在确认入口还原比对。
- 角色卡和全身候选共用 `extract_card_features`；头像、全身和换装共用 `generate_with_moderation_retry`，审核命中只改写提示词重试一次，结果未知不重试。

### 后台任务与初始资产

场景任务在用户维护期间自然收敛并保存已付费结果，显式取消和进程停止仍可取消；角色卡任务与视频包上传导入纳入维护与启停，导入中断即按失败落库，只能重新提交。

默认外观、启动标记与错误用 ORM 字段，标记与首视频包同事务写入。外观描述按用户/外观合并在途任务，停机等待退出；命名先读取实际采纳图再转写，失败保留原名、不阻塞就绪，回写核对路径。

## 场景改动链

创建、描述分析、图片重生成和启用是不同操作，先读[任务与原位换图](../../../../docs/PROTOCOL.md#场景任务与原位换图)、[输入冻结](../../../../docs/PIPELINE.md#场景图片重绘)。

| 环节 | 定位入口 | 修改时联动 |
|---|---|---|
| 用户操作 | [scene-page.tsx](../../../../client/renderer/app/windows/living/scene-page.tsx)、[scene-detail-view.tsx](../../../../client/renderer/app/windows/living/scene-detail-view.tsx) | 保存描述、重生成、取消与旧图呈现 |
| 状态与事件 | [scene-store.ts](../../../../client/renderer/modules/scene/scene-store.ts) 的 `regenerateScene` / `onSceneEvent` | POST 后水合、版本与换号守卫；当前背景见 [scene-backdrop.tsx](../../../../client/renderer/app/windows/living/scene-backdrop.tsx) |
| API 与结构 | [companion_scenes.py](../../../api/v1/companion_scenes.py) 的 `post_scene_regenerate`、[schemas_scene.py](../../../modules/companion/schemas_scene.py) | 响应状态、创建任务与重生成任务的区分 |
| 执行与持久化 | [scene_service.py](scene_service.py) 的 `regenerate_scene` / `_run_scene_regeneration` / `resume_scene_jobs`，模型 [scene.py](../../../modules/companion/scene.py) | 单任务互斥、身份快照、原位提交与恢复 |
| 提示词与供应商 | [scene_prompt.py](scene_prompt.py)、[character_images.py](character_images.py) | 创建和重生成输入不同；质量门禁与未知提交不重发 |

验收至少核对：成功原位换图而不改变启用关系；取消/失败保留旧图；身份变化拒绝晚到产物；重启复用冻结输入；换号后旧响应失效。

## 图像输入与装配

[image_generation.py](image_generation.py)的 `resolve_image_gen_chain` 按参考图、编辑和透明能力筛选供应商链，`generate_images` 按 `persist_user_assets` 决定返回用户资产、原生 URL 或 data URI；能力位由[供应商基类](../../infrastructure/llm/providers/base.py)声明。提示词按点位选择，头像条款不能直接用于换装；参考优先级与编辑前置条件归 [PIPELINE](../../../../docs/PIPELINE.md#身份造型与参考输入)。

聊天与动态图片共用 [visual_identity.py](visual_identity.py) 的 `build_self_image_prompt`，`SelfVisualPlan` 冻结造型。视频首帧生成/校准也走图片质量链，恢复沿用已保存首帧，具体规则见 [出镜图片与视频](../../../../docs/PIPELINE.md#出镜图片与视频首帧)。

## 视频包与质量链

聊天媒体预算由 `MediaTurnState` 跨工具调用共享；验图重做与交付语义见 [媒体协议](../../../../docs/PROTOCOL.md#媒体引用验图与原位交付)。结构化回复（生活空间）的聊天视频终态经 `domains/conversation` 的 `update_video_reply` 原位更新所属气泡；其他会话追加媒体状态系统消息，并发 `video_gen.*` 事件与渠道投递。没有目标会话的视频任务只保存结果，由[动态应用](../posts/README.md)接收，不执行聊天交付。

上传导入（`create_pack_from_clips`）与按参考生成（`create_pack_from_reference`）共用片段处理和发布；[video/state.py](video/state.py)保存上下文与单动作结果，上传包没有可重做的冻结参考。任务按 `status × stage` 持久化，FFmpeg 在工作线程执行，新包失败不清空旧激活包；供应商句柄提交后立即落库，重启只续轮询，不重复付费提交。

探身补齐由 [video/service.py](video/service.py)编排，定位校准在 [video/script.py](video/script.py)，接口契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。生成任务收尾须兑现新排队动作的唤醒；空队列停止，不循环恢复未知结果任务。

- 每个动作素材都要抠像。整包、就绪包重做、失败包续跑和探身补齐在请求时检查，动态动作在提案受理时检查；后台制作在每个新的付费步骤前再查 `require_action_matting_model`。缺失时在付费前失败并保留进度，模型恢复后沿用同名重做或失败包续跑；已有可用候选时不再追加提交，按最佳候选收尾。已有供应商句柄只续查与下载，抠像失败保留源视频，结果未知不重发。

姿态图的透明输出、留白准备与视频透明化边界见 [视频与交付](../../../../docs/PIPELINE.md#视频与交付)。

[media_chain.py](media_chain.py)保存无凭据供应商快照、游标和候选；`character_images.py`、`identity_review.py`、`media_review.py`分别负责身份图链、评分和人工复核。图片、视频进度分别持久化并可复用原始参考、已选首帧和成功动作；动作包独立冻结全身身份图。恢复与清理见 [恢复规则](../../../../docs/PIPELINE.md#持久化与恢复)。

## 验证入口

检查命令见 [Backend](../../../README.md#契约与验证)，生成与恢复验收见 [PIPELINE](../../../../docs/PIPELINE.md#验证)。
