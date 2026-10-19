# Client 渲染层

交互与呈现的工程入口。产品体验归 DESIGN，跨端语义归 PROTOCOL，磁盘缓存归 Client README；本文维护装配、状态所有者和异步资源约束。

## 分层与目录

| 层 | 边界 |
|---|---|
| `app` | 窗口外壳、按业务组织的 features、导航与跨域编排；runtime／workflows 不反向导入 windows，窗口间不互相导入 |
| `modules` | 不依赖 app；conversation 可消费 media 展示原语，其余协作由 app 装配 |
| `shared` | 无业务依赖 |
| `@ipc` | 跨进程契约，renderer 不重复定义 |

跨模块只经公共入口，app 另可用 `character/rendering` 呈现入口；模块内部用相对路径，character 渲染域经本模块公共入口访问角色能力。[架构规则](../scripts/architecture-rules.mjs) 经 ESLint 解析实际路径，覆盖别名与相对导入、当前和新增模块／窗口、运行时值依赖环；纯类型引用不作为运行时环。只有 [platform/desktop.ts](shared/platform/desktop.ts) 读取 preload 全局桥，其他代码按用途消费窄能力接口；后端数据与资产仍经主进程请求，直连例外逐处说明 URL 来源。

| 改动 | 入口 |
|---|---|
| 宿主与独立窗口 | [sprite-entry.tsx](sprite-entry.tsx)、[bootstrap/sprite.tsx](app/bootstrap/sprite.tsx)、[bootstrap/surface.tsx](app/bootstrap/surface.tsx) |
| Windows 桌面与生活视频 | [bootstrap/surface.tsx](app/bootstrap/surface.tsx)、[windows/desktop](app/windows/desktop/)、[desktop-videos](modules/desktop-videos/) |
| 网关与账户 | [host-runtime.ts](app/runtime/host-runtime.ts)、[gateway-event-router.ts](app/runtime/gateway-event-router.ts)、[account-lifecycle.ts](app/workflows/account-lifecycle.ts) |
| 引导、激活与启动失败 | [onboarding](app/onboarding/) |
| 角色、动作、拖拽与空间 | [modules/character](modules/character/)、[sprite-stage.tsx](app/components/sprite-stage.tsx) |
| 衣柜与逐动作预览、重做、采纳 | [appearance-page.tsx](app/features/appearance/appearance-page.tsx)、[video-section.tsx](app/features/appearance/video-section.tsx)、[video-pack-store.ts](modules/character/rendering/video/video-pack-store.ts)；采纳入口复用 [media-review-queue.tsx](app/features/companion/media-review-queue.tsx) |
| 公共对话、设置与状态交接 | [conversation-page.tsx](app/features/conversation/conversation-page.tsx)、[app-settings.tsx](app/features/settings/app-settings.tsx)、[app-ui-state.ts](app/workflows/app-ui-state.ts) |
| 对话与音频联动 | [modules/conversation](modules/conversation/)、[modules/speech](modules/speech/)、[conversation-speech.ts](app/workflows/conversation-speech.ts) |
| 动态、日记与记忆页 | [modules/posts](modules/posts/)、[modules/diary](modules/diary/)、[memory-section.tsx](app/features/companion/memory-section.tsx) |
| 场景与背景 | [scene-store.ts](modules/scene/scene-store.ts) |
| 文案与主题 | [strings/dictionaries](shared/strings/dictionaries/)、[shared/panel](shared/panel/)；`en` 按 `zh` 类型校验，引导问答和人格预设仍有硬编码中文 |

## 装配与状态归属

`app/windows` 管精灵、主窗口与桌面外壳，`app/features` 按场景、外观、动态、日记、对话、执行轨迹及设置组织可复用页面；统一导航归 `app/navigation`。主窗口与桌面共用 [surface.tsx](app/bootstrap/surface.tsx) 的账户、网关与 Provider 装配，并经 [app-content-page.tsx](app/components/app-content-page.tsx) 分派非聊天业务页；窗口内部实现不作为其他窗口的依赖。

[bind-presentation.ts](app/bootstrap/bind-presentation.ts) 在渲染前绑定 [presentation-ports.ts](shared/presentation-ports.ts) 的窄端口，角色、语音和窗口能力显式注入，不依赖 barrel 副作用。新增端口用 `createPort`，未装配调用即抛错；订阅与注入随所有者释放。

