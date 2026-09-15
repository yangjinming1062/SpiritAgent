# Renderer 架构指南

本文是渲染层的修改入口，定义依赖方向、状态归属与容易引发回归的约束。产品体验归 [DESIGN](../../docs/DESIGN.md)，跨模块载荷归 [PROTOCOL](../../docs/PROTOCOL.md)，主进程与窗口职责归 [client README](../README.md)。

修改跨模块接缝读 §1–§4；角色状态、交互与移动读 §6；会话及媒体读 §7；渲染资源读 §8。2D 几何、素材与动作机制按需读 [Puppet README](modules/character/rendering/2d/puppet/README.md)，无需为普通会话或界面修改加载该部分。

## 1. 分层与目录归属

- `app/` 组合模块能力：bootstrap 装配、runtime 网关路由、workflows 跨模块流程、windows 三窗口体验与 onboarding 引导。
- `modules/` 持有可复用能力：conversation、character、speech、media、memory 与 room；2D / 3D 渲染属于 character 的渲染域。
- `shared/` 是无业务依赖的 UI、主题、存储与平台适配代码；跨进程契约在 `client/shared/ipc/`（`@ipc`），不与本目录混用。
- 各窗口入口只做初始化与根组件挂载；生活空间、工作台和精灵窗分别装配。

## 2. 依赖规则

| 调用方 | 允许依赖 | 禁止依赖 |
|---|---|---|
| `app/**` | 模块公共入口、渲染域入口与 shared | runtime / workflows 反向导入 windows；三个 windows 互相导入 |
| `modules/*` | 本模块、shared、跨进程契约 | app、其他同级模块，以下只读例外除外 |
| `character/rendering/**` | character 公共入口、speech 口型振幅 | app、conversation、media、character 内部深路径 |
| `shared/**` | 共享代码、第三方基础库 | app 与业务模块 |

只读例外为 conversation → media 的展示原语与媒体源解析，以及 character 渲染域 → speech 的口型振幅订阅。app 访问模块使用公共 barrel，渲染域提供独立的 2D / 3D 入口。

[ESLint 配置](../eslint.config.mjs)检查这些边界；新增依赖边时同步规则和本文，不能用内部文件直连绕过公共入口。

## 3. 跨模块接缝与绑定纪律

[bind-presentation.ts](app/bootstrap/bind-presentation.ts)在各窗口渲染前显式绑定能力，不依赖 barrel 的副作用导入顺序：

- [呈现端口](shared/presentation-ports.ts)向 conversation 提供角色状态、TTS 与媒体查看能力。接口应表达消费者需要的能力，避免暴露另一模块的完整状态。
- [语音接缝](modules/conversation/voice-link.ts)由 [conversation-speech 工作流](app/workflows/conversation-speech.ts)绑定。播放队列归 speech，消息中的播放状态和时长只是投影，不再维护第二套播放状态机。
- 模块需要调用窗口能力时定义窄槽位，由装配层注入；例如仪式行走台词与通知打开轻语。工作流不能为此反向导入窗口组件。

订阅和注入能力要有与其生命周期一致的清理；登出、关闭窗口与开发期重新挂载不能留下重复监听或旧用户状态。

## 4. 事件路由与多窗口运行时

[网关路由](app/runtime/gateway-event-router.ts)负责鉴权、会话和窗口角色守卫，再按能力分派给 handlers。精灵窗宿主持唯一 WebSocket 与 Runner 派发能力，生活空间 / 工作台通过主进程代理；连接架构见 [client README §4](../README.md)。

同一份模块在不同窗口运行不代表共享内存。每个窗口独立水合，变化经 IPC、网关与既有同步机制传播；语音、附件、草稿和历史的异步回写必须核对用户、会话、回合及清理代次。

### 设备指令与会话事件

[tool-dispatch.ts](app/runtime/handlers/tool-dispatch.ts)只在宿主执行。协议语义见 [PROTOCOL §1.3](../../docs/PROTOCOL.md)：

- `tool.call` 是用户级设备指令，不受当前会话闸门拦截；`message.*`、`tool.start` 和 `tool.complete` 仍受会话守卫限制。
- 重放按 `call_id` 去重，容量覆盖服务端重放窗口。后端丢弃迟到结果无法撤销已发生的本机副作用，不能把执行去重后移。
- `headless` 指令照常执行，但不自行切换精灵工作态。
- 非无头且不属于当前可见会话的指令需自行管理工作态，因为它收不到当前界面的回合终局。并发用引用计数，进入和复位显式强制切换；退出时只复位仍由本分支持有的工作态或仪式交互态，不能覆盖新到达的状态。

### 主动消息与通知

