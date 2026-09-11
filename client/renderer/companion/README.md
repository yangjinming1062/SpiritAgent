# Companion 状态机与表情契约

> Client 伙伴层的运行时契约。`ARCHITECTURE.md` 锁定跨模块设计意图，本文件记录 Client 侧独有的状态机优先级、表情覆盖、过渡语义与不能从代码结构直接读出的边界。

## 1. 动画状态机（8 态）

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

### 1.1 状态切换规则

- **低优先级不能打断高优先级**（`setSpriteState(name)` 默认 `force: false`）—— 已在 `working` 时调用 `setSpriteState('idle')` 被门控逻辑直接吞掉。需强制回退必须传 `{ force: true }`（working 状态 10s 无活动自动 force 回 idle）。
- **`emotional` / `interacting` 是叠加而非抢占**：进入前若当前不是这两个状态，原子 `$previousState` 记录原态；瞬态 timer 结束后回到 `previousState`（若 prev 也是 emotional/interacting，则回 `idle`）。
- **crossfade ~250ms**：clip 切换通过 sprite-stage 的 fade 层处理，避免硬切。

### 1.2 生活空间语音条

在生活空间「始终语音」模式下，流式生成阶段气泡仅展示“正在输入...”；气泡收尾后对整段正文一次性发起 TTS 合成并落盘缓存，等待期间保持 thinking 状态（左栏同步「在想事情」）。语音条就绪后展示并自动起播，起播时进入 speaking 状态（左栏同步「在说话」）；多气泡连发按到达顺序接播；遇到停止、异常、提交新消息或锁屏即刻中止并守卫复位。

聊天完成帧只收尾文本、媒体与语音状态，不触发 EMOTIONAL。语音播放结束或被打断后直接复位 `idle`；桌面视觉表达由独立 `companion.affect` 事件驱动，不等待聊天或 TTS。

## 2. 情绪表达（肢体动画）

情绪枚举固定为 21 项内置（不含 `neutral`；Backend 权威 `services/companion/emotions.py` 的 22 项含 neutral）。自主视觉引擎只返回白名单 token；`neutral` 本身不触发 EMOTIONAL，只有动作时作为动作状态的中性基调。情绪只驱动现有模型参数与动画，不存在自定义表情注册或表情图片资产。

情绪的渲染分工（3D 面部不承载情绪表情——生成模型脸部在桌面尺寸下精细度不足）：

- **肢体动画**：按模型映射解析当前状态可用动画；解析与兜底规则见 [docs/PIPELINE.md §5](../../../docs/PIPELINE.md)。2D 模式同时把内置情绪映射到参数化眉、眼、嘴通道；3D 模式只兑现模型已有的动作 clip。

### 2.1 当前心情身份轨

`companion.mood {mood}` 与视觉 affect 完全独立。事件到达后直接写 `$companionMood`，生活空间左栏在头像和名字下方动态展示；它不切换动画状态、不生成聊天消息，也不受打扰档位或桌面表面显隐门控。首次水合从 Persona 的 `current_mood` 恢复。

## 3. 三档打扰（Client 实现）

档位产品规则与生效条件见 [DESIGN.md §6.2](../../../docs/DESIGN.md)。Renderer 只消费生效档位：用户偏好保存在伴生设置，活动感知器写入覆盖值，空间策略、主动消息呈现与 TTS 门控读取同一生效值；生效档位经配置管道上云（[PROTOCOL.md §2.4](../../../docs/PROTOCOL.md)），是后端主动闸门的唯一档位来源。

## 4. 3D 渲染资源降级与功耗调度

渲染栈是 `three/webgpu` 的 **WebGPURenderer + 四层回退**：WebGPU 后端 → three 内置 WebGL2 后端（同 API 面，零代码）→ 经典 `WebGLRenderer`（仅当 `init()` 整体 reject；必须换新 canvas——webgpu 上下文成功过的 canvas 要不到 webgl2）→ `EngineInitError`（程序化蛋形兜底）。`Engine.create()` 是异步工厂，canvas 由 Engine 自建自管（React 只渲染容器），companion-3d 的 load effects 一律 await 引擎就绪 Promise；实际后端写 dev log。

