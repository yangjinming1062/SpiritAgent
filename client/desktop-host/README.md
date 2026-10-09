# Windows 桌面宿主

原生宿主、恢复与构建的工程入口，Electron 生命周期归主进程 README。命令和载荷在 [main.rs](src/main.rs)，接管及恢复在 [windows.rs](src/windows.rs)，运行窗口在 [applications.rs](src/windows/applications.rs)，工作区在 [workspace.rs](src/windows/workspace.rs)。

| 模式 | 职责 |
|---|---|
| `host` | 从 stdin 接收受限 JSON，使用主进程登记的 HWND 接管桌面 |
| `guardian` | 独立监测主进程、host 与十秒续租，异常或 Ctrl+Alt+Shift+D 时恢复 |
| `recover` | 恢复遗留 journal |
| `check-runtime` | 验证可执行文件可加载，无桌面接管副作用 |

## 挂载与输入

传统 Explorer 使用顶层 WorkerW；背景 WorkerW 为禁用的 Progman 子窗时使用已验证 Progman。接管临时隐藏 `SHELLDLL_DefView` 和任务栏，保留图标列表可见偏好，不写系统持久设置。交互屏背景排在 Explorer 子窗前，界面保持普通顶层，probe 保留图标层在前。新接管只接受同一显示器的一份背景与一份界面；恢复仍识别旧精灵窗口记录。

附着前核对父进程、窗口身份和完整 DPI context。请求须对应完整显示器，四项物理几何误差均不超过两像素；附着后复验外框和客户区。Electron 关闭 `frame`、`thickFrame`，helper 去掉非客户区；背景 `SetParent` 后移除 `WS_CLIPSIBLINGS`，保持 Explorer 背景合成。样式、父层、几何和置顶信息都进 journal。

`focus` 仅接受身份、父层和可见性匹配的登记窗口，且系统须处于桌面前台。界面即使已有焦点仍先置前；修饰键与鼠标释放后临时附着输入线程，完成复验焦点和实际前台。线程解除失败退出 host，交 guardian 恢复。桌面前台只认可挂载层及自有窗口家族。

## 运行窗口

枚举当前虚拟桌面各屏普通与最小化窗口，排除系统壳、工具浮窗、隐藏和产品窗口。会话共用 WinEvent，命令及轮询前处理消息，合并相邻重复事件但保留移动、销毁顺序；每两秒补扫，队列溢出清登记后全量校准，COM 与监听随会话释放。

未变化不广播；快照按 revision 分批，每行最多 64 KiB，主进程收齐才应用，换代丢弃未完成批次。总量和等待预算归 [explorer-desktop-host.ts](../main/lifecycle/explorer-desktop-host.ts)。

- `activate_external` 复核会话 ID、窗口身份、虚拟桌面和前台，恢复最小化并优先所属模态窗口；临时连接前台和目标输入线程，去重、反序释放并复验实际前台。
- `close_external` 整批核验后逐个投递 `WM_CLOSE`，有未决模态对话框则拒绝整批；回执不代表窗口已关闭。
- 隐藏 owner 下多个主窗保持分支隔离，不能用 `GA_ROOTOWNER` 代替用户所选窗口；禁用主窗只查找该分支可交互弹窗。

## 工作区与层级

`SPI_SETWORKAREA` 临时预留交互屏区域，按原生显示器保留四侧预留量，避免 DIP 取整漂移。工作区变化重应用，写入与 guardian 恢复共用锁；v2 journal 保存显示器、原工作区及窗口角色并兼容 v1，probe 不改工作区或外部程序。

WinEvent 合并显隐、前台、位置、还原和移动缩放后校正普通窗口：先平移，过大再缩小，不激活窗口；系统菜单、输入法候选、隐藏、最小化和真正全屏不调整。全屏判断排除 Explorer 与 WorkerW；期间收起界面，退出恢复，回系统桌面不收起界面。

`showInactive()` 可能让 Windows 收窗进工作区，须按实际原生矩形异步复验，不能信 Electron 缓存。几何和显隐校正用 `SWP_NOZORDER` 保层级；子窗顺序或两像素容差几何持续两秒不收敛则诊断并恢复。全屏、最小化及退出顶层承载清复验计时。

## 恢复

guardian 持恢复原值并成功注册紧急键后才接管。恢复信号绑定会话及 host 创建身份，在恢复锁内置位后拒绝新的接管写入；信号写入失败只停止核验通过的自有 host，诊断失败仍优先恢复。

journal 区分未改动和开始挂载，旧记录缺阶段仍恢复，旧 `SysListView32` 保持兼容。异常写 `.interrupted`、正常停止不写；新会话不以旧标记判断恢复信号，host 不覆盖 guardian 原因。诊断有界写 `.guardian.log` 和 stderr，失败保留 journal、guardian 重试；所有恢复进程均被强杀时只能由下次启动处理遗留记录。

完整挂载摘除后重应用当前壁纸，修复 DWM 长期遮挡后不再合成导致的黑桌面；逐屏壁纸和幻灯片无法无损 SPI 刷新，跳过由系统重绘，刷新失败不影响其他恢复。

## 构建

构建需 Rust 1.85 以上、MSVC 链接工具与 Windows SDK；Windows 开发和打包自动准备 helper，终端用户不需工具链。手动入口：

```sh
pnpm --dir client build:native
```

[build-desktop-host.cjs](../scripts/build-desktop-host.cjs) 按 Electron 架构生成 `client/build/<arch>/desktop-host.exe`，随包为 `resources/desktop-host.exe`；复用前检查架构、源码时效及普通／延迟导入，不接受额外 VC／UCRT 依赖。`cargo check --locked` 不验证链接和打包。

开发 launcher 使用独立 Electron 副本，开发和 Windows 打包均写 `PerMonitorV2, PerMonitor` manifest，保留其他元数据；helper／DPI 准备失败阻止桌面模式，旧 helper 仍可恢复遗留记录。补齐工具链后须重启客户端。主进程及两种 preload 重编译先正常恢复桌面再重启；取消开发亦先等 Electron 退出，60 秒超时仅停自有主进程，由 guardian 恢复。

### 安全运行检查

`check-runtime` 只输出 `runtime_ready` 并以 0 退出，不设置 DPI、访问 Explorer、读写 journal 或注册热键。

## 原生验收

`SPIRITAGENT_DESKTOP_PROBE=1` 会挂载子窗但保留系统图标与任务栏；加载检查和挂载探测均不代替 [Windows 原生验收](../../scripts/README.md#windows-桌面验收)。
