# 场景

拥有生活空间场景的描述、制作任务、激活和恢复。固定 16:9 画幅、原位换图与启用语义归 [PROTOCOL](../../../docs/PROTOCOL.md#场景任务与原位换图) 和 PIPELINE。

## 场景改动链

| 环节 | 入口 | 联动 |
|---|---|---|
| 业务 | [service.py](service.py)、[http_use_cases.py](http_use_cases.py) | 创建、上传、分析、重生成、启用、取消与恢复 |
| 模型装配 | [prompt_assembly.py](prompt_assembly.py)、[媒体检查](../media/scene_review.py) | 环境参考、尺寸与重复伙伴检查 |
| 数据 | [models.py](models.py)、[schemas.py](schemas.py) | 制作状态、版本和只读快照 |
| REST | [companion_scenes.py](../../entrypoints/http/routes/companion_scenes.py) | 鉴权、输入和响应转换 |
| 页面 | [scene-page.tsx](../../../client/renderer/app/features/scene/scene-page.tsx)、[scene-detail-view.tsx](../../../client/renderer/app/features/scene/scene-detail-view.tsx) | 保存描述、重做、取消与旧图呈现 |
| 缓存与背景 | [scene-store.ts](../../../client/renderer/modules/scene/scene-store.ts)、[scene-backdrop.tsx](../../../client/renderer/app/features/scene/scene-backdrop.tsx) | 版本与账户守卫 |

场景使用独立账户场景锁。创建、描述分析、重生成与激活是独立操作；上传源和旧可用图片在新制作失败时继续保留。供应商等待在事务外，写回重核任务与版本；过期结果可以保留为就绪素材，不能自动切换当前场景。

维护期间已提交结果继续查询并保存，后续付费步骤暂停；未知结果不重新提交。模块的 [lifecycle.py](lifecycle.py)、[backup.py](backup.py) 和 [asset_references.py](asset_references.py) 提供恢复、备份与引用贡献。跨模块读取返回快照，不暴露场景 ORM。

验证归 [Scripts](../../../scripts/README.md)；同时核对创建/重做状态、上传源保留、取消迟到结果、激活版本、维护和重启。
