# 影像库

## 模块职责

管理专门用于伙伴呈现的组合成品（图片或视频影像）、制作工作项、呈现状态与播放账本。组合快照（`ComboSnapshot`）冻结身份、衣柜、环境、动作三份素材引用、活动计划、目标画幅与生成要求；素材删除后快照与文件引用继续有效，本模块不读取外观、场景与动作的 ORM，组合由调用方装配后传入。

## 关键入口

| 入口 | 职责 |
|---|---|
| `service.py` | 制作受理与重做、播放与回执、工作项暂停/继续/取消/重试、呈现偏好 |
| `queries.py` | 影像分页与详情、工作项列表、呈现状态、实际呈现快照、按触发条件的可播目录 |
| `use_cases.py` | HTTP 薄壳：参数收敛与 RequestError 转换（404/409/400） |
| `generation.py` | 进程内按工作项单飞的制作流水线：图片链→图片检查→运动设计→视频队列→视频检查→发布 |
| `video_queue.py` | 公共视频队列的任务有效性检查（owner kind 为 `visuals`） |
| `lifecycle.py` / `backup.py` / `backup_assets.py` / `asset_references.py` | 恢复登记、备份表与素材校验、活引用收集 |

## 易错约束

- `VisualJob` 一条等于一次新建或重做。目标含视频时先发布起始图影像（`media_kind='image'`）再发布视频影像；图片按 `(user_id, content_hash)` 去重，同内容复用既有行；封面只写 `poster_path` 不建行。视频行的 `input_image_path/input_image_hash` 冻结真实输入图，`input_image_id` 只指向同文件的图片影像，文件不同则留空。
- 重做成功在事务内核对 `target_visual_id + expected_version` 后同 ID 替换（`version+1`）；失败、暂停、取消一律保留旧成品，草稿文件交统一回收。
- 呈现指针在已认领的开播回执中切换：图片与 loop 动作同时更新 `loop_visual_id`，冷却只看 `started_at`/结账时间。短动作（视频 + `once`）在回执到达终态时回到 `loop_visual`，收尾由 `record_playback` 的回执路径完成。
- `repository.presented_item` 投影冻结快照，`service.restore_presentation` 登记恢复播放，事实归属见[呈现契约](../../../docs/PROTOCOL.md#影像成品与模式隔离)。
- 待播指令存 `pending_command_json`；认领按 `presentation_epoch + state_revision + 过期时间 + 自主资格` 重新校验，恢复与换号用 `backup.advance_playback_versions` 作废旧指令。
- 提交结果未知（`submission_unknown`）时保留占用不重投，工作项进入 `unknown` 状态等人工处理；恢复备份时视频队列句柄一律清空。
- 库与工作项变化走 `notify_visuals_updated`（不推进 `state_revision`），呈现状态变化走 `touch_presentation`（递增修订），两者都广播 `companion.visual.updated`；避免制作进度保存打断在途播放认领。
- 自主播放资格与冷却常量在 `policy.py`；触发条件、播放方式与生成要求都来自组合快照，不在本模块内改写。
