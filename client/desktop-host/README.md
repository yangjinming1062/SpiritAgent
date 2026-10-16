# Windows 桌面宿主

原生宿主、恢复与构建的工程入口，Electron 生命周期归主进程 README。命令和载荷在 [main.rs](src/main.rs)，接管及恢复在 [windows.rs](src/windows.rs)，运行窗口在 [applications.rs](src/windows/applications.rs)，工作区在 [workspace.rs](src/windows/workspace.rs)。

| 模式 | 职责 |
|---|---|
| `host` | 从 stdin 接收受限 JSON，使用主进程登记的 HWND 接管桌面 |
| `guardian` | 独立监测主进程、host 与十秒续租，异常或 Ctrl+Alt+Shift+D 时恢复 |
| `recover` | 处理待恢复 journal |
| `check-runtime` | 验证可执行文件可加载，无桌面接管副作用 |
| `display-bounds` | 按 Electron 转换后的显示器矩形只读查询原生像素尺寸，不访问 Explorer、journal 或桌面接管 |

## 挂载与输入

宿主分别识别 `SHELLDLL_DefView` 图标层与壁纸承载层，使视频位于原生图标下方、系统壁纸上方。透明图标层结构采用同父层的分层视频窗口；传统结构使用图标层后方的 WorkerW，缺失时在 guardian 就绪后有界请求 Explorer 创建并重新识别。不能只按 Windows 版本选择层级，也不能通过隐藏共享图标层或修改图标偏好实现接管。仅捕获并隐藏交互屏任务栏，重探测发现任务栏移出该屏时先恢复原状态。界面保持普通顶层；接管只接受同一显示器的一份背景与一份界面。

附着前核对父进程、窗口身份和完整 DPI context。请求须对应完整显示器，四项物理几何误差均不超过两像素；附着后复验外框和客户区。Electron 关闭 `frame`、`thickFrame`，helper 去掉非客户区并按实际 Explorer 结构设置背景样式。样式、父层、几何和置顶信息都进 journal。视频不接收鼠标输入，透明界面只命中已登记交互区域，其余区域交给 Explorer；挂载与巡检验证图标在视频上方，不能再以视频获得桌面输入作为成功依据。

`display-bounds` 接受一组近似物理矩形，设置自身 PMv2 context 后复用上述匹配规则，通过 `GetMonitorInfoW` 返回真实矩形；无法匹配的项返回空值。Electron 的 DIP 宽高已取整，乘缩放因子或再次转换都可能偏差一至两像素，不能据此记录桌景分辨率。该查询独立于桌面模式，在启动及显示器变更时刷新；缓存绑定显示器 ID 和当前几何，读取失败时由用户手动填写。

`focus` 仅接受身份、父层和可见性匹配的登记窗口，且系统须处于桌面前台。界面即使已有焦点仍先置前；修饰键与鼠标释放后临时附着输入线程，完成复验焦点和实际前台。线程解除失败退出 host，交 guardian 恢复。桌面前台只认可挂载层及自有窗口家族。

## 运行窗口

枚举当前虚拟桌面各屏普通与最小化窗口，附带交互屏归属，排除系统壳、工具浮窗、隐藏和产品窗口。会话共用 WinEvent，命令及轮询前处理消息，合并相邻重复事件但保留移动、销毁顺序；位置与跨屏移动后更新归属，每两秒补扫，队列溢出清登记后全量校准，COM 与监听随会话释放。

helper 通过 `get_jump_list_tasks` 只读解析用户 Jump List 的 `Task` 分类，通过 `activate_jump_list_task` 重新读取并校验任务 ID、目标程序和桌面会话后启动。解析仅接受有界的 Shell Link 数据，支持 exe 与 `shell:AppsFolder` AUMID 任务；无法识别的链接跳过，仅因 `--user-data-dir` 不同的同一任务合并。损坏文件跳过并记录诊断，不把程序参数交给渲染层，任务解析诊断不打断现有菜单。

未变化不广播；快照按 revision 分批，每行最多 64 KiB，主进程收齐才应用，换代丢弃未完成批次。总量和等待预算归 [explorer-desktop-host.ts](../main/lifecycle/explorer-desktop-host.ts)。

- `activate_external` 复核会话 ID、窗口身份、虚拟桌面和前台；固定项与目录入口的异屏回退允许跨屏迁移，其余请求要求窗口仍在交互屏。迁移保留最大化状态并按 DPI 适配工作区，恢复最小化并优先所属模态窗口；程序自行约束尺寸时，校正一次后以稳定的可见窗口完整进入目标工作区为准。临时连接前台和目标输入线程，去重、反序释放并复验实际前台。
- `close_external` 整批核验交互屏归属后逐个投递 `WM_CLOSE`，有未决模态对话框则拒绝整批，投递前再次核验；回执不代表窗口已关闭。
- 隐藏 owner 下多个主窗保持分支隔离，不能用 `GA_ROOTOWNER` 代替用户所选窗口；禁用主窗只查找该分支可交互弹窗。

