# Renderer 架构指南

SpiritAgent 桌面渲染层的唯一架构文档：分层与依赖规则、跨模块接缝、事件路由、各模块契约要点，以及尚未收敛的归属与收敛判据。产品体验见 [DESIGN.md](../../docs/DESIGN.md)，客户端全局见 [client/README.md](../README.md)，跨进程契约见 [PROTOCOL.md](../../docs/PROTOCOL.md)。

## 1. 分层与目录归属

渲染层分四类目录，设计目标是让「改一处」的影响半径最小：业务能力可被三个窗口复用，跨能力协作只发生在一个位置，渲染实现与业务状态互不渗透。

- `app/` 应用组合层——把模块能力组合成窗口体验：`bootstrap/`（入口装配与端口绑定）、`runtime/`（网关事件路由、宿主 / 代理运行时）、`workflows/`（跨模块业务流程）、`windows/`（精灵、生活空间、工作台）、`onboarding/`（引导界面与流程）。
- `modules/` 可复用业务能力——`conversation`（会话与消息）、`character`（角色呈现，含 `rendering/2d`、`rendering/3d` 渲染域）、`speech`（语音合成与播放）、`media`（媒体预览与资源生命周期）、`memory`（片刻与日记状态）、`room`（房间背景）。
- `shared/` 无业务共享代码——UI 组件、hooks、主题、字符串、存储与网关协议库。跨进程契约仍在 `client/shared/ipc/`（`@ipc`），与本目录职责不同。
- 窗口入口（根目录 `main.tsx`、`sprite-entry.tsx`、各 `app/windows/*/*-entry.tsx`）保持薄入口，只做初始化调用与组件挂载。

## 2. 依赖规则

| 调用方 | 允许依赖 | 禁止依赖 |
|---|---|---|
| `app/**` | 所有模块的公共 barrel、渲染域入口、shared | runtime / workflows 不得导入 `windows/`；三个 `windows/` 互不导入 |
| `modules/*` | 本模块、shared、跨进程契约 | `app/`、其他同级模块（白名单例外见下） |
| `character/rendering/**` | character 公共 barrel、speech（口型振幅） | `app/`、conversation / media、character 深路径 |
| `shared/**` | 纯共享代码、第三方基础库 | `app/`、任何业务模块 |

- 白名单例外（均为文档化的只读边）：`conversation` → `media`（气泡内媒体卡消费展示原语与媒体源解析）；`character/rendering` → `speech`（`registerAmplitudeSink` 订阅播放振幅驱动口型）。
- 跨模块访问只走目标模块的公共 barrel（`@/modules/*`）与 character 渲染域入口（`@/modules/character/rendering/2d`、`/3d`）；ESLint `no-restricted-imports` 对上述全部规则实施检查，新增边界约束时同步本节。

## 3. 跨模块接缝与绑定纪律

模块之间不直接导入，协作通过三类注入式接缝完成，全部由 `app/bootstrap/bind-presentation.ts` 在**入口渲染前**显式绑定：

- **呈现端口**（`shared/presentation-ports.ts`）：conversation 读取形象状态（头像、响应模式、锁屏）与 TTS、媒体查看器入口；character / speech / media 提供实现。端口保持窄接口，新增字段需说明为什么不适合接缝或工作流。
- **语音接缝**（`modules/conversation/voice-link.ts`）：语音条播放队列的真相在 `modules/speech`，消息中的 `voiceStatus` / `voiceDuration` / 播放中状态只是 UI 投影；投影与控制由 `app/workflows/conversation-speech.ts` 装配（`bindVoiceBarProjection` + sink / control 两个窄接口）。
- **槽位模式**：窗口或工作流能力需要被模块反向使用时，在模块侧定义最小槽位、由 bootstrap 绑定实现——如 `modules/character/proactive-speak.ts`（仪式行走台词 → proactive-delivery 工作流）、`app/workflows/session-delivery.ts` 的 whisper opener（通知跳转 → 轻语卡片）。

绑定纪律：端口绑定不得依赖模块 barrel 的副作用导入顺序（模块循环初始化会让隐式顺序变成启动崩溃）；登出、窗口关闭与开发期重新挂载都必须能清空全部订阅（`registerStorageClearHandler` 链）。

## 4. 事件路由与多窗口运行时

- `app/runtime/gateway-event-router.ts` 只做分派与公共守卫：鉴权 `pending` 丢弃、`session_id` 闸门、代理窗口过滤。状态更新按能力在 `handlers/` 组织：`conversation-events`（message.* 与 slash / 压缩 / 撤回）、`character-events`（心情、具身表达、模型与 2D 拆分、衣柜、头像）、`delivery-events`（主动消息、通知、视频任务、IM 通道）、`tool-dispatch`（宿主专属 Runner 分发）。
- **`tool.call` 是用户级设备指令**（PROTOCOL §1.3）：信封不带 `session_id`、按 `call_id` 关联，重放帧按 `call_id` 去重（本机副作用不可撤销）；`headless` 指令照常执行但不驱动精灵工作态。这套宿主专属逻辑只在宿主运行时存在，代理窗口在装配层即过滤 `tool.call`。
- 连接角色：精灵窗宿主持唯一 WebSocket（`app/runtime/host-runtime.ts`，鉴权后挂载、登出即卸载拆链）；生活空间 / 工作台经主进程代理共享连接（`app/runtime/proxy-runtime.ts` 事件泵 + `IpcGatewayProxy`）。**复用同一份模块代码不等于共享内存**——各窗口分别水合运行时状态，跨窗口变化走既有 IPC、网关事件与水合机制；异步副作用（语音合成、历史水合、草稿回填）必须校验所属会话与回合，切换后不得写入错误会话。

## 5. 目标归属与收敛路径

以下差异是已知且有意暂缓的收敛项；改动对应区域时按判据推进，不做无调用方的预备抽象。

- **会话表现输入化**：conversation 目前在提交、语音录入与回合事件中经呈现端口下达 `thinking` / `listening` / `idle` 命令，优先级裁决仍由 character 状态机承担。收敛方向是 conversation 只暴露「回合正在生成 / 用户正在说话」等事实，由待建的 presence 工作流（`app/workflows/conversation-presence`）转换成 character 的表现输入（`createInput` + `dispose`），废弃端口上的 `setSpriteState`。判据：表现命令出现在第三个调用方、或需要跨模块优先级仲裁时启动。
- **preferences 模块**：`prefs.ts` 现驻 character，因为它与打扰档位状态机（`companion-store` 的 `$userPreferredTier`）双向耦合。收敛前需先把「生效档位 = 偏好 + 活动覆盖」的裁决移入单一所有者。
- **platform 层**：`shared/lib/` 中的网关协议、IPC 代理、资产桥与 OPFS 缓存本质是平台适配。当前只有一个消费者面（渲染层），抽出 `platform/` 是纯改名；当出现第二个实现（如非 Electron 壳）或需与 main 共享时再抽。
- **character / conversation 内部细分**（model / services / ui）：内部组织随文件规模增长再分，模块边界（barrel + ESLint 规则）已锁定，内部重排不影响依赖图。
- **external-attachment 工作流**：精灵投喂目前由 sprite 窗口拖拽行为经 conversation 公共入口直达，单一路径不抽工作流；出现第二条投喂路径（如跨窗口信箱直投）时收敛。