| 状态 | 所有者与消费方式 |
|---|---|
| 音频播放 | speech，conversation 持投影；app 工作流连接二者 |
| 表现优先级、偏好与空间 | character 统一裁决，窗口不重建状态机 |
| 会话数据与流式回合 | conversation runtime；视图只持导航、输入与展示资格 |
| 窗口开关、附件信箱与播放认领 | 主进程，来源窗口不能只写本地 store |
| 跨模式页面、会话、草稿与阅读位置 | 主进程账户快照；`app-ui-state.ts` 捕获与恢复，渲染层确认交接完成后才释放来源资格 |

[conversation-activity.ts](app/workflows/conversation-activity.ts) 汇总各会话与主进程工具活动，声音和瞬态优先级仍由 character 裁决。

## 事件与异步生命周期

制作页通过 `shared/panel` 的 `CreationWorkspace` 将页头和操作区放在正文滚动容器之外，`CreationFeedback` 在相关按钮或预览旁呈现普通文字与图标，不显示横幅或预留顶部状态区域。`CreationActionBar` 只维护操作区样式、不自行吸附。业务模块投影真实阶段与恢复能力，公共组件不发请求、不推测进度。结果未知时先核对服务端状态，结束本地等待提醒不等于取消原任务。

声音管理与引导复用同一制作组件。[voice-creation-store.ts](app/features/companion/voice-creation-store.ts) 按账户会话保存设计、克隆请求与输入和候选，页面失活或卸载不取消制作；录音、样本上传、播放与页面确认回调继续遵守活动资格。换号和账户清理使原任务回调失效，显式取消仍等待原任务的服务端回执。声音页介绍与当前声音摘要在正文中滚动，分类与主操作保留于有界制作区，进度放在主操作旁。`DirectoryShell` 在矮窄面板改用可横向滚动的单行分类，并保持所选分类可见，为最小桌面面板保留制作空间。

