# 数据库字段语义字典

本页用于从业务含义定位字段所有者，说明容易混用的字段组。列名、类型、空值、默认值、枚举、索引和外键以链接的 ORM 及 [0001 基线](../backend/alembic/versions/0001_baseline.py)为准；完整注册见 [bootstrap.models](../backend/bootstrap/models.py)。关系与删除理由见 [DATABASE](DATABASE.md)，不在此复制 DDL 或逐表说明通用字段。

## 公共字段与关系入口

| 需要判断的含义 | 定义与边界 |
|---|---|
| 实体主键、备份身份与时间 | [ModelBase / TimestampMixin](../backend/infrastructure/models.py)：`id` 定位当前库实体，`source_key` 在账户内匹配导入记录，创建/更新时间不承担请求身份；API 编码见 [ID 契约](PROTOCOL.md#id-语义) |
| 归属与配对 | [schema.py](../backend/infrastructure/schema.py)展开账户联合外键；业务仍检查预设。任务详情的 `owner_id` 与一对一用户扩展不能按普通资源 ID 重映射 |
| 任务执行与业务结果 | [JobRecord](../backend/infrastructure/job_models.py)保存控制状态、取消、修订与检查点；详情复用父任务主键，专有结果由业务采纳 |
| 文件与展示路径 | 领域资产外键/关联表保护活引用，路径和 JSON 快照供展示及恢复，不能替代资产关系；删除见 [DATABASE](DATABASE.md#删除与恢复) |
| JSON 结构与导出 | 可变输入和冻结文档由所属 schema/contracts 校验；各领域 `backup.py` 经[备份装配](../backend/bootstrap/backup.py)登记，决定导出、重映射和排除派生值 |

## 账户、配置与设备身份

| 字段组与模型入口 | 语义 |
|---|---|
| 账户、桌面登录与管理员会话：[accounts/models.py](../backend/features/accounts/models.py) | 账户开关、登录有效性和管理员身份各有撤销范围；注销登录不删除账户 |
| 手机配对与设备授权：[remote/models.py](../backend/features/accounts/remote/models.py) | 一次性授权摘要、兑换期限与设备撤销分别保存，独立于桌面登录，不进入用户备份 |
| 用户/系统设置：[settings/models.py](../backend/features/configuration/settings/models.py) | 用户值为结构化设置；`system_settings` 的每项值是 JSON 文本，由 [system_settings.py](../backend/features/configuration/system_settings.py)校验水合，不能直接按 JSONB 对待 |
| 模型配置与资讯连接：[configuration/models.py](../backend/features/configuration/models.py) | 模型配置按用户一对一；资讯连接按账户/平台唯一，凭证修订和扫码登录修订各自隔离迟到结果，均不属于普通设置同步 |
| 人格与政策：[companion/models.py](../backend/features/companion/models.py) | `personas` 定义伙伴及自主政策，当前活动和实际显示各有独立状态 |
| 声音与分发：[voice_models.py](../backend/features/media/voice_models.py)、[updates/models.py](../backend/features/updates/models.py) | 声音当前槽位指向不可变方案版本，消息继续引用旧版本；更新版本是全局分发资料，不归某个产品账户 |

## 会话与话题

| 字段组与模型入口 | 语义 |
|---|---|
| 会话、消息和媒体引用：[conversation/models.py](../backend/features/conversation/models.py) | `parent_id` 表示依附子会话，`forked_from_id` 表示可失去的分叉来源；预设固定记忆域。消息原文与交付快照须一致，文件和本地声音另有关系表；统计由[消息触发器](../backend/features/conversation/statistics.py)派生，不备份 |
| 受理账本：[submission_models.py](../backend/features/conversation/submission_models.py) | 请求标识和内容指纹决定重放资格，结果/替换回复是实体关系；批量输入经有序关联与请求一次提交 |
| 话题资料、分享和反馈：[topics/models.py](../backend/features/topics/models.py) | 批次保存日期与完成情况，来源保存原文依据，usage 才表示随消息实际分享；反馈不自动成为聊天消息或用户事实 |

## 任务与资产

| 字段组与模型入口 | 语义 |
|---|---|
| 任务请求和临时引用：[jobs/models.py](../backend/features/jobs/models.py) | 请求指纹独立于制作身份，多个请求可复用同一工作项；检查点清理不删除幂等身份 |
| 远端名额与尝试：[video_queue/models.py](../backend/features/media/video_queue/models.py) | lane 按供应商凭据分组保存名额和冷却，attempt 保存提交、句柄、结果和未知事实；本地任务删除不能结束远端占用 |
| 文件库存：[assets/models.py](../backend/features/assets/models.py) | 账户路径、内容哈希、媒体属性和回收待办；最后活引用释放后才可回收 |
| 异步通知：[events/models.py](../backend/runtime/events/models.py) | outbox 载荷、认领和投递状态与业务事务协作；数据库通知只唤醒，不承担恢复身份 |

## 身份与制作素材

| 字段组与模型入口 | 语义 |
|---|---|
| 身份、特征与用户覆盖：[appearance/models.py](../backend/features/appearance/models.py) | 确认形象维护统一修订；自动提取值和人工覆盖分离。候选与分析任务独立于当前身份，失败不撤销已发布资料 |
| 衣柜与造型任务：同一[模型文件](../backend/features/appearance/models.py) | 衣柜保存已采纳造型及来源；`outfit_creation_jobs` 保存自主制作详情并继承共享任务。多文件关系只归属身份、造型、候选或分析任务之一 |
| 环境与制作：[scenes/models.py](../backend/features/scenes/models.py) | 素材保存上传源、展示图和目标/源/实际尺寸，初始和替换任务独立；没有“当前启用环境”字段，新制作失败保留可用素材 |
| 动作定义：[action_library/models.py](../backend/features/action_library/models.py) | 保存动作含义、适用条件、播放方式和冻结评审依据；名称供检索，定义不是可播成品 |

## 影像、播放与媒体制作

| 字段组与模型入口 | 语义 |
|---|---|
| 成品与版本：[visuals/models.py](../backend/features/visuals/models.py) | 稳定成品 ID 与不可变版本分离；版本保存冻结组合、实际输入图和文件关系，成功重做才切换当前指针 |
| 制作与呈现：同一[模型文件](../backend/features/visuals/models.py) | 工作项的目标/预期版本控制采纳；呈现保存当前/持续版本、静态素材目标与已显示快照、待播指令和代次；播放记录保存具体版本的指令与回执 |
| 公共视频：[media/models.py](../backend/features/media/models.py) | 来源消息、回复绑定和视频制作详情；通用控制状态归 jobs，远端状态归 provider attempts |

成品/版本成对外键还校验版本属于该成品；循环指针可分阶段写入，提交时必须完整。显示事实不得从生活活动、待播指令或文件存在推断。

## 动态与日记

| 字段组与模型入口 | 语义 |
|---|---|
| 内容、评论与附件：[entries/models.py](../backend/features/entries/models.py) | 动态/日记共享正文和阅读状态，日记按账户/业务日期唯一；来源动态用有方向和顺序的关联保存，评论回复限定同账户同内容 |
| 发布工作：[publication_models.py](../backend/features/posts/publication_models.py) | 计划、媒体任务和结果归发布详情；`outcome` 表示业务事实，继承的执行状态表示进度，二者不能互换 |

## 生活、夜间与调度

| 字段组与模型入口 | 语义 |
|---|---|
| 状态、事项、步骤与事件：[life/models.py](../backend/features/life/models.py) | 生活状态行串行化写入；事项保存目标/进展和调整来源，步骤保存可执行动作及永久阅读成果，事件保留事实性质、主体和结果；完成证据经关联表连接 |
| 请求额度与提交：同一[模型文件](../backend/features/life/models.py) | `life_action_admissions` 独立保存额度归属和已提交事实，覆盖恢复按请求合并，不能从可覆盖的事项历史重新计数 |
| 夜间账本：[nightly/models.py](../backend/workflows/nightly/models.py) | 按账户/本地日期组织阶段、预算和动作身份，关联实际任务与结果；账本计划不证明执行完成 |
| 自动化与等待：[automation/models.py](../backend/features/automation/models.py)、[intent_models.py](../backend/features/companion/intent_models.py) | Cron 保存计划和任务会话，陪伴意图保存等待条件、租约和终态；`source_kind` 与 `cron_job_id` 表示触发来源，`source_key` 仍用于可移植身份 |

事实、额度、来源权限与恢复规则见 [生活契约](PROTOCOL.md#生活状态与生活事件)和[调度契约](PROTOCOL.md#调度)。

## 记忆与证据

[记忆模型](../backend/features/memory/models.py)分别保存当前内容、原始证据、修订、遗忘指纹和向量：分类及槽位有显式字段，`context` 仅是展示话题；证据消息缺失不能产生新事实。指纹可失去原记忆指针，继续抑制同一来源重学；向量按内容版本、模型和实际维度保存，只比较同作用域同空间的有效内容。

保留期与删除关系见 [必要历史](DATABASE.md#必要历史)，人工编辑、审阅与遗忘的行为见 [记忆模块](../backend/features/memory/README.md)。
