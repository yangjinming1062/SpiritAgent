# 外观

负责头像、全身身份、内部视觉观察和衣柜素材的生成、上传、分析与采纳。

## 关键入口

| 修改任务 | 入口 |
|---|---|
| 头像、全身与身份确认 | [avatar_service.py](avatar_service.py)、[image_jobs.py](image_jobs.py) |
| 身份版本、后台准备与观察 | [identity.py](identity.py)、[background_tasks.py](background_tasks.py)、[observations.py](observations.py) |
| 衣柜草稿、上传、确认与描述 | [outfit_service.py](outfit_service.py)、[outfit_jobs.py](outfit_jobs.py) |
| 影像提取着装的受控采纳与撤销 | [import_materials.py](import_materials.py)；入库复核当前身份与真实透明图，采纳事务由调用方持有 |
| 生成输入与出镜参考 | [prompt_assembly.py](prompt_assembly.py)、[visual_identity.py](visual_identity.py) |
| 跨域读取与资产交接 | [queries.py](queries.py)、[views.py](views.py)、[asset_bindings.py](asset_bindings.py) |
| 启停与回收 | [lifecycle.py](lifecycle.py)、[asset_references.py](asset_references.py) |

## 制作与采纳

形象与衣柜共用账户外观锁。头像与全身的 `appearance_image_jobs` 沿公共图片链冻结输入、保存供应商结果和候选，相同未完成请求继续原结果；下载失败和未知提交不重新生成，业务资产保存与制作任务完成共用采纳事务。生成任务冻结确认图、身份版本与来源，结果回写核对当前身份；`asset_bindings.py` 在事务提交前维护文件引用，状态和事件共同保存，响应出口签名。

身份确认保存不可变原图并建立身份版本，独立登记可选视觉观察；仅在默认造型尚未就绪时登记首次透明准备。已有默认造型时，采纳新全身不改旧素材，也不重排首次准备或触发基础动态；后续制作按新身份和旧造型的职责对齐。透明图只作为派生造型，不覆盖身份原图；准备失败保留原图并提供独立重试。新身份取消旧的待完成准备，迟到结果不发布初始化事件。观察失败不阻塞制作，结果只在原身份版本仍有效时供文字理解。候选图片复核与用户正式采纳分开，采纳核对原图和版本；透明质量必须由实际处理与校验证明。

衣柜以 `OutfitSource` 保存来源，读取和恢复使用同一结构校验。内建制作与外部提示词共用设计准备；AI 换图沿生成前冻结的来源写回，上传替换在锁内重读。草稿图直接保存为用户资产，确认入柜只验收当前草稿和图片，不因临时文件清理丢失已生成结果，也不因后续身份变化否定草稿；原始来源继续供下游区分身份、身体与造型参考。

## 后台任务与初始资产

手动与自主造型复用持久制作结构，以 `request_source` 分离权限；手动检查通过后仍等待保存草稿，不绑定生活步骤。待复核和续审由 [outfit_reviews.py](outfit_reviews.py) 管理，明确采纳仍走原衣柜入口。自主造型受理时经 [outfit_jobs.py](outfit_jobs.py) 保存请求和生活步骤，`outfit_progress.py` 维护图片链检查点与资产保护；成品采纳和关联步骤结算共用事务。已生成图片的下载失败保留原地址与冻结输入，步骤进入结果待核对，延后继续同一图片链；继续接收时不新增生成候选。其他制作异常保留其实际阶段，不能一律描述为下载失败。命名与描述按账户/造型单飞，以实际图片为依据，写回重核路径；失败保留已有名称、描述和诊断。

默认造型就绪后发出持久事件，由 [visual_decisions.py](../../workflows/visual_decisions.py) 登记内置动作并请求有限基础动态准备；初始环境由引导环境步骤决定后创建（跳过走 [scene_ports](../companion/scene_ports.py) 触发默认场景），重启由 [initial_appearance.py](../../workflows/onboarding/initial_appearance.py) 核对未完成准备。造型入柜不等于采用，也不补齐动作视频。重启与账户维护复用原任务及进度，收妥已提交结果后暂停后续付费阶段；未知提交不重新生成。本地透明准备不含供应商调用，可从恢复后的未知或暂停状态续跑，已有默认造型时不再次抠图。读取素材不重做图像；已入柜造型缺少描述时可后台补全，失败保留重试入口。

全身候选独立保存检查证据和人工采用依据，检查任务只保存冻结参考与执行控制。采纳或拒绝后的候选按任务历史保留 90 天，避免候选回收提前删除检查依据。

验证覆盖身份替换、候选采纳、并发修订、描述单飞和维护恢复；真实透明度、供应商输入与客户端呈现分别验收。
