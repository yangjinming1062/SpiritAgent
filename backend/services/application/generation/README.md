# 生成服务

编排形象、外观、场景和媒体生成，管理任务互斥、提交、落库与激活；供应商传输归 infrastructure。输入语义、产物和跨端恢复见 [PIPELINE](../../../../docs/PIPELINE.md)。

## 关键入口

| 入口 | 职责 |
|---|---|
| [appearance_prompts.py](appearance_prompts.py) | 头像、衣柜、身体结构与服装参考提示词装配；Persona 读取由公共模型辅助完成，文本与视觉调用走 LLM 公共入口，显式输出预算与未完成响应按失败处理 |
| [avatar_service.py](avatar_service.py) / [fullbody_reference_prompt.py](fullbody_reference_prompt.py) | 头像、全身草稿与候选、全身确认（锁定身份并建默认外观）、立绘裸路径读写与响应签名；全身参考提示词与自备图画幅 |
| [character_card.py](character_card.py) | 角色卡分析任务 |
| [initial_appearance.py](initial_appearance.py) | 首个动作包启动（含补排默认外观描述）；首个动作包与初始场景的重启恢复（初始场景由角色卡就绪后经 `scene_service.schedule_initial_scene` 启动） |
| [visual_identity.py](visual_identity.py) | 出镜身份与本次造型（`SelfVisualPlan`），共用 `build_self_image_prompt` 与 `build_self_video_prompt` |
| [outfit_service.py](outfit_service.py) | 衣柜外观草稿、重绘、自备图、确认、穿着、删除（同事务删除已停稳的关联动作包）、替换策略与后台命名；描述失败经 `description/retry` 手动补全 |
| [image_generation.py](image_generation.py) / [scene_prompt.py](scene_prompt.py) | 图像参考装配与环境壁纸提示词装配 |
| [chat_images.py](chat_images.py) | 聊天图片批次登记、实际验图、版本与一次重做预算；工具入口见 [image_generation_tool.py](../../adapters/tools/builtin/image_generation_tool.py) |
| [scene_service.py](scene_service.py) / [scene_image_review.py](scene_image_review.py) | 场景创建、描述、重生成与切换版本；伙伴参考仅用于独立检查 |
| [video/](video/) / [video_jobs.py](video_jobs.py) | 图片与视频动作包（script、state、service）及聊天、动态视频任务；视频供应商轮询 `poll_video_task` 共用，聊天工具入口见 [video_generation_tool.py](../../adapters/tools/builtin/video_generation_tool.py) |
| [media_chain.py](media_chain.py) / [character_images.py](character_images.py) / [identity_review.py](identity_review.py) | 供应商链与进度、身份保持图片及环境壁纸、身份评分与严格复核 |
| [media_review.py](media_review.py) | 用户复核项的创建、查询、采纳与拒绝；所属动作以 `MediaReviewPublication` 落库，复核项只作用于生成它的成品（失效语义见 [PROTOCOL](../../../../docs/PROTOCOL.md#媒体复核与激活)），原位重做同事务结束动作仍待确认的复核项；采纳动作时同事务发布所属包目录，包仍激活时广播目录变更并兑现有效表达意图，拒绝时作废该次生成尝试；同一素材只复用仍待确认的复核项 |
| [response_builders.py](response_builders.py) | 头像/外观响应装配 |

## 事务与任务所有权

所有形象、衣柜、角色卡和动作包任务共用 `avatar_service.get_avatar_job_lock` 用户级锁；角色卡编辑使用数据库行锁与预期修订，场景另用 `scene_service` 的场景锁。任务按“冻结资料 → 事务外等待供应商 → 校验身份、状态和源路径 → 状态和事件同事务提交”执行，迟到结果不得覆盖新任务，候选与正式资产分开清理。头像生成、基于图片重绘、自备图采纳与提示词入口由服务重读 persona 校验引导完成和身份锁定，不接受调用方在锁外读到的快照；API 的锁外检查只作快速拒绝。

已就绪动作包启用时比对冻结全身身份图与当前全身图字节，在途任务自动启用还要核对角色卡修订；角色卡文字修订不阻止已保存动作包启用。场景壁纸不绑定角色身份，按账户、任务与切换版本守卫提交。完整规则见 [PIPELINE](../../../../docs/PIPELINE.md#角色卡与并发写入) 与 [场景契约](../../../../docs/PROTOCOL.md#场景启用与授权)。

### 全身候选与草稿

- 身份确认前，全身生成和自备图直接替换草稿；`confirm_fullbody_seed` 锁定身份、登记角色卡并保存默认外观快照。确认后先写 `FullbodyCandidate`，分析可重试，采纳时校验原图与角色卡修订并同事务更新；未采纳候选可清理，已采纳旧图留给历史任务。完整身份语义见 [PIPELINE](../../../../docs/PIPELINE.md#全身候选采纳)。
- 草稿转存失败可重试，只有全部图片过期的头像行才清理，不连带正式参考。服务内部和 ORM 使用裸路径（草稿为 `temp-media/`，正式资产为 `companion-assets/{user_id}/`），URL 只在响应出口签名，客户端地址只在确认入口还原比对。
- 全身与衣柜生成、微调、自备图共用 [`prepare_transparent_image`](../../infrastructure/video_processing/image.py)：有效 alpha 原样保留，其余对已有产物本地抠图后再验收；成品 PNG 保持原尺寸与构图。生成前检查模型，换装先透明化再评分，确认与候选采纳再次验收；失败不安装产物。完整交付与背景规则见 [PIPELINE](../../../../docs/PIPELINE.md#全身与着装透明成品)。
- 角色卡和全身候选共用 `extract_card_features`；头像、全身和换装共用 `generate_with_moderation_retry`，审核命中只改写提示词重试一次，结果未知不重试。
- 外观的生成来源、已接受反馈与身份修订守卫存于 `CompanionOutfit.source_json`，一律经 [`OutfitSource`](../../../modules/companion/models.py) 读写：损坏或类型异常的字段按缺省处理，遗留未知键原样保留；动作包据其中的身份全身图路径判断冻结参考是否需要校准。

### 后台任务与初始资产

场景、角色卡和动作包在用户维护期间保存已付费结果，但暂停尚未提交的后继模型调用；显式取消和进程停止仍可取消。动作包上传导入不涉及付费，维护中断即按失败落库，只能重新提交。动作包在每次脚本、图片或视频提交、评分与复核调用前核对维护边界和当前身份；维护时保留进度，只续已有句柄、下载和本地处理，不启动新的付费步骤。维护结束时 `resume_user_dynamic_actions`、`resume_user_scene_jobs`、`resume_user_character_extraction` 分别恢复持久任务，用户已被删除则无行可续；失效身份的已有素材继续保留，新付费步骤明确失败。

视觉调用的业务守卫随请求传到 `vision_chat`，每次实际供应商调用（含回退）前重查；维护、撤权或身份失效不降级为评分不可用，也不被此前供应商错误覆盖。IM 视频提交与评分共用原对端授权守卫；同一 HTTP 请求的内部重试仍由既有供应商传输策略管理。

默认外观、启动标记与错误用 ORM 字段，标记与首动作包同事务写入。外观描述按用户/外观合并在途任务，命名先读取实际采纳图再转写，回写核对路径。失败或重启中断保留原名与已完成描述，持久化失败原因，由衣柜手动补全，不隐式重复付费。空描述的新包在首次模型调用前有界等待已受理的描述任务，再原子冻结描述及评审快照；等待耗尽或描述失败时按参考图兜底，既有冻结包不回填新描述。

## 场景改动链

创建、描述分析、图片重生成和启用是不同操作，先读[任务与原位换图](../../../../docs/PROTOCOL.md#场景任务与原位换图)、[输入冻结](../../../../docs/PIPELINE.md#场景图片重绘)。

| 环节 | 定位入口 | 修改时联动 |
|---|---|---|
| 用户操作 | [scene-page.tsx](../../../../client/renderer/app/features/living/scene-page.tsx)、[scene-detail-view.tsx](../../../../client/renderer/app/features/living/scene-detail-view.tsx) | 保存描述、重生成、取消与旧图呈现 |
| 状态与事件 | [scene-store.ts](../../../../client/renderer/modules/scene/scene-store.ts) 的 `regenerateScene` / `onSceneEvent` | POST 后水合、版本与换号守卫；当前背景见 [scene-backdrop.tsx](../../../../client/renderer/app/features/living/scene-backdrop.tsx) |
| API 与结构 | [companion_scenes.py](../../../api/v1/companion_scenes.py) 的 `post_scene_regenerate`、[schemas_scene.py](../../../modules/companion/schemas_scene.py) | 响应状态、创建任务与重生成任务的区分 |
| 执行与持久化 | [scene_service.py](scene_service.py) 的 `regenerate_scene` / `_run_scene_regeneration` / `resume_scene_jobs`，模型 [scene.py](../../../modules/companion/scene.py) | 单任务互斥、尺寸冻结、原位提交与恢复 |
| 提示词与供应商 | [scene_prompt.py](scene_prompt.py)、[character_images.py](character_images.py) | 创建和重生成的环境输入；独立重复伙伴检查、尺寸适配与未知提交不重发 |

场景验收覆盖屏幕尺寸、重复伙伴与无关主体、分析恢复和换号，清单见 [PIPELINE](../../../../docs/PIPELINE.md#验收范围)。

## 图像输入与装配

[image_generation.py](image_generation.py)的 `resolve_image_gen_chain` 按参考图、编辑、多参考与提示词长度筛选供应商链，不按透明能力过滤（透明优先与回退见[能力筛选](../../../../docs/PIPELINE.md#能力筛选)），`generate_images` 按 `persist_user_assets` 决定返回用户资产、原生 URL 或 data URI；能力位由[供应商基类](../../infrastructure/llm/providers/base.py)声明。提示词按点位选择，头像条款不能直接用于换装；参考优先级与编辑前置条件归 [PIPELINE](../../../../docs/PIPELINE.md#身份造型与参考输入)。

聊天与动态媒体共用 [visual_identity.py](visual_identity.py) 装配提示词，视频参考通过 `self_video_references` 提供；参考与造型规则见 [出镜图片与视频](../../../../docs/PIPELINE.md#出镜图片与视频)。没有 `save_progress` 的同步生图调用（如换装草稿）只对结果下载的可恢复传输错误做有界重试，不重新提交生图；最终失败时回收已落盘文件。调用方须把非 `ImageGenerationError` 的失败转为公开错误，不能落成 500 或结果未知。

## 动作包与媒体质量链

聊天媒体预算由 `MediaTurnState` 跨工具调用共享；验图重做与交付语义见 [媒体协议](../../../../docs/PROTOCOL.md#媒体引用验图与原位交付)。结构化回复（生活空间）的聊天视频终态经 `domains/conversation` 的 `update_video_reply` 原位更新所属气泡；其他会话追加媒体状态系统消息，并发 `video_gen.*` 事件与渠道投递。视频任务的固定文件名包含冻结的生成 ID，避免恢复后数据库序列与旧媒体文件重名。没有目标会话的视频任务只保存结果，由[动态应用](../posts/README.md)接收，不执行聊天交付。

上传导入（`create_pack_from_clips`）与按参考生成（`create_pack_from_reference`）按素材类型调用图片或视频处理并共用发布；[video/state.py](video/state.py)保存冻结生成上下文，单动作结果共用 [schema](../../../modules/companion/schemas_video.py) 的 `ActionResult`，上传包没有可重做的冻结参考。任务按 `status × stage` 持久化，图片处理及 FFmpeg 在工作线程执行，新包失败不清空旧激活包；供应商成品下载共用有界重试，只重试传输错误和 5xx，不重新提交制作；供应商句柄提交后立即落库，重启只续轮询，不重复付费提交。

系统规格当前仅 drag 为图片，其余生成动作是视频。图片请求独立整理静态描述，不选择或校验视频链；结果、已采纳快照及目录中的图片不填充时长、帧数、帧率或循环参数。图片基础设施入口为 [`prepare_action_image`](../../infrastructure/video_processing/image.py)，素材与命中结构归 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

动作的 `accepted_asset_json` 保存已采纳素材和播放版本，当前制作列只属于本次尝试。原位重做、待复核与拒绝候选保留旧已采纳素材，新成品成功或人工采纳时才替换；目录和播放指令消费已采纳版本。跨包继承只迁移已采纳版本，待复核候选仍绑定原动作；缺少已采纳版本时新包明确失败，不复制悬空复核状态。动作反馈每次重做替换本动作字段，空串清除，衣柜回显当前值。确定性素材门禁拒绝记为 `invalid_asset`，不提供重复处理同一素材的“继续”。释放的素材与旧目录同事务登记到 `action_asset_retirements`，24 小时后重查引用再回收；结束的复核保留素材 7 天，之后保留结论并释放文件引用。

历史孤儿回收只扫描可证明属于动作的目录、源视频、姿态图、片段及配套文件；普通图片仅在同生成 ID 的动作姿态图仍存在时可归属。首次确认无引用后另起 24 小时宽限，仍在发布的旧目录中的引用也参与保护；目录不可读时暂停该用户回收。旧引用已丢失、无法证明归属的通用图片保留，避免误删其他生成链的资产。

探身补齐由 [video/service.py](video/service.py)编排，定位校准在 [video/script.py](video/script.py)，接口契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。生成任务收尾须兑现新排队动作的唤醒；空队列停止，不循环恢复未知结果任务；队列清空后还须补做因在途制作而推迟的旧版本退役（见[恢复与历史包](../../../../docs/PIPELINE.md#恢复与历史包)）。

- 生成动作保留有效原生 alpha，其余由本地模型抠像。整包、就绪包重做、失败包续跑和探身补齐在请求时检查抠像模型，动态动作在提案受理时检查；后台制作在每个新的付费步骤前再查 `require_action_matting_model`。缺失时在付费前失败并保留进度，模型恢复后沿用同名重做或失败包续跑；已有可用候选时不再追加提交，按最佳候选收尾。已有供应商句柄只续查与下载，抠像失败保留源素材，结果未知不重发。上传素材要求真实透明背景，处理不调用付费模型。

图片成品、姿态图留白及视频透明化边界见 [图片交付](../../../../docs/PIPELINE.md#图片交付)与 [视频与交付](../../../../docs/PIPELINE.md#视频与交付)。

[media_chain.py](media_chain.py)保存无凭据供应商快照、游标和候选；`character_images.py`、`identity_review.py`、`media_review.py`分别负责身份图链、评分和人工复核。图片、视频分别持久化质量进度；动作包还独立冻结全身身份图，复用已选姿态图和成功动作。恢复与清理见 [恢复规则](../../../../docs/PIPELINE.md#持久化与恢复)。

聊天与动作视频共用 `media_chain.py` 的失败分类和 `video_failure_message` 文案；审核拒绝与结果未知保留独立原因，供应商原文不进入用户提示。

## 验证入口

检查命令见 [Backend](../../../README.md#契约与验证)，生成与恢复验收见 [PIPELINE](../../../../docs/PIPELINE.md#验证)。
