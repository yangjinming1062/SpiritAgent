# 外观

负责头像、全身身份、角色卡和衣柜素材的生成、上传、分析与采纳。身份与参考职责以 [PIPELINE](../../../docs/PIPELINE.md#身份造型与参考输入) 为准；数据关系见 [字段字典](../../../docs/DATABASE_FIELDS.md)。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 头像、全身与身份确认 | [avatar_service.py](avatar_service.py) |
| 角色卡编辑、分析与恢复 | [character_card.py](character_card.py)、[character_extraction.py](character_extraction.py) |
| 衣柜草稿、上传、确认与描述 | [outfit_service.py](outfit_service.py)、[outfit_jobs.py](outfit_jobs.py) |
| 影像提取着装的受控采纳与撤销 | [import_materials.py](import_materials.py)；入库复核当前身份与真实透明图，采纳事务由调用方持有 |
| 生成输入与出镜参考 | [prompt_assembly.py](prompt_assembly.py)、[visual_identity.py](visual_identity.py) |
| 跨域读取与资产交接 | [queries.py](queries.py)、[views.py](views.py)、[asset_bindings.py](asset_bindings.py) |
| 启停、备份与回收贡献 | [lifecycle.py](lifecycle.py)、[backup.py](backup.py)、[asset_references.py](asset_references.py) |

## 制作与采纳

形象、衣柜和角色卡共用账户外观锁；角色卡编辑另核对行锁和预期修订。供应商读取冻结输入，结果写回再核对身份、源图与任务。文件外键由 `asset_bindings.py` 在 flush/commit 前维护，状态与事件共同提交；裸路径仅用于冻结资料和展示，响应出口签名。

身份确认先保存原图并登记角色卡任务，再异步准备透明全身和默认造型。透明处理失败保留原图；默认造型及角色资料尚未发布时可替换上传，原图变更与任务重置共同提交。生成候选、分析结果和正式采纳分开保存，采纳时核对原图与修订，避免迟到结果覆盖新身份。透明图必须经过实际处理与检查，不能按文件后缀判定。

衣柜以 `OutfitSource` 保存来源，读取和恢复使用同一结构校验。内建制作与外部提示词共用设计准备；AI 换图沿生成前冻结的来源写回，上传替换在锁内重读。确认入柜只验收当前草稿和图片，不因后续角色卡变化否定已生成草稿，原始来源继续供下游区分身份、身体与造型参考。

## 后台任务与初始资产

自主造型受理时经 `outfit_jobs.py` 保存请求和生活步骤，`outfit_progress.py` 维护图片链检查点与资产保护；成品采纳和关联步骤结算共用事务。命名与描述按账户/造型单飞，以实际图片为依据，写回重核路径；失败保留已有名称、描述和诊断。

角色卡就绪后由 [character_ready.py](../../workflows/onboarding/character_ready.py) 登记内置动作、请求初始场景及有限基础动态准备；重启由 [initial_appearance.py](../../workflows/onboarding/initial_appearance.py) 核对未完成准备。基础动态的触发、预算和恢复归 [影像库](../visuals/README.md#成品与任务)。造型入柜不等于采用，也不补齐动作视频。重启与账户维护复用原任务及进度，收妥已提交结果后暂停后续付费阶段；未知提交不重新生成。读取素材不触发补齐制作。

验证按 [Scripts](../../../scripts/README.md) 执行，覆盖身份替换、候选采纳、并发修订、描述单飞和维护恢复；真实透明度、供应商输入与客户端呈现分别验收。
