# Client 渲染层

交互与呈现的工程入口。产品体验归 DESIGN，跨端语义归 PROTOCOL，磁盘缓存归 Client README；本文维护装配、状态所有者和异步资源约束。

## 分层与目录

| 层 | 边界 |
|---|---|
| `app` | 窗口入口初始化与挂载；runtime／workflows 不反向导入 windows，窗口间不互相导入 |
| `modules` | 不依赖 app；conversation 可消费 media 展示原语，其余协作由 app 装配 |
| `shared` | 无业务依赖 |
| `@ipc` | 跨进程契约，renderer 不重复定义 |

跨模块只经公共 barrel，app 另可用 `character/rendering/video` 公共入口；模块内部用相对路径，character 渲染域经本模块 barrel 访问角色能力。规则归 [ESLint](../eslint.config.mjs)：flat config 的后续 `no-restricted-imports` 会替换先前集合，目录规则须通过 `restrictImports` 组合完整限制。后端数据与资产统一走主进程桥，直连例外逐处说明 URL 来源。

| 改动 | 入口 |
|---|---|
| 宿主与独立窗口 | [sprite-entry.tsx](sprite-entry.tsx)、[bootstrap/sprite.tsx](app/bootstrap/sprite.tsx)、[bootstrap/surface.tsx](app/bootstrap/surface.tsx) |
| Windows 桌面与独立舞台 | [bootstrap/desktop.tsx](app/bootstrap/desktop.tsx)、[windows/desktop](app/windows/desktop/)、[desktop-stage.ts](app/workflows/desktop-stage.ts) |
| 网关与账户 | [host-runtime.ts](app/runtime/host-runtime.ts)、[gateway-event-router.ts](app/runtime/gateway-event-router.ts)、[account-lifecycle.ts](app/workflows/account-lifecycle.ts) |
| 引导、激活与启动失败 | [onboarding](app/onboarding/) |
| 角色、动作、拖拽与空间 | [modules/character](modules/character/)、[sprite-stage.tsx](app/components/sprite-stage.tsx) |
| 对话与音频联动 | [modules/conversation](modules/conversation/)、[modules/speech](modules/speech/)、[conversation-speech.ts](app/workflows/conversation-speech.ts) |
| 动态、日记与记忆页 | [modules/posts](modules/posts/)、[modules/memory](modules/memory/)、[memory-section.tsx](app/features/living/settings/memory-section.tsx) |
| 场景与背景 | [scene-store.ts](modules/scene/scene-store.ts) |
| 文案与主题 | [strings/dictionaries](shared/strings/dictionaries/)、[shared/panel](shared/panel/)；`en` 按 `zh` 类型校验，引导问答和人格预设仍有硬编码中文 |

## 装配与状态归属

[bind-presentation.ts](app/bootstrap/bind-presentation.ts) 在渲染前绑定 [presentation-ports.ts](shared/presentation-ports.ts) 的窄端口，角色、语音和窗口能力显式注入，不依赖 barrel 副作用。新增端口用 `createPort`，未装配调用即抛错；订阅与注入随所有者释放。

| 状态 | 所有者与消费方式 |
|---|---|
| 音频播放 | speech，conversation 持投影；app 工作流连接二者 |
| 表现优先级、偏好与空间 | character 统一裁决，窗口不重建状态机 |
| 会话数据与流式回合 | conversation runtime；视图只持导航、输入与展示资格 |
| 窗口开关、附件信箱与播放认领 | 主进程，来源窗口不能只写本地 store |

[conversation-activity.ts](app/workflows/conversation-activity.ts) 汇总所有执行会话与宿主工具，一个会话结束只释放自己持有的工作态；headless 不显示工作态，声音和瞬态优先级仍由 character 裁决。

## 事件与异步生命周期

网关路由先核对认证状态，再按 `session_id` 交给该账户保留的 runtime；角色、工具和投递由 [handlers](app/runtime/handlers/) 处理，场景、动态、日记进入所属 module。`tool.call/cancel` 只由宿主执行，按 `call_id` 去重和撤回，不受当前可见会话过滤。