视觉兜底层级见 [DESIGN.md §1.2](../../../docs/DESIGN.md)。本节只记录 3D 引擎加载行为：模型字节到达并完成解析后才视作可渲染（避免把"模型就绪事件早于字节落地"误当成可渲染状态）；GLB 解析失败回退到程序化蛋兜底，3D 引擎 init 失败亦同。

**渲染功耗三档**（[3d/PowerProfile.ts](../3d/PowerProfile.ts) 判定 + [3d/power-signals.ts](../3d/power-signals.ts) 订阅，Engine 自门控循环执行）：主进程为后台流式聊天全局禁用了 Chromium 节流，浏览器不会替 7x24 常驻的精灵窗降频，所以循环在 Engine 内按信号自门控——active 60fps（speaking/thinking/listening/working/emotional/interacting）、idle 30fps（idle/disconnected）、dormant 4fps（`$screenLocked`、`document.hidden`、`$focusContext.fullscreen`）。信号全部来自既有渲染端 atom，功耗调度是纯 Client 内部决策（ARCH §7 语义/渲染解耦），无协议与主进程参与。

两条防坑约束：**Ready 保护**——首个模型 `loadCharacter` 落定（`$modelLoadSettled`）前强制 active，避免孵化动画被误降频拉长；**隐藏窗口降级**——Chromium 对 hidden 窗口硬停 rAF（禁节流开关管不到合成层），active/idle 档在 `document.hidden` 时改由 16/37ms timer 驱动，`visibilitychange` 恢复 rAF。dormant 恒为 250ms timer（进程级禁 timer 节流，锁屏下稳定）；档位回升时 Engine 层把 delta 钳制到 50ms，防 mixer 在长暂停后跳变。

**性能取舍**（与上述 3D 渲染叠加生效）：阴影默认关闭（300×360 精灵窗的 PBR 环境光足以体现深度，2048² PCFSoft 阴影是单项最大 GPU 成本），开启时强制 1024² PCF；MSAA 开启（4×——精灵悬浮在任意桌面内容之上，剪影锯齿是首要画质破绽，此尺寸下 resolve 成本可忽略）、贴图预乘 alpha 关闭、DPR 封顶 1.5——小透明窗口的其余 GUI 成本剔除。GLB 解析模板走 [gltf-instance-cache.ts](../3d/gltf-instance-cache.ts)：按 `contentHash` 缓存解析后的 scene + animations，切换模型时走 `SkeletonUtils.clone` 深克隆重建骨骼与蒙皮绑定并隔离 AnimationClip 状态；模板持有 GPU 资源并通过引用计数管理生命周期，实例卸载不释放共享资源；支持 LRU 淘汰并在登出时安全释放。

**OPFS 二进制缓存**：GLB 与 PSD 字节均走 [shared/lib/opfs-blob-cache.ts](../shared/lib/opfs-blob-cache.ts) 共享抽象——按 `contentHash` 为键（同源私有文件系统，不是 `caches.open` 的 HTTP 缓存），元数据（schema version / size / writtenAt）独立存 `<hash>.meta.json`，blob 文件 `<hash>.glb` / `<hash>.psd`。GLB（[3d/glb-opfs-cache.ts](../3d/glb-opfs-cache.ts)，maxFiles=5 / maxBytes=512 MiB）与 PSD（[mesh2d/psd-opfs-cache.ts](../2d/mesh2d/psd-opfs-cache.ts)，maxFiles=10 / maxBytes=512 MiB）共用序列化队列 + LRU 字节预算裁剪 + 魔术字节校验（PSD 验 '8BPS'）。fetch 包装层负责 IPC 拉取 + dedup + AbortSignal 串接 + 写入闸门（`authed` && `!aborted` && `clearEpoch` 未变——登出 race 期间过期 fetch 不会把陈旧字节写进刚清空的 OPFS）。第二次加载走 0 ms 落盘读。Renderer 错误隔离（`$engineError`）：Engine tick 抛错时停止 ticker 并上报，避免"每帧抛+日志洪水"循环。这些都是默认关闭或收紧后的基线，需要在 Settings 重新打开的功能必须经过实测。

