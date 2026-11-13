# 数据库字段语义字典

本页用于从业务含义定位字段所有者，说明容易混用的字段组。列名、类型、空值、默认值、枚举、索引和外键以链接的 ORM 及[迁移序列](../backend/alembic/versions)为准；完整注册见 [bootstrap.models](../backend/bootstrap/models.py)。关系与删除理由见 [DATABASE](DATABASE.md)，不在此复制 DDL 或逐表说明通用字段。

## 公共字段与关系入口

| 需要判断的含义 | 定义与边界 |
|---|---|
| 实体主键与时间 | [ModelBase / TimestampMixin](../backend/infrastructure/models.py)：`id` 定位当前库实体，创建/更新时间不承担请求身份；API 编码见 [ID 契约](PROTOCOL.md#id-语义) |
| 归属与配对 | [schema.py](../backend/infrastructure/schema.py)展开账户联合外键；业务仍检查预设。任务详情的 `owner_id` 与一对一用户扩展不能按普通资源 ID 重映射 |
| 任务执行与业务结果 | [JobRecord](../backend/infrastructure/job_models.py)保存控制状态、取消、修订与检查点；详情复用父任务主键，专有结果由业务采纳 |
| 文件与展示路径 | 领域资产外键/关联表保护活引用，路径和 JSON 快照供展示及恢复，不能替代资产关系；删除见 [DATABASE](DATABASE.md#删除与恢复) |
| JSON 结构 | 可变输入和冻结文档由所属 schema/contracts 校验，派生值不落库、按权威内容重建 |

## 账户、配置与设备身份

| 字段组与模型入口 | 语义 |
|---|---|
| 账户、桌面登录与管理员会话：[accounts/models.py](../backend/features/accounts/models.py) | 账户开关、登录有效性和管理员身份各有撤销范围；注销登录不删除账户 |
| 手机配对与设备授权：[remote/models.py](../backend/features/accounts/remote/models.py) | 一次性授权摘要、兑换期限与设备撤销分别保存，独立于桌面登录 |
| 用户/系统设置：[settings/models.py](../backend/features/configuration/settings/models.py) | 用户值为结构化设置；`system_settings` 的每项值是 JSON 文本，由 [system_settings.py](../backend/features/configuration/system_settings.py)校验水合，不能直接按 JSONB 对待 |
| 模型配置与资讯连接：[configuration/models.py](../backend/features/configuration/models.py) | 模型配置按用户一对一；资讯连接按账户/平台唯一，凭证修订和扫码登录修订各自隔离迟到结果，均不属于普通设置同步 |
| 人格与政策：[companion/models.py](../backend/features/companion/models.py) | `personas` 保存人物背景、独立引导进度及自主政策，当前活动和实际显示各有独立状态 |
| 声音与分发：[voice_models.py](../backend/features/media/voice_models.py)、[updates/models.py](../backend/features/updates/models.py) | 声音当前槽位指向不可变方案版本，消息引用制作时的版本；更新版本是全局分发资料，不归某个产品账户 |

## 会话与话题

| 字段组与模型入口 | 语义 |
|---|---|
| 会话、消息和媒体引用：[conversation/models.py](../backend/features/conversation/models.py) | `parent_id` 表示依附子会话，`forked_from_id` 表示可失去的分叉来源；预设固定记忆域。消息原文与交付快照须一致，文件和本地声音另有关系表；统计由[消息触发器](../backend/features/conversation/statistics.py)派生。通话记录与通话发言也是同一表的行，`call_record_id` 表达归属，`call_last_seen_at` 与 `call_duration_ms` 是结算依据 |
| 受理账本：[submission_models.py](../backend/features/conversation/submission_models.py) | 请求标识和内容指纹决定重放资格，结果/替换回复是实体关系；批量输入经有序关联与请求一次提交；`is_realtime_call` 区分通话受理与普通提交 |
| 话题资料、分享和反馈：[topics/models.py](../backend/features/topics/models.py) | 批次保存日期与完成情况，来源保存原文依据，usage 才表示随消息实际分享；反馈不自动成为聊天消息或用户事实 |

## 任务与资产

| 字段组与模型入口 | 语义 |
|---|---|
| 任务请求和资产引用：[jobs/models.py](../backend/features/jobs/models.py) | 请求与制作身份的分离见 [DATABASE](DATABASE.md#任务供应商尝试与资产)；检查点清理不删除幂等身份。普通引用随终态保留期释放，保留角色随业务资料精确替换或任务删除释放 |
| 远端名额与尝试：[video_queue/models.py](../backend/features/media/video_queue/models.py) | lane 按供应商凭据分组保存名额和冷却，attempt 保存提交、句柄、结果和未知事实；本地任务删除不能结束远端占用 |
| 文件库存：[assets/models.py](../backend/features/assets/models.py) | 账户路径、内容哈希、媒体属性和回收待办；最后活引用释放后才可回收 |
| 异步通知：[events/models.py](../backend/runtime/events/models.py) | outbox 载荷、认领和投递状态与业务事务协作；数据库通知只唤醒，不承担恢复身份 |

## 身份与制作素材

| 字段组与模型入口 | 语义 |
|---|---|
| 视觉身份与内部观察：[appearance/models.py](../backend/features/appearance/models.py) | 确认图维护独立身份版本；透明准备和视觉观察任务分别拥有冻结来源。观察仅供文字理解，失败不影响身份资格 |
| 衣柜与造型任务：同一[模型文件](../backend/features/appearance/models.py) | 衣柜保存已采纳造型、来源及最终检查依据；`outfit_creation_jobs` 按请求来源区分手动与自主制作，只有自主路径关联生活步骤。全身候选的检查任务独立保留判断及证据，采纳后的依据存入头像快照。多文件关系只归属身份、造型、候选、准备任务或观察任务之一 |
| 环境与制作：[scenes/models.py](../backend/features/scenes/models.py) | 素材保存上传源、展示图、目标/源/实际尺寸及最终检查依据与派生文件哈希，初始和替换任务独立；没有“当前启用环境”字段，新制作失败保留可用素材 |
| 动作定义：[action_library/models.py](../backend/features/action_library/models.py) | 保存动作含义、适用条件和播放方式；自创动作另冻结评审依据，定义不是可播成品 |

## 影像、播放与媒体制作

| 字段组与模型入口 | 语义 |
|---|---|
| 成品、版本与原件分析：[visuals/models.py](../backend/features/visuals/models.py) | 稳定成品 ID 与不可变版本分离；`asset_kind` 区分 actor/scene/cinematic 三类资产（语义见[影像契约](PROTOCOL.md#影像成品与模式隔离)），版本保存判别联合冻结输入（`snapshot_json.source.kind`，分析期间可为 `analysis`，完成后为普通资产类型；原始文件信息和呈现审核资格可附着于任何来源）、分析任务的上下文/结果/条目操作队列（含命令与补充说明快照，条目结果保存业务结论与修订，执行状态由队列投影，素材创建归属独立保存）及稳定原件哈希（派生透明视频后仍用于重复内容去重）、视频实际表现与适用条件（`source.media_semantics`）、实际输入图、文件关系与 actor 主体元数据（`body_json`），成功重做才切换当前指针 |
| 制作与呈现：同一[模型文件](../backend/features/visuals/models.py) | 工作项的目标/预期版本控制采纳；呈现保存当前/持续版本、舞台组合（`composition_json`，含持续画面选择来源与生活驱动组合的生活上下文）与生活意图（`intent_json`，含结构化活动类别与建议视觉标签）、静态素材目标与已显示快照、待播指令和代次；播放记录保存具体版本的指令与回执，`selection` 与生活上下文两列随指令登记、回执时推进组合；动作定义（[action_library/models.py](../backend/features/action_library/models.py)）另存语义标签、表现角色、动作强度与可中断属性，随冻结输入进入影像快照 |
| 公共媒体：[media/models.py](../backend/features/media/models.py) | 图片按稳定媒体身份和槽位、视频按原任务保存冻结输入、来源消息与交付绑定，供应商信息及尝试序号从冻结链与执行游标读取；检查尝试只保存独立请求、候选额度依据和执行状态，人工操作保存所选候选内容与原判断快照，业务继续记录保存所属任务、原权限及结果。通用控制状态归 jobs，远端状态归 provider attempts；模型判断与独立人工放行依据见 [review_contracts.py](../backend/features/media/review_contracts.py)，待复核与待业务确认均为非终态 |

成品/版本成对外键还校验版本属于该成品；循环指针可分阶段写入，提交时必须完整。显示事实不得从生活活动、待播指令或文件存在推断。

## 动态与日记

| 字段组与模型入口 | 语义 |
|---|---|
| 内容、评论与附件：[entries/models.py](../backend/features/entries/models.py) | 动态/日记共享正文和阅读状态，日记按账户/业务日期唯一；来源动态用有方向和顺序的关联保存，评论回复限定同账户同内容 |
| 日记检索：同一[内容模型](../backend/features/entries/models.py) | `diary_search_index` 保存来源、可重建文本、内容指纹和兼容空间向量；来源删除级联索引，正文不复制进记忆 |
| 发布工作：[publication_models.py](../backend/features/posts/publication_models.py) | 计划、媒体任务和结果归发布详情；`outcome` 表示业务事实，继承的执行状态表示进度，二者不能互换 |

## 生活、夜间与调度

| 字段组与模型入口 | 语义 |
|---|---|
| 状态、事项、步骤与事件：[life/models.py](../backend/features/life/models.py) | 生活状态行串行化写入，`activity_type` 与 `scene_tags_json` 保存当前活动类别及有依据的环境、物品条件供视觉投影；状态、事项、步骤和事件分别保存模型生成的用户叙事，事项保存目标/进展和调整来源，步骤保存可执行动作、永久阅读成果及结构化 `failure_code`，原始错误留在任务边界；事件保留事实性质、主体、叙事和结果；完成证据经关联表连接。 |
| 生活检索：同一[模型文件](../backend/features/life/models.py) | `life_event_search_index` 引用本账户事件，索引受控事实、叙事及阅读所得，按内容指纹、模型和实际维度核对向量 |
| 请求额度与提交：同一[模型文件](../backend/features/life/models.py) | `life_action_admissions` 独立保存额度归属和已提交事实，不能从事项历史重新计数 |
| 夜间账本：[nightly/models.py](../backend/workflows/nightly/models.py) | 按账户/本地日期保存阶段摘要、计划输入和动作身份；计划项唯一关联本账户生活步骤，只保存未受理项的跳过或阻断原因，执行状态与成果读取步骤；账本计划不证明执行完成 |
| 自动化与等待：[automation/models.py](../backend/features/automation/models.py)、[intent_models.py](../backend/features/companion/intent_models.py) | Cron 保存计划和任务会话，陪伴意图保存等待条件、租约和终态；`source_kind` 与 `cron_job_id` 表示触发来源 |

事实、额度、来源权限与恢复规则见 [生活契约](PROTOCOL.md#生活状态与生活事件)和[调度契约](PROTOCOL.md#调度)。

## 记忆与证据

[记忆模型](../backend/features/memory/models.py)只承载普通认知、用户资料和相处理解，分别保存当前内容、原始证据、修订、遗忘指纹和向量：分类及槽位有显式字段，`context` 仅是展示话题；证据消息缺失不能产生新事实。指纹可失去原记忆指针，继续抑制同一来源重学；向量按内容版本、模型和实际维度保存，只比较同作用域同空间的有效内容。

保留期与删除关系见 [必要历史](DATABASE.md#必要历史)，人工编辑、审阅与遗忘的行为见 [记忆模块](../backend/features/memory/README.md)。
