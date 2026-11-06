# 场景

负责环境素材、制作任务与原位换图。

## 场景改动链

| 修改任务 | 入口与消费方 |
|---|---|
| 创建、上传、描述分析、重做与取消 | [service.py](service.py)、[generation_state.py](generation_state.py)、[http_use_cases.py](http_use_cases.py) |
| 原件环境的受控入库 | [import_materials.py](import_materials.py)；采纳已检查的提取或生成补全结果，按导入来源键去重 |
| 生成输入与图片检查 | [prompt_assembly.py](prompt_assembly.py)、[scene_review.py](../media/scene_review.py) |
| REST 与页面 | [companion_scenes.py](../../entrypoints/http/routes/companion_scenes.py)、[场景页](../../../client/renderer/features/creation/environment/scene-page.tsx)、[详情](../../../client/renderer/features/creation/environment/scene-detail-view.tsx) |
| 只读快照与缓存 | [queries.py](queries.py)、[scene-store.ts](../../../client/renderer/features/creation/environment/scene-store.ts) |
| 启停与资产引用 | [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) |

## 状态与并发

`companion_scenes` 保存已采纳素材及可用性，初次制作与替换分别关联 `scene_jobs`；执行控制和检查点归共享任务，重生成状态直接从任务状态投影，不另存可用性镜像；素材与任务在受理、采纳或失败事务中显式更新。上传源、当前图像和制作依赖显式登记资产引用。列表按 ID 游标读取摘要，总数按需查询；状态快照包含就绪素材及最新在途任务，不携带供应商检查点。

账户场景锁串行受理，供应商等待在事务外。创建与重生成共用同一冻结生成状态（提示词、画幅、图片链、已裁切结果），任务身份取 `SceneJob.token`，不另存重复的任务标识。创建、分析和重生成各有恢复入口，结果提交核对任务身份；新制作失败保留上传源与旧可用图。生活步骤在受理前再次校验，并与实际任务绑定，终态结算沿对应业务结果。

环境库存更新不直接改变伙伴当前画面；生活呈现与成品使用冻结素材。就绪环境可制作动态环境循环视频，制作请求可冻结适用时段（黎明/白天/黄昏/夜间）。跨模块只返回快照，不暴露 ORM。

## 恢复与验证

维护期间收妥已提交结果，暂停后续付费阶段；未知提交保留原状态供核对，启动恢复不重新提交。取消后的迟到结果、已采纳图片与未采纳文件分别按任务和资产引用处理，不能通过清空页面状态宣告完成取消。

已取得图片地址但下载失败时保留原图片链，生活步骤保持结果待核对；创建与原位换图的重试均继续接收原结果，不新增候选或替换请求身份。

图片已通过检查但描述失败时保留成品路径，重试只继续描述与采纳；尚待视觉判断的候选仍走独立复核入口。

核对创建/替换、分析重试、上传源保留、取消迟到结果及重启；画幅和重复伙伴检查须查看真实图片。
