# 影像库

负责可播放视觉成品、制作工作项、舞台呈现与播放账本。

## 资产分类

成品按 `asset_kind` 分 `actor` / `scene` / `cinematic` 三类，共用一份身份与不可变版本结构，差别由冻结输入的判别联合（`snapshot_json.source.kind`）承载。动作定义是语义层，一个动作可拥有多个媒体实现（不同造型/不同形式），动作详情经 `by-action` 端点查询已制作清单。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 制作受理、重做与工作项控制 | [service.py](service.py)、[policy.py](policy.py)、[use_cases.py](use_cases.py) |
| 图片/视频制作与发布 | [generation.py](generation.py)、[publication.py](publication.py)、[video_queue.py](video_queue.py) |
| 视频目标与性格装配 | [prompt_assembly.py](prompt_assembly.py)、[prompts.py](prompts.py) |
| 原件保存、分析、匹配与整理 | [imports.py](imports.py)、[import_jobs.py](import_jobs.py)、[import_reasoning.py](import_reasoning.py)、[import_prompts.py](import_prompts.py)；跨业务采纳归 [visual_imports.py](../../workflows/visual_imports.py) |
| 生活请求与结果绑定 | [life.py](life.py) |
| 库查询与实际呈现快照 | [queries.py](queries.py)、[repository.py](repository.py) |
| 舞台组合、意图与静态回执 | [static_presentation.py](static_presentation.py) |
| 资产地址与事件出口 | [client_assets.py](client_assets.py) |
| 启停与引用 | [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) |

## 成品与任务

用户组合制作经 `workflows/visuals_companion.py` 冻结已有素材，图片/视频共用现有工作项。原件分析期间使用 `source.kind=analysis` 暂存，完成后转为普通的 actor/scene/cinematic 来源；收藏资格与自动演出资格分开，上传和生成共用成品模型与规则。上传界面只需提交原件，分析得到的标题和描述在完成时写回成品；分析期间的临时标题仅用于列表识别。分析结果由 `visual_import_jobs` 持久保存，`selections_json` 保存含操作身份的条目队列；`import_operations.py` 定义队列与恢复动作判定，`import_jobs.py` 在事务内领取、合并单条进度并保留新入队项。队列清空后的呈现审核与发布须再次核对队列，新增操作使旧审核失效。原件和已分析版本共用既有成品与版本附件；分析发布归 `import_publication.py`。

调用方装配三类资产的冻结输入，动作语义及呈现选择来源须按契约完整保存；读取和恢复共用结构校验。本模块不跨域读取素材 ORM。请求身份与冻结输入分别去重；手动制作由客户端保留请求身份，响应丢失后沿原请求核对，不能重新付费提交。环境静态图直接复用环境素材，受理端只制作环境循环视频，制作请求可带可选 `day_phase` 冻结适用时段：复用可恢复图片链先准备对应光线的起始图，再设计环境循环；复核同时读取原环境、起始图与视频抽样；它与角色视频共用队列、候选选择和恢复流程，按环境抽样复核，不做人物身份检查。导入关联修改会保留本次创建过的素材归属；撤销或切换前由 workflow 检查影像、生活和当前任务引用，仍被共享时只解除本次关联，不删除素材。

`publication.py` 组合业务采纳与公共任务完成守卫，核对目标和预期版本后追加不可变版本、切换当前指针并登记资产；调用方在同一采纳事务经 `life.py` 结算关联步骤。完整场景和环境视频可先采纳起始图，主体动画只保留真实输入图的文件引用，不创建主体图片成品。重做失败、暂停或取消保留旧成品。

生成主体视频按固定帧率逐帧抠像；原生透明的用户视频保留实际 alpha 并作逐帧检查，二者都用确认后的 RGBA 帧编码 WebM，原件另存；遮罩时间与交付视频对应，首末帧、遮罩及锚点写入版本 `body_json`，并以版本附件保护文件。边界和底部支撑锚点来自真实 alpha，头部锚点随视频复核读取实际首帧，无法确认则留空。处理链见 [actor_clip.py](../../infrastructure/video_processing/actor_clip.py)。自动使用的文件、合成数据与语义资格统一由 [eligibility.py](eligibility.py) 校验；成品检查依据随冻结快照保存，任务检查点回收不清除该依据。