`launch_external` 与 Jump List 共用原生启动定位。文件及快捷方式通过带目标显示器的 Shell 启动，打包应用通过激活接口取得进程身份；工作线程承担启动等待，host 心跳不阻塞。定位监听与会话同生命周期，15 秒内关联启动前窗口集合、进程身份及新增窗口，一次成功即消费；歧义、超时或拒绝移动报告实际失败，不能持续吸附同程序窗口。

## 工作区与层级

Shell AppBar 仅注册交互屏底部预留，协商位置时计入系统已有占用；不新增顶部预留，也不因打开菜单、轻语或功能窗口扩大预留。尺寸由 Client 的共享布局传入，helper 以原生像素协商并上报实际工作区，供原生窗口定位和内部界面布局使用。注册、位置变化处理、延迟启动定位与 guardian 恢复共用锁；probe 不注册 AppBar 或调整外部程序。共享 Explorer 前台按指针所在屏幕判定，其他屏桌面操作不取得交互屏焦点资格。

WinEvent 合并显隐、前台、位置、还原和移动缩放后校正普通窗口：先平移，过大再缩小，不激活窗口；系统菜单、输入法候选、隐藏、最小化和真正全屏不调整。全屏判断排除 Explorer 与 WorkerW；期间收起界面，退出恢复，回系统桌面不收起界面。

`showInactive()` 可能让 Windows 收窗进工作区，须按实际原生矩形异步复验，不能信 Electron 缓存。几何和显隐校正用 `SWP_NOZORDER` 保层级；图标与视频的原生层级或两像素容差几何持续两秒不收敛则诊断并恢复。Explorer 重启后重新识别承载层与 AppBar 注册，恢复失败退回窗口模式。全屏、最小化及退出顶层承载清复验计时。

## 恢复

guardian 持恢复原值并成功注册紧急键后才接管。恢复信号绑定会话及 host 创建身份，在恢复锁内置位后拒绝新的接管写入；信号写入失败只停止核验通过的自有 host，诊断失败仍优先恢复。

journal 必须包含明确的接管阶段，仅未开始挂载的记录可直接清理。AppBar 窗口身份和背景样式随接管记录保存；正常停止和 guardian 恢复均先注销 AppBar 释放工作区预留，再恢复原生窗口与系统界面。不保存或回写桌面图标坐标，保留用户使用期间的整理结果。异常写 `.interrupted`、正常停止不写；恢复信号按会话隔离，host 不覆盖 guardian 原因。诊断有界写 `.guardian.log` 和 stderr，失败保留 journal、guardian 重试；所有恢复进程均被强杀时由下次启动处理待恢复记录。

挂载摘除后仅重绘释放屏幕的 Explorer 区域，不重设系统壁纸；恢复失败保留 journal 继续重试。

## 构建

构建需 Rust 1.85 以上、MSVC 链接工具与 Windows SDK；Windows 开发和打包自动准备 helper，终端用户不需工具链。手动入口：

```sh
pnpm --dir client build:native
```

[build-desktop-host.cjs](../scripts/build-desktop-host.cjs) 按 Electron 架构生成 `client/build/<arch>/desktop-host.exe`，随包为 `resources/desktop-host.exe`；复用前检查架构、源码时效及普通／延迟导入，不接受额外 VC／UCRT 依赖。`cargo check --locked` 不验证链接和打包。

开发 launcher 使用独立 Electron 副本，开发和 Windows 打包均写 `PerMonitorV2, PerMonitor` manifest，保留其他元数据；helper／DPI 准备失败阻止桌面模式，恢复入口仍可调用已有 helper 处理待恢复记录。补齐工具链后须重启客户端。主进程及两种 preload 重编译先正常恢复桌面再重启；取消开发亦先等 Electron 退出，60 秒超时仅停自有主进程，由 guardian 恢复。

### 安全运行检查

`check-runtime` 只输出 `runtime_ready` 并以 0 退出，不设置 DPI、访问 Explorer、读写 journal 或注册热键。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 会挂载背景但保留系统任务栏，不预留工作区；系统图标沿用原有显示偏好。加载检查和挂载探测均不代替 [Windows 原生验收](../../scripts/README.md#windows-桌面验收)。