陪伴消息只消费后端已提交的 `companion.message`，追加到主陪伴会话；是否展示提醒不影响消息入列。普通自动化消费 `system.notification`，带会话标识时提供查看入口，不使用陪伴打扰档位过滤。

[session-delivery.ts](app/workflows/session-delivery.ts)按会话归属选择轻语或工作台；不能把工作会话传给轻语，否则会回到主陪伴会话并丢失目标上下文。历史水合与送达形状归 [PROTOCOL](../../docs/PROTOCOL.md)。

## 5. 当前归属与限制

- conversation 经呈现端口下达思考、倾听等表现命令，优先级仍由 character 裁决；不能在消费者中复制另一套优先级规则。
- `prefs.ts` 与 character 的打扰偏好、生效档位相邻维护。若调整模块归属，需保留偏好与活动覆盖的单一裁决点。
- shared 中的网关、IPC、资产桥和 OPFS 是渲染侧平台适配；主进程不能直接导入这些依赖窗口环境的实现。
- 附件入口由所在窗口接收，经会话公共入口或主进程转交。增加入口时核对目标会话和跨窗口交付，不假设所有附件都在精灵窗处理。
- 目录中的内部拆分不改变公共入口与依赖约束；实际限制和调用需求决定是否重组，不预建尚无消费者的工作流。

## 6. 角色呈现契约（modules/character）

### 动画状态机（8 态）

状态与优先级以 [companion-store.ts](modules/character/companion-store.ts)为准，不在各窗口重建状态表。

持续状态的低优先级更新不能打断高优先级状态，需要退出工作态等场景时显式使用强制转换。瞬态 `emotional` / `interacting` 是例外：保存原态、定时恢复，期间若已有新的持续状态则保留该状态；不能让旧计时器覆盖新活动。重复瞬态不把恢复目标套成另一层瞬态。

陪伴语音准备期间维持 thinking，实际播放进入 speaking。停止、异常、新消息或锁屏会中止播放并按当前归属复位；默认文字模式的尾随播放按钮不切 speaking，但仍可驱动口型。聊天完成只收尾消息与语音，不触发 emotional。分段交付与幂等边界见 [PROTOCOL §1.4](../../docs/PROTOCOL.md)。

### 情绪表达与当前心情

视觉 `companion.affect` 与身份状态 `companion.mood` 分离。视觉情绪只驱动可用动画与模型参数，`neutral` 不单独触发 emotional；2D 可兑现面部通道，3D 只消费已有 clip。枚举与能力清单归 [PROTOCOL §1.4](../../docs/PROTOCOL.md)，3D 映射与降级归 [PIPELINE §5](../../docs/PIPELINE.md)。

心情事件与首次 Persona 水合更新同一个心情状态，显示在头像和名称下方。它不切换动画、不生成消息，也不被主动打扰档位或桌面显隐过滤。

### 三档打扰与自主行为

[companion-store.ts](modules/character/companion-store.ts)由用户偏好和活动覆盖计算生效档位；[activity.ts](modules/character/activity.ts)提供锁屏、焦点和空闲感知。空间策略、主动提醒与语音共用这一结果，不能各自推导。产品条件归 [DESIGN §6.2](../../docs/DESIGN.md)，上云契约归 [PROTOCOL §2.4](../../docs/PROTOCOL.md)。

视觉表达与空间智能的请求、消费两侧都检查自主档、桌面精灵可见和解锁条件；视觉请求另检查空闲阈值与开关，阈值在 activity 维护。屏锁或完整入口打开后，迟到的表达不能重新启动桌面动作。恢复在线或解锁只恢复视觉，不自动补台词。

活动监视器同时向陪伴等待队列发送轻量可用性与变化事件，是否进入主动回合由后端裁决；重新连接或解锁不直接补话。快照轮询单飞，Runner 停止或停止监视后的迟到快照不能更新状态或发送新信号；变化事件在可用信号上报成功后才消费。不可用或上报失败期间的变化保留至恢复，即使随后回到原应用类别或退出全屏，也不能漏掉已经发生的变化。

空闲变体池随场景选择，缺少动画时回退 idle；它与云端情境表达独立，不要求模型具有完整变体目录。

### 用户直接交互与命中

[精灵舞台](app/windows/sprite/behaviors/sprite-stage.tsx)负责指针事件与窗口命中接缝，[手势识别](app/windows/sprite/behaviors/gesture-tracker.ts)把连续操作转换为语义。单击需给双击留出判定窗口，双击成立后取消尚未执行的单击。