基础准备由 [visual_preparation.py](../../workflows/visual_preparation.py) 协调：只为已采用造型（首次无呈现时取初始造型）准备内置基础动作，先完成默认待机循环，成功后按固定顺序渐进补充互动反应与常用循环（准备集与顺序以该文件常量为准）；待机起始姿态由角色身份、身体结构、当前造型和性格气质共同决定，保持安静、可持续的动作语义，不固定统一站姿或手脚位置；先复用身份、造型及动作版本相符且已确认可自动使用的成品。制作完成不等于可自动演出：成品照常保留在影像库，只有实际语义确认合格才计入该动作的准备；待机循环没有合格成品时结束本轮，渐进动作的不可自动使用只记录原因并继续后续动作。任一项失败、暂停或未知终止本轮并留给原任务恢复入口，同一请求不重复付费。工作项来源 `base_preparation` 与自主制作分开，不读取生活策划资料、不创建生活步骤或经历，共用现有有限修复与供应商预算。实际身份、造型图片与动作内容派生稳定请求身份，描述更新、重启及重复通知不重置额度；新造型仅入柜不触发准备，实际采用、首次就绪与维护恢复才请求准备。

制作按工作项单飞，视频阶段接入公共队列。业务保留自己的采纳条件，不改写调用方的生成要求。身份、目标与质量的定性结论和证据随候选保存；待复核保留原媒体，续审不发布，应用结果经独立业务操作核对版本。列表与详情共用检查依据，不补造缺失判断。

## 呈现与文件

呈现读取指定的冻结版本，素材修改或重做不能追改已显示的事实。呈现行保存舞台组合 `composition_json`、生活意图 `intent_json`、静态目标与已显示快照、待播指令和代次。

呈现响应在 `composition.media` 附各层可播媒体投影（含 actor 版本的 `body` 锚点与命中元数据），播放指令对 actor 成品同样携带 `body`；`ambient_actor` 从可自动使用的待机成品中匹配冻结身份和造型，`ambient_catalog` 与 `ambient_scene` 投影当前身份、造型与环境可用的常驻和环境媒体，目录资格与限额在服务端筛选。三者不制作新素材、不建立逐圈播放账本；窗口模式播放透明主体视频按 `video` 回执并推进组合，静态投影仍按 `window_static`。客户端导演点播（`POST /{visual_id}/play?version=...`，desktop 身份）走同一指令管线，按目录指定版本登记播放与回执，`origin` 与 `life_revision` 按影像契约核对。

生活情境投影与静态兜底归 [life_presentation](../../workflows/life_presentation.py)：动态表现关闭或无可播媒体时生活变化只落到静态目标，自主播放与制作受主动档位及自主开关约束。

`notify_visuals_updated` 通知库、任务和生活意图投影变化，但不推进呈现修订；正式呈现经 `touch_presentation` 及回执推进，制作进度和意图刷新不能使在途播放认领失效。

静态呈现冻结已有素材，不创建影像成品。上线、重连及模式变化通过 `companion_signal` 恢复：事务内重绑当前生活修订及新请求身份，成功才消费在线代次；有效待播指令优先，并发恢复复用已有目标。窗口宿主不接收完整场景恢复指令，改由当前身份与造型可用的透明主体或静态目标承载。缺少素材只产生准备状态，不记录为已显示伙伴。

内部快照保留裸路径，HTTP 与事件出口统一经 `client_assets.py` 转换地址且不修改冻结来源。列表使用摘要与 ID 游标，可播目录在数据库筛选；回收核对当前版本、舞台组合各层版本、呈现、播放和任务引用。

## 恢复与验证

已删除来源保留描述和文件、来源 ID 留空，避免误绑定新身份；历史成品仍可查看，重做使用当前有效角色资料。未知提交保留原任务核对终态。

核对请求重放、共享步骤、发布并发、重做与旧回执、静态恢复、账户/模式切换和资产保护；真实视频接续、图片显示与供应商质量分别验收。