## 6. 角色呈现契约（modules/character）

### 动画状态机（8 态）

| 状态 | 优先级 | 触发源 | 持续 |
|---|---|---|---|
| `disconnected` | 100 | Backend WS 断连 | 持续；恢复需 WS 重连 |
| `interacting` | 80 | 用户戳 / 拖 / 悬停 | 瞬态 0.5–3.0s（按交互类型定长），回到 `previousState` |
| `working` | 70 | 用户活动 ≥ 6 次/10s | 持续；10s 无活动 `force: true` 回 `idle` |
| `speaking` | 60 | 伙伴发起的 TTS 播放（生活空间语音条自动或点按播放、主动消息）；默认文字模式的尾随播放按钮不进入此状态，口型由音频振幅直驱 | 与 TTS 音频等长 |
| `thinking` | 50 | LLM 流式响应开始 | 持续至 `message.complete`（始终语音模式下持续至语音条就绪） |
| `listening` | 40 | 用户开始输入 | 持续至用户停止输入或后端响应 |
| `emotional` | 35 | 自主 `companion.affect` 事件到达 | 瞬态 2.5s，回到 `previousState`（**叠加非抢占**） |
| `idle` | 10 | 默认 | 持续；10–25s 随机切 IDLE 变体 |

状态切换规则：

- **低优先级不能打断高优先级**（`setSpriteState(name)` 默认 `force: false`）——已在 `working` 时调用 `setSpriteState('idle')` 被门控逻辑直接吞掉。需强制回退必须传 `{ force: true }`（working 状态 10s 无活动自动 force 回 idle）。
- **`emotional` / `interacting` 是叠加而非抢占**：进入前若当前不是这两个状态，原子 `$previousState` 记录原态；瞬态 timer 结束后回到 `previousState`（若 prev 也是 emotional/interacting，则回 `idle`）。
- **crossfade ~250ms**：clip 切换通过 sprite-stage 的 fade 层处理，避免硬切。

生活空间语音条：在「始终语音」模式下，流式生成阶段气泡仅展示「正在输入...」；气泡收尾后对整段正文一次性发起 TTS 合成并落盘缓存，等待期间保持 thinking 状态（左栏同步「在想事情」）。语音条就绪后展示并自动起播，起播时进入 speaking 状态（左栏同步「在说话」）；多气泡连发按到达顺序接播；遇到停止、异常、提交新消息或锁屏即刻中止并守卫复位。聊天完成帧只收尾文本、媒体与语音状态，不触发 EMOTIONAL。语音播放结束或被打断后直接复位 `idle`；桌面视觉表达由独立 `companion.affect` 事件驱动，不等待聊天或 TTS。

### 情绪表达与当前心情

情绪枚举固定为 21 项内置（不含 `neutral`；Backend 权威 `services/companion/emotions.py` 的 22 项含 neutral）。自主视觉引擎只返回白名单 token；`neutral` 本身不触发 EMOTIONAL，只有动作时作为动作状态的中性基调。情绪只驱动现有模型参数与动画，不存在自定义表情注册或表情图片资产。情绪的渲染分工：**肢体动画**按模型映射解析当前状态可用动画，解析与兜底规则见 [docs/PIPELINE.md §5](../../docs/PIPELINE.md)；2D 模式同时把内置情绪映射到参数化眉、眼、嘴通道；3D 模式只兑现模型已有的动作 clip。

`companion.mood {mood}` 与视觉 affect 完全独立：事件到达后直接写 `$companionMood`，生活空间左栏在头像和名字下方动态展示；它不切换动画状态、不生成聊天消息，也不受打扰档位或桌面表面显隐门控。首次水合从 Persona 的 `current_mood` 恢复。

### 三档打扰（Client 实现）

档位产品规则与生效条件见 [DESIGN.md §6.2](../../docs/DESIGN.md)。Renderer 只消费生效档位：用户偏好保存在伴生设置，活动感知器写入覆盖值，空间策略、主动消息呈现与 TTS 门控读取同一生效值；生效档位经配置管道上云（[PROTOCOL.md §2.4](../../docs/PROTOCOL.md)），是后端主动闸门的唯一档位来源。

### 3D 渲染资源降级与功耗调度

渲染栈是 `three/webgpu` 的 **WebGPURenderer + 四层回退**：WebGPU 后端 → three 内置 WebGL2 后端（同 API 面，零代码）→ 经典 `WebGLRenderer`（仅当 `init()` 整体 reject；必须换新 canvas——webgpu 上下文成功过的 canvas 要不到 webgl2）→ `EngineInitError`（程序化蛋形兜底）。`Engine.create()` 是异步工厂，canvas 由 Engine 自建自管（React 只渲染容器），companion-3d 的 load effects 一律 await 引擎就绪 Promise；实际后端写 dev log。视觉兜底层级见 [DESIGN.md §1.2](../../docs/DESIGN.md)；模型字节到达并完成解析后才视作可渲染，GLB 解析失败回退到程序化蛋兜底，3D 引擎 init 失败亦同。

**渲染功耗三档**（[3d/PowerProfile.ts](modules/character/rendering/3d/PowerProfile.ts) 判定 + [3d/power-signals.ts](modules/character/rendering/3d/power-signals.ts) 订阅，Engine 自门控循环执行）：主进程为后台流式聊天全局禁用了 Chromium 节流，浏览器不会替 7x24 常驻的精灵窗降频，所以循环在 Engine 内按信号自门控——active 60fps（speaking/thinking/listening/working/emotional/interacting）、idle 30fps（idle/disconnected）、dormant 4fps（`$screenLocked`、`document.hidden`、`$focusContext.fullscreen`）。信号全部来自既有渲染端 atom，功耗调度是纯 Client 内部决策（ARCH §7 语义/渲染解耦），无协议与主进程参与。两条防坑约束：**Ready 保护**——首个模型 `loadCharacter` 落定（`$modelLoadSettled`）前强制 active，避免孵化动画被误降频拉长；**隐藏窗口降级**——Chromium 对 hidden 窗口硬停 rAF（禁节流开关管不到合成层），active/idle 档在 `document.hidden` 时改由 16/37ms timer 驱动，`visibilitychange` 恢复 rAF。dormant 恒为 250ms timer（进程级禁 timer 节流，锁屏下稳定）；档位回升时 Engine 层把 delta 钳制到 50ms，防 mixer 在长暂停后跳变。

