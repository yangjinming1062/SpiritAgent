# Client 渲染层

负责交互与呈现，本文维护依赖、状态权威和资源生命周期。产品行为见 [DESIGN](../../docs/DESIGN.md)，跨端载荷见 [PROTOCOL](../../docs/PROTOCOL.md)，连接与缓存见 [Client](../README.md)。

## 分层与目录

| 层      | 边界                                                                             |
| ------- | -------------------------------------------------------------------------------- |
| app     | 窗口入口只初始化和挂载；runtime / workflows 不反向导入 windows，窗口间不互相导入 |
| modules | 不依赖 app；conversation 可消费 media 展示原语，其余跨模块协作由 app 装配        |
| shared  | 无业务依赖                                                                       |
| `@ipc`  | 跨进程契约来源，渲染侧不重复定义                                                 |

character 内含 `actions`、`presentation`、`reactions`、`rendering/{video,fallback}`、`sprite`、`wardrobe`，偏好、人格与空间等保留根入口。跨模块只经公共 barrel；character 另以 `rendering/video` 作为渲染域公共入口。[modules/posts](modules/posts/) 管动态与评论，[modules/memory](modules/memory/) 的 `journal-store` 只管日记；记忆管理页面在 [memory-section.tsx](app/features/living/settings/memory-section.tsx)。

边界规则写在 [ESLint](../eslint.config.mjs)，不绕过内部路径；模块内部用相对路径，character 渲染域只经 `@/modules/character` barrel 访问角色能力。flat config 对同一文件按序合并配置对象，后面的对象再次配置 `no-restricted-imports` 会整体替换前面的 patterns 而不合并，因此各目录规则块都用文件顶部的共用限制组合出完整集合；新增或修改规则块时同样组合，不能只写本目录新增的部分。生产数据与资产统一走主进程桥；直连例外须说明 URL 来源。

窗口入口：精灵窗 [sprite-entry.tsx](sprite-entry.tsx) 初始化后并列挂载 [bootstrap/sprite.tsx](app/bootstrap/sprite.tsx)（宿主网关 WS）与按账户重挂载的 [sprite-window.tsx](app/windows/sprite/sprite-window.tsx)（单击、双击、拖拽与命中捕获在 [sprite-stage.tsx](app/components/sprite-stage.tsx)）；生活空间与工作台由 [living-entry.tsx](app/windows/living/living-entry.tsx) / [workbench-entry.tsx](app/windows/workbench/workbench-entry.tsx) 调用 [bootstrap/surface.tsx](app/bootstrap/surface.tsx) 挂载。激活、引导与启动失败浮层在 [onboarding](app/onboarding/)。界面文案在 [strings/dictionaries](shared/strings/dictionaries/)，`en` 按 `zh` 的字典类型校验；引导问答与人格预设仍在代码中硬编码中文。

## 装配与状态归属

[bind-presentation.ts](app/bootstrap/bind-presentation.ts)在渲染前显式绑定窄能力端口，不依赖 barrel 副作用。[presentation-ports.ts](shared/presentation-ports.ts)是绑定真源；角色与语音实现经端口注入，窗口能力由 app 注入，模块不反向导入窗口。新增端口用 [port.ts](shared/lib/port.ts) 的 `createPort`，未绑定即使用按装配错误抛出。

| 状态或能力                     | 权威模块与消费方式                   |
| ------------------------------ | ------------------------------------ |
| 音频播放                       | speech；conversation 只维护投影      |
| 表现优先级                     | character；消费者不重建状态机        |
| 窗口能力                       | app 注入，模块不导入窗口             |
| 偏好、生效档位、活动与空间策略 | character 统一裁决，各窗口不重新推导 |
| 窗口开关与跨窗附件             | 主进程；不能只写来源窗口 store       |

订阅与注入随所有者清理，重新挂载不叠加监听。

[conversation-activity](app/workflows/conversation-activity.ts)汇总仍在执行的会话和宿主工具，避免一个会话结束时清除其他会话的工作表现；它只消费现有执行状态，语音与瞬态优先级仍由 character 裁决。

## 事件与异步生命周期

