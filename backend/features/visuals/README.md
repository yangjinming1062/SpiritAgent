# 影像库

负责组合图片/视频成品、制作工作项、正式呈现与播放账本。跨端资格、回执和恢复见 [呈现契约](../../../docs/PROTOCOL.md#影像成品与模式隔离)，制作规格见 [组合影像制作](../../../docs/PIPELINE.md#组合影像制作)。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 制作受理、重做与工作项控制 | [service.py](service.py)、[policy.py](policy.py)、[use_cases.py](use_cases.py) |
| 图片/视频制作与发布 | [generation.py](generation.py)、[publication.py](publication.py)、[video_queue.py](video_queue.py) |
| 生活请求与结果绑定 | [life.py](life.py) |
| 库查询与实际呈现快照 | [queries.py](queries.py)、[repository.py](repository.py) |
| 静态素材与回执 | [static_presentation.py](static_presentation.py) |
| 资产地址与事件出口 | [client_assets.py](client_assets.py) |
| 启停、备份与引用 | [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) |

## 成品与任务

调用方装配 `ComboSnapshot`，冻结身份、衣柜、环境、动作、活动计划、画幅及要求；本模块不跨域读取素材 ORM。请求身份来自一次工具调用、夜间动作或自主决定，完整冻结组合仅用于在途复用与政策判断，不能代替请求幂等。

`publication.py` 组合业务采纳与公共任务完成守卫，核对目标和预期版本后追加不可变版本、切换当前指针并登记资产；调用方在同一采纳事务经 `life.py` 结算关联步骤。视频工作项可以先采纳起始图再采纳视频，输入图关系须对应实际文件和版本。重做失败、暂停或取消保留旧成品。

制作按工作项单飞，视频阶段接入公共队列；共享供应商等待与未知结果规则归 [PIPELINE](../../../docs/PIPELINE.md#全局视频队列)。业务保留自己的采纳条件，不改写调用方的生成要求。

## 呈现与文件

呈现读取指定的冻结版本，素材修改或重做不能追改已显示的事实。`notify_visuals_updated` 仅通知库和任务变化，正式呈现经 `touch_presentation` 及回执推进，制作进度不能使在途播放认领失效。

静态呈现冻结已有素材，不创建影像成品。上线、重连及模式变化通过 `companion_signal` 恢复：事务内重绑当前生活修订及新请求身份，成功才消费在线代次；有效待播指令优先，并发恢复复用已有目标。缺少素材只产生准备状态，不记录为已显示伙伴。

内部快照保留裸路径，HTTP 与事件出口统一经 `client_assets.py` 转换地址且不修改冻结来源。列表使用摘要与 ID 游标，可播目录在数据库筛选；回收核对当前版本、呈现、播放和任务引用，保留范围见 [必要历史](../../../docs/DATABASE.md#必要历史)。

## 恢复与验证

恢复先重映射物理关系和冻结素材，再重建呈现快照、作废旧指令与回执。已删除来源保留描述和文件、来源 ID 留空，避免误绑定新库身份；历史成品仍可查看，重做使用当前有效角色资料。未知提交沿 [备份契约](../../../docs/PROTOCOL.md#备份校验与覆盖恢复) 保留原任务。

按 [Scripts](../../../scripts/README.md) 核对请求重放、共享步骤、发布并发、重做与旧回执、静态恢复、账户/模式切换和资产保护；真实视频接续、图片显示与供应商质量分别验收。
