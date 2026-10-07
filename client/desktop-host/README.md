# Windows 桌面宿主

独立 Rust 可执行文件，不依赖 Electron ABI。`host` 通过标准输入的受限 JSON 命令接收主进程登记的 HWND；`guardian` 独立监测主进程、host 与十秒续租，负责异常和 Ctrl+Alt+Shift+D 恢复；`recover` 处理遗留 journal。

## 挂载与输入

Explorer 布局需原生校验：传统布局使用顶层 WorkerW；背景 WorkerW 是禁用的 Progman 子窗口时使用已验证的 Progman，仅调整自有窗口层序。接管临时隐藏 `SHELLDLL_DefView` 和任务栏，保留图标列表原有可见偏好，不修改系统持久设置。桌面前台仅认可挂载层和自有窗口家族。

自有子窗口连续排列在 Explorer 子窗前方，默认伙伴在背景上方；Progman 的 probe 保留图标层在前。背景在 `SetParent` 后移除 `WS_CLIPSIBLINGS`，避免整屏透明伙伴将其合成表面裁空；原始样式由 journal 恢复。每轮复核子窗口顺序，仅在变化时异步校正，持续两秒未收敛则恢复系统。

附着前核对父进程、窗口身份和完整 DPI context。矩形须对应完整显示器，四项物理几何误差均不超过两像素才按系统边界落位；附着后复验外框和客户区。Electron 须关闭 `frame` 与 `thickFrame`，helper 移除非客户区，恢复时还原原始样式、父窗口、几何和置顶层序。