**性能取舍**：阴影默认关闭（300×360 精灵窗的 PBR 环境光足以体现深度，2048² PCFSoft 阴影是单项最大 GPU 成本），开启时强制 1024² PCF；MSAA 开启（4×——精灵悬浮在任意桌面内容之上，剪影锯齿是首要画质破绽，此尺寸下 resolve 成本可忽略）、贴图预乘 alpha 关闭、DPR 封顶 1.5。GLB 解析模板走 [gltf-instance-cache.ts](modules/character/rendering/3d/gltf-instance-cache.ts)：按 `contentHash` 缓存解析后的 scene + animations，切换模型时走 `SkeletonUtils.clone` 深克隆重建骨骼与蒙皮绑定并隔离 AnimationClip 状态；模板持有 GPU 资源并通过引用计数管理生命周期，实例卸载不释放共享资源；支持 LRU 淘汰并在登出时安全释放。

**OPFS 二进制缓存**：GLB 与 PSD 字节均走 [shared/lib/opfs-blob-cache.ts](shared/lib/opfs-blob-cache.ts) 共享抽象——按 `contentHash` 为键（同源私有文件系统，不是 `caches.open` 的 HTTP 缓存），元数据独立存 `<hash>.meta.json`，blob 文件 `<hash>.glb` / `<hash>.psd`。GLB（[3d/glb-opfs-cache.ts](modules/character/rendering/3d/glb-opfs-cache.ts)，maxFiles=5 / maxBytes=512 MiB）与 PSD（[mesh2d/psd-opfs-cache.ts](modules/character/rendering/2d/mesh2d/psd-opfs-cache.ts)，maxFiles=10 / maxBytes=512 MiB）共用序列化队列 + LRU 字节预算裁剪 + 魔术字节校验（PSD 验 '8BPS'）。fetch 包装层负责 IPC 拉取 + dedup + AbortSignal 串接 + 写入闸门（`authed` && `!aborted` && `clearEpoch` 未变——登出 race 期间过期 fetch 不会把陈旧字节写进刚清空的 OPFS）。第二次加载走 0 ms 落盘读。Renderer 错误隔离（`$engineError`）：Engine tick 抛错时停止 ticker 并上报，避免「每帧抛+日志洪水」循环。这些都是默认关闭或收紧后的基线，需要在 Settings 重新打开的功能必须经过实测。

### 屏锁与端忙

- [activity.ts](modules/character/activity.ts) 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 抑制主动消息文本与语音，并停止桌面视觉表达与空间智能请求；到达的 `companion.affect` 只有在自主档、桌面精灵可见且解锁时才消费。当前心情 `companion.mood` 是身份状态更新，照常刷新。
- 屏锁恢复后静默恢复；断连降级（disconnected）曾被表达过时，重连后由 boot 层平滑切回 idle 状态（动画状态机纯视觉恢复，保持静默回神，不触发口语台词与语音合成）。

### 自主行为（IDLE 时）

- **微动作**：10–25s 随机间隔切 `idle` / `idle_look_around` / `idle_blink` / `idle_stretch` scene。由 3D 引擎骨骼动画直接驱动，不需生成资产。
- **情境视觉表达**：[activity.ts](modules/character/activity.ts) 只在 `$llmAffect` 开启、生效档位为 autonomous、`$surfaceOpen === null`、屏幕解锁且空闲 ≥ 30 分钟时请求 `companion.check_affect`。返回事件包含可选 emotion 与最多 3 个 actions；Client 再按同一档位/表面/锁屏条件消费，2D 依序播放动作、3D 使用首个可兑现 clip。该链不接触聊天消息或 TTS。
- **情境动作**：基于 `$focusContext`（[activity.ts](modules/character/activity.ts) 维护）。focused-app 分类（ide/music/reader/gaming/browsing/other/unknown）按平台白名单映射（Windows 进程名、macOS bundle id）。IDLE 微动作池按分类切换：

  | 分类 | 微动作池（未就绪 fallback 到 `idle`） |
  |------|----------------------------------|
  | ide | `idle_thinking` → `idle_typing` → `idle_look_around` → `idle` |
  | music | `idle_bounce` → `idle_sway` → `idle_blink` → `idle` |
  | reader | `idle_calm` → `idle_look_around` → `idle` |
  | gaming | `idle_engaged` → `idle_stretch` → `idle` |
  | 其他 | 沿用既有 `idle_look_around` / `idle_blink` / `idle_stretch` |

  每个变体对应 GLB 内置的一个骨骼动画 clip；模型未提供该 clip 时引擎回退到 `idle`，符合「永不空白」不变量。

### 用户直接交互