## 5. 屏锁与端忙

- `companion/activity.ts` 每 30s 调 `system.is_screen_locked`（`runnerInvoke`）。结果写入 `$screenLocked` atom。
- `$screenLocked.get() === true` 抑制主动消息文本与语音，并停止桌面视觉表达与空间智能请求；到达的 `companion.affect` 只有在自主档、桌面精灵可见且解锁时才消费。当前心情 `companion.mood` 是身份状态更新，照常刷新。
- 屏锁恢复后静默恢复；断连降级（disconnected）曾被表达过时，重连后由 boot 层平滑切回 idle 状态（动画状态机纯视觉恢复，保持静默回神，不触发口语台词与语音合成）。

## 6. 自主行为（IDLE 时）

- **微动作**：10–25s 随机间隔切 `idle` / `idle_look_around` / `idle_blink` / `idle_stretch` scene。由 3D 引擎骨骼动画直接驱动，不需生成资产。
- **情境视觉表达**：[activity.ts](activity.ts) 只在 `$llmAffect` 开启、生效档位为 autonomous、`$surfaceOpen === null`、屏幕解锁且空闲 ≥ 30 分钟时请求 `companion.check_affect`。返回事件包含可选 emotion 与最多 3 个 actions；Client 再按同一档位/表面/锁屏条件消费，2D 依序播放动作、3D 使用首个可兑现 clip。该链不接触聊天消息或 TTS。
- **情境动作**：基于 `$focusContext`（[activity.ts](activity.ts) 维护）。focused-app 分类（ide/music/reader/gaming/browsing/other/unknown）按平台白名单映射（Windows 进程名、macOS bundle id）。IDLE 微动作池按分类切换：

  | 分类 | 微动作池（未就绪 fallback 到 `idle`） |
  |------|----------------------------------|
  | ide | `idle_thinking` → `idle_typing` → `idle_look_around` → `idle` |
  | music | `idle_bounce` → `idle_sway` → `idle_blink` → `idle` |
  | reader | `idle_calm` → `idle_look_around` → `idle` |
  | gaming | `idle_engaged` → `idle_stretch` → `idle` |
  | 其他 | 沿用既有 `idle_look_around` / `idle_blink` / `idle_stretch` |

  每个变体对应 GLB 内置的一个骨骼动画 clip；模型未提供该 clip 时引擎回退到 `idle`，符合 §1.3 "永不空白" 不变量。

## 7. 用户直接交互