`focus` 仅接受当前会话登记、身份和父层仍匹配、可见且处于桌面前台的自有窗口。交互界面即使已有焦点也先重新置前；需要转移焦点时，在修饰键与鼠标按钮释放后临时附着线程，最后复验输入焦点及界面实际前台。解除附着失败退出 host，由 guardian 恢复；请求与超时裁决见 [主进程](../main/README.md#桌面承载与恢复)。

## 运行窗口

[applications.rs](src/windows/applications.rs)独立筛选当前虚拟桌面所有屏幕的普通及最小化窗口，排除系统壳、隐藏窗口、工具浮窗和产品窗口。会话统一持有 WinEvent，分发给观察器与工作区；每轮命令与轮询前处理系统消息，只合并相邻重复事件并保留移动与销毁顺序；每两秒补扫，队列溢出废弃登记后全量校准。COM 接口与监听随会话释放。未变化的枚举不广播；快照按 revision 分批，每行不超过 64 KiB，主进程收齐后应用，代次变化丢弃未完成批次；总量上限见[桥接实现](../main/lifecycle/explorer-desktop-host.ts)。

`activate_external` 使用登记的会话 ID；切换前处理待分发事件，复核窗口身份、当前虚拟桌面与系统前台，恢复最小化窗口并优先选择所属模态窗口。临时连接桌面前台及目标输入线程，去重并按反序释放，再验证实际前台；已显示窗口也须连接目标线程。

`close_external` 沿用外部窗口交互准入，整批核验通过后逐个投递 `WM_CLOSE`；窗口正在等待模态对话框时拒绝该批请求。关闭回执与状态语义见[启动器契约](../../docs/PROTOCOL.md#桌面呈现与本机启动器)。

隐藏 owner 下的主窗口须保持分支隔离：最近活动与模态激活沿 owner 链关联所选窗口，不能将 `GA_ROOTOWNER` 当作用户窗口。主窗口禁用时查找其分支内可交互的弹窗，避免激活同一隐藏 owner 下的其他主窗口。

## 工作区与层级

背景与默认精灵附着 Explorer；界面为普通顶层窗口，开启置顶的精灵独立保持置顶层。不能将置顶精灵排在普通界面后方，否则 Windows 会取消其置顶。

Windows 可能在 `showInactive()` 后将窗口收进工作区，须按实际原生矩形异步校正，不能依赖 Electron 缓存。几何与显隐校正使用 `SWP_NOZORDER` 保持层序。复验采用两像素容差；持续两秒不稳定则记录实际与目标矩形并回退。全屏、最小化或退出顶层承载时清理复验计时。

[workspace.rs](src/windows/workspace.rs)用 `SPI_SETWORKAREA` 临时预留交互屏工作区，按原生显示器边界保留请求矩形的四侧预留量，避免 DIP 取整改变边界。工作区变化后重新应用；写操作与 guardian 恢复共用锁。v2 journal 保存显示器标识、原工作区和窗口角色，兼容 v1；probe 不修改工作区或外部程序。

WinEvent 合并显示、前台、位置、还原和移动缩放事件后校正普通窗口；先平移，尺寸过大再缩小，校正不激活窗口。系统菜单、输入法候选、隐藏、最小化和真正全屏窗口不参与，拒绝调整时有界上报。全屏避让只判断外部顶层窗口，排除 Explorer 桌面与 WorkerW；焦点切回系统桌面不收起界面。全屏期间收起界面与置顶精灵，退出后恢复并保持层序。

## 恢复

guardian 持有原始状态并注册紧急键后才允许接管。host 与 guardian 共享绑定会话及 host 创建身份的恢复信号；恢复锁内置位后拒绝继续接管写操作，避免部分恢复失败时再次隐藏系统界面。恢复信号写入失败时 guardian 只停止身份核验通过的自有 host，诊断失败仍优先恢复系统。

journal 区分尚未改动和已开始挂载；缺少阶段的旧记录仍须恢复，旧 `SysListView32` 记录保持兼容。异常恢复写 `<journal>.interrupted`，正常停止不写；旧标记不决定新会话的恢复信号，首次异常覆盖旧原因，host 不覆盖 guardian 原因。标记写入失败仍优先恢复，诊断有界写入 `<journal>.guardian.log` 和 stderr。

恢复失败保留 journal 和 guardian 重试，不删除唯一恢复依据。所有恢复进程同时被强制终止不能保证即时恢复，下次启动须处理遗留记录。

完成挂载的恢复会重应用当前壁纸：全屏子窗长期遮挡可令 DWM 不再合成壁纸表面，摘除后偶发黑桌面。逐显示器壁纸（`PerMonitorSettings` 含 `WallpaperSRC`）与幻灯片壁纸无法用 SPI 无损刷新，跳过并保留系统自身重绘；刷新失败不影响恢复结果。

## 构建

构建机需要 Rust 1.85 以上、Windows MSVC 链接工具和 Windows SDK。Windows 打包自动构建 helper，失败中止；终端用户使用随包组件，无需安装构建工具。

```sh
pnpm --dir client build:native
```

目标参数及门禁见 [build-desktop-host.cjs](../scripts/build-desktop-host.cjs)。产物在 `client/build/<arch>/desktop-host.exe`，开发和打包均须匹配 Electron 架构，随包路径为 `resources/desktop-host.exe`。helper 静态链接 C++ 运行库，普通及延迟导入含额外 VC／UCRT 依赖时拒绝复制或复用。`cargo check --locked` 不能替代链接与打包验证。

Windows `pnpm dev` 自动准备 helper，仅复用架构、源码时效和运行库检查通过的产物。开发入口使用独立 Electron 缓存副本，Windows 打包在签名前写入相同的 `PerMonitorV2, PerMonitor` manifest；保留其他应用元数据及原版开发 Electron。helper 或 DPI 准备失败时向主进程传递原因并阻止启用桌面模式，旧 helper 仍可用于遗留恢复；补齐工具链并执行 `build:native` 后须重启开发客户端。

开发 launcher 独立持有 Electron，`concurrently` 只管理 Vite 与 tsup。主进程和两种 preload 重编译均走正常退出链，先恢复桌面再重启；取消开发或构建工具退出时，launcher 先请求 Electron 恢复桌面，等待退出后清理其余开发工具。60 秒超时仅停止自有 Electron 主进程，guardian 负责异常恢复。开发启动与 manifest 入口分别见 [launch-dev-electron.cjs](../scripts/launch-dev-electron.cjs)、[windows-dpi-manifest.cjs](../scripts/windows-dpi-manifest.cjs)。

### 安全运行检查

Windows `desktop-host.exe check-runtime` 验证可执行文件能加载并输出 `runtime_ready` 后以 0 退出；参数和输出见 [CLI 入口](src/main.rs)。它不设置 DPI、不访问 Explorer、不读写恢复记录、不注册热键，不能替代原生交互验收。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 下选择桌面模式会挂载 Explorer 子窗口，保留系统图标与任务栏；探测成功不代表交互与恢复已验收。完整矩阵见 [Windows 桌面验收](../../scripts/README.md#windows-桌面验收)。
