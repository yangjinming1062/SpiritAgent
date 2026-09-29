# Client 渲染层

负责交互与呈现，本文维护依赖、状态权威和资源生命周期。产品行为见 [DESIGN](../../docs/DESIGN.md)，跨端载荷见 [PROTOCOL](../../docs/PROTOCOL.md)，连接与缓存见 [Client](../README.md)。

## 分层与目录

| 层 | 边界 |
|---|---|
| app | 窗口入口只初始化和挂载；runtime / workflows 不反向导入 windows，窗口间不互相导入 |
| modules | 不依赖 app；conversation 可消费 media 展示原语，character 渲染域可消费 speech，其余跨模块协作由 app 装配 |
| shared | 无业务依赖；其中窗口环境的 IPC 与网关实现不得被主进程导入 |
| `@ipc` | 跨进程契约来源，渲染侧不重复定义 |

character 内含 `actions`、`presentation`、`reactions`、`rendering/{video,fallback}`、`sprite`、`wardrobe`，偏好、人格与空间等保留根入口。跨模块只经公共 barrel，character 渲染域可经 `rendering/video`。

边界由 [ESLint](../eslint.config.mjs)检查，不绕过内部路径。生产数据与资产统一走主进程桥；直连例外须说明 URL 来源。

## 装配与状态归属

[bind-presentation.ts](app/bootstrap/bind-presentation.ts)在渲染前显式绑定窄能力端口，不依赖 barrel 副作用。[presentation-ports.ts](shared/presentation-ports.ts)是绑定真源；角色与语音实现经端口注入，窗口能力由 app 注入，模块不反向导入窗口。

| 状态或能力 | 权威模块与消费方式 |
|---|---|
| 音频播放 | speech；conversation 只维护投影 |
| 表现优先级 | character；消费者不重建状态机 |
| 窗口能力 | app 注入，模块不导入窗口 |
| 偏好、生效档位、活动与空间策略 | character 统一裁决，各窗口不重新推导 |
| 窗口开关与跨窗附件 | 主进程；不能只写来源窗口 store |

订阅与注入随所有者清理，重新挂载不叠加监听。

## 事件与异步生命周期

- 网关路由先校验用户、会话和窗口角色，再分派事件。
- 各窗口独立水合，任何异步回写须核对用户、会话、回合和清理代次；共享代码不代表共享内存。
- 账户切换清理旧账户资料、会话与通知并按 `accountId` 重挂载；桌面精灵按目标账户状态自动进入未完成的 onboarding。完整入口开关状态由主进程维护。
- 鉴权请求仅接受发起会话仍有效的结果。
- `tool.call` 只由宿主执行，按 call_id 去重，不受可见会话过滤；其他会话过程受会话守卫。
- headless 不显示工作态，非当前会话的可见调用自行以引用计数持有工作态，只释放自身仍拥有的状态。
- 消息入列与提醒分开：已提交陪伴消息按 ID 合并，提醒受可见性等条件控制；自动化系统通知不套陪伴打扰闸门。
- 跳转按会话归属选择入口，工作会话不能送进轻语。
- 未读只在所属对话可见时清除。

## 角色呈现契约

### 状态、动作与回执

[companion-store.ts](modules/character/companion-store.ts)维护表现状态优先级，视频系统动作键定义于 [presentation/types.ts](modules/character/presentation/types.ts)，两者不混用。

