# Renderer 架构指南

本文定义渲染层依赖、状态和资源生命周期。产品行为见 [DESIGN](../../docs/DESIGN.md)，跨端载荷见 [PROTOCOL](../../docs/PROTOCOL.md)，多窗口连接与缓存见 [Client](../README.md)。

## 1. 分层与目录归属

`app` 负责装配、路由、跨模块流程和窗口；`modules` 管 conversation、character、speech、media、memory、scene；`shared` 只放无业务依赖的 UI 与平台适配。窗口入口只初始化和挂载，跨进程契约来自 `@ipc`，不在渲染侧另定义。

character 内部再分 `actions`、`presentation`、`reactions`、`rendering/{video,fallback}`、`sprite`、`wardrobe`，以及偏好、人格、空间等根文件。跨模块只经公共 barrel（character 渲染域可经 `rendering/video`）。

## 2. 依赖规则

app 经公共入口使用 modules；runtime / workflows 不反向导入 windows，窗口之间不互相导入。modules 不依赖 app 或其他模块；例外仅为 conversation 读取 media 展示能力。shared 不依赖业务。

边界由 [ESLint](../eslint.config.mjs)检查，不通过内部路径绕过。生产数据与资产统一走主进程桥，禁止裸 fetch；确需直连的例外须说明 URL 来源。

## 3. 跨模块接缝与绑定纪律

[bind-presentation.ts](app/bootstrap/bind-presentation.ts)在渲染前显式绑定窄能力端口，不依赖 barrel 副作用。[presentation-ports.ts](shared/presentation-ports.ts)是绑定真源；角色与语音实现经端口注入，窗口能力由 app 注入，模块不反向导入窗口。

speech 拥有播放状态，conversation 只维护其投影；character 拥有表现优先级，消费者不重建状态机。订阅与注入随所属生命周期清理，重新挂载不叠加监听。

## 4. 事件路由与多窗口运行时

网关路由先校验用户、会话和窗口角色，再分派事件。各窗口独立水合，任何异步回写须核对用户、会话、回合和清理代次；共享代码不代表共享内存。

`tool.call` 只由宿主执行，按 call_id 去重，不受可见会话过滤；其他会话过程受会话守卫。headless 不显示工作态，非当前会话的可见调用自行以引用计数持有工作态，只释放自身仍拥有的状态。消息入列与提醒分开：已提交陪伴消息按 ID 合并，提醒受可见性等条件控制；自动化系统通知不套陪伴打扰闸门。跳转按会话归属选择入口，工作会话不能送进轻语。未读只在所属对话可见时清除。

## 5. 当前归属与限制

偏好、生效档位、活动监视和空间策略共用 character 的裁决，不在各窗口重新推导。shared 中的 IPC 与网关依赖窗口环境，主进程不得导入。跨窗口附件由主进程转交，不能只写来源 store。

## 6. 角色呈现契约（modules/character）

### 动画状态机与动作键

[companion-store.ts](modules/character/companion-store.ts)拥有 8 态优先级真源（idle / listening / thinking / speaking / working / emotional / interacting / disconnected）。瞬态保存恢复目标，旧计时器不得覆盖新持续状态，重复瞬态不嵌套恢复目标。语音准备与实际播放分开；尾随点播不切 speaking，聊天完成不触发 emotional。

视频层系统动作键（[presentation/types.ts](modules/character/presentation/types.ts)）是另一套：`idle` / `walk_left` / `walk_right` / `drag`。动态动作以 play_id + epoch 经 `actions` 下发，不进入 8 态机。`presentation/render-resolver` 按 `videoReady` 裁决 video 或 fallback；包未就绪不空挂视频元素。

[actions](modules/character/actions/)按 play_id 去重、pack_id 归属与 appearance epoch 失效迟到结果，回执含 rejected / interrupted。

### 情绪、打扰与自主行为

视觉表达只兑现 `companion.action.play_requested`；目录与任务事件只刷新身份/资产，不直接切动画。mood 只更新身份区，不生成消息或切动画。控制字段不得编码进聊天正文。

打扰档位为 still / normal / autonomous（[companion-store.ts](modules/character/companion-store.ts)）。still 硬锁并压制主动表达；请求与消费两侧均检查档位、可见性及锁屏，视觉请求另检查空闲。收起或锁屏后丢弃迟到表达，重连只恢复状态，不自动补话。

活动快照单飞，停止后旧结果失效；变化信号在可用上报成功后才消费，失败期间保留变化。只上报约定的粗粒度信号，不上传应用名或窗口标题。夜间政策权威在服务端，客户端只传 `local_hour`（[activity.ts](modules/character/activity.ts)）；自主媒体/语音偏好在 [prefs.ts](modules/character/prefs.ts)。

### 用户直接交互与命中

精灵舞台对接窗口捕获，手势识别产生语义。单击等待双击判定，双击成立取消待执行单击；就绪后按实际像素命中，光晕和透明留白不计入。捕获在 mousemove 阶段完成，不能等 mousedown；异步命中更新后即使指针静止也重判。菜单只登记自身区域，关闭事件不穿透触发手势。有效按下即捕获指针并持有窗口鼠标捕获，手势结束前不因透明像素变化开启穿透；取消、失焦、隐藏和卸载统一释放，不触发点击或拖拽释放反馈。

