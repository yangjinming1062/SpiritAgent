# 数据库字段语义字典

本页帮助从业务概念定位持久字段、关系约束及其所有者，覆盖当前 63 张应用表。精确列名、PostgreSQL 类型、空值、默认值、枚举、索引和约束以各行链接的模型及 [完整 0001 基线](../backend/alembic/versions/0001_baseline.py)为准；不另存一份 DDL。实体关系、设计理由、删除语义和测量依据见 [数据库设计](DATABASE.md)。

## 公共字段与关系入口

| 需要确认的含义 | 权威定义 |
|---|---|
| 实体 ID、可移植身份 `source_key`、创建和更新时间 | [ModelBase / TimestampMixin](../backend/infrastructure/models.py)；`source_key` 用于备份匹配，不能替代请求幂等键 |
| API 资源 ID 与不透明令牌的区别 | [ID 契约](PROTOCOL.md#id-语义)、[ResourceId](../backend/infrastructure/api_ids.py) |
| 同账户联合外键、任务详情的 `owner_id`、一对一用户扩展 | 各模型声明及 [归属约束装配](../backend/infrastructure/schema.py)；完整模型集合由 [bootstrap.models](../backend/bootstrap/models.py)登记 |
| 通用任务状态、取消、修订与检查点 | [JobRecord](../backend/infrastructure/job_models.py)；业务详情主键复用任务 ID，专有结果由所属模块采纳 |
| 固定资源引用、多资源角色与顺序、删除行为 | 各领域资产外键／关联表及 [删除与恢复](DATABASE.md#删除与恢复) |
| 配置与冻结文档的 JSONB 结构 | 所属模块的 schema / contracts；系统设置的 JSON 文本编码单独由 [system_settings.py](../backend/features/configuration/system_settings.py)定义 |
| 导出字段、引用重映射、派生字段排除 | [备份装配](../backend/bootstrap/backup.py)登记的各领域 `backup.py`；格式和覆盖边界见 [备份契约](PROTOCOL.md#备份校验与覆盖恢复) |

下表只摘录影响理解的字段组，不重复公共继承字段。表名直接链接到定义；关系中的账户隔离同时受公共约束装配保护。

## 账户、配置与设备身份

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [users](../backend/features/accounts/models.py) | 激活身份和账户开关；与登录记录、设备授权分别管理 |
| [login_records](../backend/features/accounts/models.py) | 桌面登录 token 身份、有效性和登录／退出时间；撤销登录不等于删除账户 |
| [admin_sessions](../backend/features/accounts/models.py) | 管理员会话及撤销状态，不复用产品账户的登录关系 |
| [remote_pairings](../backend/features/accounts/remote/models.py) | 一次性配对授权摘要、期限与兑换状态 |
| [remote_sessions](../backend/features/accounts/remote/models.py) | 设备身份、凭据摘要与撤销状态；独立于桌面登录 |
| [user_settings](../backend/features/configuration/settings/models.py) | 按账户和配置键保存结构化值；批量读写入口在同目录 `values.py` |
| [system_settings](../backend/features/configuration/settings/models.py) | 系统运营参数的键与逐值 JSON 文本；水合、校验与热更新见 [配置与迁移](../backend/README.md#配置与迁移) |
| [user_model_configs](../backend/features/configuration/models.py) | 以 `user_id` 为主键的模型配置；引用和能力校验归配置领域 |
| [user_social_accounts](../backend/features/configuration/models.py) | 按账户与平台唯一保存凭证、连接状态和修订；扫码会话另有登录修订，凭证不进入普通配置同步 |
| [personas](../backend/features/companion/models.py) | 以 `user_id` 为主键的伙伴定义与创作政策；当前生活、呈现状态各有独立权威 |
| [custom_voices](../backend/features/media/voice_models.py) | 设计／克隆方案版本、当前槽位及试听／参考资产；被消息引用的版本继续保留 |
| [update_versions](../backend/features/updates/models.py) | 全局更新包版本与发布资料，不属于某个产品账户 |

## 会话与话题

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [conversations](../backend/features/conversation/models.py) | `parent_id` 是依附子会话，`forked_from_id` 是可失去的分叉来源；预设决定记忆域。统计列由 [消息触发器](../backend/features/conversation/statistics.py)派生，不进入备份 |
| [messages](../backend/features/conversation/models.py) | 正文、内容类型、工具调用／结果及交付快照分工；摘要水位指向原始消息边界 |
| [prompt_submissions](../backend/features/conversation/submission_models.py) | 账户内请求标识、内容指纹、受理状态及结果／替换回复外键；重放复用原受理结果 |
| [submission_messages](../backend/features/conversation/submission_models.py) | 请求与输入消息的有序关联；整批输入和关联一次提交 |
| [message_assets](../backend/features/conversation/models.py) | 消息实际引用的正式文件，与展示正文中的路径快照分别维护 |
| [message_voice_refs](../backend/features/conversation/models.py) | 消息使用的本地声音版本；供应商外部声音令牌不冒充本地实体 |
| [companion_topic_batches](../backend/features/topics/models.py) | 按账户、预设和日期记录采集批次及实际完成情况 |
| [companion_topics](../backend/features/topics/models.py) | 原始来源、内容与所属批次；来源身份在账户及预设内去重 |
| [companion_topic_usages](../backend/features/topics/models.py) | 话题实际分享与消息的关系，受理或选材不算已分享 |
| [companion_topic_feedback](../backend/features/topics/models.py) | 已分享来源的显式反馈；不写成聊天消息或自动转成用户事实 |

## 任务与资产

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [jobs](../backend/infrastructure/job_models.py) | 持久执行控制行；任务种类选择业务详情，结果采纳和恢复条件仍由业务所有者决定 |
| [job_requests](../backend/features/jobs/models.py) | 请求身份及指纹；多个请求可以关联同一制作，清理检查点不能破坏幂等身份 |
| [job_assets](../backend/features/jobs/models.py) | 按角色保留任务输入、候选和产物，释放关系后才具备文件回收资格 |
| [provider_lanes](../backend/features/media/video_queue/models.py) | 供应商凭据分组的名额与冷却，不等同于某个业务任务 |
| [provider_attempts](../backend/features/media/video_queue/models.py) | 远端提交、句柄、结果与未知事实；通过任务和队列外键关联，业务删除不代表远端结束 |
| [assets](../backend/features/assets/models.py) | 账户内路径、内容哈希、媒体属性及回收待办；活引用限制删除 |
| [ws_events](../backend/runtime/events/models.py) | 同事务 outbox 的载荷、认领及投递状态；数据库通知只负责唤醒 |

## 身份与制作素材

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [avatar_assets](../backend/features/appearance/models.py) | 确认形象、自动特征、人工覆盖及统一修订；每账户至多一个活动身份 |
| [character_analysis_jobs](../backend/features/appearance/models.py) | 身份分析的专有输入和结果；关联目标形象，执行控制继承 `jobs` |
| [companion_fullbody_candidates](../backend/features/appearance/models.py) | 尚未采纳的全身结果及其来源，采纳必须复核目标身份与修订 |
| [companion_outfits](../backend/features/appearance/models.py) | 已采纳造型与来源描述；不承担角色身份或生成执行状态 |
| [appearance_assets](../backend/features/appearance/models.py) | 身份、造型、候选或分析任务的多文件引用；每行只归属其中一种拥有者 |
| [companion_scenes](../backend/features/scenes/models.py) | 当前环境素材、上传源及首次／替换任务指针；可用素材不随新制作失败而失效 |
| [scene_jobs](../backend/features/scenes/models.py) | 一次环境制作的专有输入和采纳目标；共享控制状态属于 `jobs` |
| [action_definitions](../backend/features/action_library/models.py) | 动作含义、适用条件、播放方式及评审依据；名称用于检索，不是独立身份或可播放证明 |

## 影像、播放与媒体制作

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [visual_items](../backend/features/visuals/models.py) | 稳定成品身份及当前版本指针；成功重做切换版本，不替换资源身份 |
| [visual_versions](../backend/features/visuals/models.py) | 按成品和版本号唯一的不可变产物；保存冻结组合、实际输入图版本与媒体属性 |
| [visual_version_assets](../backend/features/visuals/models.py) | 具体版本使用的文件角色与顺序，冻结内容和文件生命周期共同保留 |
| [visual_jobs](../backend/features/visuals/models.py) | 新建／重做的冻结输入、目标和结果版本；预期版本用于并发采纳守卫 |
| [visual_presentations](../backend/features/visuals/models.py) | 每账户当前／持续版本、待播指令和呈现代次；实际呈现不由生活状态字段推断 |
| [visual_playbacks](../backend/features/visuals/models.py) | 指令和回执对应的实际版本；结束记录的保留期见 [必要历史](DATABASE.md#必要历史) |
| [video_gen_jobs](../backend/features/media/models.py) | 公共视频制作、来源消息与回复绑定；执行状态和供应商尝试分别保存 |

成品与版本的成对外键还会核对版本所属成品，不能仅因同账户就互换引用；循环指针在事务提交时须满足完整关系。

## 动态与日记

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [companion_entries](../backend/features/entries/models.py) | 动态／日记共享正文、发布时间和阅读状态；日记按账户和业务日期唯一 |
| [entry_links](../backend/features/entries/models.py) | 日记引用动态的方向与顺序，不从正文文本推断关系 |
| [entry_assets](../backend/features/entries/models.py) | 内容附件的角色和顺序，文件归属由资产关系校验 |
| [entry_comments](../backend/features/entries/models.py) | 评论、回复对象及生成状态；回复限定同账户同内容，每条用户评论至多一条伙伴回复 |
| [post_publications](../backend/features/posts/publication_models.py) | 发布预留、计划、媒体任务与发布结果；`outcome` 与共享执行状态分别表达业务事实和执行进度 |

## 生活、夜间与调度

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [life_states](../backend/features/life/models.py) | 当前生活、聚焦事项及修订；是生活写入的串行锁入口，不代表桌面已经呈现 |
| [life_pursuits](../backend/features/life/models.py) | 持续事项、进展及完成结果；完成须满足 [生活证据契约](PROTOCOL.md#生活状态与生活事件) |
| [life_steps](../backend/features/life/models.py) | 执行步骤及实际任务外键；结果通过内容／影像／版本等明确外键保存 |
| [life_events](../backend/features/life/models.py) | 已发生事实、主体和来源；事件键用于幂等，来源或结果删除不抹掉事实内容 |
| [pursuit_evidence](../backend/features/life/models.py) | 事项完成所引用的事实事件，证据不嵌入不可核对的 JSON 列表 |
| [nightly_activity_logs](../backend/workflows/nightly/models.py) | 账户本地日期的阶段账本、预算及完成情况 |
| [nightly_activity_actions](../backend/workflows/nightly/models.py) | 日志内动作身份、真实任务及结果；执行恢复不以计划冒充完成 |
| [cron_jobs](../backend/features/automation/models.py) | 计划、暂停、下次执行时间及任务会话；调度交接规则见 [Cron 契约](PROTOCOL.md#cron-双轨) |
| [companion_intents](../backend/features/companion/intent_models.py) | 有界主动回合的条件、租约和终态；`source_kind` 区分来源，`cron_job_id` 明确关联 Cron，`source_key` 仍是可移植身份 |

## 记忆与证据

| 表 / 定义 | 字段语义与主要约束 |
|---|---|
| [memories](../backend/features/memory/models.py) | 当前内容、显式分类／槽位和来源；`context` 是展示话题，不承担业务类型判定 |
| [memory_evidence](../backend/features/memory/models.py) | 当前判断引用的原始消息与摘录；失去消息后不能凭引用缺失产生新事实 |
| [memory_revisions](../backend/features/memory/models.py) | 有限的内容修订快照；明确遗忘须同时清除，保留范围见 [必要历史](DATABASE.md#必要历史) |
| [memory_fingerprints](../backend/features/memory/models.py) | 来源指纹及抑制状态，可在正文和原记忆删除后继续防止重学 |
| [memory_embeddings](../backend/features/memory/models.py) | 按内容版本、模型标识和实际维度保存可重建向量；检索只比较同作用域、同空间的有效内容 |