- **命中模型**：精灵区域在矩形命中后做像素级精化——3D 模式由 [3d/silhouette-hit.ts](../3d/silhouette-hit.ts) 驱动引擎剪影探测——`Engine` 把场景渲进 1/4 分辨率离屏 RT（clear alpha 0，只有实际绘制的像素计入，天然含当前姿态）异步读回 alpha，window 级 mousemove（穿透态下 pointer 事件到不了 canvas）rAF 合并请求、250ms TTL 让并发请求共享一次刷新，答案落地后手动触发 capture probe 处理静止光标；2D 模式由 puppet 链判定（CPU 轻量，PROTOCOL §1.4）——[PuppetStage](../2d/puppet/PuppetStage.tsx) 把舞台归一化坐标经 contain-fit 画布矩形换算成 rig 像素，`PuppetRuntime.hitPart` 按当前帧形变网格自顶向下逐层点测（命中 = 可见部件本身而非层矩形——层 bbox 会把部件四周的透明留白算进命中），区域 = 最上层命中部件的映射，经 `$mesh2dHitmap` 总线下发，hitmap 落地/清空时同样触发 capture probe。两条路径的就绪信号落地前（boot/加载空挡）回退精灵矩形，之后未命中点严格判否——扫过矩形空白区不捕获。精灵矩形不外扩 padding——CSS 光晕是装饰而非命中可供性，点击可见光晕不触发交互。capture 必须在 mousemove 阶段判定成功：`setIgnoreMouseEvents({ forward: true })` 不转发 mousedown，窗口必须在 mousedown 到达前 un-ignore。
- **戳**（`onTap`）：走 LLM 推理（受设置开关与 5 分钟频控门限控制）或从 [reactions/manifest.json](reactions/manifest.json) 预制台词池中按 (bucket, tone) 挑选；云端推理接受 `poke` / `pet` / `dizzy` 三类语义 kind（PROTOCOL §1.4），摸头与戳同走该通道（手势识别器 [sprite/gesture-tracker.ts](sprite/gesture-tracker.ts)：head/face 横向往复 = 摸头）；
- **拖拽**（`onDragEnd`）：纯本地预制反馈（零 RPC），从 `manifest.json` 的 drag 桶（性格 + 通用分组）随机挑选。
- **预制反馈 TTS 缓存**：预制台词由 `speakScripted`（[tts.ts](tts.ts)）→ `spiritagent:media:tts { persist: true }` 合成并按 `sha1(音色 + 台词)` 内容寻址缓存在 `$SPIRITAGENT_HOME/audio/tts-cache/<lang>/`：首次播放合成一次并落盘，之后都是本地读盘，同一组 (音色, 台词) 一辈子只花一次云端额度。换音色或改台词会让缓存键变化从而自然失效，没有需要维护的失效逻辑。音色试听句走同一条路径。
- **悬停**：视线跟随光标（2D/3D 同规则）；2D 模式命中头发/裙摆区域额外触发 jiggle 物理抖动（200ms 节流）。贴边吸附态不因悬停解除，仅点击/拖拽主动解除。情绪 / 交互粒子反馈（爱心、怒气、冷汗、眩晕星环、音符、睡眠气泡）由 [vfx.tsx](vfx.tsx) 挂载在 SpriteStage 上层。
- **右键**：托盘菜单与精灵窗口内右键开自定义 in-sprite 菜单（[sprite/context-menu.tsx](sprite/context-menu.tsx)）——仅保留「生活空间」、「工作台」、免打扰开关、「一键归位」与「隐藏角色」顶层入口，设置项全面收归生活空间。菜单表面走独立 overlay（跟色彩轴走的可读实底玻璃），不继承清透档窗壳的低 alpha。状态走 `$contextMenuPos` 原子（[sprite/context-menu-store.ts](sprite/context-menu-store.ts)），菜单自身订阅，宿主 `CompanionRoot` 不参与。菜单可见时注册全屏交互区域与透明 backdrop，点击外部区域、窗口失焦或按下 Escape 键时自动关闭菜单并拦截事件，避免误触精灵拖拽或戳动；若在菜单开启时右键精灵身体部位则直接重定位菜单。

**每日互动统计**：戳击 / 对话轮次两类互动经互动统计上报接口（无 LLM）上报，后端按用户本地日聚合 + OR 门限（任一类 ≥ 10）按日 upsert 一条统计记忆（含小时分布快照），喂给后续 LLM "用户当日活跃度 + 高峰时段" 信号。

## 8. Cron 双轨交付

陪伴型 `special` 调度由 outbox 路由到持有用户 WS 的副本，在唯一 companion 主会话上完成无头感知与发言决策。Client 不接收评估阶段的正文增量、工具流或打字态；只有后端已经原子持久化的 `companion.message` 才进入展示链。当前打开 companion 会话时增量追加消息，历史以主会话水合为准；提醒可见性与媒体交付遵循 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)，不以是否允许打扰决定消息入列。