- **命中模型**：精灵区域在矩形命中后做像素级精化——3D 模式由 [3d/silhouette-hit.ts](modules/character/rendering/3d/silhouette-hit.ts) 驱动引擎剪影探测——`Engine` 把场景渲进 1/4 分辨率离屏 RT（clear alpha 0，只有实际绘制的像素计入，天然含当前姿态）异步读回 alpha，window 级 mousemove（穿透态下 pointer 事件到不了 canvas）rAF 合并请求、250ms TTL 让并发请求共享一次刷新，答案落地后手动触发 capture probe 处理静止光标；2D 模式由 puppet 链判定（CPU 轻量，PROTOCOL §1.4）——[PuppetStage](modules/character/rendering/2d/puppet/PuppetStage.tsx) 把舞台归一化坐标经 contain-fit 画布矩形换算成 rig 像素，`PuppetRuntime.hitPart` 按当前帧形变网格自顶向下逐层点测（命中 = 可见部件本身而非层矩形——层 bbox 会把部件四周的透明留白算进命中），区域 = 最上层命中部件的映射，经 `$mesh2dHitmap` 总线下发，hitmap 落地/清空时同样触发 capture probe。两条路径的就绪信号落地前（boot/加载空挡）回退精灵矩形，之后未命中点严格判否——扫过矩形空白区不捕获。精灵矩形不外扩 padding——CSS 光晕是装饰而非命中可供性，点击可见光晕不触发交互。capture 必须在 mousemove 阶段判定成功：`setIgnoreMouseEvents({ forward: true })` 不转发 mousedown，窗口必须在 mousedown 到达前 un-ignore。
- **戳**（`onTap`）：走 LLM 推理（受设置开关与 5 分钟频控门限控制）或从 [reactions/manifest.json](modules/character/reactions/manifest.json) 预制台词池中按 (bucket, tone) 挑选；云端推理接受 `poke` / `pet` / `dizzy` 三类语义 kind（PROTOCOL §1.4），摸头与戳同走该通道（手势识别器 [app/windows/sprite/behaviors/gesture-tracker.ts](app/windows/sprite/behaviors/gesture-tracker.ts)：head/face 横向往复 = 摸头）。
- **拖拽**（`onDragEnd`）：纯本地预制反馈（零 RPC），从 `manifest.json` 的 drag 桶（性格 + 通用分组）随机挑选。
- **预制反馈 TTS 缓存**：预制台词由 `speakScripted`（[tts.ts](modules/speech/tts.ts)）→ `spiritagent:media:tts { persist: true }` 合成并按 `sha1(音色 + 台词)` 内容寻址缓存在 `$SPIRITAGENT_HOME/audio/tts-cache/<lang>/`：首次播放合成一次并落盘，之后都是本地读盘，同一组 (音色, 台词) 一辈子只花一次云端额度。换音色或改台词会让缓存键变化从而自然失效，没有需要维护的失效逻辑。音色试听句走同一条路径。
- **悬停**：视线跟随光标（2D/3D 同规则）；2D 模式命中头发/裙摆区域额外触发 jiggle 物理抖动（200ms 节流）。贴边吸附态不因悬停解除，仅点击/拖拽主动解除。情绪 / 交互粒子反馈（爱心、怒气、冷汗、眩晕星环、音符、睡眠气泡）由 [vfx.tsx](modules/character/vfx.tsx) 挂载在 SpriteStage 上层。
- **右键**：托盘菜单与精灵窗口内右键开自定义 in-sprite 菜单（[app/windows/sprite/context-menu.tsx](app/windows/sprite/context-menu.tsx)）——仅保留「生活空间」、「工作台」、免打扰开关、「一键归位」与「隐藏角色」顶层入口，设置项全面收归生活空间。菜单表面走独立 overlay（跟色彩轴走的可读实底玻璃），不继承清透档窗壳的低 alpha。状态走 `$contextMenuPos` 原子，菜单自身订阅，宿主 `SpriteWindow` 不参与。菜单可见时注册全屏交互区域与透明 backdrop，点击外部区域、窗口失焦或按下 Escape 键时自动关闭菜单并拦截事件，避免误触精灵拖拽或戳动；若在菜单开启时右键精灵身体部位则直接重定位菜单。

**每日互动统计**：戳击 / 对话轮次两类互动经互动统计上报接口（无 LLM）上报，后端按用户本地日聚合 + OR 门限（任一类 ≥ 10）按日 upsert 一条统计记忆（含小时分布快照），喂给后续 LLM「用户当日活跃度 + 高峰时段」信号。

### Cron 双轨交付

陪伴型 `special` 调度由 outbox 路由到持有用户 WS 的副本，在唯一 companion 主会话上完成无头感知与发言决策。Client 不接收评估阶段的正文增量、工具流或打字态；只有后端已经原子持久化的 `companion.message` 才进入展示链。当前打开 companion 会话时增量追加消息，历史以主会话水合为准；提醒可见性与媒体交付遵循 [PROTOCOL.md §1.3](../../docs/PROTOCOL.md)，不以是否允许打扰决定消息入列。

普通 `standard` 自动化在独立任务会话执行，Client 只接收 `system.notification`；通知携带任务会话 id 时提供「查看」动作并切到对应工作台会话。它不受陪伴打扰档位的展示门控。机制与派发守卫见 [ARCHITECTURE.md §5](../../docs/ARCHITECTURE.md) 与 [backend/README.md §6](../../backend/README.md)。

