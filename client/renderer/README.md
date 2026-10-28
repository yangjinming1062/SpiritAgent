# Client 渲染层

渲染层负责页面状态、会话投影、媒体播放与交互。产品体验归 [DESIGN](../../docs/DESIGN.md)，跨端交付归 [PROTOCOL](../../docs/PROTOCOL.md)，磁盘缓存归 [Client](../README.md#资产与历史缓存)。本文维护代码分层、状态所有者及异步资源约束。

## 分层与目录

| 层 | 边界 |
|---|---|
| `app` | 窗口外壳、页面、导航与跨域编排；runtime／workflows 不反向导入 windows，窗口实现不互相导入 |
| `modules` | 各业务状态与能力，不依赖 app；conversation 可使用 media 展示原语，其余跨域协作由 app 装配 |
| `shared` | 无业务依赖的组件、端口与平台适配 |
| `@ipc` | 跨进程真源，renderer 不复制类型 |

跨模块走公共入口，app 另可使用 `character/rendering`；模块内用相对路径。只有 [desktop.ts](shared/platform/desktop.ts) 访问 preload 全局桥，其他代码消费窄接口；后端请求与资产经主进程，直连例外须注明 URL 来源。[架构门禁](../scripts/architecture-rules.mjs) 解析真实路径、窗口边界和运行时依赖环，纯类型引用不算运行时环。

| 修改任务 | 入口 |
|---|---|
| 窗口装配与账户恢复 | [sprite.tsx](app/bootstrap/sprite.tsx)、[surface.tsx](app/bootstrap/surface.tsx)、[account-lifecycle.ts](app/workflows/account-lifecycle.ts) |
| 网关事件 | [gateway-runtime.ts](app/runtime/gateway-runtime.ts)、[gateway-event-router.ts](app/runtime/gateway-event-router.ts)、[handlers](app/runtime/handlers/) |
| 引导、激活与恢复 | [onboarding](app/onboarding/) |
| 精灵、手势与空间 | [character](modules/character/)、[sprite-stage.tsx](app/components/sprite-stage.tsx) |
| 生活浏览 | [life](modules/life/)、[生活页面](app/features/life/) |
| 对话、语音与执行过程 | [conversation](modules/conversation/)、[speech](modules/speech/)、[conversation-speech.ts](app/workflows/conversation-speech.ts) |
| 资料、声音与记忆 | [companion](app/features/companion/) |
| 衣柜、环境、动作与影像 | [appearance](app/features/appearance/)、[scene](modules/scene/)、[actions](app/features/actions/)、[visuals](modules/visuals/)、[影像页](app/features/visuals/visuals-page.tsx) |
| 动态、日记与设置 | [posts](modules/posts/)、[diary](modules/diary/)、[settings](app/features/settings/) |
| 通用 UI 与文案 | [panel](shared/panel/)、[components](shared/components/)、[字典](shared/strings/dictionaries/)；跨端中性文案与错误分类归根 [shared](../../shared/) |

## 装配与状态归属

窗口通过 `app/bootstrap` 装配账户、网关和 Provider，共用 `app/features`、`app/navigation` 与 [app-content-page.tsx](app/components/app-content-page.tsx)。[bind-presentation.ts](app/bootstrap/bind-presentation.ts) 在首次渲染前注入窄端口，未绑定端口调用立即失败；不能借 barrel 导入副作用装配资源。

| 状态 | 所有者 |
|---|---|
| 会话历史、流式回合、待发批次 | conversation runtime；`chat-store.ts` 管选择、注册与引用回收 |
| 音频队列与合成 | speech；conversation 持投影，app 工作流连接两者 |
| 表现优先级、偏好与空间 | character；窗口不另造状态机 |
| 开窗、附件信箱、显示资格 | 主进程；来源窗口不能只写本地 store |
| 跨模式页面、草稿、筛选与阅读位置 | 主进程账户快照；[app-ui-state.ts](app/workflows/app-ui-state.ts) 捕获、恢复并确认交接 |

[conversation-activity.ts](app/workflows/conversation-activity.ts) 汇总会话与本机工具活动，声音和瞬态仍由 character 裁决。订阅、注入和计时器随所属运行态释放。

## 事件与异步生命周期

`app/runtime` 先订阅再读快照，网关按账户与 `session_id` 把事件路由给保留的 runtime。业务请求经 [resource-request.ts](shared/lib/resource-request.ts) 传稳定操作标识，窗口卸载不关闭主进程网络。

- store／工作流用 [captureAuthScope](shared/lib/authed-api.ts) 守卫鉴权会话及清理代次，组件用 [use-async-guard](shared/hooks/use-async-guard.ts) 守卫挂载和请求代次。旧回包不得写入新账户、新查询或新草稿。
- [storage.ts](shared/lib/storage.ts) 登记账户键和恢复回调，换号清内存并重挂载；重置期不回写索引，命名空间就绪后才应用云广播。`runner-status.ts` 的快照不能覆盖较新事件，握手超时须进入可恢复错误态。
- 用户提交保留稳定请求身份，响应不明先查回执，停止绑定具体回合。挂载先应用快照再按水位合并事件；缺少流基线不应用增量。媒体终态和历史失效见 [历史同步](../README.md#历史同步)。
- 未读确认取所属视图的可见性。动态、日记各自消费服务端镜像；[proactive-delivery.ts](app/workflows/proactive-delivery.ts) 仅对合格舞台发气泡或声音，不可见消息仍进入未读，工作会话不跳入轻语。
- 错误分类经根 `error-classification.ts`，显示经 `ipc-error.ts`；业务正文错误用 `notifyError`。与状态相关的错误行内显示，动作失败用 toast，同一失败不重复呈现。

引导 flow 只装配导航及未提交表单，`onboarding-draft.ts` 串行保存并按服务端 `next_field` 恢复，本地编辑优先。读取失败暂停重试，不能当作新引导覆盖。头像和声音各自持有异步制作状态。

制作组件只展示业务方提供的阶段和恢复动作，不发请求、不推测进度。`CreationWorkspace` 分离正文与操作区，`CreationFeedback` 避免重复按钮阶段，`CreationActionBar` 不自行吸附；确认与焦点由 `use-dialog-focus` 和 `ConfirmDialog` 管理。未知结果的本地等待结束不代表服务端取消。

## 主窗口与桌面装配

主窗口外壳装配独立生活区，聊天组件不读取生活数据；桌面生活和聊天分别使用内部窗口。共用页面的筛选、草稿及阅读位置参与账户快照，来源视图在目标确认恢复后才释放资格。桌面内部窗口集合仅在本次会话有效，不跨启动恢复。

[panel-activity](shared/context/panel-activity.tsx) 把活动资格传入子面板和 Portal，子级不能解除父级禁用。切页保留对话实例及草稿，但关闭录音和新播放资格；旧麦克风或转写回包不能因重新激活而复活。查看器归发起视图，离开来源页释放。参考图片由 [creation-draft-image.ts](shared/lib/creation-draft-image.ts) 按账户保存并跨窗通知，不等同服务端成品。

[desktop-visual-wallpaper.ts](app/windows/desktop/desktop-visual-wallpaper.ts) 消费呈现状态、认领和回执；静态回执共用 `visuals-store.ts` 的目标检查，按请求、呈现代次和状态修订去重。回执冲突不当作图片失败，服务端退避期间不重复上报。缓存与预加载使用成品 ID、版本及去签名不可变路径，不用签名 URL 作持久键。播放语义归 [呈现契约](../../docs/PROTOCOL.md#影像成品与模式隔离)。

[背景播放器](app/windows/desktop/desktop-background-entry.tsx) 双缓冲等实际首帧，切换先预加载；短动作只能回到仍有效的持续画面。环境层与透明主体层（`actor` 字节，WebM）独立接收与更换，主体层短动作结束后回常驻循环；预览、播放和过渡共用 [desktop-media-geometry.ts](../shared/desktop-media-geometry.ts)，媒体失败保留当前画面并释放临时 URL；主进程同时核对账户与选中路径。

桌面根组件持窗口状态，Dock 菜单共用互斥态和面板代次。旧启动回执不能关闭新面板，失焦卸载也不丢失败反馈；固定标记刷新复用当前目录版本，手动重扫才换句柄。图标按可见条目分批读取。下拉框留在面板 DOM 内，避免原生弹窗导致失焦收起。

几何使用主进程共享的 `desktop-layout.ts`。底板装饰与向上弹层分开，内部窗口保留 `contain: layout`；光环和声波只变透明度／缩放，不消费录音音量、不扩大命中。命中区仅含实际控件、弹层与连接间隙，空白穿透。桌面 SVG 经 Vite `BASE_URL` 加载。外观和窗口交互见 [桌面模式](../../docs/DESIGN.md#桌面模式)。

### 生活读取与浏览

[生活模块](modules/life/) 持只读副本，生活区和生活页共用状态／聚焦事项的合并读取；列表、详情与事件守卫查询代次。新查询清旧内容，同查询刷新失败保留内容。事件、连接恢复及实际可见性触发刷新，可见生活窗不要求置前，读取不推进判断或制作。

生活记录按已展开范围合并主体、类型和结果，跨页重复成果只展示一次。筛选、事项、展开范围与实际滚动位置参与模式交接；后台刷新不打断阅读，资料页不另持近况副本。事项调整经聊天，步骤、等待原因及阅读范围来自后端，事实边界见 [生活契约](../../docs/PROTOCOL.md#生活状态与生活事件)。

## 角色呈现契约

### 状态与动作

[companion-store.ts](modules/character/companion-store.ts) 统一表现优先级、声音和程序反馈。精灵与生活区消费统一舞台计划的主体层（透明视频，缺失时退服务端冻结的静态投影或独立静态素材），资格由主进程快照、本窗显隐和舞台相交区域共同决定；收起或滚出视口不能报已呈现。完整影像由桌面播放器管理。

### 打扰与自主行为

仅精灵宿主持完整活动信号并上报生效档位，其他窗口同步偏好和临时安静。[activity.ts](modules/character/activity.ts) 单飞读取，停止使旧结果失效，成功上报才消费变化；只传粗粒度状态，不上传应用名或标题。游戏／全屏可压为静止，IDE／阅读专注不压档。自主判断归 Backend，renderer 不用本地计时器另造生活；已有静态目标恢复与主动重选的区别见 [呈现契约](../../docs/PROTOCOL.md#影像成品与模式隔离)。

### 直接交互与命中

[sprite-stage.tsx](app/components/sprite-stage.tsx) 持手势和捕获，[gesture.ts](modules/character/sprite/gesture.ts) 将整体形变交容器、情绪缩放交形象层。绘制和命中共用最终 alpha，留白与装饰不扩大范围。

按下后持有指针及窗口捕获，取消、失焦、隐藏或卸载统一释放，取消不触发反馈；单击等待双击判定。mousemove 先捕获，异步遮罩就绪后重判静止指针，相同状态复用在途 IPC，失败可重试，穿透释放防抖。精灵捕获 ID 为 0，主窗口／交互桌面及其 Portal 经 `CaptureWindowIdContext` 使用 1，不能混挂捕获循环。菜单仅登记自身区域，关闭不穿透成手势；预制反馈经 `speakScripted`。

### 偏好与形象水合

水合恢复偏好，不将设备生效值回写。角色卡 store 管已保存资料，页面持编辑起点及草稿，事件与轮询不覆盖编辑。外观候选按不可变路径校验，新图未就绪不确认；全身参考共享在途读取并守卫账户、头像及卸载，草稿清理前按 `preferCache` 预取所需素材。

### 空间行为

[spatial.ts](modules/character/spatial.ts) 装配位置、比例与舞台，`spatial/geometry.ts` 计算几何，`motion.ts` 管 RAF。默认比例经主进程跨窗同步。窗口位置由用户拖拽及直接交互改变，生活和影像判断不驱动窗口移动；隐藏、最小化或锁屏停止交互，恢复后重新约束几何。

## 会话与媒体

### 会话与草稿

每个会话 runtime 显式装配状态、消息流、提交队列和历史控制器；视图、执行轨迹及参数页直接持所属 runtime，不经全局“当前会话”转发异步结果。挂载先水合预设、权限和设置，再投影历史。本地快照代次用于去重，事件水位仅判断覆盖关系，两者不能混用。

附件绑定加入时的会话，视频上传完成才允许发送；编辑与普通草稿分开，编辑不解析 Slash。撤回附件为单槽，多图只回填首张，其余提示仅发起窗口可见。主对话草稿按会话隔离，轻语另有作用域。

预设名由 [preset-labels.ts](modules/conversation/preset-labels.ts) 翻译，目录查询按网关与清理代次共享缓存；只读与历史权限取后端 `info.kind`、`info.is_automation`。主陪伴 ID 由主会话接口校准并由 `conversation-state.ts` 持久化，保存 ID 不代表视图已水合。对话外观按所属 runtime 的预设选择，不能靠列表或主会话 ID 猜测。参数只采纳当前会话最新响应，Slash 目录只服务补全，执行由后端判定。

### 语音播放

speech 持唯一合成与播放队列，自动和点播共用。[conversation-speech.ts](app/workflows/conversation-speech.ts) 用稳定消息 ID、气泡索引及会话映射收听记录，临时组件 ID 和签名 URL 不持久化。乐观进度与主进程版本合并，不回退已听状态或断点。

新入队要求活动资格，已开始播放及连播只要求所属会话仍有可见视图；失焦或菜单不打断。视图登记变动在同轮末合并，防止窗口交接误判隐藏；锁屏、录音或真正隐藏使旧下载和播放回包失效，重新打开不自动重启队列。结果区分完成、中断和失败，抢占不等于不可用。AudioContext 预热后保持 running，避免 suspend／resume 与重路由叠加丢首帧。预制反馈和试听使用 `speakScripted`，保存与重试归 [语音契约](../../docs/PROTOCOL.md#语音保存与重试)。

### 媒体查看

查看器按窗口挂载，经端口打开，图片共用灯箱并限制在所属内容面板。正文投影移除 `MEDIA:`，跨 chunk 保留待解析前缀，结构化字段才是展示来源。图片卡和查看器共享同 URL 的解析，失败可重试；对象 URL 按 MIME 建立并随所有者释放。

## 记忆与场景

[companion-editor-state.ts](app/features/companion/companion-editor-state.ts) 按账户保存资料／角色卡编辑起点与草稿并参与交接。记忆页按预设保存搜索、分页、选中和其他未保存条目，个人资料按批保存；日记 store 不管理记忆编辑。

[scene-store.ts](modules/scene/scene-store.ts) 分别持环境、任务和分页库。详情写入锁随任务而非页面存活，未知结果先回源核对，迟到读取不能覆盖新任务。详情先刷新恢复状态再加载图片，长等待降低轮询频率，查询不重试分析或生图。新图预载成功才换背景，失败保留旧图但更新任务和政策；环境固定画幅，不读取显示器尺寸。恢复与发布边界见 [场景契约](../../docs/PROTOCOL.md#场景任务与原位换图)。

素材库共用 `asset-library-header.tsx` 与 `asset-library-card.tsx`，公共件只接数据和回调。衣柜本地筛选，环境服务端提交搜索，两者不能共用隐含的查询状态。

## 主体舞台与静态呈现

[design-session.ts](modules/character/wardrobe/design-session.ts) 按草稿持有制作、确认和未知状态，离页仍收敛原任务；完成不抢选其他草稿，按不可变路径核对结果后回收空闲会话。参考图在取得新设计 ID 时消费，迟到预览不能清理后续输入。

窗口主体画面优先播放透明主体视频：[stage-plan.ts](modules/visuals/stage-plan.ts) 解析有效组合与当前造型可复用的待机成品；只有认领成功的临时指令覆盖基础主体，结束后恢复仍有效的循环。[visual-presentation.ts](app/workflows/visual-presentation.ts) 经 [window-media.ts](modules/character/window-media.ts) 装配窄端口，character 不依赖影像库。[CharacterStage](modules/character/rendering/character-stage.tsx) 与桌面主体层共用 [stage-video-layer.ts](shared/lib/stage-video-layer.ts)：新首帧就绪前保留旧画面，失败恢复有效循环或静态造型，一次性动作按独立指令身份消费。命中读取当前帧 alpha，几何与头部、脚底锚点按实际画幅适配；环境与主体独立换图，完整影像不叠加第二份伙伴。

客户端表现导演（[director.ts](modules/visuals/director.ts)）由唯一舞台宿主装配（窗口为精灵窗、桌面为桌面窗）：常驻循环在服务端 `ambient_catalog` 内本地轮换（时长 + 冷却 + 概率权重，轻量档偏向待机），不建播放账本；互动反应与生活意图相符的持续画面切换经播放端点走完整指令管线（认领与回执沿既有通道）。导演不依赖其他业务模块——登录与动态表现偏好由装配方注入。互动入口：精灵单击入队 `look`，`companion.reaction` 事件（聊天后模型推断的身体反应）按内置动作键入队；队列有界、冷却独立。动态表现偏好（`companion.motion_level`）与打扰档位独立（[prefs.ts](modules/character/prefs.ts)），off 仅静态、subtle 保留安静循环；环境层按本地时段加轻量色调（[day-phase.ts](shared/lib/day-phase.ts)），不创建生活经历。

视频下载和磁盘缓存由主进程按账户共享；[stage-video-cache.ts](shared/lib/stage-video-cache.ts) 合并本窗口对象地址，每个可见舞台自行解码，引用释放与换号使迟到加载失效。窗口与桌面只在实际播放后回执，首帧预载或取得地址不算开播；短动作结束才完成，普通待机循环不逐圈记账。桌面按指令目标由一个图层回执，主体失败仍可显示冻结造型。锁屏、隐藏、减少动态和全屏时保留静帧并暂停解码推进。

边缘栖息在有效拖拽结束后贴边；取消不触发吸附。轻量移动由客户端执行，受档位、减少动态、全屏、可见性与交互状态约束，不创建生活经历。

## 主题与玻璃效果

控件使用 panel 与语义 token，首帧主题遵守 [Client](../README.md#窗口与主题)。减透明由系统、设备、偏好与帧预算共同决定，仅用户偏好上云；监视器随表面释放，后台时间不参与性能判断。弹层命中随拖动更新，透明装饰不能侵占内容可读区域。

中英文显示件由字典校验；引导问答、人格预设和部分模块错误仍有中文硬编码，不能仅以 `en` 类型检查宣称全量本地化。

## 验证入口

按 [Scripts](../../scripts/README.md#按改动选择验证) 运行检查。重点覆盖换窗、换号、卸载、迟到回写、声音资格、静止指针命中和 GPU 回退；几何、透明、焦点及真实媒体播放必须在相应平台验收。