- 形象就绪前可退回矩形命中；就绪后按可见形象精化，透明留白与 CSS 光晕不参与交互。
- 3D 使用 [剪影命中](modules/character/rendering/3d/silhouette-hit.ts)，2D 使用当前最终网格与纹理透明度。异步结果更新后重做捕获判断，静止指针也要得到新结果。
- 命中捕获须在 mousemove 阶段完成；穿透时只能转发移动事件，等 mousedown 再解除穿透会丢点击。
- 菜单登记自己的交互区域，关闭时拦截事件，避免关闭操作同时戳到或拖动精灵；不修改整个窗口的置顶状态。
- 云端戳摸反应失败或受限时使用预制反馈，拖拽始终为机械反馈；两者边界见 [DESIGN §6.3](../../docs/DESIGN.md)。预制音频沿用共享语音缓存，缓存失效规则不能只依赖文案。
- 戳摸与聊天统计独立于是否成功调用 LLM，上报语义归后端；渲染侧不另写统计记忆。

### 偏好与形象水合

localStorage 是窗口缓存，主进程配置镜像与云端所有权见 [PROTOCOL §2.4](../../docs/PROTOCOL.md)。[prefs.ts](modules/character/prefs.ts)同步偏好和原子状态；水合不能把设备派生的生效档位当成用户偏好写回。

高频面板几何先防抖，未交互过的挂载不上传默认值；已打开面板不因迟到水合突然跳位置，下一次打开应用新值并按视口钳制。精灵坐标由主进程文件单独保存，不以面板缓存替代。

引导状态未知时先等待，避免显示错误阶段。头像与全身确认分别消费协议结果，不在渲染端组装供应商请求。角色编辑保持已锁定身份与完成状态；衣橱切换在新资产完整就绪后替换，旧装保持可见。资产获取失败与生成失败提供不同恢复路径，不能把下载重试变成付费重生成。