### 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 24 MiB → 客户端 IPC 边界拒绝（[main/ipc/media.ts](../main/ipc/media.ts) 的 `STT_MAX_AUDIO_BYTES`；后端另有 25 MiB 上限（[backend/api/v1/media.py](../../backend/api/v1/media.py)），先到先拒）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket
- **Stop 按钮双通道**：`session.interrupt`（停 LLM 流）+ `runnerCancel`（置 Runner 全局中断标记，让在跑的本地工具尽早退出）；两者均为 best-effort，本地 finalize 兜底 UX
- **持久化键**：伙伴偏好（音色 / 响应模式 / 打扰档位 / 智能反应三开关 / 默认缩放）与各面板位置尺寸均经 localStorage 跨重启保留；`voiceId` 在 ready 后由 [voice-validity.ts](modules/speech/voice-validity.ts) 对云端目录校验（供应商裁剪 / 换源时提示重选，不硬性拒绝）。精灵位置持久化在 `companion-position.json`（Electron userData 目录，非 localStorage）。
- **偏好上云（localStorage 只是窗口缓存）**：偏好 setter 在写 localStorage 的同时经 `prefs:set` 通道上报主进程（点键 `companion.voice_id / response_mode / llm_reactions / llm_affect / llm_autonomy / disturbance_preference / settings_panel`），由主进程合入配置镜像随云端管道上云（[PROTOCOL.md §2.4](../../docs/PROTOCOL.md)）；水合广播（`prefs-hydrated`，[prefs.ts](modules/character/prefs.ts) 的 `initCompanionPrefsSync` 订阅）用云端值回写 localStorage 与 atom，跨端收敛、清缓存重装也能恢复。高频源（面板拖拽/缩放）先在渲染侧防抖再上报，且未交互过的挂载首跑不上报（避免本机默认值覆写另一端几何）；面板几何水合只在下次开面板时生效，渲染期仍做视口钳制。打扰档位分两键：生效值（`companion.disturbance_tier`，设备派生）供后端闸门消费；用户偏好（`companion.disturbance_preference`）跨端恢复。水合只回写偏好，不回写生效值。
- **角色编辑双路径**：`PersonaSection`（表单式直接改 3 个可编辑字段：名字 / 关系定位 / 性格；锁定的视觉锚点字段原样带回，[DESIGN.md §5.4](../../docs/DESIGN.md)）+ `PersonaRetune`（[persona-retune.tsx](app/windows/living/settings/persona-retune.tsx) 5 步对话式 wizard＋收尾确认，含说话风格与 user_* 字段），后者单 PUT 收尾、保留 `is_complete=True`。两条路径都是纯 persona 维度调整，不重跑形象流水线——形象确认后头像与模型的重生路径已关闭，两步形象确认 UI 只存在于 onboarding。
- **形象生成入口分工**：头像重生与全身生成分别走协议定义的独立入口；Renderer 只消费引导状态与生成事件，不组装供应商请求。接口契约见 [PROTOCOL.md §1.2](../../docs/PROTOCOL.md)，用户流程见 [DESIGN.md §5](../../docs/DESIGN.md)。引导模式未知时显示加载占位，避免先以错误文案渲染再闪烁。
- **换装（衣柜）**：外观生成 / 穿着 / 删除走 REST（[wardrobe-store](modules/character/wardrobe/wardrobe-store.ts)）；衣柜入口只在 2D 渲染模式下渲染（3D 模型不随服装变）；换装状态事件触发衣柜重拉，穿着翻转时重水合 2D 渲染层（按新 PSD 重建 puppet），换装期间旧装不断档。衣橱列表先恢复持久元数据与本机图片，再刷新远端；图片逐项呈现，不等待全部下载。刷新后顺序预取未缓存的资产包，失败保留已有内容；登出及过期请求通过清理代次隔离。
- **签名资产消费**：签名、时效与校验规则见 [PROTOCOL.md §1.5](../../docs/PROTOCOL.md)；Renderer 只按返回 URL 拉取并缓存。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window）。任何弹层（chat / 设置）都**不**开关窗口置顶——z-order 恒置顶是 DESIGN §3.7 不变量，设置期间关掉置顶会让精灵连同面板一起沉到别的窗口底下（恢复时还用 `floating` 档，macOS 的 `screen-saver` 档会被降级）。
- **主题（UI 皮肤）**：主题状态在 shared 侧（[shared/store/theme.ts](shared/store/theme.ts)）——切换入口仅生活空间「外观设置」，提供夜色、日色、夜色透明、日色透明四档，本窗经主进程广播实时换肤、启动时从 localStorage 恢复，自身不提供切换入口。主题只作用于 UI 铬面（面板 / 气泡 / 菜单 / toast），不覆盖伙伴形象本体（蛋 / 2D puppet / 3D / VFX）。
- **对话内媒体展示（气泡轻量化原则）**：精灵气泡只承载轻量文本；伙伴生成的图片/视频统一在对话窗以媒体卡内联预览、点击放大播放（图片与视频同一交互，[chat-media-card](modules/conversation/chat-media-card.tsx) + [media-viewer-overlay](modules/media/media-viewer-overlay.tsx)）。媒体经主进程 IPC 取回（图片 data URL 走 `apiAsset`、视频字节转 blob URL 卸载回收），不直连后端 URL。聊天窗收起时收到媒体，精灵气泡只提示「点击查看」，点击打开对话窗（媒体属于其他会话时先切过去）；正看其他会话时由通知 toast 承载跳转。后台视频完成的送达行与历史水合同形状（协议见 [PROTOCOL.md §1.3](../../docs/PROTOCOL.md)）。
- **IM 通道事件 toast**：`channel.status`（连接/登录过期/异常）与 `channel.peer_request`（陌生对端配对请求）在精灵窗以通知提醒，屏锁静默；通道绑定与审批的真相源在「设置」面板的「聊天通道」tab（REST），toast 只是提醒入口。
- **im 会话只读**：外接 IM（微信）桥接的会话（conversation kind `im`）在会话列表与历史中正常可见可读，但输入区整体禁用并显示「IM 对话 · 只读」角标——im 回合由后端通道桥独占写入（外部 IM 消息驱动），桌面不能代伙伴在渠道会话里发言。服务端 `prompt.submit` 侧另有守卫双保险。协议见 [PROTOCOL.md §1.7](../../docs/PROTOCOL.md)。
- **设备指令豁免会话闸门**：事件路由用 session_id 闸门挡住非当前会话的回合事件，但 `tool.call` **信封不带 session_id**、天然绕过该闸门——它是用户级设备指令而非会话事件，按 `call_id` 与 `tool.result` 配对（契约 [PROTOCOL.md §1.3](../../docs/PROTOCOL.md)）。`headless=true` 的设备指令照常交给 Runner，但不驱动精灵工作态、工具流或打字态；这是主动 Cron、普通自动化、IM 与子 agent 的统一无头边界。**闸门对 `message.*` / `tool.start` / `tool.complete` 的拦截必须原样保留**，别顺手把它们一起放行。
- **设备指令按 call_id 去重**：`tool.call` 同样进服务端重放缓冲，断连重连会重发。本机副作用不可撤销，重复执行一次「删文件」无法挽回，因此重复 `call_id` 直接丢弃——后端只会丢弃迟到的结果，拦不住已发生的副作用。去重表上限对齐服务端重放缓冲容量。
- **非无头遥控回合的精灵工作态由 tool.call 分支自持**：不可见会话的 `tool.start` 与终局 `message.complete` 会被闸门挡下，因此 `headless` 未启用且载荷 session_id 不等于当前会话时，该分支自行进 WORKING 并在工具返回后复位。三个约束不可省：并发工具用引用计数；进入与复位都传 `force`；仅在状态仍是 WORKING 或仪式行走留下的 interacting 时复位。无头指令不进入这条分支。
- **新建对话与工作台预设选择**：工作台侧边栏「+ 新建对话」按钮打开预设选择模态框，从 4 套内置职能预设（开发工程师、产品经理、文案秘书、语言老师）中点选并创建工作会话，严格过滤「陪伴」预设（生活空间专属）；选定后 presetId 写入 `session.create` 的 `system_preset_id`；侧边栏据后端下发的 `system_preset_icon_key` 渲染职能图标与徽标。预设元数据由 `system.list_presets` 一次拉取、进程内缓存。协议见 [PROTOCOL.md §1.8](../../docs/PROTOCOL.md)。
- **用户侧聊天附件**：入口四条——粘贴（图片位图存盘、视频文件取真实路径）、拖拽到面板/精灵、附件按钮选择器、精灵投喂共用拖拽管线。图片以 data URL 直发多模态；视频附加即经主进程 IPC 上传后端换取会话级 URL，上传完成前发送按钮禁用，失败态可重试可移除。附件只属于上传时的会话，切换会话即丢弃。上传限额、双模式消费与清理降级契约见 [PROTOCOL.md §1.3](../../docs/PROTOCOL.md)。

### 空间行为（位置 × 移动 × 缩放）

设计意图见 [DESIGN.md §3](../../docs/DESIGN.md)。本节记录 Client 侧的实现契约。

**单一权威源**：[spatial.ts](modules/character/spatial.ts) 拥有所有空间状态——`$spatialPos`、`$spatialScale`、`$spatialLocale`、`$spatialLocomotion`。sprite-stage 是纯消费者（`useStore` + 事件转发到 spatial 函数），只消费位置状态。

**二维贴边姿态归属**：分层木偶在最终网格内处理探身与双手接触，舞台保持水平，以保证屏幕边缘和命中坐标一致；其他渲染分支保留舞台倾斜。动作约束见 §8.1。

