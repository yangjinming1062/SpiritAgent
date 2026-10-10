# 动作编排

受理动态动作提案、后台评审并协调播放，制作调用 `generation/video`，收尾由动作领域发布目录。制作语义归 [PIPELINE](../../../../docs/PIPELINE.md#动作资产制作)，跨端目录、额度、播放和回执归 [PROTOCOL](../../../../docs/PROTOCOL.md#动作目录与播放)。

## 模块入口

| 入口 | 职责 |
|---|---|
| [design.py](design.py) | 去重和门禁，返回提案受理、复用、待复核或重做结果 |
| [pipeline.py](pipeline.py) | 受理提交后启动评审/重做，评审任务所有权与重启恢复 |
| [review.py](review.py) | 独立评审、结论硬校验、批准时占额和冻结动作规格 |
| [playback.py](playback.py) | 校验可点播动作，保存播放账本/指令或延迟表达意图 |
| [context.py](context.py) | 当前包、已采纳动作、在途和近期拒绝提案快照 |
| [desktop.py](desktop.py) | 桌面组合复用、按需制作、独立提案评审、偏好、播放意图与回执协调；视频制作交给 `generation/desktop_video` |

调用来自 [action_tool.py](../../adapters/tools/builtin/action_tool.py)、[companion_actions.py](../../../api/v1/companion_actions.py) 和夜间规划；工具与 `companion.idle_expression` 发起播放，REST 回执交给 `domains/actions/usage`。依赖可指向 companion、memory 和 generation，不反向调用 chat 或 nightly。

桌面入口为 [desktop_action_tool.py](../../adapters/tools/builtin/desktop_action_tool.py) 和 [companion_desktop_videos.py](../../../api/v1/companion_desktop_videos.py)，按实际模式提供工具与上下文；资源、模式隔离和恢复语义归 [桌面生活契约](../../../../docs/PROTOCOL.md#桌面生活视频与模式隔离)。桌面状态写入共用用户行锁，应用层单飞锁只协调本进程任务；模型等待在短事务之外。同名动作保持冻结设计，已有成品复用，临时中断沿原进度继续且不重复计额。外部提示词与原始视频上传不进入系统制作额度或人工复核，上传成功后直接发布为动作版本。

## 提案处理

受理去重、门禁与 flush 在 `get_action_accept_lock(user_id)` 内完成，调用方在锁外提交，随后调用 `schedule_accepted_proposal`。进入受理前请求会话必须结束鉴权事务，等待用户锁时不占数据库连接。评审使用同一把锁覆盖读取、模型判断、额度和提交，期间只使用短事务。

- 同包规范化名称已有采纳素材即复用；在制不重复评审或占额，待确认返回复核项。失败或已结束复核却仍停在 `review` 的动作原位重做，清尝试前重查身份、开关和抠像模型，并占独立制作额度。
- 同创意在审提案直接返回；最近复用结论仅在目标仍可播放时复用。已批准或已复用但素材不可用时保留历史提案，另建新行；`deferred` / `rejected` 可复用原行重新评审。
- 受理阶段共用 `policy.check_can_accept` 和 `require_action_matting_model`；同包任务在途时只记录生成唤醒，待确认素材不自行推进。
- 幂等键按用户、来源、包和语义指纹分配序号，保存点内由唯一约束保护；跨来源复用提案须更新来源键，并发落败返回已有受理结果。
- 评审失败落 `deferred`。重启恢复所有 `pending`，只恢复创建未超过 `DEFERRED_PROPOSAL_WINDOW` 的 `deferred`；模型快照的暂缓窗口按 `updated_at` 起算，重提后超出创建窗口的旧提案不会每次重启付费重审。拒绝抑制从最近拒绝的 `updated_at` 计时。

## 评审输入与额度

同包名称用完整 NFKC + casefold 确定身份，新 key 取名称哈希，不按截断名称或运动描述判断。旧数据有多个同名动作时报告冲突；同名重做沿冻结设计，改内容须换名称。

评审只读取所属包冻结参考、角色、人设与着装，不读取当前角色卡。`existing_actions` 来自同包可点播表达动作，保留语义与播放参数，词面排序后最多取 `review.py` 声明的条数；零分不能据此删除，中文近义词未必有词面命中。模型复用 ID 必须属于实际提供列表。

评审前和提交前复核同名状态：已有采纳素材复用，其余暂缓；批准只新建动作行。额度硬校验在批准或独立重做受理事务中执行，不把额度数字注入模型；账本与包生命周期独立。动态快照保留制作状态和列表截断标记，受理、制作与表演是不同事实。

## 验证入口

命令归 [Scripts](../../../../scripts/README.md)。重点核对同名及同创意并发、来源幂等、重做占额、待复核分支、暂缓恢复、冻结评审资料和延迟播放意图；制作与播放分别按全局文档验收。
