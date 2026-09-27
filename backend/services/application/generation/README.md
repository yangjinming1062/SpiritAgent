# 生成服务

编排形象、外观、场景和媒体生成，管理任务互斥、提交、落库与激活；供应商传输归 infrastructure。输入语义、产物和跨端恢复见 [PIPELINE](../../../../docs/PIPELINE.md)。

## 关键入口

| 入口 | 职责 |
|---|---|
| [character_card.py](character_card.py) | 角色卡分析任务 |
| [visual_identity.py](visual_identity.py) | 出镜身份与本次造型（`SelfVisualPlan`），共用 `build_self_image_prompt` |
| [initial_appearance.py](initial_appearance.py) / [avatar_service.py](avatar_service.py) | 初始外观与头像/全身候选 |
| [outfit_service.py](outfit_service.py) / [fullbody_reference_prompt.py](fullbody_reference_prompt.py) | 换装命名与全身参考提示词/画幅 |
| [image_generation.py](image_generation.py) / [scene_prompt.py](scene_prompt.py) | 图像参考装配与场景提示词装配 |
| [scene_service.py](scene_service.py) | 场景创建、描述分析、图片重生成与切换版本 |
| [video/](video/) / [video_jobs.py](video_jobs.py) | 视频包（script、manifest、state、service）与聊天视频 |
| [media_chain.py](media_chain.py) / [character_images.py](character_images.py) / [identity_review.py](identity_review.py) | 供应商链择优、身份保持图片、评分与严格复核 |
| [media_review.py](media_review.py) | 出镜媒体用户复核状态 |
| [response_builders.py](response_builders.py) | 头像/外观响应装配 |

## 事务与任务所有权

头像生成、全身生成、选择和确认共用用户级锁。短会话读配置与冻结资料 → 事务外等待供应商 → 校验身份、状态和源路径 → 状态与事件同事务提交。迟到结果不得覆盖新任务；候选与正式资产分别清理。

### 全身候选与草稿

全身重绘与自备图先写 `FullbodyCandidate`，分析可重试；用户采纳时校验原图和角色卡修订，同事务更新全身图与身体字段。被替换的未采纳候选清理图片，已采纳旧图保留给历史任务；完整身份语义见 [PIPELINE](../../../../docs/PIPELINE.md#全身候选采纳)。

草稿转存失败可重试，只有全部图片均为过期草稿的头像行才能清理，不连带删除正式参考。读取后脱离 ORM 再签名，临时 URL 不写回资产列。

### 后台任务与初始资产

场景任务在用户维护期间自然收敛并保存已付费结果，显式取消和进程停止仍可取消；角色卡任务纳入维护与启停。

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

[image_generation.py](image_generation.py)按供应商原生能力装配参考，生成与采纳共用资产落库和清理入口。提示词按点位选择，头像可改外貌的条款不能用于换装；参考优先级与编辑前置条件归 [PIPELINE](../../../../docs/PIPELINE.md#身份造型与参考输入)。

聊天与夜间图片共用 [visual_identity.py](visual_identity.py) 的 `build_self_image_prompt`，`SelfVisualPlan` 冻结造型。视频首帧生成/校准也走图片质量链，恢复沿用已保存首帧，具体规则见 [出镜图片与视频](../../../../docs/PIPELINE.md#出镜图片与视频首帧)。

## 视频包与质量链

上传导入与生成共用交付链，[video/state.py](video/state.py)保存上下文与单动作结果，上传包没有可重做的冻结参考。

探身补齐由 [video/service.py](video/service.py)编排，定位校准在 [video/script.py](video/script.py)，接口契约见 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。生成任务收尾须兑现新排队动作的唤醒；空队列停止，不循环恢复未知结果任务。

姿态图的透明输出、留白准备与视频透明化边界见 [视频与交付](../../../../docs/PIPELINE.md#视频与交付)。

[media_chain.py](media_chain.py)持有无凭据的供应商快照、游标与候选；[character_images.py](character_images.py)执行身份图片链与可选画幅门禁；[identity_review.py](identity_review.py)评分复核，[media_review.py](media_review.py)维护人工复核。任务分别持久化图片、视频进度，复用原始参考、已选首帧与成功动作；动作包独立冻结全身身份图。保底和清理见 [恢复规则](../../../../docs/PIPELINE.md#持久化与恢复)。

## 验证入口

检查命令见 [Backend](../../../README.md#契约与验证)，生成与恢复验收见 [PIPELINE](../../../../docs/PIPELINE.md#验证)。