**移动引擎**：3D 模式下采用 rAF 插值（非 CSS transition），walk ≈ 80 px/s、fly ≈ 400 px/s。用户拖拽瞬时覆盖一切其他移动。任何新 `moveTo` 或 drag 自动取消正在进行的动画。拖拽松手一律就地定居为新 home 并持久化（DESIGN §3.3）。

**`initSpatial()`**：在精灵窗根组件（[app/windows/sprite/sprite-window.tsx](app/windows/sprite/sprite-window.tsx)）mount 时调用一次，注册所有空间反应——`$surfaceOpen`（打开生活空间或工作台时终止移动保持就地、桌面精灵自动隐藏收起并暂停自主走位；工作台采用复合视窗一体化内嵌伴工精灵）、`$spriteState`（自适应缩放）、`$effectiveTier`（空间策略 + 缩放）、`$focusContext`（perch 决策）。返回 cleanup 函数。

**决策树**（`updateSpatialDecision`）：drag > 生活空间或工作台开启（收起桌面精灵舞台，冻结桌面空间移动）> still → home > 非 autonomous（常规）→ 停留原地，仅停掉进行中的漫游 > 智能驱动开 → LLM 决策（[autonomy.ts](modules/character/autonomy.ts) 仅在自主档且 `$surfaceOpen === null` 时咨询云端）> 焦点窗口几何可用 + category ∉ {unknown, gaming} + !fullscreen → perch > idle + 桌面空闲 + 无 perch 目标 → roam > home。每次 tier / focus / state / surface 变化触发重评估。「沉浸式 → 静止」的档位覆盖只把 gaming / 全屏算作沉浸上下文——专注工作不压档（DESIGN §6.2）。

**perch 位置**：从焦点窗口几何（`$focusContext.windowGeom`）计算——优先窗口右下角外侧，右溢出则尝试左侧；两侧放不下全尺寸时等比例缩到能舒适栖身（不低于 0.5×，缩放上限随 perch 场所生效、离开即解除，压过情绪放大）。连最小尺寸都容不下才放弃。perch 仅在 idle 时发起；进入 perch 后 work/think/speak 状态不踢出（"陪"语义）。工作台开启时桌面精灵收起，由工作台复合窗口内置的伴工插槽一体化呈现。

**roam**：自补充式 waypoint 循环（每个点停 5–15s），waypoint 在屏幕下半部随机生成。自主档 + idle + 桌面空闲（Runner 上报的空闲秒数 ≥ 90s，未知信号保守不漫游）+ 无 perch 目标时触发（2D/3D 均漫游；2D 走位移积分复合步态）。任何 drag / chat / focus / tier 变化或用户回到桌面通过 `stopRoam` 终止。

**approach（走过去搭话）**：`companion.should_act` 的第三类动作（[autonomy.ts](modules/character/autonomy.ts) `executeApproach`），仅智能驱动开 + 自主档——本地规则路径不搭话（说什么需要人格）。开场白由后端在同一决策中产出并经 `companion.message` 通道投递（边走边说，气泡随精灵移动），客户端只走位：有焦点窗口落在窗口旁（复用 perch 落位与缩身，搭话后就地陪工），用户在桌面时走到屏幕中下部站定（不动 locale，后续空间决策接管）；途中视线锁定目标中心 6s。锁屏 / 聊天开启时不执行（消息侧由各自的既有门控决定）。低频闸在后端（30 分钟冷却，冷却内连同开场白一起降级 stay，不出现"说了话没走过来"）；协议契约见 [PROTOCOL.md §1.4](../../docs/PROTOCOL.md)。

**缩放**：`$defaultScale`（用户设置，localStorage）是基准。EMOTIONAL 状态的 excited/surprised/playful 触发 1.3–1.6× 临时放大，静止档不放大。缩放也是 rAF 动画（~300ms），通过容器 `transform: scale()` 实现——与 sprite 内部的程序化动画（呼吸/浮动）在不同 DOM 层，不冲突。

**Backend 零感知**：空间状态单一权威在 Client——Backend 只返回 `should_act` 语义动作，从不产出 locale 字段或像素坐标；Runner 提供感知能力（`system.get_windows` 窗口枚举、`system.get_focused_app` 焦点窗口几何）但 Runner 也不知道空间行为存在。

**Ritual walk**（[ritual-walk.ts](modules/character/ritual-walk.ts)）：交互类工具（`system.open_application` / `browser_*` / `system.click_at`）在事件路由的 tool.call 分发里拦截。目标解析按工具分派：`click_at` 的目标就是点击坐标本身（包成虚拟窗口几何，**execute 即那次点击，不再额外补一次 click**——否则双击）；其余工具从 args（name/url/keyword）提取关键词经 `system.get_windows` 匹配既有窗口，关键词为空不进入仪式（空串会让 `includes` 恒真、匹配到第一个无关窗口）。找到目标后：途中视线锁定目标中心（`$gazeTarget` 显式覆盖指针跟随，2D/3D 同规则）→ 远距离（>400px）fly、近距离 walk 到目标旁 → 抵达后按方位播 `point_left/right` 再接 `click`（open_application 等先在目标中心补一次聚焦点击，click_at 跳过）→ INTERACTING 1.5s → execute 原工具 → 返回原 locale。找不到目标窗口或无处栖身时以一句人格化台词表达（走 proactive-delivery 工作流的档位门控，经 proactive-speak 槽位绑定）后静默走常规工具调用兜底；gaze 的清除走 try/finally，异常路径不泄漏。chat 开启或屏锁时直接执行不走路。

## 7. 会话、语音、媒体、记忆与房间契约

### Conversation（modules/conversation）

会话与消息能力：消息列表 / 内容状态（`chat-store.ts`）、流式投影与回合收尾、待发批次、会话列表（`session-list-store.ts`）、slash 命令，以及对话 UI（表面、输入、气泡、语音条投影、媒体卡、参数面板）。不导入 character / speech；形象状态、TTS 与媒体查看器经呈现端口读取，语音条经 voice-link 接缝。允许消费 `modules/media` 的展示原语。表现命令当前仍经端口下达（见 §5 收敛路径）。媒体与语音副作用不得绕过回合绑定：异步回写前必须核对会话与消息归属。

### Speech（modules/speech）

TTS 合成（`tts.ts`）、音频播放与口型振幅（`audio-track.ts`）、合成准备状态（`voice-state.ts`）、音色目录与有效性校验（`voice.ts` / `voice-validity.ts`），以及语音条播放引擎（`voice-bar.ts`：合成缓存、时长缓存、自动接播队列与播放中断）。引擎不认识消息：对会话消息体的全部读写经投影注入，playing / loading 播放状态也经投影写入会话侧原子——本模块不持有第二套播放状态机。音色默认值等偏好经呈现端口读取。