- store／工作流用 [captureAuthScope](shared/lib/authed-api.ts) 核对鉴权会话与清理代次，组件用 [use-async-guard](shared/hooks/use-async-guard.ts) 核对挂载与代次；失败记录经 `apiSucceeded`，错误文案经 `ipc-error.ts`。
- [storage.ts](shared/lib/storage.ts) 登记账户持久键、清理及恢复回调。换号释放内存并按 `accountId` 重挂载，重置期间不回写索引；所属账户命名空间切换完成后才应用云偏好广播。全局缓存保留和移除语义归 PROTOCOL。
- [runner-status.ts](shared/store/runner-status.ts) 先订阅后读快照，同清理代次共用在途读取，迟到快照不能覆盖新事件或新账户。网关握手超时落到 `error`，不能停留 `connecting`。
- 引导逐字段增量保存，未连网关时不提交；连通后按服务端 `next_field` 续答，本地非空编辑优先。草稿读取失败暂停待重试，不能当作新引导覆盖。
- 已提交消息按 ID 合并，入列与提醒分开；未读只在所属视图可见时清除。动态、日记菜单消费服务端未读镜像，生活空间补查，页面确认快照，迟到请求仍受账户和挂载守卫。
- [proactive-delivery.ts](app/workflows/proactive-delivery.ts) 的气泡与声音复用舞台可见性；不可见时消息仍进入未读，恢复后由待读气泡承接。工作会话跳转不进入轻语。

## 统一桌面装配

