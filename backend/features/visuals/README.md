# 影像库

拥有成品及不可变版本、制作工作项、舞台状态和播放账本。动作定义可对应多个可播版本，按 `by-action` 查询制作清单。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 受理、重做和工作项控制 | [service.py](service.py)、[policy.py](policy.py)、[use_cases.py](use_cases.py) |
| 描述整理、候选和冻结输入 | [interpret.py](interpret.py)、[material_selection.py](material_selection.py)、[visuals_companion.py](../../workflows/visuals_companion.py) |
| 起始图、视频、候选与发布 | [generation.py](generation.py)、[prompt_assembly.py](prompt_assembly.py)、[publication.py](publication.py)、[video_queue.py](video_queue.py) |
| 原件分析、恢复和关联整理 | [imports.py](imports.py)、[import_jobs.py](import_jobs.py)、[import_operations.py](import_operations.py)、[import_reasoning.py](import_reasoning.py)、[visual_imports.py](../../workflows/visual_imports.py) |
| 播出目录、回执和查询 | [stage_selection.py](stage_selection.py)、[static_presentation.py](static_presentation.py)、[queries.py](queries.py)、[repository.py](repository.py) |
| 整景及精灵基础准备 | [stage_preparation.py](stage_preparation.py)、[visual_stage_preparation.py](../../workflows/visual_stage_preparation.py)、[visual_preparation.py](../../workflows/visual_preparation.py) |
| 生活结算、地址转换与回收 | [life.py](life.py)、[client_assets.py](client_assets.py)、[asset_references.py](asset_references.py) |

## 成品与任务

创作整理先按描述检索候选并核对组合条件。命中未覆盖整类素材时补读最新一页并标记目录不完整；从环境详情发起时直接按 ID 读取指定环境。跨域采纳由 workflow 经公开操作复核素材。工作项与版本均使用 `source.kind` 校验冻结输入，库摘要和恢复入口共用结构校验。

每个工作项由 `TaskScope` 单飞。输入准备、动作规划与视频提交独立存进度。视频按工作项、制作代次和供应商游标找回已登记尝试，登记与检查点写入之间中断时继续原尝试。当前候选及 `VisualReview` 归制作进度，切换后移入候选历史；人工选回时移出历史。起始图、视频和专项检查分别保存证据，续审复用冻结输入。`publication.py` 在事务内核对目标和预期版本，追加不可变版本、切换当前指针、登记资产并经 `life.py` 结算关联步骤。列表和详情共用尝试进度及检查投影。

生成和导入的主体动画统一经 [actor_asset.py](actor_asset.py) 保存视频与播放素材；[actor_clip.py](../../infrastructure/video_processing/actor_clip.py) 负责逐帧处理。转码中间文件位于账户临时工作目录，正式资产回收只扫描交付文件。首末帧、逐帧遮罩及锚点写入版本 `body_json`。边界与支撑位置取实际 alpha，头部锚点取独立实际首帧，无法确认时留空。`eligibility.py` 为详情阻塞说明、自动目录和播出入口提供同一资格判断。

原件分析保存冻结身份、抽样、匹配依据和素材创建归属。候选目录只在执行中读取；`selections_json` 持久保存含命令及补充说明的条目队列。`import_jobs.py` 在同事务保存条目结论和队列进度，合并其它新入队操作，并在提交后推进内存修订。重复受理按原件和条目来源键去重；撤销或改绑前由 workflow 核对共享引用，仍被引用的素材仅解除本次关联。发布前复核队列与审核修订，在 `import_publication.py` 向版本转交原件和采用的资产。失败或重启按各条目的生成、下载、检查及采纳阶段恢复，落选文件随进度释放。

## 呈现与文件

呈现行保存舞台组合、默认壁纸、生活意图、静态目标、实际快照、待播指令与代次。`stage_plan` 按账户作息、生活修订、采用造型和可播版本筛选，按当前环境查场景别称及活动道具条件。别称与候选排序共用一次读取的等待窗口，耗尽时使用基础计划。正式静态目标由 [life_presentation.py](../../workflows/life_presentation.py) 冻结。`notify_visuals_updated` 只刷新读模型，`touch_presentation` 和实际回执推进呈现修订。

整景准备把模型决定、输入修订和复查时间落库，按普通工作项执行。精灵基础准备以 `base_preparation` 冻结来源和请求身份，按待机、安睡、起身接续。起身绑定合格睡姿的实际开场帧及摘要；引用不可用时跳过该配对。已有合格作品或可恢复工作项时复用，暂停、失败或结果未知沿原阶段处理。两类准备共享库存并使用各自的资格和来源。

内部快照使用裸路径，`client_assets.py` 统一转换出口地址。任务引用保留冻结输入及待接收结果，正式版本引用保留采用文件及检查依据；无版本的静态目标保护快照内路径。回收核对当前版本、舞台各层、播放及恢复引用。已删除来源保留可读描述和冻结文件，重做按有效输入和明确要求受理。

## 恢复与验证

核对请求重放、任务结算、并发发布、在途回执、静态恢复、账户及模式切换和文件保护。实际媒体接续、供应商质量及客户端首帧分别验收。