独立全身种子图由 character 的共享面板供 onboarding 与角色设置使用；引导传入已上传的参考图，两处均可更换或移除，参考职责见 [PIPELINE §1](../../docs/PIPELINE.md#1-3d-链拓扑)。在途请求跨面板复用，重新进入先读取已有结果，只有引导首次缺图才自动生成。账号清理或激活头像变化使迟到响应失效；生成或读取失败后须成功加载当前结果才能继续，不能拿旧预览确认新图。2D 立绘的重新加载只读取已有草稿，不触发付费生成。

### 空间行为（位置 × 移动 × 缩放）

[spatial.ts](modules/character/spatial.ts)拥有位置、缩放、场所和移动方式，舞台只消费；[autonomy.ts](modules/character/autonomy.ts)消费云端语义，坐标始终由客户端计算。

- 空间订阅在宿主初始化并统一清理。拖拽立即取消旧移动；完整入口打开后冻结桌面移动，重新显示时不能复活旧路径。
- 自主档且桌面可见时才咨询智能空间决策；关闭智能驱动后才使用本地陪工 / 漫游规则。云端返回 stay 或失败不自动转本地移动。
- 窗口旁落位尝试边缘空隙，放不下时等比例缩小，低于可辨识的最小比例仍不够则放弃。进入陪工后，思考和说话不把伙伴踢走；离开时解除该位置的缩放上限。
- 本地漫游要求真实桌面空闲信号，未知时不漫游；拖拽、对话、焦点或档位变化及时取消。
- 显式任务视线目标优先于指针，结束与取消都清除。2D 扶边由形变与姿态纹理处理，外层舞台保持水平，避免坐标与命中二次倾斜。

[仪式行走](modules/character/ritual-walk.ts)是工具执行的可省略呈现。找不到目标或锁屏、打开完整入口时直接执行原工具；关键词为空不能匹配任意窗口。`system.click_at` 的原工具已是实际点击，不能再补预览点击；其他目标可按需要先聚焦。异常和取消时清除注视与移动资源，不重复执行工具。

## 7. 会话、语音、媒体、记忆与房间契约

### Conversation（modules/conversation）

会话列表、消息历史、流式投影、待发批次与输入归 conversation；展示使用端口和公共媒体原语。停止同时请求中断会话与 Runner，均为 best-effort，本地仍需完成回合收尾，不能把界面停止当成已撤销副作用。

- 陪伴与专业会话在入口和异步水合两侧隔离；IM 会话只读，输入禁用不能替代服务端守卫。预设和参数作用域见 [PROTOCOL §1.8 / §2.4](../../docs/PROTOCOL.md)。
- 连发消息的等待与冲刷规则归 [DESIGN §6.6](../../docs/DESIGN.md)；消息完成不能越过尚未到期的等待窗口。
- 附件通过粘贴、拖放与选择器进入，视频先上传，未完成时不能发送；失败可重试或移除。附件绑定加入时的会话，切换后丢弃旧附件及其迟到上传结果。纯图片 / 视频投喂和混合文件的入口分流见 [DESIGN §6.3](../../docs/DESIGN.md)。
- 时间条、语音条、媒体卡和运行轨迹都是结构化消息的投影，不写回正文；完整媒体交付与历史缓存规则见 [client README §4](../README.md)。

### Speech（modules/speech）

speech 拥有合成、音频播放、振幅与语音队列，消息读写经 §3 的投影完成。播放取消与替换需校验当前音频和代次，防止旧合成结果重启播放；嘴型只消费实际输出振幅，不从聊天正文推断。

[音频输出](modules/speech/audio-track.ts)与语音条共用播放路径；[音色校验](modules/speech/voice-validity.ts)在目录就绪后检查已选声音，缺失时提示重选。语音请求限额与缓存归主进程媒体入口，不能在窗口复制限流值或另一套音频缓存规则。

### Media（modules/media）

每个可打开媒体的窗口独立挂载查看器，打开能力经端口注入；精灵窗内轻语是浮层，生活空间与工作台是独立窗口，不能假设弹层共享内存。

媒体字节经主进程桥获取，临时 URL 在卸载时回收。签名 URL 是交付地址，不是稳定身份；签名、缓存与媒体永久存储契约见 [PROTOCOL](../../docs/PROTOCOL.md) 和 [client README](../README.md)。

### Memory 与 Room（modules/memory、modules/room）

[journal-store.ts](modules/memory/journal-store.ts)维护片刻与日记的分页、水合和增量。记忆管理按预设隔离，换预设或卸载后旧请求不能覆盖当前页面；具体管理契约见 [client README 的预设记忆交互](../README.md#预设记忆交互)。

[backdrop-store.ts](modules/room/backdrop-store.ts)负责房间等待、就绪、失效、失败恢复与历史。工作台不使用房间图；换装联动和锁定政策归 [DESIGN §6.1](../../docs/DESIGN.md)。

房间页收集参考图与文字要求，页面内重试保留输入，允许更换或移除图片；选图的迟到结果在卸载或账号清理后丢弃。生成请求提交期间不重复提交，也不提前轮询旧房间而误判完成；旧水合响应按生成代次丢弃，后端接受任务后才启动轮询，提交失败则重新水合当前房间与历史。参考图契约见 [PROTOCOL §1.2](../../docs/PROTOCOL.md#12-伙伴生命周期方法方法级契约)。

## 8. 渲染域（modules/character/rendering）

### 3D 初始化、功耗与缓存

渲染器回退顺序归 [client README §4](../README.md)。[3D 入口](modules/character/rendering/3d/companion-3d.tsx)等待异步引擎就绪后再加载模型；经典 WebGL 回退必须更换 canvas，不能复用已获得另一种上下文的画布。

- Chromium 为后台聊天关闭节流，因此引擎自己控制帧率与停止。[PowerProfile](modules/character/rendering/3d/PowerProfile.ts)决定活跃、空闲和休眠档，参数只在源码维护。首次模型落定前保持活跃；隐藏窗口不能依赖 rAF，用定时器续接；恢复时钳制时间增量，避免动画跳变。
- [GLB 实例缓存](modules/character/rendering/3d/gltf-instance-cache.ts)按内容哈希复用解析模板。骨骼和实例动画状态需深克隆隔离；GPU 资源由模板引用计数管理，实例卸载不能释放其他实例仍使用的资源，淘汰与登出才安全回收。
- [OPFS 缓存](shared/lib/opfs-blob-cache.ts)共享 GLB / PSD 的串行写入、预算淘汰与魔术字节校验；拉取、缓存写入和装配都尊重取消及登出代次，旧请求不能在清空后写回旧用户资产。缓存上限由各格式适配器维护。
- 引擎 tick 异常后停止循环并上报，避免逐帧重抛；形象回退由 [companion-store.ts](modules/character/companion-store.ts)统一选择，产品可见性要求见 [DESIGN §1.2](../../docs/DESIGN.md)。

### Puppet（2D 高保真渲染路径）

2D 使用分层立绘与独立扶边姿态包。修改网格、绑骨、形变、动作或预览时读 [Puppet README](modules/character/rendering/2d/puppet/README.md)；资产描述符、发布与生成恢复归 [PIPELINE §6](../../docs/PIPELINE.md)。

### Mesh2D 水合 Store

[mesh2d-store.ts](modules/character/rendering/2d/mesh2d/mesh2d-store.ts)维护 2D 行镜像、PSD 缓存与命中总线：

- 先取得资产行和描述符，再装配木偶；不能颠倒依赖顺序。渲染模式切换保持幂等，避免跨窗口回环。
- 本机缓存和远端字节均验证 PSD 魔术字节，中止与读取失败向装配层传播，由其决定回退。
- 部件命中由当前实际呈现的运行时写入，隐藏的 PSD、预览或旧资源不能覆盖桌面命中。
- 生成失败保留状态与原因；下载失败走读取重试，重新生成遵循生成契约。