- 瞬态保存恢复目标，旧计时器不得覆盖持续状态，重复瞬态不嵌套目标；语音准备与播放分开，尾随点播不切 speaking，完成聊天不触发 emotional。
- [actions](modules/character/actions/)按 play_id 去重，目录外观代次落后时先刷新再受理，已认领却不能播放的请求回执 rejected；换包或外观代次变化作废在播实例。动态动作不新增表现状态，表达真实可见（上报 started）期间以 emotional 瞬态呈现、收尾即恢复；判定与回执遵循 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。
- 拖拽释放、接取与长按的整体形变，以及仪式指向与点击提示，经 [gesture](modules/character/sprite/gesture.ts) 由舞台容器呈现，不参与命中；情绪放大只作用于形象层，静止档、栖息与探身时不放大。
- 两个完整入口共用 [侧边伙伴组件](app/components/surface-companion/surface-companion.tsx)；入口与播放器共用 [可见性判断](modules/character/actions/action-visibility.ts)，主进程快照按版本应用，锁屏不依赖 Runner 轮询。播放认领与取消遵循 [播放契约](../../docs/PROTOCOL.md#动作目录与播放)。
- `presentation/render-resolver` 按动作目录和生成状态选择 video 或 fallback，并提供对应的本地化状态；未就绪不空挂视频元素。

### 打扰与自主行为

视觉表达只消费 `play_requested`，目录/任务事件只刷新资产，mood 只更新身份区，控制字段不进正文。

[companion-store.ts](modules/character/companion-store.ts)裁决档位；请求和消费两侧检查[自主行为条件](../../docs/DESIGN.md#自主动作与空间智能)，收起或锁屏后丢弃迟到结果，重连不补话。

生效档位只由精灵窗推送：其他窗口缺少活动覆盖，只改偏好或临时安静，经 storage 事件同步。

[activity.ts](modules/character/activity.ts)的快照单飞，停止后旧结果失效；变化只在可用上报成功后消费，失败期间保留。只报约定粗粒度信号，不传应用名或窗口标题；夜间政策由服务端决定，客户端只传 local_hour，自主媒体/语音偏好在 [prefs.ts](modules/character/prefs.ts)。

### 直接交互与命中

- 精灵舞台对接窗口捕获，手势识别产生语义。
- 单击等待双击判定，双击成立取消待执行单击；就绪后按实际像素命中，光晕和透明留白不计入。
- 捕获在 mousemove 阶段完成，不能等 mousedown；异步命中更新后即使指针静止也重判。
- 菜单只登记自身区域，关闭事件不穿透触发手势。
- 命中区域按窗口捕获 ID 分组：精灵窗默认 0，完整入口在 [surface.tsx](app/bootstrap/surface.tsx) 经 `CaptureWindowIdContext` 提供 1，弹层（含 Portal）不传 ID 即随所在窗口；完整入口不挂载 ID 0 的捕获，否则会切换精灵窗穿透。
- 有效按下即捕获指针并持有窗口鼠标捕获，手势结束前不因透明像素变化开启穿透；取消、失焦、隐藏和卸载统一释放，不触发点击或拖拽释放反馈。
- [reactions](modules/character/reactions/)提供拖拽等反应池；预制台词经 `speakScripted` 送达，不直连语音引擎。

### 偏好与形象水合

水合只恢复偏好，不把设备生效值当偏好回写。未交互面板不上传默认几何，迟到水合不移动已打开面板。

- 角色卡 store 管已保存资料，页面保留编辑草稿；事件、重聚焦和分析轮询不覆盖局部修改。
- 冲突处理见 [PROTOCOL](../../docs/PROTOCOL.md#角色卡编辑)；换号、换形象和卸载使迟到回写失效。
- 外观预览绑定不可变资产路径；新图未加载不能确认，超时保留草稿身份。
- 全身参考面板复用在途请求，旧请求在换号、换头像、卸载和重置时失效。
- 初始化引导的全身图历史索引保存在 `fullbody-reference-store` 的账户持久化键中，缩略图读取主进程资产缓存；全身草稿会被服务端替换清理，因此水合时须经 `preferCache` 预取。
- 账户存储清理同时删除历史索引。
- 产品交互见 [DESIGN](../../docs/DESIGN.md#认识伙伴)。

[wardrobe](modules/character/wardrobe/)管外观与替换策略（llm_may_replace / locked），设计会话锁定五官。

### 空间行为

- [spatial.ts](modules/character/spatial.ts)拥有位置与异步意图生命周期，[spatial-peek.ts](modules/character/spatial-peek.ts)计算探身落位及绘制、命中共用的遮挡矩形，[autonomy.ts](modules/character/autonomy.ts)解释云端意图。
- 衣柜页修改默认比例后经主进程同步到精灵窗，并沿用平滑缩放路径即时生效。
- 拖拽、换包和卸载撤销探身准备；自主请求返回时重验可见性、档位、锁屏、智能开关和服务代次。栖息交互见 [DESIGN](../../docs/DESIGN.md#位置移动与缩放)，坐标与兼容见 [PROTOCOL](../../docs/PROTOCOL.md#动作目录与播放)。
- 探身位置、遮挡和命中随解码首帧一起生效，失败保留旧画面。表演移出遮挡后才上报 `started`，结束时重验返回目标。
- stay 或推理失败不触发本地漫游；本地空间规则仅在智能关闭时生效。
- 本地漫游需真实空闲信号，未知则不动；位置适配不足时放弃，不缩成不可辨识大小。
- 仪式行走可跳过，失败仍执行原工具，`system.click_at` 不补第二次点击；目标经主进程换算到精灵视口，不在视口内不走动，未抵达或指向被打断时跳过指向与预点击。

## 会话与媒体

### 会话与草稿

- 会话历史、输入、待发批次和流式投影归 conversation。
- 停止对会话和 Runner 都是尽力请求，本地仍需收尾，不能表示副作用已撤销。
- 附件绑定加入时的会话，视频上传完成前不可发送；切换后丢弃旧附件及迟到结果。
- 编辑与普通草稿分离，取消恢复普通草稿；编辑文本不解析 Slash，成功只消费修订事件。
- 会话参数显示后端生效值，只接受当前会话最新保存结果；恢复默认删除覆盖。
- 会话只读状态直接消费历史水合的 `info.kind`；陪伴归属由 `system_preset_id` 判定。
- 工作台确认目标不是陪伴后才挂载对话面板。
- 快照、增量与重放按 [Client](../README.md#资产与历史缓存)处理。

### 轻语与入口

轻语固定使用主陪伴会话，不挂参数面板；完整入口打开时收起。附件与通知通过统一入口路由，不把专业目标转成陪伴会话。

### 语音播放

- speech 管音频播放与直接交互台词合成。
- 聊天文字没有合成入口；聊天语音只在点击时播放后端保存的音频，缺失音频经会话语音重试端点恢复。
- 播放状态归 speech，会话气泡与音频视图归 conversation，由应用工作流装配。
- 新播放、停止、换会话、表面隐藏或锁屏使旧下载和播放结果失效。
- 播放结果区分完成、中断与失败；其他声音抢占属于中断，不把语音条标记为不可用。
- 直接交互与仪式反馈用 `speak`（不落盘），预制/反应用 `speakScripted`（落盘）；朗读文本清理不改写聊天原文。
- 限额和字节缓存归主进程。

### 媒体查看

每个窗口独立挂载查看器，经端口打开；完整入口的浮层与命中限定在内容面板，保留外侧伙伴。`MEDIA:` 标记在实时和历史投影中移除，结构化媒体才是展示来源；跨 chunk 保留待解析前缀。媒体字节经主进程读取，临时 URL 按 MIME 创建并在卸载回收。

## 记忆与场景

### 记忆页面

记忆页面按预设重建列表与编辑状态，迟到结果失效；保存一条记录不清除其他未保存草稿，不增加人工审核流程。

### 场景状态与背景

- [scene-store.ts](modules/scene/scene-store.ts)分别维护当前环境、创建任务、图片重生成状态与分页场景库；水合按后端版本、请求代次和账号清理代次丢弃旧结果。
- 生活空间场景页以场景库为入口，详情按场景 ID 单独读取，离开详情后保留会话内编辑草稿。
- 当前环境的替换图预加载成功后才切换背景；图片加载失败保留旧图，任务与政策仍按后端状态刷新。
- 参考图、外部制作与上传共用创建任务状态，重生成独立跟踪且不使成品失效。
- 保存、启用与失败呈现见 [DESIGN](../../docs/DESIGN.md#场景与当前环境)。

## 视频渲染

- 视频层消费 manifest 与透明 WebM；字节走主进程资产桥和内容哈希缓存，双 video 待新帧就绪后替换旧画面。
- 命中按实际播放时间查询逐帧 alpha 遮罩，并扣除等比显示留白。
- 移动与拖拽由容器位移表达，播放不驱动嘴部或视线。
- 包未就绪或加载失败由 render-resolver 落 [fallback](modules/character/rendering/fallback/)，不空挂视频元素；蛋上区分准备中、生成中、失败与尚未就绪。

## 主题与玻璃效果

控件消费共享 panel 与语义 token，不硬编码主题色。首帧播种遵守 Client 规则，弹层命中随拖动更新。

减透明由 OS、设备、用户偏好和帧预算共同决定，只有用户偏好上云；监视器随表面释放，后台时间不计入性能判断。场景预模糊结果按图、尺寸和主题失效，避免叠加 CSS 模糊；降级不得填满圆角外透明区。

## 验证入口

命令见 [Scripts](../../scripts/README.md#按改动选择验证)。覆盖换窗、换号、卸载、迟到回写、播放抢占、静止指针命中和 GPU 回退。桌面效果需真实平台验证，几何修改另验证呈现与命中共用同一最终坐标。