普通 `standard` 自动化在独立任务会话执行，Client 只接收 `system.notification`；通知携带任务会话 id 时提供「查看」动作并切到对应工作台会话。它不受陪伴打扰档位的展示门控。机制与派发守卫见 [ARCHITECTURE.md §5](../../../docs/ARCHITECTURE.md) 与 [backend/README.md §6](../../../backend/README.md)。

## 9. 不能从代码结构直接读出的边界

- **3 种 token 通过守卫**：
  - `STT` 数据 > 24 MiB → 客户端 IPC 边界拒绝（[client/main/ipc/media.ts](../../../main/ipc/media.ts) 的 `STT_MAX_AUDIO_BYTES`；后端另有 25 MiB 上限（[backend/api/v1/media.py](../../../../backend/api/v1/media.py)），先到先拒）
  - `TTS` 文本 > 4000 字符 → 拒绝
  - `runner:invoke` 60 次/秒 token bucket
- **Stop 按钮双通道**：`session.interrupt`（停 LLM 流）+ `runnerCancel`（置 Runner 全局中断标记，让在跑的本地工具尽早退出）；两者均为 best-effort，本地 finalize 兜底 UX
- **持久化键**：伙伴偏好（音色 / 响应模式 / 打扰档位 / 智能反应三开关 / 默认缩放）与各面板位置尺寸均经 localStorage 跨重启保留；`voiceId` 在 ready 后由 [voice-validity.ts](voice-validity.ts) 对云端目录校验（供应商裁剪 / 换源时提示重选，不硬性拒绝）。精灵位置持久化在 `companion-position.json`（Electron userData 目录，非 localStorage）。
- **偏好上云（localStorage 只是窗口缓存）**：偏好 setter 在写 localStorage 的同时经 `prefs:set` 通道上报主进程（点键 `companion.voice_id / response_mode / llm_reactions / llm_affect / llm_autonomy / disturbance_preference / settings_panel`），由主进程合入配置镜像随云端管道上云（[PROTOCOL.md §2.4](../../../docs/PROTOCOL.md)）；水合广播（`prefs-hydrated`，[prefs.ts](prefs.ts) 的 `initCompanionPrefsSync` 订阅）用云端值回写 localStorage 与 atom，跨端收敛、清缓存重装也能恢复。高频源（面板拖拽/缩放）先在渲染侧防抖再上报，且未交互过的挂载首跑不上报（避免本机默认值覆写另一端几何）；面板几何水合只在下次开面板时生效，渲染期仍做视口钳制。打扰档位分两键：生效值（`companion.disturbance_tier`，设备派生）供后端闸门消费；用户偏好（`companion.disturbance_preference`）跨端恢复。水合只回写偏好，不回写生效值。
- **角色编辑双路径**：`PersonaSection`（表单式直接改 3 个可编辑字段：名字 / 关系定位 / 性格；锁定的视觉锚点字段原样带回，[DESIGN.md §5.4](../../../docs/DESIGN.md)）+ `PersonaRetune`（[living/settings/persona-retune.tsx](../living/settings/persona-retune.tsx) 5 步对话式 wizard＋收尾确认，含说话风格与 user_* 字段），后者单 PUT 收尾、保留 `is_complete=True`。两条路径都是纯 persona 维度调整，不重跑形象流水线——形象确认后头像与模型的重生路径已关闭，两步形象确认 UI 只存在于 onboarding。
- **形象生成入口分工**：头像重生与全身生成分别走协议定义的独立入口；Renderer 只消费引导状态与生成事件，不组装供应商请求。接口契约见 [PROTOCOL.md §1.2](../../../docs/PROTOCOL.md)，用户流程见 [DESIGN.md §5](../../../docs/DESIGN.md)。引导模式未知时显示加载占位，避免先以错误文案渲染再闪烁。
- **换装（衣柜）**：外观生成 / 穿着 / 删除走 REST（[wardrobe-store](wardrobe/wardrobe-store.ts)）；衣柜入口只在 2D 渲染模式下渲染（3D 模型不随服装变）；换装状态事件触发衣柜重拉，穿着翻转时重水合 2D 渲染层（按新 PSD 重建 puppet），换装期间旧装不断档。衣橱列表先恢复持久元数据与本机图片，再刷新远端；图片逐项呈现，不等待全部下载。刷新后顺序预取未缓存的资产包，失败保留已有内容；登出及过期请求通过清理代次隔离。
- **签名资产消费**：签名、时效与校验规则见 [PROTOCOL.md §1.5](../../../docs/PROTOCOL.md)；Renderer 只按返回 URL 拉取并缓存。
- **CORS / 跨窗口**：精灵窗口与对话面板共享同一 Electron 渲染进程（panel 是 React child of sprite window）。任何弹层（chat / 设置）都**不**开关窗口置顶——z-order 恒置顶是 DESIGN §3.7 不变量，设置期间关掉置顶会让精灵连同面板一起沉到别的窗口底下（恢复时还用 `floating` 档，macOS 的 `screen-saver` 档会被降级）。
- **主题（UI 皮肤）**：主题状态在 shared 侧（[shared/store/theme.ts](../shared/store/theme.ts)）——切换入口仅生活空间「外观设置」，提供夜色、日色、夜色透明、日色透明四档，本窗经主进程广播实时换肤、启动时从 localStorage 恢复，自身不提供切换入口。主题只作用于 UI 铬面（面板 / 气泡 / 菜单 / toast），不覆盖伙伴形象本体（蛋 / 2D puppet / 3D / VFX）。
- **对话内媒体展示（气泡轻量化原则）**：精灵气泡只承载轻量文本；伙伴生成的图片/视频统一在对话窗以媒体卡内联预览、点击放大播放（图片与视频同一交互，[chat-media-card](chat-media-card.tsx) + [media-viewer-overlay](media-viewer-overlay.tsx)）。媒体经主进程 IPC 取回（图片 data URL 走 `apiAsset`、视频字节转 blob URL 卸载回收），不直连后端 URL。聊天窗收起时收到媒体，精灵气泡只提示「点击查看」，点击打开对话窗（媒体属于其他会话时先切过去）；正看其他会话时由通知 toast 承载跳转。后台视频完成的送达行与历史水合同形状（协议见 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)）。
- **IM 通道事件 toast**：`channel.status`（连接/登录过期/异常）与 `channel.peer_request`（陌生对端配对请求）在精灵窗以通知提醒，屏锁静默；通道绑定与审批的真相源在「设置」面板的「聊天通道」tab（REST），toast 只是提醒入口。
- **im 会话只读**：外接 IM（微信）桥接的会话（conversation kind `im`）在会话列表与历史中正常可见可读，但输入区整体禁用并显示「IM 对话 · 只读」角标——im 回合由后端通道桥独占写入（外部 IM 消息驱动），桌面不能代伙伴在渠道会话里发言。服务端 `prompt.submit` 侧另有守卫双保险。协议见 [PROTOCOL.md §1.7](../../../docs/PROTOCOL.md)。
- **设备指令豁免会话闸门**：`handleCompanionEvent` 用 session_id 闸门挡住非当前会话的回合事件，但 `tool.call` **信封不带 session_id**、天然绕过该闸门——它是用户级设备指令而非会话事件，按 `call_id` 与 `tool.result` 配对（契约 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)）。`headless=true` 的设备指令照常交给 Runner，但不驱动精灵工作态、工具流或打字态；这是主动 Cron、普通自动化、IM 与子 agent 的统一无头边界。**闸门对 `message.*` / `tool.start` / `tool.complete` 的拦截必须原样保留**，别顺手把它们一起放行。
- **设备指令按 call_id 去重**：`tool.call` 同样进服务端重放缓冲，断连重连会重发。本机副作用不可撤销，重复执行一次「删文件」无法挽回，因此重复 `call_id` 直接丢弃——后端只会丢弃迟到的结果，拦不住已发生的副作用。去重表上限对齐服务端重放缓冲容量。
- **非无头遥控回合的精灵工作态由 tool.call 分支自持**：不可见会话的 `tool.start` 与终局 `message.complete` 会被闸门挡下，因此 `headless` 未启用且载荷 session_id 不等于当前会话时，该分支自行进 WORKING 并在工具返回后复位。三个约束不可省：并发工具用引用计数；进入与复位都传 `force`；仅在状态仍是 WORKING 或仪式行走留下的 interacting 时复位。无头指令不进入这条分支。
- **新建对话与工作台预设选择**：工作台侧边栏「+ 新建对话」按钮打开预设选择模态框，从 4 套内置职能预设（开发工程师、产品经理、文案秘书、语言老师）中点选并创建工作会话，严格过滤「陪伴」预设（生活空间专属）；选定后 presetId 写入 `session.create` 的 `system_preset_id`；侧边栏据后端下发的 `system_preset_icon_key` 渲染职能图标与徽标。预设元数据由 `system.list_presets` 一次拉取、进程内缓存。协议见 [PROTOCOL.md §1.8 新建对话预设选择](../../../docs/PROTOCOL.md)。
- **用户侧聊天附件**：入口四条——粘贴（图片位图存盘、视频文件取真实路径）、拖拽到面板/精灵、附件按钮选择器、精灵投喂共用拖拽管线。图片以 data URL 直发多模态；视频附加即经主进程 IPC 上传后端换取会话级 URL，上传完成前发送按钮禁用，失败态可重试可移除。附件只属于上传时的会话，切换会话即丢弃。上传限额、双模式消费与清理降级契约见 [PROTOCOL.md §1.3](../../../docs/PROTOCOL.md)。

