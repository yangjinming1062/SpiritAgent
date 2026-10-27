# 影像库

负责可播放视觉成品、制作工作项、舞台呈现与播放账本。跨端资格、回执和恢复见 [呈现契约](../../../docs/PROTOCOL.md#影像成品与模式隔离)，制作规格见 [组合影像制作](../../../docs/PIPELINE.md#组合影像制作)。

## 资产分类

成品按 `asset_kind` 分三类，共用一份身份与不可变版本结构，差别在冻结输入的判别联合（`snapshot_json.source.kind`）：

| 类型 | 冻结输入 | 用途 |
|---|---|---|
| `actor` | 身份 + 造型 + 动作，不绑定环境 | 透明主体动画；窗口精灵直接播放，桌面合成到环境层；版本另存主体元数据 `body_json`（遮罩/首末帧/锚点/命中区域） |
| `scene` | 单张环境素材 | 环境媒体；独立于伙伴与动作，制作归环境素材域（本库当前只受理查询与播放） |
| `cinematic` | 身份 + 造型 + 环境 + 动作整体组合 | 完整场景影像；直接作为主画面，不强制透明处理 |

动作定义是语义层，一个动作可拥有多个媒体实现（不同造型/不同形式）；动作详情经 `by-action` 端点查询已制作清单。不同造型不默认共用同一角色视频。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 制作受理、重做与工作项控制 | [service.py](service.py)、[policy.py](policy.py)、[use_cases.py](use_cases.py) |
| 图片/视频制作与发布 | [generation.py](generation.py)、[publication.py](publication.py)、[video_queue.py](video_queue.py) |
| 生活请求与结果绑定 | [life.py](life.py) |
| 库查询与实际呈现快照 | [queries.py](queries.py)、[repository.py](repository.py) |
| 舞台组合、意图与静态回执 | [static_presentation.py](static_presentation.py) |
| 资产地址与事件出口 | [client_assets.py](client_assets.py) |
| 启停、备份与引用 | [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) |

## 成品与任务

调用方装配冻结输入（actor 或 cinematic 的判别快照），本模块不跨域读取素材 ORM。请求身份来自一次工具调用、夜间动作或自主决定，完整冻结输入仅用于在途复用与政策判断，不能代替请求幂等。`scene` 类型由环境素材域制作，本库受理端直接拒绝。

`publication.py` 组合业务采纳与公共任务完成守卫，核对目标和预期版本后追加不可变版本、切换当前指针并登记资产；调用方在同一采纳事务经 `life.py` 结算关联步骤。视频工作项可以先采纳起始图再采纳视频，输入图关系须对应实际文件和版本。重做失败、暂停或取消保留旧成品。

制作按工作项单飞，视频阶段接入公共队列；共享供应商等待与未知结果规则归 [PIPELINE](../../../docs/PIPELINE.md#全局视频队列)。业务保留自己的采纳条件，不改写调用方的生成要求。

## 呈现与文件

呈现读取指定的冻结版本，素材修改或重做不能追改已显示的事实。呈现行保存舞台组合 `composition_json`（layered 引用环境层与主体层 / cinematic 整片引用，互斥）与生活意图 `intent_json`（生活侧只写投影，呈现系统只读取）；两者是呈现配置，不构成第二份生活事实。开播回执按成品类型推进组合：actor/scene 各自替换所在图层，cinematic 整片接管。

`notify_visuals_updated` 仅通知库和任务变化，正式呈现经 `touch_presentation` 及回执推进，制作进度不能使在途播放认领失效。

静态呈现冻结已有素材，不创建影像成品。上线、重连及模式变化通过 `companion_signal` 恢复：事务内重绑当前生活修订及新请求身份，成功才消费在线代次；有效待播指令优先，并发恢复复用已有目标。缺少素材只产生准备状态，不记录为已显示伙伴。

内部快照保留裸路径，HTTP 与事件出口统一经 `client_assets.py` 转换地址且不修改冻结来源。列表使用摘要与 ID 游标，可播目录在数据库筛选；回收核对当前版本、呈现、播放和任务引用，保留范围见 [必要历史](../../../docs/DATABASE.md#必要历史)。

## 恢复与验证

恢复先重映射物理关系和冻结素材（判别联合按 `source.kind` 分支重映射 outfit/scene/action 引用），再重建呈现快照、作废旧指令与回执。已删除来源保留描述和文件、来源 ID 留空，避免误绑定新库身份；历史成品仍可查看，重做使用当前有效角色资料。未知提交沿 [备份契约](../../../docs/PROTOCOL.md#备份校验与覆盖恢复) 保留原任务。

按 [Scripts](../../../scripts/README.md) 核对请求重放、共享步骤、发布并发、重做与旧回执、静态恢复、账户/模式切换和资产保护；真实视频接续、图片显示与供应商质量分别验收。
