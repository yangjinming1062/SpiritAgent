# 场景

负责环境素材、制作任务与原位换图。画幅、输入及保留旧图的跨端约束见 [场景契约](../../../docs/PROTOCOL.md#场景任务与原位换图)，生成参考见 [PIPELINE](../../../docs/PIPELINE.md#场景创建与描述)。

## 场景改动链

| 修改任务 | 入口与消费方 |
|---|---|
| 创建、上传、描述分析、重做与取消 | [service.py](service.py)、[http_use_cases.py](http_use_cases.py) |
| 原件中完整环境的受控入库 | [import_materials.py](import_materials.py)；只采纳已检查的真实像素，按导入来源键去重，不启动生图 |
| 生成输入与图片检查 | [prompt_assembly.py](prompt_assembly.py)、[scene_review.py](../media/scene_review.py) |
| REST 与页面 | [companion_scenes.py](../../entrypoints/http/routes/companion_scenes.py)、[场景页](../../../client/renderer/app/features/scene/scene-page.tsx)、[详情](../../../client/renderer/app/features/scene/scene-detail-view.tsx) |
| 只读快照与缓存 | [queries.py](queries.py)、[scene-store.ts](../../../client/renderer/modules/scene/scene-store.ts) |
| 启停、备份与资产引用 | [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) |

## 状态与并发

`companion_scenes` 保存已采纳素材及可用性，初次制作与替换分别关联 `scene_jobs`；执行控制和检查点归共享任务。上传源、当前图像和制作依赖显式登记资产引用。列表按 ID 游标读取摘要，总数按需查询；状态快照包含就绪素材及最新在途任务，不携带供应商检查点。

账户场景锁串行受理，供应商等待在事务外。创建、分析和重生成各有恢复入口，结果提交核对任务身份；新制作失败保留上传源与旧可用图。生活步骤在受理前再次校验，并与实际任务绑定，终态结算沿对应业务结果。

环境库存更新不直接改变伙伴当前画面；生活呈现与成品使用冻结素材，消费规则见 [影像契约](../../../docs/PROTOCOL.md#影像成品与模式隔离)。就绪环境可制作动态环境循环视频并可选冻结适用时段（黎明/白天/黄昏/夜间），呈现层按本地时间优先使用匹配版本，时段只是画面光线、不构成生活事实；跨模块只返回快照，不暴露 ORM。

## 恢复与验证

维护期间收妥已提交结果，暂停后续付费阶段；未知提交保留原状态供核对，启动恢复不重新提交。取消后的迟到结果、已采纳图片与未采纳文件分别按任务和资产引用处理，不能通过清空页面状态宣告完成取消。

已取得图片地址但下载失败时保留原图片链，生活步骤保持结果待核对；创建与原位换图的重试均继续接收原结果，不新增候选或替换请求身份。

按 [Scripts](../../../scripts/README.md) 核对创建/替换、分析重试、上传源保留、取消迟到结果及重启；画幅和重复伙伴检查须查看真实图片。