[reactions](modules/character/reactions/)提供拖拽等反应池；预制台词经 `speakScripted` 送达，不直连语音引擎。

### 偏好与形象水合

水合只恢复偏好，不把设备生效值当偏好回写。未交互面板不上传默认几何，迟到水合不移动已打开面板。

角色卡 store 管已保存资料，页面保留编辑草稿；事件、重聚焦和分析轮询不覆盖局部修改。冲突处理见 [PROTOCOL](../../docs/PROTOCOL.md#12-伙伴生命周期方法方法级契约)；换号、换形象和卸载使迟到回写失效。外观预览绑定不可变资产路径；新图未加载不能确认，超时保留草稿身份。全身参考面板复用在途请求，旧请求在换号、换头像、卸载和重置时失效。

[wardrobe](modules/character/wardrobe/)管外观与替换策略（llm_may_replace / locked），设计会话锁定五官。

### 空间行为（位置 × 移动 × 缩放）

[spatial.ts](modules/character/spatial.ts)拥有位置，[autonomy.ts](modules/character/autonomy.ts)解释云端意图。拖拽取消旧路径，松手按可见范围落位并保存；历史屏外落点恢复到屏内，不以容器旋转或半隐藏表示姿态。完整入口打开冻结桌面移动；stay 或推理失败不转成本地漫游，本地规则仅在智能关闭时生效。

本地漫游需真实空闲信号，未知则不动；位置适配不足时放弃，不缩成不可辨识大小。仪式行走可跳过，失败仍执行原工具，`system.click_at` 不补第二次点击。

## 7. 会话、语音、媒体、记忆与场景契约

### Conversation（modules/conversation）

会话历史、输入、待发批次和流式投影归 conversation。停止对会话和 Runner 都是尽力请求，本地仍需收尾，不能表示副作用已撤销。

附件绑定加入时的会话，视频上传完成前不可发送；切换后丢弃旧附件及迟到结果。编辑与普通草稿分离，取消恢复普通草稿；编辑文本不解析 Slash，成功只消费修订事件。会话参数显示后端生效值，只接受当前会话最新保存结果；恢复默认删除覆盖。工作台确认目标不是陪伴后才挂载对话面板。快照、增量与重放按 [Client](../README.md#资产与历史缓存)处理。

### 轻语与窗口入口

轻语固定使用主陪伴会话，不挂参数面板；完整入口打开时收起。附件与通知通过统一入口路由，不把专业目标转成陪伴会话。

### Speech（modules/speech）

speech 管音频播放与直接交互台词合成。聊天文字没有合成入口；聊天语音只在点击时播放后端保存的音频，缺失音频经会话语音重试端点恢复。播放状态归 speech，会话气泡与音频视图归 conversation，由应用工作流装配。新播放、停止、换会话、表面隐藏或锁屏使旧下载和播放结果失效。

播放结果区分完成、中断与失败；其他声音抢占属于中断，不把语音条标记为不可用。直接交互与仪式反馈用 `speak`（不落盘），预制/反应用 `speakScripted`（落盘）；朗读文本清理不改写聊天原文。限额和字节缓存归主进程。

### Media（modules/media）

每个窗口独立挂载查看器，经端口打开。`MEDIA:` 标记在实时和历史投影中移除，结构化媒体才是展示来源；跨 chunk 保留待解析前缀。媒体字节经主进程读取，临时 URL 按 MIME 创建并在卸载回收。

### Memory 与 Scene（modules/memory、modules/scene）

记忆页面按预设重建列表与编辑状态，迟到结果失效；保存一条记录不清除其他未保存草稿，不增加人工审核流程。

[scene-store.ts](modules/scene/scene-store.ts)分别维护当前环境、任务与分页场景库；水合按后端版本、请求代次和账号清理代次丢弃旧结果。已启用图片预加载后替换当前图；图片加载失败保留旧图，任务与政策仍按后端状态刷新。参考图、外部制作与上传共用任务状态。保存、启用与失败呈现见 [DESIGN](../../docs/DESIGN.md#61-双入口窗口架构与交互范式)。

## 8. 渲染域（modules/character/rendering）

视频层消费视频包 manifest 与透明 WebM 片段：包字节走主进程资产桥与内容哈希缓存，播放为原地循环；动作切换经双 video 首帧就绪后淡入淡出交换，不以黑帧或空帧过渡。命中按实际播放时间查询逐帧 alpha 遮罩，并扣除等比显示留白。移动与拖拽由容器位移表达，播放不驱动嘴部或视线。包未就绪或加载失败由 render-resolver 落 [fallback](modules/character/rendering/fallback/)，不空挂视频元素。

## 9. 界面、主题与玻璃效果

控件消费共享 panel 与语义 token，不硬编码主题色。首帧播种遵守 Client 规则，弹层命中随拖动更新。

减透明由 OS、设备、用户偏好和帧预算共同决定，只有用户偏好上云；监视器随表面释放，后台时间不计入性能判断。场景预模糊结果按图、尺寸和主题失效，不再叠 CSS 模糊；降级不得填满圆角外透明区。

## 10. 验证入口

命令见 [Scripts](../../scripts/README.md#8-按改动选择验证)。覆盖换窗、换号、卸载、迟到回写、播放抢占、静止指针命中和 GPU 回退。桌面效果需真实平台验证，几何修改另验证呈现与命中共用同一最终坐标。