- [网关路由](app/runtime/gateway-event-router.ts)在尚未认证、换号或退出期间丢弃事件，按信封 `session_id` 路由到该账户已保留的会话 runtime 后分派：会话、工具、角色与投递事件进 [handlers](app/runtime/handlers/)（会话事件与 `tool.call/cancel` 另收宿主或代理角色 `isProxy`）；场景、动态与日记分别直达 `modules/scene`、`modules/posts` 与 `modules/memory`。
- 各窗口独立水合，任何异步回写须核对用户、会话、回合和清理代次；清理代次与账户存储键登记见 [storage.ts](shared/lib/storage.ts)。判活优先复用共享件，不在站点重写：store 与工作流用 [authed-api.ts](shared/lib/authed-api.ts) 的 `captureAuthScope`（鉴权会话 + 清理代次），`authedApi` 结果的失败记录用 `apiSucceeded`，组件用 [use-async-guard.ts](shared/hooks/use-async-guard.ts)（挂载 + 清理代次）；错误文案取 [ipc-error.ts](shared/lib/ipc-error.ts)。
- 网关休眠唤醒重连的握手超时须落到 `error`，不能永久停在 `connecting`，否则调用方无法重试。
- 账户切换释放旧账户的内存资料、会话与通知并按 `accountId` 重挂载；持久索引按账户隔离，切回时恢复，只在明确移除账户时删除。重置期间不回写持久索引，云端偏好广播等所属账户的存储切换完成后再应用。桌面精灵按目标账户状态自动进入未完成的 onboarding。完整入口开关状态由主进程维护。
- 引导答题逐字段增量持久化（[onboarding-flow.tsx](app/onboarding/onboarding-flow.tsx) fire-and-forget，网关未打开前空操作）；网关连通后拉回服务端草稿按 `next_field` 续答，本地非空编辑优先，读取失败暂停待重试且不得当作新引导覆盖草稿，成功后不重复。
- 鉴权请求仅接受发起会话仍有效的结果。
- `tool.call` / `tool.cancel` 只由宿主执行，按 call_id 去重与撤回，不受可见会话过滤；其他会话过程受会话守卫。
- headless 不显示工作态，非当前会话的可见调用自行以引用计数持有工作态，只释放自身仍拥有的状态。
- 消息入列与提醒分开：已提交陪伴消息按 ID 合并，提醒受可见性等条件控制；自动化系统通知不套陪伴打扰闸门。精灵旁的提示气泡与主动台词只在精灵舞台可见时出现（[proactive-delivery.ts](app/workflows/proactive-delivery.ts) 的 `isSpriteOverlayVisible`），不可见时主动消息仍记入未读，重新可见后由待读气泡承接。
- 跳转按会话归属选择入口，工作会话不能送进轻语。
- 未读只在所属对话可见时清除。
- 动态与日记菜单分别订阅 `modules/posts`、`modules/memory` 的服务端未读镜像；生活空间负责补查，各页面负责快照确认。异步请求保留账户与组件生命周期守卫，迟到结果不能改变当前页面状态；确认条件与恢复契约见[动态与日记](../../docs/PROTOCOL.md#动态与日记)。

## 统一桌面装配

[desktop](app/windows/desktop/)的交互界面以单个 renderer 装配共享 [features](app/features/)及内部窗口；独立精灵舞台只装配角色与空间逻辑、订阅角色事件和网关状态，不恢复或保存会话历史。各窗分别登记交互区域，[desktop-companion-activity](app/workflows/desktop-companion-activity.ts)负责活动镜像。生活空间和工作台仍独立装配业务页。[ConversationViewProvider](modules/conversation/conversation-view.tsx)持有视图状态，[chat-store](modules/conversation/chat-store.ts)登记和回收按会话隔离的 [runtime](modules/conversation/chat-runtime.ts)；共享与隔离语义见[呈现契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

首次历史水合也须保留在途回合，历史修改使较早回包失效；缓存语义见 [Client 历史同步](../README.md#历史同步)。视图切换保留各自草稿与附件；录音资格失效后，旧麦克风等待或转写结果不能因视图再次活动而恢复。

Dock 的应用选择面板（[desktop-dock-picker.tsx](app/windows/desktop/desktop-dock-picker.tsx)）是 Dock 的兄弟节点：`.dock` 的 backdrop-filter 会成为 fixed 元素的包含块。面板与条目菜单共用开启态上报，打开期间停用面板活动与精灵右键菜单；条目只持有主进程句柄，图标按可见条目分批取回。「已添加」标记仅随固定配置版本刷新，窗口与图标更新不重取目录。交互规则见[桌面模式](../../docs/DESIGN.md#桌面模式)。

桌面设置的下拉框使用 [PanelSelect](shared/panel/components.tsx)，列表保留在设置弹层的 DOM 和交互区域内；原生 `select` 的独立弹窗会与失焦收起和原生焦点激活冲突。

[desktop-stage](app/workflows/desktop-stage.ts)接入主进程舞台代次、宿主活动镜像和可取消的仪式请求；工具派发始终由宿主负责。内部面板的活动资格由 [panel-activity](shared/context/panel-activity.tsx)向子组件和 Portal 传递，子面板不能覆盖父级的禁用状态；聊天媒体查看器归打开它的视图，失活时隐藏并暂停媒体，不能抢占其他面板的焦点与 Escape。桌面布局和角色位置按账户保存，防抖写入须守卫清理代次。

桌面角色的普通落位、移动与实际缩放受顶栏、Dock 和展开轻语的可用区域约束，并为情绪放大及其退出过渡预留范围；布局、视口及角色内容边界变化时重新约束，保存的默认比例不随临时空间限制改写。窗口模式保留原有缩放与探身规则，空间实现见 [spatial.ts](modules/character/spatial.ts)。

## 角色呈现契约

### 状态、动作与回执

[companion-store.ts](modules/character/companion-store.ts)维护表现状态优先级，系统动作键定义于 [presentation/types.ts](modules/character/presentation/types.ts)，两者不混用；系统动作的界面名称由 [action-names.ts](modules/character/presentation/action-names.ts) 的 `systemActionNames` 按字典生成，外观页与复核队列共用。

- 瞬态保存恢复目标，旧计时器不得覆盖持续状态，重复瞬态不嵌套目标；语音准备与播放分开，尾随点播不切 speaking，完成聊天不触发 emotional。
- [actions](modules/character/actions/)的 `acceptPlayCommand` 按 play_id 去重并校验包、外观代次、素材与有效期，不符即回执 rejected；换包或外观代次变化作废在播实例。强制刷新目录（同包旧代次除外）、主进程认领，以及认领后目录缺失或舞台不可用的 rejected 回执在 [character-events.ts](app/runtime/handlers/character-events.ts)；开播时过期与加载失败由播放器回执。动态动作不新增表现状态，表达真实可见（上报 started）期间以 emotional 瞬态呈现、收尾即恢复；判定与回执遵循 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。
- 拖拽释放、接取与长按的整体形变，以及仪式指向与点击提示，经 [gesture](modules/character/sprite/gesture.ts) 由舞台容器呈现，不参与命中；情绪放大只作用于形象层，静止档、栖息与探身时不放大。
- 两个完整入口共用 [侧边伙伴组件](app/components/surface-companion/surface-companion.tsx)；入口与播放器共用 [可见性判断](modules/character/actions/action-visibility.ts)，主进程快照按版本应用，其中桌面精灵窗的实际显隐（托盘、快捷键、右键隐藏或最小化）取快照的 `spriteVisible`，锁屏不依赖 Runner 轮询。精灵窗关闭了后台节流，页面可见性 API 始终报告可见，不能据此判断隐藏。播放认领与取消遵循 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。

### 打扰与自主行为

视觉表达只消费 `play_requested`，目录/任务事件只刷新资产，mood 只更新身份区，控制字段不进正文。打扰档位只门控主动外发与主动推理，用户主动行为永不被门控。活动「沉浸式→静止」只覆盖游戏与全屏应用，IDE/阅读专注不压档（专注≠不可打扰）。

[companion-store.ts](modules/character/companion-store.ts)裁决档位；请求和消费两侧检查[自主行为条件](../../docs/DESIGN.md#自主动作与空间智能)，其中精灵可见与播放共用[可见性判断](modules/character/actions/action-visibility.ts)的条件（精灵窗未隐藏或最小化、未被完整入口收起、未开轻语、未锁屏），在发出请求时与结果返回后重验；收起、隐藏或锁屏后丢弃迟到结果，重连不补话。

生效档位只由精灵窗推送：其他窗口缺少活动覆盖，只改偏好或临时安静，经 storage 事件同步。

[activity.ts](modules/character/activity.ts)的快照单飞，停止后旧结果失效；变化只在可用上报成功后消费，失败期间保留。只报约定粗粒度信号，不传应用名或窗口标题；夜间政策由服务端决定，客户端只传 local_hour，动态创作偏好在 [prefs.ts](modules/character/prefs.ts)。

### 直接交互与命中

- 精灵舞台对接窗口捕获，手势识别产生语义。
- 单击等待双击判定，双击成立取消待执行单击；就绪后按实际像素命中，光晕和透明留白不计入。
- 捕获在 mousemove 阶段完成，不能等 mousedown；异步命中更新后即使指针静止也重判。
- 相同鼠标捕获状态复用在途或已成功的 IPC 设置，失败后允许下次探测重试；穿透释放仍防抖，手势持有期间保持捕获。
- 菜单只登记自身区域，关闭事件不穿透触发手势。
- 命中区域按窗口捕获 ID 分组：精灵窗默认 0，完整入口在 [surface.tsx](app/bootstrap/surface.tsx) 经 `CaptureWindowIdContext` 提供 1，弹层（含 Portal）不传 ID 即随所在窗口；完整入口不挂载 ID 0 的捕获，否则会切换精灵窗穿透。
- 有效按下即捕获指针并持有窗口鼠标捕获，手势结束前不因透明像素变化开启穿透；取消、失焦、隐藏和卸载统一释放，不触发点击或拖拽释放反馈。
- [reactions](modules/character/reactions/)提供拖拽等反应池；预制台词经 `speakScripted` 送达，不直连语音引擎。

### 偏好与形象水合

水合只恢复偏好，不把设备生效值当偏好回写。

- 角色卡 store 管已保存资料，页面保留编辑草稿；事件、重聚焦和分析轮询不覆盖局部修改。
- 冲突处理见 [PROTOCOL](../../docs/PROTOCOL.md#角色卡编辑)；换号、换形象和卸载使迟到回写失效。
- 外观预览绑定不可变资产路径；新图未加载不能确认，超时保留草稿身份。
- 全身参考面板复用在途请求，旧请求在换号、换头像、卸载和重置时失效。
- 初始化引导的全身图历史索引保存在 `fullbody-reference-store` 的账户持久化键中，缩略图读取主进程资产缓存；全身草稿会被服务端替换清理，因此水合时须经 `preferCache` 预取。
- 移除账户时同时删除其历史索引。
- 产品交互见 [DESIGN](../../docs/DESIGN.md#认识伙伴)。

[wardrobe](modules/character/wardrobe/)管外观与替换策略（llm_may_replace / locked），设计会话锁定五官。

### 空间行为

- [spatial.ts](modules/character/spatial.ts)拥有位置与异步意图生命周期，[spatial-peek.ts](modules/character/spatial-peek.ts)计算探身落位及绘制、命中共用的遮挡矩形，[autonomy.ts](modules/character/autonomy.ts)解释云端意图。
- 衣柜页修改默认比例后经主进程同步到精灵窗，并沿用平滑缩放路径即时生效。
- 拖拽、换包和卸载撤销探身准备；自主请求返回时重验可见性、档位、锁屏、智能开关和服务代次。栖息交互见 [DESIGN](../../docs/DESIGN.md#位置移动与缩放)，坐标与兼容见 [PROTOCOL](../../docs/PROTOCOL.md#动作目录与播放)。
- 探身位置、遮挡和命中随素材就绪（图片加载完成或视频首帧解码）一起生效，失败保留旧画面。表演移出遮挡后才上报 `started`，结束时重验返回目标。
- stay 或推理失败不触发本地漫游；本地空间规则仅在智能关闭时生效。
- 本地漫游需真实空闲信号，未知则不动；位置适配不足时放弃，不缩成不可辨识大小。
- 精灵窗隐藏或最小化与完整入口收起同样暂停走位、漫游与探身（含计时器与窗口跟踪），重新显示后收回栖身、恢复贴边并重新裁决。
- 仪式行走可跳过，失败仍执行原工具，`system.click_at` 不补第二次点击；目标经主进程换算到精灵视口，不在视口内不走动，未抵达或指向被打断时跳过指向与预点击。仪式与表达播放共用舞台可见性判断，每一步行动前重验：不可见时不走动、不出声、不预点击，行走或指向中变为不可见立即执行原工具。

## 会话与媒体

### 会话与草稿

- 会话历史、输入、待发批次和流式投影归 conversation。
- 停止对会话和 Runner 都是尽力请求，本地仍需收尾，不能表示副作用已撤销。
- 附件绑定加入时的会话，视频上传完成前不可发送；切换后丢弃旧附件及迟到结果。
- 编辑与普通草稿分离，取消恢复普通草稿；编辑文本不解析 Slash，成功只消费修订事件。
- 撤回把锚点草稿（正文与图片，契约见 [PROTOCOL](../../docs/PROTOCOL.md#会话种类与历史修改)）退回输入框；待发送附件是单槽，多张图片只回填第一张，其余未恢复的提示只在发起撤回的窗口显示。
- 会话参数显示后端生效值，只接受当前会话最新保存结果；恢复默认删除覆盖。
- 系统预设与固定预设会话（`kind=special`）的显示名、预设说明按界面语言取字典 `presets`，经 [preset-labels.ts](modules/conversation/preset-labels.ts) 显示；后端目录只有中文，中文字典须与其同步，未知预设回落目录值。
- 会话只读状态消费历史水合的 `info.kind` 与 `info.is_automation`，IM 和任务会话均只读；`system_preset_id` 标识陪伴预设归属，唯一陪伴主会话仍由服务端 `session.get_main` 确定。
- 斜杠命令元数据权威在服务端注册表，本地副本只服务自动补全与确认弹窗，dispatch 仍以服务端为准。
- `$companionSessionId` 在 [conversation-state](modules/conversation/conversation-state.ts)统一持有并按账户持久化；主会话加载、列表与 `companion.message` 校准同一份归属。
- 生活空间打开对话或重连时加载陪伴历史；持久化会话 ID 不代表本窗口已水合消息列表。
- 工作台确认目标不是陪伴后才挂载对话面板。
- 快照、增量与重放按 [Client](../README.md#资产与历史缓存)处理；`syncSessionHistory` 的 `last_seq` 由调用方以活动聊天列表水位传入，不用缓存 `currentSeq`。

### 轻语与入口

轻语固定使用主陪伴会话，不挂参数面板；窗口模式中完整入口打开时收起，桌面模式中独立常驻。附件与通知通过统一入口路由，不把专业目标转成陪伴会话。

### 语音播放

- speech 管音频播放与台词合成。
- 聊天语音播放后端保存的音频；点播与自动连播共用一个队列，缺失音频直接呈现台词。自动播放开关与回应偏好独立，交互见 [DESIGN](../../docs/DESIGN.md#对话与语音)。
- 播放状态归 speech，会话气泡与音频视图归 conversation，由应用工作流装配。
- conversation 以稳定消息 ID 与气泡索引映射本机收听记录，组件临时 ID 和签名 URL 不作持久键。乐观进度与带版本的主进程快照合并，旧同步结果不能回退已听状态或当前断点。
- 播放资格同时要求聊天组件挂载、所属窗口实际可见和未锁屏；生活空间其他页面、最小化及录音期间不播放，显示聊天不会重新启动队列。
- 新播放、停止、换会话、表面隐藏或锁屏使旧下载和播放结果失效。
- 播放结果区分完成、中断与失败；其他声音抢占属于中断，不把语音条标记为不可用。
- AudioContext 预热后保持 running，不主动 suspend（resume 与 MediaElementSource 重路由叠加会丢首帧）。
- 语音准备态在 companion renderer 间引用计数（begin/end 成对），不能直接 `set(true)`。
- 主动台词经 [proactive-delivery.ts](app/workflows/proactive-delivery.ts) 用 `speak`（不落盘，当前生产调用只有仪式行走失败提示），合成或朗读中精灵变为不可见即停声；拖拽反应、音色试听等预制台词用 `speakScripted`（落盘）；朗读文本清理不改写聊天原文。
- 限额和字节缓存归主进程。

### 媒体查看

每个窗口独立挂载查看器，经端口打开；对话图片复用[共用灯箱](shared/components/portrait-lightbox.tsx)，完整入口的浮层与命中限定在内容面板，保留外侧伙伴。`MEDIA:` 标记在实时和历史投影中移除，结构化媒体才是展示来源；跨 chunk 保留待解析前缀。媒体字节经主进程读取，图片卡片与查看器共享同 URL 的在途读取和成功结果，失败后可重新读取；临时 URL 按 MIME 创建并在卸载回收。

## 记忆与场景

### 记忆页面

记忆页面按预设重建列表与编辑状态，迟到结果失效；保存一条记录不清除其他未保存草稿，不增加人工审核流程。

### 场景状态与背景

- [scene-store.ts](modules/scene/scene-store.ts)分别维护当前环境、创建任务、图片重生成状态与分页场景库；水合按后端版本、请求代次和账号清理代次丢弃旧结果。
- 生活空间场景页以场景库为入口，详情按场景 ID 单独读取，离开详情后保留会话内编辑草稿。
- 当前环境的替换图预加载成功后才切换背景；图片加载失败保留旧图，任务与政策仍按后端状态刷新。
- 参考图、外部制作与上传共用创建任务状态，重生成独立跟踪且不使成品失效。
- 壁纸尺寸同步由精灵宿主 [host-runtime.ts](app/runtime/host-runtime.ts) 持有，手动生成读取主进程快照；尺寸、候选恢复与重试契约见 [场景任务](../../docs/PROTOCOL.md#场景任务与原位换图)。
- 保存、启用与失败呈现见 [DESIGN](../../docs/DESIGN.md#场景与当前环境)。

## 动作素材呈现

- 媒体层消费 manifest 中按 `media_type` 分型的图片与透明视频；网络目录与持久缓存均校验公共结构、默认动作与遮罩网格，并核对包 ID、`catalog_version` 和缓存元数据，非法目录保留已有可用画面。素材字节经 `apiAsset`（`preferCache`）走主进程磁盘缓存，同目录、同素材版本共用在途读取和成功结果，双缓冲待图片解码或视频首帧就绪后替换旧画面。
- 目录与遮罩 JSON 经 `apiAssetBuffer` 直接解码；同一遮罩共用在途请求和解析结果，片段与遮罩加载失败不保留结果，账户清理使旧请求失效。
- 完整入口的侧边伙伴按动作素材的内容轮廓适配侧栏宽高并贴近内容面板，桌面精灵沿用自身的舞台比例；缺少 `content_rect` 时的回退与遮罩格式见[播放契约](../../docs/PROTOCOL.md#动作目录与播放)，实现见 [MediaStage.tsx](modules/character/rendering/video/MediaStage.tsx)。
- 图片使用静态 alpha 遮罩，视频按实际播放时间查询逐帧遮罩；两者均扣除等比显示留白，侧边缺少遮罩时只在已知内容边界内命中。
- 桌面气泡用 alpha 遮罩上部轮廓作为头部锚点，缺少遮罩时回落内容边界；轮廓按整段素材合并以免逐帧抖动，气泡和轻语都订阅素材轮廓变化，按实际宽高避让屏幕边缘。
- 图片只随基础动作状态呈现，不带时长、帧率、帧数或循环参数；动态表达仅接受视频。拖拽结束后切回当前基础动作。移动与拖拽由容器位移表达，播放不驱动嘴部或视线。
- `presentation/render-resolver` 按动作目录和生成状态选择 media 或 [fallback](modules/character/rendering/fallback/)，并提供对应的本地化状态；包未就绪或加载失败不空挂媒体元素，蛋上区分准备中、生成中、失败与尚未就绪。
- 渲染层不得经生成 store 触发付费；缺失动作由显式服务流程统一鉴权、去重、记账。

## 主题与玻璃效果

控件消费共享 panel 与语义 token，不硬编码主题色。首帧播种遵守 Client 规则，弹层命中随拖动更新。

减透明由 OS、设备、用户偏好和帧预算共同决定，只有用户偏好上云；监视器随表面释放，后台时间不计入性能判断。场景预模糊结果按图、尺寸和主题失效，避免叠加 CSS 模糊；降级不得填满圆角外透明区。

## 验证入口

命令见 [Scripts](../../scripts/README.md#按改动选择验证)。覆盖换窗、换号、卸载、迟到回写、播放抢占、静止指针命中和 GPU 回退。桌面效果需真实平台验证，几何修改另验证呈现与命中共用同一最终坐标。