### Media（modules/media）

全屏查看器浮层（`media-viewer-overlay.tsx`）、内联媒体渲染（`inline-media.tsx`）、经主进程 IPC 的媒体源解析与进程内缓存（`media-src.ts`）。每个可打开媒体的窗口独立挂载 `MediaViewerOverlay`，打开请求经呈现端口注入；媒体字节统一经主进程桥取回，不直连后端 URL。

### Memory 与 Room（modules/memory、modules/room）

`modules/memory/journal-store.ts`：片刻与日记的水合、分页缓存与 WS 增量（`companion.moment.created` / `companion.diary.upserted`）；底层向量检索记忆不经过渲染层。`modules/room/backdrop-store.ts`：房间背景状态机（none → pending → ready、换装 invalidated、失败重试、策略与历史）。工作台不使用房间图。

## 8. 渲染域（modules/character/rendering）

### Puppet（2D 高保真渲染路径）

2D 形象的高保真渲染路径：消费 see-through 产出的分层 PSD（22 语义层，含遮挡补全），在浏览器内自动装配并驱动。底座自 [Anime2.5DRig](https://github.com/852wa/Anime2.5DRig)（MIT）移植；机制升级对标 PuppetLoom（AGPL-3.0）——**只学机制，不搬代码与文字**，规避许可证传染。

模块边界：`vendor/` 保留上游 UMD 与许可证，不参与 lint / format；加载器以 Vite `?url` 生成哈希资源，注入经典脚本并等待就绪，不能依赖 HTML 裸脚本路径。装配边界隔离 vendor 全局类型、PSD 语义与本仓运行时；头部控制笼和身体骨骼独立求解，挂载层只负责资源与生命周期。驱动层消费视线、语音、情绪和动作，状态与水合见下文 mesh2d 约束；几何、命中与确定性验证约束见下文。

关键契约与设计：

- **PSD 层命名**是装配的输入契约（face / eyewhite / irides / eyelash / eye_close / eyebrow / mouth_open / mouth_close / nose / ears / neck / topwear / bottomwear / legwear / handwear / footwear / front hair_N / back hair），上游 see-through 的产出逐字吻合；`rigger.normName` 内置少量别名归一（`mouth`→`mouth_open` 等）。**侧名与节段补丁**：see-through 产出 `-l/-r` 后缀层名与四肢分段层名（eyewhite-l、arm_upper_l 等），绕过 vendor SLOTS 匹配导致侧别/淡出/眼与四肢锚点缺失（虹膜移动/眉毛/远眼收窄/耳淡出全部失效）——装配边界统一补齐眼与四肢锚点并规范层名、侧别与开眼层淡出标记。
- **头部五官权威图层层序排序**：装配阶段仅针对头部五官与前发槽位（face < facedetail < mouth < eyewhite < irides < eyelash/eye_close < nose < eyebrow < front hair）在其占用的槽位间进行解剖学稳定重排，确保任意刘海/碎发自然覆盖眼眉并彻底解决「眼睛悬浮在头发上」问题；**身体、躯干、四肢、服装与配饰图层 100% 保留上游 PSD 的原始层级顺序**，杜绝手臂与裙服层级穿模。
- **形变数学与上游逐字一致**（depth 视差、发束权重、弹簧参数）；外围（GL 装配、rAF 生命周期、参数注入、TS 类型）与本仓动画自动化层为本仓代码。升级机制时以"同种子同参数输出可复现"为回归基线，中立姿态几何严格恒等。
- **ArtMesh**：每层按 alpha 轮廓采样三角剖分，顶点密度贴形（发梢/睫毛/下巴缘），退化层回退 quad；域外三角形靠 alpha discard 遮蔽，无需完整 CDT 的约束恢复。
- **头部控制笼**：左右颊+颅顶三角笼，每顶点预计算重心权重与有效深度（脸面↔头骨混合；前发根随脸、梢随颅）。deform 头部块 = 控制点位移 + 重心混合，位移场仿射。
- **伪 3D 转头**（机制取自 PuppetLoom）：头/颈走圆投影分支——角度参数为归一化正弦（中心位移对参数线性），横向位移乘有界缘斜率的可见度轮廓（缘部保底位移、斜率有上界，保证压缩项叠加下局部映射单调不折叠——纯圆根在缘部斜率无界会导致网格折叠），远/近缘压缩项按真实余弦把两侧拉向轴心（远缘发层滑盖向脸 = 侧发贴脸缘，近缘转回身后）。六点脸面深度曲线（额/鼻梁/鼻尖/上下唇/下巴锚点，仅作用于脸面表面）让鼻口等靠前点转/俯时移动更多。远眼收窄 = 对侧眼向眼心水平压缩（纯几何，不动透明度）；周边可见度 = 远端侧挂件（耳等，眼/眉除外）随转角淡出。颈上端跟头、下端跟领的双隶属 + 上身同源跟随（直接读平滑后头部偏航，不经第二套慢响应）。
- **2D 骨骼系统与四肢 IK**（[skeleton.ts](modules/character/rendering/2d/puppet/skeleton.ts) / [skin.ts](modules/character/rendering/2d/puppet/skin.ts) / [limb-split.ts](modules/character/rendering/2d/puppet/limb-split.ts)）：
  - **头部与身体解耦**：头部五官与发束严格保留三角控制笼形变与多段弹簧链，不参与身体骨骼变换；身体图层走 18 骨骼层次结构与线性混合蒙皮（LBS），颈部上 40% 隶属控制笼、下 60% 隶属颈脊椎骨。
  - **2-Bone 解析 IK**：上臂/前臂/手、大腿/小腿/脚双骨求解，超距目标连续夹到可达半径，骨长保持不变；左右肩、肘、髋、膝的角限位按弯折方向镜像。
  - **四肢绑定坐标约定**：骨架左右以屏幕为准，四肢按图层几何位置归侧；素材后缀可能按角色自身左右命名，不能直接用于绑骨。腕部与踝部横坐标取对应高度的可见像素重心，髋膝踝纵向位置优先依据腿部图层估算，整肢与节段图层共用骨架关节切线，相邻关节过渡只混合相邻骨骼。
  - **袖口与衣身隔离**：袖口影响在肩部附近沿纵横两轴连续衰减，衣身与裙摆保留躯干驱动，避免整层连衣裙随手臂拉起。
  - **四肢完整度三级降级**：`segmented`（全 IK + 迈步划弧，各肢节独立切分）/ `sided`（左右整肢连续网格蒙皮与双骨求解）/ `blob`（整体微移兜底，无独立四肢），兼容各种层级完整度的 PSD 资产。
  - **步态反向接地补偿与对侧摆臂**：行走时支撑脚向位移反方向平移补偿（$-dx$）实现零脚滑接地，摆动脚做抛物线抬腿（$-dy$），双臂自然对侧反向摆动；停步后经 350ms 平滑指数衰减无缝回归中立绑定姿态。
  - **驱动层动作优先级**：拖拽悬挂与贴边接触独占四肢，避免瞬时手势、步态和接触约束争抢同一关节；自由状态下手势优先于步态，未参与动作的手臂保持摆臂或自然下垂。
- **次级运动**（机制取自 PuppetLoom）：发束为节点弹簧链（根节点硬跟头/身混合位移 + 风动，下游节点逐节追踪父节点、刚度/阻尼沿链递减——自由端逐步获得惯性）；渲染按发束进度在链上取样、相对根节点的偏差即次级运动。裙摆腰线固定、双频正弦左右摆（受 idle 门控）；耳为种子化偶发快速抬落、严格回中立；呆毛由前发顶部窄条检测 + 纵向弹簧、偶发事件激发弹动。自主漫游为**种子化观察段落**（种子 + mulberry32，十几秒环：左右观察→抬头→低头、动作间回正，无每帧随机数；耳/呆毛事件同受自动化开关门控保证姿态定格确定性）。
- **13 姿态安全验证与三级降级**：`poseSafety()` 按当前参数重算形变后检查全部三角形的有向面积翻转（亚像素级边界退化细条不计）与最大边拉伸比。`assessTier` 按 PSD 语义完整度分级：semantic（全语义层+眼锚点+发束链，全机制）/ grouped（整体运动 + 头转缩幅，远眼收窄等特征级机制关闭）/ minimal（仅整体呼吸/重心横移/倾斜，形变逐顶点早退）。`frozen` 冻结呼吸相位与事件调度，姿态定格逐位可复现。
- **拖拽悬挂反馈**：按下时保存抓取点，整体悬挂变换围绕该点施加阻尼摆动与轻微纵向伸展，双腿保留垂落姿态。输入停止后惯性衰减，松手连续回正。整体变换作用于头身最终顶点，命中检测沿用最终网格；降低动态偏好缩小惯性幅度。
- **素材限制**：整肢立绘只能近似平面屈伸，无法重建手指抓握、肩部遮挡与侧身透视；大角度折臂的自然程度仍依赖素材，不能以骨骼到达接触点代替视觉验收。
- **动画自动化层**：非对称呼吸（含偶发深呼吸）、眨眼曲线（全眨/半眨/连眨）、视线跟随（眼先动头跟随，无更新过期回落漫游）、微扫视（指数衰减的小幅快速眼动）、说话合成（每句独立振幅 + 音素级嘴型目标）。参数平滑按语义分速率（眼快、头身慢）。
- **模拟/渲染解耦**：`advanceSim(seconds)` 以固定步进接管内部时钟（rAF 退化为纯渲染），供无头验证与回归做确定性断言——姿态安全验证以此为地基；`snapshot()` 暴露平滑后参数只读快照，`forceBlink()` 为确定性眨眼钩子。
- **差分合成**：PSD 缺 eye_close / mouth_close 时用内置 genericparts 自动合成并染色适配（上游行为，保留）。
- **数据来源与渲染级联**：see-through 产出 `spiritagent.2d.psd/1` 描述符（`kind=psd`）复用 mesh2d 行与 WS 事件路径；`companion.2d.ready` / outfit 穿着 / 头像重生事件后 `hydratePuppet` 判 kind。精灵窗根组件渲染级联：**puppet（PSD）→ 3D → 程序化蛋**——puppet 装配失败写 error 熄灭 `$puppetReady` 自动落级，永不空白（DESIGN §1.2）。
- **驱动层映射**：视线 = 指针归一化注入 + `$gazeTarget` 显式目标周期续注（ritual walk / perch 锁定）；说话 = TTS 振幅接管嘴型并暂停合成说话、静默后交还；情绪 = 后端情绪词表全对齐 → 眉/嘴型/眼缩放参数（puppet 独有面部通道）；动作 = 动作白名单键 → 定时包络 + 队列续播；hover 发区 → 发束冲量（节流，方向随戳侧）。

已知限制：

- 尾巴/头饰圆弧摆动机制待有对应部件的模型接入（当前测试 PSD 无 tail/headwear 弹性层）
- 13 姿态在满幅缩放仍有 back hair 一处小面积三角形翻转（降一档即全绿）；驱动层动作包络幅度按安全包络设计，姿态安全缩放报告尚未自动约束 LLM 动作幅度
- 说话嘴型按 TTS 振幅包络驱动（非音素级）；音素驱动留待 TTS 层暴露音素流
- 自动绑骨以自然站姿立绘为前提；交叉四肢、遮挡补全缺失或衣服与肢体未分层会限制可用动作幅度，无法保证任意立绘达到人工逐关节调校的表现。

扶边使用独立姿态包与参数化渲染器。姿态资产与加载约束见 [PIPELINE §6.2 扶边姿态包](../../docs/PIPELINE.md)。命中检测按当前形变后的三角形映射回纹理透明度，避免隐藏的 PSD 或透明留白挡住鼠标。扶边纹理加载失败按侧别最多尝试三次，间隔一秒、两秒，卸载或换包时取消待执行重试；失败日志包含侧别与次数。描述符解析失败与双手区域降级分别记录，避免正常 PSD 装配日志掩盖扶边不可用。衣橱预览使用独立运行时与桌面共享动作包络；只在预览可见且播放时推进时钟，暂停保留当前帧，卸载释放资源。预览的扶边接触线对齐取景框边缘，桌面仍对齐窗口边缘；两者不共享情绪、动作队列、命中总线或空间状态。

### Mesh2D 水合 Store

负责 2D 资产行的客户端镜像与 PSD 缓存（[mesh2d-store.ts](modules/character/rendering/2d/mesh2d/mesh2d-store.ts)），渲染装配见上文 Puppet。关键约束：

- 先水合 2D 行，再装配木偶；装配依赖行中的 PSD 描述符地址，不能并发颠倒。
- 渲染模式切换必须幂等，防止跨窗口广播回环。
- OPFS 缓存与远端字节均验证 PSD 魔术字节；中止和读取错误向装配层传播，由其决定降级，不能吞错伪装成功。
- 部件命中由木偶写入、精灵交互消费，使用当前帧网格与归一化坐标；统一命中总线定义见 [mesh2d-store.ts](modules/character/rendering/2d/mesh2d/mesh2d-store.ts)。
- 生成失败保留状态与原因，由设置页提供重切分入口；生成契约见 [PIPELINE](../../docs/PIPELINE.md)。
