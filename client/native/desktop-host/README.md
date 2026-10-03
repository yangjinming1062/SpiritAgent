# Windows 桌面宿主

独立 Rust 可执行文件，不依赖 Electron ABI。`host` 通过标准输入的受限 JSON 命令接收主进程登记的 HWND；`guardian` 独立监测主进程、host 与十秒续租，负责异常和 Ctrl+Alt+Shift+D 恢复；`recover` 处理遗留 journal。

## 挂载与输入

Explorer 布局需原生校验：传统布局使用顶层 WorkerW；背景 WorkerW 是禁用的 Progman 子窗口时使用已验证的 Progman，仅调整自有窗口层序。接管临时隐藏 `SHELLDLL_DefView` 和任务栏，保留图标列表原有可见偏好，不修改系统持久设置。桌面前台仅认可挂载层和自有窗口家族。

附着前核对父进程、窗口身份和完整 DPI context。矩形须对应完整显示器，四项物理几何误差均不超过两像素才按系统边界落位；附着后复验外框和客户区。Electron 须关闭 `frame` 与 `thickFrame`，helper 移除非客户区，恢复时还原原始样式、父窗口和几何。

`focus` 仅接受当前会话登记、身份和父层仍匹配、可见且处于桌面前台的自有窗口；不设置其他应用前台。已有焦点时直接返回，否则在修饰键与鼠标按钮释放后临时附着线程，设置并复验焦点。解除附着失败退出 host，由 guardian 恢复；请求与超时裁决见 [主进程](../../main/README.md#桌面承载与恢复)。

## 恢复

guardian 持有原始状态并注册紧急键后才允许接管。host 与 guardian 共享绑定会话及 host 创建身份的恢复信号；恢复锁内置位后拒绝继续接管写操作，避免部分恢复失败时再次隐藏系统界面。恢复信号写入失败时 guardian 只停止身份核验通过的自有 host，诊断失败仍优先恢复系统。

journal 区分尚未改动和已开始挂载；缺少阶段的旧记录仍须恢复，旧 `SysListView32` 记录保持兼容。异常恢复写 `<journal>.interrupted`，正常停止不写；旧标记不决定新会话的恢复信号，首次异常覆盖旧原因，host 不覆盖 guardian 原因。标记写入失败仍优先恢复，诊断有界写入 `<journal>.guardian.log` 和 stderr。

恢复失败保留 journal 和 guardian 重试，不删除唯一恢复依据。所有恢复进程同时被强制终止不能保证即时恢复，下次启动须处理遗留记录。

## 构建

构建机需要 Rust 1.85 以上、Windows MSVC 链接工具和 Windows SDK。Windows 打包自动构建 helper，失败中止；终端用户使用随包组件，无需安装构建工具。

```sh
pnpm --dir client build:native
```

目标参数及门禁见 [build-desktop-host.cjs](../../scripts/build-desktop-host.cjs)。产物在 `client/build/<arch>/desktop-host.exe`，开发和打包均须匹配 Electron 架构，随包路径为 `resources/desktop-host.exe`。helper 静态链接 C++ 运行库，普通及延迟导入含额外 VC／UCRT 依赖时拒绝复制或复用。`cargo check --locked` 不能替代链接与打包验证。

Windows `pnpm dev` 自动准备 helper，仅复用架构和源码时效检查通过的产物。开发入口使用独立 Electron 缓存副本，Windows 打包在签名前写入相同的 `PerMonitorV2, PerMonitor` manifest；保留其他应用元数据及原版开发 Electron。准备失败提示原因并继续窗口模式；补齐工具链并执行 `build:native` 后须重启开发客户端。

主进程和两种 preload 重编译均走正常退出链，先恢复桌面再重启；60 秒超时仅停止自有 Electron 主进程，guardian 负责异常恢复。开发启动与 manifest 入口分别见 [launch-dev-electron.cjs](../../scripts/launch-dev-electron.cjs)、[windows-dpi-manifest.cjs](../../scripts/windows-dpi-manifest.cjs)。

### 安全运行检查

Windows `desktop-host.exe check-runtime` 验证可执行文件能加载并输出 `runtime_ready` 后以 0 退出；参数和输出见 [CLI 入口](src/main.rs)。它不设置 DPI、不访问 Explorer、不读写恢复记录、不注册热键，不能替代原生交互验收。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 下选择桌面模式会挂载 Explorer 子窗口，保留系统图标与任务栏；探测成功不代表交互与恢复已验收。完整矩阵见 [Windows 桌面验收](../../../scripts/README.md#windows-桌面验收)。