交互桌面在一个 renderer 中装配 [features](app/features/) 与内部面板，独立精灵舞台只装配角色、空间、网关状态和角色事件，不恢复或保存会话历史。`ConversationViewProvider` 持视图，[chat-store.ts](modules/conversation/chat-store.ts) 登记和回收按会话隔离的 runtime；共享与资格契约归 [桌面呈现](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

[panel-activity](shared/context/panel-activity.tsx) 向子组件和 Portal 传播活动资格，子面板不能解除父级禁用；媒体查看器归打开它的视图，失活时隐藏并暂停。视图切换保留各自草稿与附件，录音资格失效后旧麦克风或转写结果不能因重新激活恢复。布局、位置和防抖保存守卫账户清理代次。

[desktop-companion-activity.ts](app/workflows/desktop-companion-activity.ts) 合并同一轮变化后转发完整活动载荷；账户或舞台代次变化重发，失败允许同值重试，舞台初始化读取主进程最新镜像。`desktop-stage.ts` 核对舞台代次并持有可取消仪式，工具派发仍在宿主。

桌面角色受顶栏、Dock 和展开轻语的区域约束，另为情绪放大及退出过渡预留范围；布局、视口或内容轮廓变化重新约束，临时受限不改写保存比例。

Dock 选择面板是 `.dock` 的兄弟节点，避免 backdrop-filter 改变 fixed 包含块；面板和条目菜单共用开启态，打开时停用面板活动与角色右键菜单。图标按可见条目分批取回，目录仅随固定配置版本刷新。桌面设置用 `PanelSelect` 将下拉框留在弹层 DOM 与命中区，避免原生弹窗触发失焦收起。

## 角色呈现契约

### 状态与动作

[companion-store.ts](modules/character/companion-store.ts) 管表现优先级，系统动作键归 [presentation/types.ts](modules/character/presentation/types.ts)，两者不混用。界面动作名称经 `systemActionNames` 本地化，外观页与复核队列复用。

瞬态保存恢复目标，重复瞬态不嵌套，旧计时器不能覆盖持续状态；语音准备与播放分开，尾随点播不切 speaking，聊天完成不触发 emotional。动态动作不新增表现状态，实际 started 期间暂呈 emotional，收尾恢复。

[actions](modules/character/actions/) 校验 play、目录与素材；[character-events.ts](app/runtime/handlers/character-events.ts) 负责强制目录刷新、主进程认领及认领后的失效回执，播放器负责加载和开播失败。去重、有效期、started 与终态定义归 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。完整入口共用侧边伙伴，实际显隐从带版本主进程快照读取，精灵宿主禁后台节流，不能用页面可见性判断隐藏。

### 打扰与自主行为

档位、活动、偏好和空间策略由 character 裁决；仅宿主拥有完整活动信号并推送生效档位，其他窗口经 storage 同步偏好和临时安静。活动覆盖只将游戏／全屏沉浸映射到静止，IDE／阅读专注不压档。

[activity.ts](modules/character/activity.ts) 快照单飞，停止使旧结果失效，成功上报才消费变化；只报粗粒度信号，不传应用名或标题。自主请求与结果消费都复用舞台可见性，另复核档位、锁屏、智能开关和服务代次；隐藏、收起或锁屏丢弃迟到意图。政策与体验归 [主动陪伴](../../docs/DESIGN.md#主动陪伴)。

### 直接交互与命中

[sprite-stage.tsx](app/components/sprite-stage.tsx) 持手势和捕获；[gesture.ts](modules/character/sprite/gesture.ts) 将整体形变、指向及点击提示交给容器，情绪放大只作用形象层。光晕、形变和透明留白不扩大像素命中。

- mousemove 时先捕获，异步遮罩更新后静止指针也重判；相同捕获状态复用在途或成功 IPC，失败可重试，穿透释放防抖。
- 按下后持有指针和窗口捕获，取消、失焦、隐藏及卸载统一释放，取消不触发点击或拖拽反馈；单击等待双击判定。
- 命中按捕获 ID 分组：精灵窗 0，完整入口和交互桌面经 `CaptureWindowIdContext` 使用 1，Portal 随所属窗口；完整入口不能挂载 ID 0 的捕获循环。
- 菜单只登记自身区域，关闭事件不穿透成手势。拖拽等反应池经 `speakScripted`，不直连语音引擎。

### 偏好与形象水合

水合只恢复偏好，不把设备生效值回写。角色卡 store 管已保存资料，页面持草稿；事件、聚焦和分析轮询不覆盖局部编辑。外观预览绑定不可变路径，新图未就绪不确认，超时保留草稿身份。

全身参考复用在途读取并守卫换号、头像和卸载；历史索引按账户持久化，缩略图经磁盘缓存。服务端会替换清理草稿，水合须用 `preferCache` 预取。衣柜的替换政策由后端校验，renderer 只呈现与提交。

### 空间行为

[spatial.ts](modules/character/spatial.ts) 持位置和意图生命周期，[spatial-peek.ts](modules/character/spatial-peek.ts) 计算落位及绘制、命中共用的遮挡矩形，[autonomy.ts](modules/character/autonomy.ts) 解释云端意图。默认比例经主进程跨窗同步并平滑生效。

拖拽、换包和卸载撤销探身准备；素材解码后才一起应用位置、遮挡和命中，失败保留旧画面，表演移出遮挡后才上报 started。隐藏／最小化暂停移动、漫游、探身及跟踪，恢复重新约束。

仪式行走的每一步复核舞台资格，目标由主进程换算；中途不可见或失败仍执行原工具，未抵达不指向或预点击，`system.click_at` 不补第二次点击。云端 stay／失败不启动本地漫游，智能关闭后的本地规则仍要求真实空闲信号。

## 会话与媒体

### 会话与草稿

会话历史、待发批次与流式数据归 runtime，视图持输入草稿；首次水合保留在途回合，历史修改使旧回包失效。快照与重连处理归 [Client 历史同步](../README.md#历史同步)，`last_seq` 由调用方从活动列表传入。

- 附件绑定加入时的会话，视频上传完成才允许发送，失效回包不回填。编辑与普通草稿分开，编辑文本不解析 Slash，成功消费修订事件；撤回附件为单槽，多图只回填第一张，其余提示只在发起窗口显示。
- [preset-labels.ts](modules/conversation/preset-labels.ts) 以字典翻译系统预设；后端目录为中文，中文字典须同步，未知项回落目录值。目录查询在同网关、同清理代次共享在途和成功缓存。
- 只读判断消费 `info.kind` 与 `info.is_automation`，主陪伴会话由 `session.get_main` 确定；[conversation-state.ts](modules/conversation/conversation-state.ts) 统一持有并持久化 ID，列表和投递事件校准同一值。持久 ID 不代表当前窗口已水合。
- 会话参数呈现后端生效值，只接受当前会话最新保存结果；斜杠目录仅服务自动补全与确认，实际派发由后端判定。
- 工作台先确认目标不是陪伴再挂载面板，轻语固定陪伴且不装参数面板；跨入口导航不改来源会话。

### 语音播放

speech 持唯一播放队列与台词合成，点播和自动连播共用队列；[conversation-speech.ts](app/workflows/conversation-speech.ts) 把稳定消息 ID、气泡索引和会话映射到收听记录，临时组件 ID、签名 URL 不作持久键。乐观进度与主进程版本快照合并，不回退已听状态或当前断点。

播放资格由挂载、真实可见、视图活动、锁屏和录音共同裁决；失效使旧下载与播放回包作废，显示聊天不重新启动队列。结果区分完成、中断和失败，声音抢占不标记不可用。AudioContext 预热后保持 running，避免 suspend／resume 与 MediaElementSource 重路由叠加丢首帧；语音准备态使用成对引用计数。

保存语音与重试归 [语音契约](../../docs/PROTOCOL.md#语音保存与重试)。当前 `speak` 的生产入口是仪式行走失败提示，预制反应和音色试听用 `speakScripted`；朗读清理不修改聊天原文。限额与字节缓存由主进程统一执行。

### 媒体查看

查看器按窗口挂载，经端口打开；图片复用共用灯箱，完整入口的浮层与命中限内容面板。`MEDIA:` 在实时和历史投影移除，跨 chunk 保留待解析前缀，结构化字段才是展示来源。图片卡和查看器共用同 URL 的在途与成功解析，失败可重试；临时 URL 按 MIME 创建，卸载回收。

## 记忆与场景

记忆页按预设重建列表与编辑状态，保存一条记录保留其他未保存草稿。日记 store 只管日记，记忆编辑页面不归该 store。

[scene-store.ts](modules/scene/scene-store.ts) 分别持当前环境、创建任务、重生成与分页库，按后端版本、请求及账户代次判活。详情按 ID 单读，离开保留会话内编辑；新图预加载成功才替换背景，失败保留旧图但更新任务和政策。不可变图片共享在途与成功解析，失败可重试；显示尺寸由宿主同步、手动生成读主进程快照。任务与激活定义归 [场景契约](../../docs/PROTOCOL.md#场景任务与原位换图)。

## 动作素材呈现

- 网络和持久 manifest 同样校验类型、默认动作、遮罩及包／目录版本，非法目录保留可用画面；片段经 `apiAsset(preferCache)`，目录和遮罩经 `apiAssetBuffer`。同版本共享在途与成功结果，失败释放缓存，账户清理阻断旧回写。
- 双缓冲等待图片解码或视频首帧再换图；[MediaStage.tsx](modules/character/rendering/video/MediaStage.tsx) 按内容轮廓适配侧栏，绘制与命中扣除同一等比留白。图片静态 alpha、视频逐帧遮罩，缺遮罩时侧栏命中限已知内容边界。
- 气泡与轻语用整段素材合并的上部轮廓锚定头部，避免逐帧抖动，轮廓更新后重算屏幕避让。
- 图片只呈现基础动作，动态表达仅视频；移动由容器位移，不从播放推断嘴部或视线。[render-resolver](modules/character/presentation/render-resolver.ts) 选择媒体或 fallback，蛋区分准备、生成、失败及未就绪。
- 渲染层不借水合触发付费制作，缺失动作由显式服务鉴权、去重和记账。

## 主题与玻璃效果

控件消费 panel 和语义 token，首帧主题遵守 Client 规则，弹层命中随拖动更新。减透明由 OS、设备、用户偏好和帧预算共同决定，仅用户偏好上云；监视器随表面释放，后台时间不计性能判断。场景预模糊按图、尺寸和主题失效，避免再次 CSS 模糊，降级保持圆角外透明。

## 验证入口

命令归 [Scripts](../../scripts/README.md#按改动选择验证)。重点覆盖换窗、换号、卸载、迟到回写、声音资格、播放抢占、静止指针命中与 GPU 回退；原生几何、透明和焦点效果须在对应平台验证，绘制与命中共用最终坐标。