## 10. 空间行为（位置 × 移动 × 缩放）

设计意图见 [DESIGN.md §3](../../../docs/DESIGN.md)。本节记录 Client 侧的实现契约。

**单一权威源**：[spatial.ts](spatial.ts) 拥有所有空间状态——`$spatialPos`、`$spatialScale`、`$spatialLocale`、`$spatialLocomotion`。sprite-stage.tsx 是纯消费者（`useStore` + 事件转发到 spatial 函数），只消费位置状态。

**二维贴边姿态归属**：分层木偶在最终网格内处理探身与双手接触，舞台保持水平，以保证屏幕边缘和命中坐标一致；其他渲染分支保留舞台倾斜。动作约束见 [木偶模块](../2d/puppet/README.md)。

**移动引擎**：3D 模式下采用 rAF 插值（非 CSS transition），walk ≈ 80 px/s、fly ≈ 400 px/s。用户拖拽瞬时覆盖一切其他移动。任何新 `moveTo` 或 drag 自动取消正在进行的动画。拖拽松手一律就地定居为新 home 并持久化（DESIGN §3.3）。

**`initSpatial()`**：在 root.tsx mount 时调用一次，注册所有空间反应——`$surfaceOpen`（打开生活空间或工作台时终止移动保持就地、桌面精灵自动隐藏收起并暂停自主走位；工作台采用复合视窗一体化内嵌伴工精灵）、`$spriteState`（自适应缩放）、`$effectiveTier`（空间策略 + 缩放）、`$focusContext`（perch 决策）。返回 cleanup 函数。