`app/runtime` 管理账户与窗口的 IPC 订阅，React Hook 绑定启停。各窗口先订阅再读连接状态，通过类型化 HTTP 操作恢复会话；主进程合并同会话的并发快照，视图释放不关闭网络。业务请求经 [resource-request.ts](shared/lib/resource-request.ts) 提交稳定操作标识，账户与请求代次使旧实例和迟到结果失效。网络连接与设备派发归[主进程网关](../main/README.md#账户网关与窗口订阅)。

网关路由先核对认证状态，再按 `session_id` 交给该账户保留的 runtime；角色、工具展示和投递由 [handlers](app/runtime/handlers/) 处理，场景、动态、日记进入所属 module。

- store／工作流用 [captureAuthScope](shared/lib/authed-api.ts) 核对鉴权会话与清理代次，组件用 [use-async-guard](shared/hooks/use-async-guard.ts) 核对挂载与代次；失败记录经 `apiSucceeded`，错误文案经 `ipc-error.ts`。
- [storage.ts](shared/lib/storage.ts) 登记账户持久键、清理及恢复回调。换号释放内存并按 `accountId` 重挂载，重置期间不回写索引；所属账户命名空间切换完成后才应用云偏好广播。全局缓存保留和移除语义归 PROTOCOL。
- [runner-status.ts](shared/store/runner-status.ts) 先订阅后读快照，同清理代次共用在途读取，迟到快照不能覆盖新事件或新账户。网关握手超时落到 `error`，不能停留 `connecting`。
- 引导 flow 只装配导航与未提交表单，问题和恢复路由为纯定义；`onboarding-draft.ts` 持串行保存与读取守卫，头像和音色流程各自管理异步结果。引导通过 REST 逐字段增量保存，按服务端 `next_field` 续答，本地非空编辑优先。草稿读取失败暂停待重试，不能当作新引导覆盖。
- 用户提交携稳定 `request_id`，响应不明时先查询回执；停止绑定具体回合。其他设备的保存通知按消息 ID 插入，不绑定本地待确认草稿。挂载期间主进程缓冲会话事件，先发布内部 `session.snapshot`，再按水位交付后续帧；缺少当前流基线时不应用增量投影，账户通知独立消费。已提交消息按 ID 合并，入列与提醒分开；未读只在所属视图可见时清除。动态、日记菜单消费服务端未读镜像，主窗口与桌面补查，页面确认快照，迟到请求仍受账户和挂载守卫。
- [proactive-delivery.ts](app/workflows/proactive-delivery.ts) 的气泡与声音复用舞台可见性；不可见时消息仍进入未读，恢复后由待读气泡承接。工作会话跳转不进入轻语。

## 主窗口与桌面装配

主窗口与桌面共用 [features](app/features/) 和导航，各入口选中位置及桌景草稿随主进程账户快照交接。对话页切页时保留实例并关闭活动资格；动态媒体查看器在离开来源页签后释放。衣柜与背景的文字、选中项保存于账户缓存，参考图片字节由 [creation-draft-image.ts](shared/lib/creation-draft-image.ts) 保存、跨窗口通知并随明确移除账户清理，不作为服务端成品。背景表面仅消费主进程缓存媒体及播放控制。窗口与轻语规则归 [DESIGN](../../docs/DESIGN.md#桌面模式)，交接与资格归 [桌面呈现](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。`ConversationViewProvider` 持视图，[chat-store.ts](modules/conversation/chat-store.ts) 管理会话 runtime。

[panel-activity](shared/context/panel-activity.tsx) 向子组件和 Portal 传播活动资格，子面板不能解除父级禁用；媒体查看器归打开它的视图，失活时隐藏并暂停。视图切换保留各自草稿与附件，录音资格失效后旧麦克风或转写结果不能因重新激活恢复。布局、位置和防抖保存守卫账户清理代次。

[desktop-companion-activity.ts](app/workflows/desktop-companion-activity.ts) 合并同一轮变化后转发完整活动载荷；账户或承载代次变化重发，失败允许同值重试。工具派发归主进程，桌面表面停用精灵空间行为。

[desktop-video-wallpaper.ts](app/windows/desktop/desktop-video-wallpaper.ts) 消费显式启用的独立套装，负责播放认领、回执与默认套装准备；衣柜、场景库变化不触发套装切换或制作。套装和当前成品、制作草稿的语义见[桌面生活契约](../../docs/PROTOCOL.md#桌面生活视频与模式隔离)。外部制作窗口绑定草稿及基准，变化后须刷新。静态首帧只使用所属套装的确认图片，不能回退到当前场景库图片。

[管理页](app/features/desktop-life/desktop-life-page.tsx) 的参考选择只更新创建草稿，素材加载结果由各自 store 持有，迟到回写守卫账户与请求代次。[视频修改](app/features/desktop-life/desktop-life-refinement.tsx) 独立持有预览检查与提交状态，按素材和套装版本校验制作起点。[预览](app/features/desktop-life/desktop-life-preview.tsx) 的草稿图片实例跟随完整资源 URL，视频保留同一资产的播放身份；图片确认资格来自实际草稿图的加载事件，当前成品封面回退不能解锁确认。页面交互与恢复体验归 [DESIGN](../../docs/DESIGN.md#桌面生活视频)。

[背景播放器](app/windows/desktop/desktop-background-entry.tsx) 双缓冲等待实际呈现的首帧；共同基准且允许自然衔接的短动作在循环接点切入，其余画面及上传视频的循环接点使用淡化。短动作返回仍有效的保留循环，套装或素材替换后不能返回旧画面。管理预览、封面、视频和过渡帧共用 [画幅布局](../shared/desktop-media-geometry.ts)，按实际媒体与视口确定裁切或居中内容区域，完整显示时让出模糊底层；规则归 [PIPELINE](../../docs/PIPELINE.md#桌面生活视频)。媒体加载失败保留当前画面，按主进程暂停状态控制播放并回收媒体 URL。套装启用前先预加载安静相伴，成功后才请求切换；主进程媒体桥同时守卫账户和选中路径。

桌面根组件持有窗口与会话状态，悬浮底栏组合外部应用 Dock、屏幕居中的伙伴头像和固定产品导航；底栏四个导航入口与包含聊天的内部窗口类型分开定义，聊天参与布局恢复，由头像打开。桌面彩色 SVG 集中于 [assets/desktop](../assets/desktop/)，通过 Vite `BASE_URL` 加载；窗口模式导航使用 Tabler 图标。右下角控制入口独立展开状态与桌面操作。主进程与 renderer 共用 [desktop-layout.ts](../shared/desktop-layout.ts) 的尺寸，左右区域等宽并按内容限宽，溢出区域各自滚动。底板装饰与向上展开的弹层分离，避免 backdrop-filter、变换或滚动容器裁切菜单；头像光环与未读声波使用独立装饰层，仅动画缩放和透明度，不参与命中或改变布局，声波不消费录音或音频分析状态。交互区域只覆盖实际底栏、头像、按钮、弹层及连接间隙，透明空白继续穿透。

桌面主题在自身作用域提供实底表面，包括共享弹层的主题覆盖；内部窗口保留 `contain: layout`，使固定定位的表单弹层受所属窗口约束。具体视觉与未读行为见 [桌面模式](../../docs/DESIGN.md#桌面模式)。

账户菜单、应用面板、条目菜单与控制面板共用互斥开启态，打开时停用面板活动与角色右键菜单。Dock 持有启动请求及面板代次，失焦卸载面板不丢失失败反馈，旧回执不能收起新面板；主动关闭与失焦关闭分别处理焦点归还。图标按可见条目分批取回，回执按扫描版本校验，固定标记刷新失败不废弃同代图标；固定配置变化时携当前目录版本重新投影固定标记，避免缓存到期重扫打断键盘焦点，手动重扫才主动更新目录句柄。桌面控制面板用 `PanelSelect` 将下拉框留在弹层 DOM 与命中区，避免原生弹窗触发失焦收起。

## 角色呈现契约

### 状态与动作

[companion-store.ts](modules/character/companion-store.ts) 管表现优先级，系统动作键归 [presentation/types.ts](modules/character/presentation/types.ts)，两者不混用。界面动作名称经 `systemActionNames` 本地化，外观页与复核队列复用。

瞬态保存恢复目标，重复瞬态不嵌套，旧计时器不能覆盖持续状态；语音准备与播放分开，尾随点播不切 speaking，聊天完成不触发 emotional。动态动作不新增表现状态，实际 started 期间暂呈 emotional，收尾恢复。

[actions](modules/character/actions/) 校验 play、目录与素材；[character-events.ts](app/runtime/handlers/character-events.ts) 负责强制目录刷新、主进程认领及认领后的失效回执，播放器负责加载和开播失败。去重、有效期、started 与终态定义归 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。主窗口使用侧边伙伴，实际显隐从带版本主进程快照读取，精灵宿主禁后台节流，不能用页面可见性判断隐藏。

### 打扰与自主行为

档位、活动、偏好和空间策略由 character 裁决；仅宿主拥有完整活动信号并推送生效档位，其他窗口经 storage 同步偏好和临时安静。活动覆盖只将游戏／全屏沉浸映射到静止，IDE／阅读专注不压档。

[activity.ts](modules/character/activity.ts) 快照单飞，停止使旧结果失效，成功上报才消费变化；只报粗粒度信号，不传应用名或标题。自主请求与结果消费都复用舞台可见性，另复核档位、锁屏、智能开关和服务代次；隐藏、收起或锁屏丢弃迟到意图。政策与体验归 [主动陪伴](../../docs/DESIGN.md#主动陪伴)。

### 直接交互与命中

[sprite-stage.tsx](app/components/sprite-stage.tsx) 持手势和捕获；[gesture.ts](modules/character/sprite/gesture.ts) 将整体形变交给容器，情绪放大只作用形象层。光晕、形变和透明留白不扩大像素命中。

- mousemove 时先捕获，异步遮罩更新后静止指针也重判；相同捕获状态复用在途或成功 IPC，失败可重试，穿透释放防抖。
- 按下后持有指针和窗口捕获，取消、失焦、隐藏及卸载统一释放，取消不触发点击或拖拽反馈；单击等待双击判定。
- 命中按捕获 ID 分组：精灵窗 0，主窗口和交互桌面经 `CaptureWindowIdContext` 使用 1，Portal 随所属窗口；主窗口不能挂载 ID 0 的捕获循环。
- 菜单只登记自身区域，关闭事件不穿透成手势。拖拽等反应池经 `speakScripted`，不直连语音引擎。

### 偏好与形象水合

水合只恢复偏好，不把设备生效值回写。角色卡 store 管已保存资料，页面持草稿；事件、聚焦和分析轮询不覆盖局部编辑。外观预览绑定不可变路径，新图未就绪不确认，超时保留草稿身份。

全身参考复用在途读取并守卫换号、头像和卸载；历史索引按账户持久化，缩略图经磁盘缓存。服务端会替换清理草稿，水合须用 `preferCache` 预取。衣柜的替换政策由后端校验，renderer 只呈现与提交。

### 空间行为

[spatial.ts](modules/character/spatial.ts) 装配位置 store、空间策略和舞台生命周期；`spatial/geometry.ts` 计算几何，`motion.ts` 持移动与缩放 RAF，`peek-controller.ts` 持探身准备与表达返回目标，`window-tracker.ts` 持外部窗口跟踪。[spatial-peek.ts](modules/character/spatial-peek.ts) 计算绘制、命中共用的遮挡矩形，[autonomy.ts](modules/character/autonomy.ts) 解释云端意图。默认比例经主进程跨窗同步并平滑生效。

拖拽、换包和卸载撤销探身准备；素材解码后才一起应用位置、遮挡和命中，失败保留旧画面，表演移出遮挡后才上报 started。隐藏／最小化暂停移动、漫游、探身及跟踪，恢复重新约束。

云端 stay／失败不启动本地漫游，智能关闭后的本地规则仍要求真实空闲信号。

## 会话与媒体

### 会话与草稿

每个会话 runtime 显式装配唯一状态、消息流、提交队列与历史恢复控制器，纯消息规则、历史投影与合并独立于界面。`chat-store.ts` 只管会话选择、runtime 注册与引用回收；视图、执行轨迹和参数页直接持有所属 runtime，不通过全局当前会话投影转发异步操作。

会话历史、待发批次与流式数据归 runtime，视图持输入草稿；挂载先水合所属 runtime 的预设、权限与设置，再投影历史。主进程为每次合并读取分配本地快照代次，广播与 HTTP 返回共用代次，视图只应用一次；事件水位只判断帧是否被覆盖。首次水合保留在途回合，历史修改使旧回包失效。快照与重连处理归 [Client 历史同步](../README.md#历史同步)，连接重放游标由主进程持有。

- 附件绑定加入时的会话，视频上传完成才允许发送，失效回包不回填。编辑与普通草稿分开，编辑文本不解析 Slash，成功消费修订事件；撤回附件为单槽，多图只回填第一张，其余提示只在发起窗口显示。
- [preset-labels.ts](modules/conversation/preset-labels.ts) 以字典翻译系统预设；后端目录为中文，中文字典须同步，未知项回落目录值。目录查询在同网关、同清理代次共享在途和成功缓存。
- 只读及历史操作权限消费 `info.kind` 与 `info.is_automation`；主陪伴会话由 `GET /api/sessions/main` 确定，[conversation-state.ts](modules/conversation/conversation-state.ts) 持有并持久化固定入口与主动消息的目标 ID，列表和投递事件校准同一值。持久 ID 不代表当前窗口已水合。
- 会话参数呈现后端生效值，只接受当前会话最新保存结果；斜杠目录仅服务自动补全与确认，实际派发由后端判定。
- 公共对话页按所属 runtime 水合的 `info.system_preset_id` 选择陪伴或专业呈现，用户拆泡使用同一归属，不依赖主会话 ID 或列表是否加载；任务会话只读，轻语固定使用主陪伴会话且不装参数面板。主对话草稿按会话隔离，与轻语使用独立作用域；导航、草稿和阅读位置接入模式交接快照。

### 语音播放

speech 持唯一播放队列与台词合成，点播和自动连播共用队列；[conversation-speech.ts](app/workflows/conversation-speech.ts) 把稳定消息 ID、气泡索引和会话映射到收听记录，临时组件 ID、签名 URL 不作持久键。乐观进度与主进程版本快照合并，不回退已听状态或当前断点。

新语音入队与点播要求视图具备活动资格；已开始的下载、播放与已入队的连播只要求所属会话仍有可见视图，失焦或展开菜单不打断。视图登记变化合并到同一轮结束后检查，避免主对话与轻语交接时误判隐藏。挂载、真实可见、锁屏和录音使播放资格失效时，旧下载与播放回包作废，显示聊天不重新启动队列。结果区分完成、中断和失败，声音抢占不标记不可用。AudioContext 预热后保持 running，避免 suspend／resume 与 MediaElementSource 重路由叠加丢首帧；语音准备态使用成对引用计数。

保存语音与重试归 [语音契约](../../docs/PROTOCOL.md#语音保存与重试)。预制反应和音色试听用 `speakScripted`；朗读清理不修改聊天原文。限额与字节缓存由主进程统一执行。

### 媒体查看

查看器按窗口挂载，经端口打开；图片复用共用灯箱，主窗口的浮层与命中限内容面板。`MEDIA:` 在实时和历史投影移除，跨 chunk 保留待解析前缀，结构化字段才是展示来源。图片卡和查看器共用同 URL 的在途与成功解析，失败可重试；临时 URL 按 MIME 创建，卸载回收。

## 记忆与场景

伙伴分类使用容器宽度切换侧栏与可换行顶部按钮，详情返回所属功能列表。资料、角色卡和记忆采用显式保存；[companion-editor-state](app/features/companion/companion-editor-state.ts) 按账户持有编辑起点和草稿，并参与主进程视图交接，后台刷新不覆盖草稿。记忆页按预设保留搜索、分页、选中项与其他未保存草稿，服务端返回筛选总数；个人资料按整批字段保存。日记 store 只管日记，记忆编辑页面不归该 store。

[scene-store.ts](modules/scene/scene-store.ts) 分别持当前环境、创建任务、重生成与分页库，按后端版本、请求及账户代次判活。详情写请求按场景保留在途与未知结果状态，离页不释放提交锁；未知结果须刷新核对或明确结束本地等待，账户清理使旧回执失效。详情按 ID 单读，进入详情时先更新任务状态和恢复入口，再异步加载图片，旧读取不得覆盖最新结果；仍在处理时由轮询同步全局状态，等待较久后降低轮询频率，状态读取本身不重试分析或生图。读取失败保留当前页面状态并提供重试入口。新图预加载成功才替换背景，失败保留旧图但更新任务和政策。不可变图片共享在途与成功解析，图片失败单独重取并校验资产；新场景按固定画幅交付，生成与上传不读取显示器尺寸。任务与激活定义归 [场景契约](../../docs/PROTOCOL.md#场景任务与原位换图)。

## 动作素材呈现

- 每套着装对应唯一动作组，衣柜先选着装再进入动作列表。请求、进度与错误按着装／包／动作归属，单动作忙碌不禁用其他动作；结果未知仍视为未结束，不能再次提交。重做候选与当前素材分别展示，采纳后刷新目录，待确认或失败继续使用已采纳素材。
- 网络和持久 manifest 同样校验类型、默认动作、遮罩及包／目录版本，非法目录保留可用画面；片段经 `apiAsset(preferCache)`，目录和遮罩经 `apiAssetBuffer`。同版本共享在途与成功结果，失败释放缓存，账户清理阻断旧回写。
- 双缓冲等待图片解码或视频首帧再换图；[MediaStage.tsx](modules/character/rendering/video/MediaStage.tsx) 按内容轮廓适配侧栏，绘制与命中扣除同一等比留白。图片静态 alpha、视频逐帧遮罩，缺遮罩时侧栏命中限已知内容边界。
- 气泡与轻语用整段素材合并的上部轮廓锚定头部，避免逐帧抖动，轮廓更新后重算屏幕避让。
- 图片只呈现基础动作，动态表达仅视频；移动由容器位移，不从播放推断嘴部或视线。[render-resolver](modules/character/presentation/render-resolver.ts) 选择媒体或 fallback，蛋区分准备、生成、失败及未就绪。
- 自定义动作展示同着装各画面的制作状态，当前版本未制作时提供显式生成入口；水合与进度刷新不触发付费制作。关联与分别制作语义见 [动作目录与播放](../../docs/PROTOCOL.md#动作目录与播放)。

## 主题与玻璃效果

控件消费 panel 和语义 token，首帧主题遵守 Client 规则，弹层命中随拖动更新。减透明由 OS、设备、用户偏好和帧预算共同决定，仅用户偏好上云；监视器随表面释放，后台时间不计性能判断。场景预模糊按图、尺寸和主题失效，避免再次 CSS 模糊，降级保持圆角外透明。

## 验证入口

命令归 [Scripts](../../scripts/README.md#按改动选择验证)。重点覆盖换窗、换号、卸载、迟到回写、声音资格、播放抢占、静止指针命中与 GPU 回退；原生几何、透明和焦点效果须在对应平台验证，绘制与命中共用最终坐标。