**决策树**（`updateSpatialDecision`）：drag > 生活空间或工作台开启（收起桌面精灵舞台，冻结桌面空间移动）> still → home > 非 autonomous（常规）→ 停留原地，仅停掉进行中的漫游 > 智能驱动开 → LLM 决策（[autonomy.ts](autonomy.ts) 仅在自主档且 `$surfaceOpen === null` 时咨询云端）> 焦点窗口几何可用 + category ∉ {unknown, gaming} + !fullscreen → perch > idle + 桌面空闲 + 无 perch 目标 → roam > home。每次 tier / focus / state / surface 变化触发重评估。「沉浸式 → 静止」的档位覆盖只把 gaming / 全屏算作沉浸上下文——专注工作不压档（DESIGN §6.2）。

**perch 位置**：从焦点窗口几何（`$focusContext.windowGeom`）计算——优先窗口右下角外侧，右溢出则尝试左侧；两侧放不下全尺寸时等比例缩到能舒适栖身（不低于 0.5×，缩放上限随 perch 场所生效、离开即解除，压过情绪放大）。连最小尺寸都容不下才放弃。perch 仅在 idle 时发起；进入 perch 后 work/think/speak 状态不踢出（"陪"语义）。工作台开启时桌面精灵收起，由工作台复合窗口内置的伴工插槽一体化呈现。

**roam**：自补充式 waypoint 循环（每个点停 5–15s），waypoint 在屏幕下半部随机生成。自主档 + idle + 桌面空闲（Runner 上报的空闲秒数 ≥ 90s，未知信号保守不漫游）+ 无 perch 目标时触发（2D/3D 均漫游；2D 走位移积分复合步态）。任何 drag / chat / focus / tier 变化或用户回到桌面通过 `stopRoam` 终止。

**approach（走过去搭话）**：`companion.should_act` 的第三类动作（[autonomy.ts](autonomy.ts) `executeApproach`），仅智能驱动开 + 自主档——本地规则路径不搭话（说什么需要人格）。开场白由后端在同一决策中产出并经 `companion.message` 通道投递（边走边说，气泡随精灵移动），客户端只走位：有焦点窗口落在窗口旁（复用 perch 落位与缩身，搭话后就地陪工），用户在桌面时走到屏幕中下部站定（不动 locale，后续空间决策接管）；途中视线锁定目标中心 6s。锁屏 / 聊天开启时不执行（消息侧由各自的既有门控决定）。低频闸在后端（30 分钟冷却，冷却内连同开场白一起降级 stay，不出现"说了话没走过来"）；协议契约见 [PROTOCOL.md §1.4](../../../docs/PROTOCOL.md)。

**缩放**：`$defaultScale`（用户设置，localStorage）是基准。EMOTIONAL 状态的 excited/surprised/playful 触发 1.3–1.6× 临时放大，静止档不放大。缩放也是 rAF 动画（~300ms），通过容器 `transform: scale()` 实现——与 sprite 内部的程序化动画（呼吸/浮动）在不同 DOM 层，不冲突。

**Backend 零感知**：空间状态单一权威在 Client——Backend 只返回 `should_act` 语义动作，从不产出 locale 字段或像素坐标；Runner 提供感知能力（`system.get_windows` 窗口枚举、`system.get_focused_app` 焦点窗口几何）但 Runner 也不知道空间行为存在。

**Ritual walk**（[ritual-walk.ts](ritual-walk.ts)）：交互类工具（`system.open_application` / `browser_*` / `system.click_at`）在 events.ts 的 tool.call 分发里拦截。目标解析按工具分派：`click_at` 的目标就是点击坐标本身（包成虚拟窗口几何，**execute 即那次点击，不再额外补一次 click**——否则双击）；其余工具从 args（name/url/keyword）提取关键词经 `system.get_windows` 匹配既有窗口，关键词为空不进入仪式（空串会让 `includes` 恒真、匹配到第一个无关窗口）。找到目标后：途中视线锁定目标中心（`$gazeTarget` 显式覆盖指针跟随，2D/3D 同规则）→ 远距离（>400px）fly、近距离 walk 到目标旁 → 抵达后按方位播 `point_left/right` 再接 `click`（open_application 等先在目标中心补一次聚焦点击，click_at 跳过）→ INTERACTING 1.5s → execute 原工具 → 返回原 locale。找不到目标窗口或无处栖身时以一句人格化台词表达（走 speakProactive 的档位门控）后静默走常规工具调用兜底；gaze 的清除走 try/finally，异常路径不泄漏。chat 开启或屏锁时直接执行不走路。
